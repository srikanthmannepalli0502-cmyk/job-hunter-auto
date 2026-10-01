from abc import ABC, abstractmethod

import httpx
from loguru import logger

from jobhunter.config import SearchConfig
from jobhunter.models import JobListing

USER_AGENT = "jobhunter/2.0 (+https://github.com/srikanthmannepalli0502-cmyk/job-hunter-auto)"


class JobSource(ABC):
    """A job board accessed through its official public API."""

    name: str = ""

    def __init__(self, client: httpx.Client | None = None):
        self.client = client or httpx.Client(timeout=20, headers={"User-Agent": USER_AGENT})
        self.logger = logger.bind(source=self.name)

    @abstractmethod
    def fetch(self, search: SearchConfig) -> list[JobListing]:
        """Return job listings matching the search."""


def dedupe(jobs: list[JobListing]) -> list[JobListing]:
    """Drop repeats of the same title + company, keeping the first one seen."""
    seen: set[str] = set()
    unique = []
    for job in jobs:
        if job.dedupe_key not in seen:
            seen.add(job.dedupe_key)
            unique.append(job)
    return unique


def is_excluded(job: JobListing, exclude_keywords: list[str]) -> bool:
    title = job.title.lower()
    return any(word.lower() in title for word in exclude_keywords)
