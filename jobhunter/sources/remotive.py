import html
import re
from datetime import datetime

from jobhunter.config import SearchConfig
from jobhunter.models import JobListing
from jobhunter.sources.base import JobSource

API_URL = "https://remotive.com/api/remote-jobs"


def strip_html(text: str) -> str:
    text = re.sub(r"<(br|/p|/li|/h\d)\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text)
    return re.sub(r"\n\s*\n+", "\n\n", text).strip()


class RemotiveSource(JobSource):
    """Remotive public API for remote jobs (no key needed).

    Remotive asks API users to link back to the original posting and credit Remotive,
    and to keep request volume low; one search per run is well within that.
    """

    name = "remotive"

    def fetch(self, search: SearchConfig) -> list[JobListing]:
        if not search.include_remote:
            return []
        resp = self.client.get(API_URL, params={"search": search.query, "limit": search.max_results})
        resp.raise_for_status()
        jobs = [self._parse(item) for item in resp.json().get("jobs", [])]
        self.logger.info(f"Remotive: {len(jobs)} jobs")
        return jobs

    @staticmethod
    def _parse(item: dict) -> JobListing:
        published = item.get("publication_date")
        return JobListing(
            title=item.get("title", "").strip(),
            company=item.get("company_name", "Unknown").strip(),
            location=f"Remote ({item.get('candidate_required_location') or 'Anywhere'})",
            job_url=item.get("url", ""),
            source="remotive",
            description=strip_html(item.get("description", "")),
            date_posted=datetime.fromisoformat(published) if published else None,
        )
