from datetime import datetime

from jobhunter.config import SearchConfig, settings
from jobhunter.models import JobListing
from jobhunter.sources.base import JobSource

API_URL = "https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"


class AdzunaSource(JobSource):
    """Adzuna job search API (free developer key: https://developer.adzuna.com/).

    Adzuna aggregates postings from many boards and company sites. Its API returns a
    shortened description, so for tailoring you can paste the full posting (see README).
    """

    name = "adzuna"

    def __init__(self, app_id: str = "", app_key: str = "", **kwargs):
        super().__init__(**kwargs)
        self.app_id = app_id or settings.adzuna_app_id
        self.app_key = app_key or settings.adzuna_app_key

    def fetch(self, search: SearchConfig) -> list[JobListing]:
        if not (self.app_id and self.app_key):
            self.logger.warning("ADZUNA_APP_ID / ADZUNA_APP_KEY not set; skipping Adzuna.")
            return []

        params = {
            "app_id": self.app_id,
            "app_key": self.app_key,
            "what": search.query,
            "results_per_page": min(search.max_results, 50),
            "max_days_old": search.max_days_old,
            "sort_by": "date",
            "content-type": "application/json",
        }
        if search.location:
            params["where"] = search.location

        resp = self.client.get(API_URL.format(country=search.country, page=1), params=params)
        resp.raise_for_status()
        jobs = [self._parse(item) for item in resp.json().get("results", [])]
        self.logger.info(f"Adzuna: {len(jobs)} jobs")
        return jobs

    @staticmethod
    def _parse(item: dict) -> JobListing:
        created = item.get("created")
        return JobListing(
            title=item.get("title", "").strip(),
            company=(item.get("company") or {}).get("display_name", "Unknown").strip(),
            location=(item.get("location") or {}).get("display_name", ""),
            job_url=item.get("redirect_url", ""),
            source="adzuna",
            description=item.get("description", ""),
            salary_min=item.get("salary_min"),
            salary_max=item.get("salary_max"),
            date_posted=datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None,
        )
