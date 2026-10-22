"""Hacker News "Ask HN: Who is hiring?" (monthly thread), read through the public Algolia HN API.

Each top-level comment is one company's post, by convention starting with a line like
"Acme | Data Analyst, ML Engineer | New York or Remote (US) | Full-time | VISA | acme.com/jobs".
A post is kept when it names a job title matching your search (a title ends in Analyst,
Scientist, Engineer...; a sentence that merely mentions "analytics" doesn't count) and its
location reads as the US. Many posts say outright whether they sponsor visas.
"""

import html
import re
from datetime import datetime, timedelta, timezone

from jobhunter.config import SearchConfig
from jobhunter.models import JobListing
from jobhunter.sources.base import JobSource, classify_job_type

SEARCH = "https://hn.algolia.com/api/v1/search_by_date"
_STATES = ("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY "
           "NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC").split()
_STATE_RX = "(?:" + "|".join(_STATES) + ")"
ITEM = "https://hn.algolia.com/api/v1/items/{id}"

_CITY_STATE = r"\b[A-Z][a-z]+, ?" + _STATE_RX + r"\b"  # "Austin, TX" (not "London, UK")
_US = re.compile(r"\b(?:us|usa|u\.s\.?|united states|remote|nyc|new york|sf|san francisco|bay area|seattle|"
                 r"boston|austin|chicago|los angeles|denver|atlanta|washington|dc)\b|" + _CITY_STATE, re.I)
_SURELY_US = re.compile(r"\b(?:US|USA|U\.S\.?|United States|NYC|SF)\b|" + _CITY_STATE)
_NOT_US = re.compile(r"\b(eu|europe|uk|emea|india|apac|latam|poland|germany|london|berlin|paris|amsterdam|"
                     r"toronto|bangalore|bengaluru|cracow|krakow|cet|cest|gmt)\b", re.I)
# A job title ends in one of these nouns, so "analytics experience" or a sentence isn't a title.
_TITLE = re.compile(
    r"((?:(?:Senior|Sr\.?|Junior|Jr\.?|Staff|Lead|Principal|Founding|Applied|Associate|Entry[- ]Level)\s+)*"
    r"(?:[A-Z][A-Za-z/&+.-]*\s+){0,4}"
    r"(?:Analyst|Scientist|Engineer|Developer|Architect|Intern|Researcher|Specialist)s?)\b")


def _text(comment_html: str) -> str:
    t = re.sub(r"<p>", "\n", comment_html or "")
    t = re.sub(r"<a [^>]*href=\"([^\"]+)\"[^>]*>.*?</a>", r"\1", t)
    return html.unescape(re.sub(r"<[^>]+>", "", t)).strip()


def parse_post(item: dict, titles: list[str]) -> JobListing | None:
    """One 'Who is hiring' comment -> a job listing, if it names one of your roles in the US."""
    text = _text(item.get("text") or "")
    if not text:
        return None
    first = text.split("\n", 1)[0]
    parts = [p.strip() for p in first.split("|") if p.strip()]
    if len(parts) < 2:
        return None
    wanted = [t.lower() for t in titles] + ["ml engineer", "ai engineer", "data engineer"]
    named = [m.group(1).strip() for m in _TITLE.finditer(" | ".join(parts[1:]) + "\n" + text)]
    role = next((n for n in named if any(w in n.lower() for w in wanted)), None)
    if role is None:
        return None
    where = " | ".join(parts[1:])
    if not _US.search(where) or (_NOT_US.search(where) and not _SURELY_US.search(where)):
        return None
    location = next((p for p in parts[1:] if _US.search(p) and role not in p), "See post")
    created = item.get("created_at")
    return JobListing(
        title=role[:150], company=re.sub(r"\s*\(?https?://\S*", "", parts[0]).strip()[:120],
        location=location[:120], job_url=f"https://news.ycombinator.com/item?id={item.get('id')}",
        source="hackernews", description=text[:6000],
        date_posted=datetime.fromisoformat(created.replace("Z", "+00:00")) if created else None,
        job_type=classify_job_type(role + " " + where),
    )


class HackerNewsSource(JobSource):
    """The latest 'Ask HN: Who is hiring?' thread."""

    name = "hackernews"

    def __init__(self, titles: list[str], client=None):
        super().__init__(client)
        self.titles = titles

    def fetch(self, search: SearchConfig) -> list[JobListing]:
        r = self.client.get(SEARCH, params={"tags": "story,author_whoishiring", "hitsPerPage": 10})
        r.raise_for_status()
        story = next((h for h in r.json().get("hits", []) if "who is hiring" in (h.get("title") or "").lower()), None)
        if story is None:
            return []
        thread = self.client.get(ITEM.format(id=story["objectID"]))
        thread.raise_for_status()
        since = datetime.now(timezone.utc) - timedelta(days=search.max_days_old)
        jobs = []
        for item in thread.json().get("children", []):
            job = parse_post(item, self.titles)
            if job and (job.date_posted is None or job.date_posted >= since):
                jobs.append(job)
        self.logger.info(f"Hacker News '{story.get('title')}': {len(jobs)} matching posts")
        return jobs
