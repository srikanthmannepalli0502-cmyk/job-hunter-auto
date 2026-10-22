"""LLM clients used for resume tailoring and cover notes.

- Azure OpenAI (default when AZURE_OPENAI_ENDPOINT is set): signs in with your Azure identity
  (`az login`), so no API key is stored. Works with reasoning models such as gpt-5-mini.
- Gemini: set LLM_PROVIDER=gemini and GEMINI_API_KEY (Google AI Studio). On the free tier Google
  may use prompts to improve its products; the resume composer sends no name, phone or email.
- Groq: set LLM_PROVIDER=groq and GROQ_API_KEY.
"""

import time

from loguru import logger

from jobhunter.config import settings

MAX_RETRIES = 3


def _retry(call, provider: str):
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            return call()
        except Exception as e:
            rate_limited = "rate_limit" in str(e).lower() or "429" in str(e)
            if rate_limited and attempt < MAX_RETRIES:
                wait = 10 * attempt
                logger.warning(f"{provider} rate limit hit; retrying in {wait}s ({attempt}/{MAX_RETRIES})")
                time.sleep(wait)
                continue
            raise
    return ""


class GroqClient:
    """Groq's chat API."""

    def __init__(self, api_key: str = "", model: str = ""):
        from groq import Groq

        key = api_key or settings.groq_api_key
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set (https://console.groq.com/keys)")
        self.client = Groq(api_key=key)
        self.model = model or settings.groq_model

    def generate(self, prompt: str, temperature: float = 0.4) -> str:
        def call():
            response = self.client.chat.completions.create(
                model=self.model, messages=[{"role": "user", "content": prompt}],
                temperature=temperature, max_tokens=4096,
            )
            return response.choices[0].message.content or ""

        return _retry(call, "Groq")


class GeminiClient:
    """Google's Gemini API (generateContent over REST, so no extra package)."""

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str = "", model: str = ""):
        import httpx

        self.key = api_key or settings.gemini_api_key
        if not self.key:
            raise RuntimeError("GEMINI_API_KEY is not set (https://aistudio.google.com/apikey)")
        self.model = model or settings.gemini_model
        self.http = httpx.Client(timeout=300)
        self.reasoning_effort = "low"  # mapped to Gemini's thinking level

    def generate(self, prompt: str, temperature: float = 0.4) -> str:
        def call():
            body = {"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                    "generationConfig": {"maxOutputTokens": 32000,
                                         "thinkingConfig": {"thinkingLevel": self.reasoning_effort}}}
            r = self.http.post(self.URL.format(model=self.model), json=body, headers={"x-goog-api-key": self.key})
            if r.status_code == 400 and "thinking" in r.text.lower():  # model without thinking levels
                body["generationConfig"].pop("thinkingConfig")
                r = self.http.post(self.URL.format(model=self.model), json=body, headers={"x-goog-api-key": self.key})
            if r.status_code in (429, 500, 503):  # rate limit or "model overloaded": wait and retry
                raise RuntimeError(f"{r.status_code} rate_limit from Gemini")
            r.raise_for_status()
            parts = (r.json().get("candidates") or [{}])[0].get("content", {}).get("parts", [])
            return "".join(p.get("text", "") for p in parts if not p.get("thought"))

        return _retry(call, "Gemini")


class AzureOpenAIClient:
    """Azure OpenAI with Entra ID sign-in (DefaultAzureCredential: `az login` on your PC)."""

    def __init__(self, endpoint: str = "", deployment: str = ""):
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
        from openai import AzureOpenAI

        endpoint = endpoint or settings.azure_openai_endpoint
        if not endpoint:
            raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set")
        token = get_bearer_token_provider(DefaultAzureCredential(), "https://cognitiveservices.azure.com/.default")
        self.client = AzureOpenAI(azure_endpoint=endpoint, azure_ad_token_provider=token,
                                  api_version=settings.azure_openai_api_version, max_retries=2, timeout=300)
        self.deployment = deployment or settings.azure_openai_deployment
        self.reasoning_effort = "low"  # resume tailoring raises it: rule-following matters more there

    def generate(self, prompt: str, temperature: float = 0.4) -> str:
        # Reasoning models ignore temperature; their reasoning also counts against the token cap.
        def call():
            response = self.client.chat.completions.create(
                model=self.deployment, messages=[{"role": "user", "content": prompt}],
                reasoning_effort=self.reasoning_effort, max_completion_tokens=32000,
            )
            return response.choices[0].message.content or ""

        return _retry(call, "Azure OpenAI")


def LLMClient():
    """The configured client: Azure OpenAI if AZURE_OPENAI_ENDPOINT is set (or LLM_PROVIDER=azure), else Groq."""
    provider = settings.llm_provider or ("azure" if settings.azure_openai_endpoint else "groq")
    if provider == "gemini":
        return GeminiClient()
    return AzureOpenAIClient() if provider == "azure" else GroqClient()
