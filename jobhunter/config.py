"""Settings from environment variables (.env) and the personal profile file (profile.toml)."""

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_PROFILE_PATH = Path(os.getenv("JOBHUNTER_PROFILE", "profile.toml"))


@dataclass
class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    llm_provider: str = os.getenv("LLM_PROVIDER", "")  # azure or groq; default azure if its endpoint is set
    azure_openai_endpoint: str = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    azure_openai_deployment: str = os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-5-mini")
    azure_openai_api_version: str = os.getenv("AZURE_OPENAI_API_VERSION", "2025-04-01-preview")
    adzuna_app_id: str = os.getenv("ADZUNA_APP_ID", "")
    adzuna_app_key: str = os.getenv("ADZUNA_APP_KEY", "")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///jobhunter.db")


@dataclass
class SearchConfig:
    query: str = "data analyst"
    queries: list[str] = field(default_factory=list)  # several searches per run; overrides query
    location: str = ""
    country: str = "us"
    max_results: int = 25
    max_days_old: int = 14
    include_remote: bool = True
    exclude_keywords: list[str] = field(default_factory=list)
    min_score: float = 0  # jobs scoring below this are not stored
    # Which kinds of positions to keep: full_time, part_time, intern, contract.
    job_types: list[str] = field(default_factory=lambda: ["full_time", "part_time", "intern", "contract"])

    @property
    def all_queries(self) -> list[str]:
        return self.queries or [self.query]


@dataclass
class ScoringConfig:
    keywords: dict[str, int] = field(default_factory=dict)
    preferred_titles: list[str] = field(default_factory=list)
    locations: dict[str, int] = field(default_factory=dict)


@dataclass
class SponsorshipConfig:
    # Sponsor table exported by the H-1B Tech Jobs Tracker (pipeline/export_sponsors.py).
    data_file: str = "data/h1b_sponsors.csv"
    # Role groups whose H-1B filings count most: analyst, data_science, ai, data_engineering, software.
    roles: list[str] = field(default_factory=lambda: ["analyst", "data_science", "ai"])
    # Share (0-100) of the overall score that comes from sponsorship; the rest is skills/title/location fit.
    weight: float = 35
    hide_non_sponsors: bool = False


@dataclass
class BoardsConfig:
    # Target companies' Greenhouse / Lever / Ashby boards (built by `discover-boards`).
    file: str = "data/company_boards.csv"
    enabled: bool = True


@dataclass
class Profile:
    name: str = ""
    linkedin_url: str = ""
    base_resume: str = "resumes/base_resume.pdf"
    output_dir: str = "resumes/tailored"
    # Text that must survive tailoring (e.g. project and certification names).
    must_keep: list[str] = field(default_factory=list)
    search: SearchConfig = field(default_factory=SearchConfig)
    scoring: ScoringConfig = field(default_factory=ScoringConfig)
    sponsorship: SponsorshipConfig = field(default_factory=SponsorshipConfig)
    boards: BoardsConfig = field(default_factory=BoardsConfig)


def load_profile(path: Path = DEFAULT_PROFILE_PATH) -> Profile:
    """Load profile.toml. Copy profile.example.toml to profile.toml to get started."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found. Copy profile.example.toml to {path} and fill in your details."
        )
    data = tomllib.loads(path.read_text(encoding="utf-8"))

    candidate = data.get("candidate", {})
    scoring = data.get("scoring", {})
    return Profile(
        name=candidate.get("name", ""),
        linkedin_url=candidate.get("linkedin_url", ""),
        base_resume=candidate.get("base_resume", "resumes/base_resume.pdf"),
        output_dir=candidate.get("output_dir", "resumes/tailored"),
        must_keep=candidate.get("must_keep", []),
        search=SearchConfig(**data.get("search", {})),
        scoring=ScoringConfig(
            keywords=scoring.get("keywords", {}),
            preferred_titles=scoring.get("preferred_titles", []),
            locations=scoring.get("locations", {}),
        ),
        sponsorship=SponsorshipConfig(**data.get("sponsorship", {})),
        boards=BoardsConfig(**data.get("boards", {})),
    )


settings = Settings()
