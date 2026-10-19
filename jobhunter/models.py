from dataclasses import asdict, dataclass, field, fields
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
    job_type: str = "full_time"  # full_time, part_time, intern, contract (see sources.base.classify_job_type)
    match_score: float = 0.0  # fit with your skills, titles and locations (0-100)
    sponsor_score: float = 0.0  # H-1B sponsorship signal (0-100), see sponsors.py
    sponsor_note: str = ""
    sponsorship_warning: str = ""  # e.g. the posting says it won't sponsor (sponsors.sponsorship_warning)
    overall_score: float = 0.0  # fit and sponsorship combined; used for ranking

    @property
    def dedupe_key(self) -> str:
        """Same role at the same company counts as one job, even if posted on several boards."""
        return f"{self.title.lower().strip()}|{self.company.lower().strip()}"

    def to_dict(self) -> dict:
        """JSON-friendly copy (dates as ISO text), for sending results to the hosted app."""
        d = asdict(self)
        for k in ("date_posted", "date_fetched"):
            d[k] = d[k].isoformat() if d[k] else None
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "JobListing":
        known = {f.name for f in fields(cls)}
        data = {k: v for k, v in d.items() if k in known}
        for k in ("date_posted", "date_fetched"):
            if data.get(k):
                data[k] = datetime.fromisoformat(data[k])
            elif k == "date_fetched":
                data.pop(k, None)
        return cls(**data)
