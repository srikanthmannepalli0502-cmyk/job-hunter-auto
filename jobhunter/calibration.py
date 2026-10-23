"""Do your scores predict results? Interview and offer rates of your applications by score band.

An application still waiting (status "applied", sent less than WAITING_DAYS ago) has no result yet
and is left out; one older than that with no reply counts as no interview. Withdrawn applications
are left out. A band with fewer than MIN_APPLICATIONS results shows no rate: a rate from two or
three applications says nothing. This only reports; it never changes the scoring weights.
The idea follows career-ops' calibrate (MIT).
"""

from dataclasses import dataclass
from datetime import datetime, timezone

BANDS = ((80, "80+"), (65, "65-79"), (50, "50-64"), (0, "under 50"))
SCORES = (("overall_score", "Overall"), ("match_score", "Fit"), ("sponsor_score", "H-1B sponsor"))
WAITING_DAYS = 21
MIN_APPLICATIONS = 5


@dataclass
class Band:
    label: str
    results: int = 0
    interviews: int = 0
    offers: int = 0
    waiting: int = 0

    def rate(self, n: int) -> str:
        return f"{round(100 * n / self.results)}%" if self.results >= MIN_APPLICATIONS else "too few"


def band_of(score: float) -> str:
    return next(label for low, label in BANDS if (score or 0) >= low)


def outcome(status: str, applied: datetime, now: datetime) -> str | None:
    """'interview', 'offer', 'none' (rejected or no reply in time), 'waiting', or None (withdrawn)."""
    if status == "withdrawn":
        return None
    if status in ("interview", "offer"):
        return status
    if status == "applied":
        applied = applied if applied.tzinfo else applied.replace(tzinfo=timezone.utc)
        return "waiting" if (now - applied).days < WAITING_DAYS else "none"
    return "none"


def calibrate(apps: list, score: str = "overall_score", now: datetime | None = None) -> list[Band]:
    """apps: (Application, Job) pairs. One Band per score band, highest first."""
    now = now or datetime.now(timezone.utc)
    bands = {label: Band(label) for _, label in BANDS}
    for app, job in apps:
        result = outcome(app.status, app.applied_date, now)
        if result is None:
            continue
        b = bands[band_of(getattr(job, score))]
        if result == "waiting":
            b.waiting += 1
            continue
        b.results += 1
        b.interviews += result in ("interview", "offer")
        b.offers += result == "offer"
    return [bands[label] for _, label in BANDS]


def table_rows(apps: list, now: datetime | None = None) -> list[tuple[str, list[Band]]]:
    return [(name, calibrate(apps, attr, now)) for attr, name in SCORES]
