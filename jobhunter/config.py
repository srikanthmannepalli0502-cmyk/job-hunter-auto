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
    adzuna_app_id: str = os.getenv("ADZUNA_APP_ID", "")
    adzuna_app_key: str = os.getenv("ADZUNA_APP_KEY", "")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///jobhunter.db")


@dataclass
class SearchConfig:
    query: str = "data analyst"
    location: str = ""
    country: str = "us"
    max_results: int = 25
    max_days_old: int = 14
    include_remote: bool = True
    exclude_keywords: list[str] = field(default_factory=list)
    min_score: float = 0  # jobs scoring below this are not stored


@dataclass
class ScoringConfig:
    keywords: dict[str, int] = field(default_factory=dict)
    preferred_titles: list[str] = field(default_factory=list)
    locations: dict[str, int] = field(default_factory=dict)


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
    )


settings = Settings()
