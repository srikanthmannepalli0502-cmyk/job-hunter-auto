from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class JobListing:
    """Standard format for a job listing from any source."""

    title: str
    company: str
    location: str
    job_url: str
    source: str
    description: str = ""
    salary_min: float | None = None
    salary_max: float | None = None
    date_posted: datetime | None = None
    date_fetched: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    match_score: float = 0.0  # fit with your skills, titles and locations (0-100)
    sponsor_score: float = 0.0  # H-1B sponsorship signal (0-100), see sponsors.py
    sponsor_note: str = ""
    overall_score: float = 0.0  # fit and sponsorship combined; used for ranking

    @property
    def dedupe_key(self) -> str:
        """Same role at the same company counts as one job, even if posted on several boards."""
        return f"{self.title.lower().strip()}|{self.company.lower().strip()}"
