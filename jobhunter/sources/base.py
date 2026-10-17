import re
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


JOB_TYPES = ("full_time", "part_time", "intern", "contract")
_INTERN = re.compile(r"\bintern(ship)?s?\b|\bco-?op\b|\bapprentice", re.I)
_CONTRACT = re.compile(r"\bcontract|\btemporary\b|\btemp\b|\bseasonal\b|fixed[- ]term|freelance", re.I)
_PART_TIME = re.compile(r"part[- ]?time", re.I)


def classify_job_type(title: str, *hints: str) -> str:
    """full_time, part_time, intern or contract, from the title and any employment-type text
    the source provides (e.g. Lever "Intern", Ashby "PartTime", Workday "Part time")."""
    text = " ".join([title, *(h or "" for h in hints)])
    if _INTERN.search(text):
        return "intern"
    if _CONTRACT.search(text):
        return "contract"
    if _PART_TIME.search(text) or "parttime" in text.lower().replace(" ", ""):
        return "part_time"
    return "full_time"


def is_excluded(job: JobListing, exclude_keywords: list[str]) -> bool:
    title = job.title.lower()
    return any(word.lower() in title for word in exclude_keywords)
