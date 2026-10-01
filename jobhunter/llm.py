import time

from loguru import logger

from jobhunter.config import settings

MAX_RETRIES = 3


class LLMClient:
    """Thin wrapper around Groq's chat API (free tier: Llama 3.3 70B)."""

    def __init__(self, api_key: str = "", model: str = ""):
        from groq import Groq

        key = api_key or settings.groq_api_key
        if not key:
            raise RuntimeError("GROQ_API_KEY is not set. Get a free key at https://console.groq.com/keys")
        self.client = Groq(api_key=key)
        self.model = model or settings.groq_model

    def generate(self, prompt: str, temperature: float = 0.4) -> str:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=temperature,
                    max_tokens=4096,
                )
                return response.choices[0].message.content or ""
            except Exception as e:
                rate_limited = "rate_limit" in str(e).lower() or "429" in str(e)
                if rate_limited and attempt < MAX_RETRIES:
                    wait = 10 * attempt
                    logger.warning(f"Groq rate limit hit; retrying in {wait}s ({attempt}/{MAX_RETRIES})")
                    time.sleep(wait)
                    continue
                raise
        return ""
