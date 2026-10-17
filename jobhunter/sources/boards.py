"""Company career sites: Greenhouse, Lever and Ashby job-board APIs, and Workday career sites.

These APIs exist so companies can show their openings on other sites; they need no key.
data/company_boards.csv lists which board each target company uses. `discover-boards`
builds it from the H-1B sponsor table by trying likely board names for each company.

Greenhouse, Lever and Ashby offer these APIs so companies can show openings elsewhere. Workday
career sites load their postings from a JSON endpoint that robots.txt does not exclude; it is
read politely (a delay between requests, a few pages per search). Postings are kept if they are
in the US (or remote without a non-US country), one of your job types, recent, and their title
matches one of your preferred titles.
"""

import csv
import html
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

from jobhunter.config import SearchConfig
from jobhunter.models import JobListing
from jobhunter.sources.base import JobSource, classify_job_type
from jobhunter.sources.remotive import strip_html
from jobhunter.sponsors import normalize_name

GREENHOUSE = "https://boards-api.greenhouse.io/v1/boards/{slug}"
LEVER = "https://api.lever.co/v0/postings/{slug}"
ASHBY = "https://api.ashbyhq.com/posting-api/job-board/{slug}"
BOARD_COLUMNS = ["employer_key", "employer", "ats", "slug", "board_name"]

# ----------------------------------------------------------------------------- filters

_STATES = (
    "AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ "
    "NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC"
).split()
_STATE_NAMES = (
    "alabama|alaska|arizona|arkansas|california|colorado|connecticut|delaware|florida|georgia|hawaii|"
    "idaho|illinois|indiana|iowa|kansas|kentucky|louisiana|maine|maryland|massachusetts|michigan|"
    "minnesota|mississippi|missouri|montana|nebraska|nevada|new hampshire|new jersey|new mexico|"
    "new york|north carolina|north dakota|ohio|oklahoma|oregon|pennsylvania|rhode island|"
    "south carolina|south dakota|tennessee|texas|utah|vermont|virginia|washington|west virginia|"
    "wisconsin|wyoming"
)
_US = re.compile(
    rf"united states|\bu\.?s\.?a?\b|\b({_STATE_NAMES})\b|,\s*({'|'.join(_STATES)})\b|"
    r"\b(san francisco|seattle|chicago|boston|austin|dallas|houston|atlanta|denver|nyc|new york city|"
    r"los angeles|bay area|silicon valley|washington,? d\.?c)\b",
    re.I,
)
_NON_US = re.compile(
    r"\b(canada|toronto|vancouver|montreal|mexico|brazil|argentina|colombia|uk|united kingdom|england|"
    r"london|ireland|dublin|germany|berlin|munich|france|paris|spain|madrid|barcelona|portugal|lisbon|"
    r"netherlands|amsterdam|poland|warsaw|switzerland|zurich|sweden|denmark|norway|finland|italy|"
    r"israel|tel aviv|india|bangalore|bengaluru|hyderabad|pune|chennai|mumbai|delhi|gurgaon|noida|"
    r"singapore|japan|tokyo|china|shanghai|beijing|hong kong|taiwan|korea|seoul|australia|sydney|"
    r"melbourne|new zealand|philippines|manila|vietnam|indonesia|malaysia|thailand|uae|dubai|"
    r"saudi|south africa|nigeria|kenya|egypt|emea|apac|latam|europe)\b",
    re.I,
)


def is_us_location(location: str, country: str = "") -> bool:
    """US, or remote/unspecified with no other country named. A posting listing both a US
    and a non-US location counts as US."""
    if country:
        return country.upper() in ("US", "USA")
    if _US.search(location or ""):
        return True
    return not _NON_US.search(location or "")


def title_matches(title: str, wanted: list[str]) -> bool:
    t = title.lower()
    return any(w.lower() in t for w in wanted)


# ----------------------------------------------------------------------------- source

@dataclass(frozen=True)
class Board:
    employer_key: str
    employer: str
    ats: str   # greenhouse, lever, ashby or workday
    slug: str  # board name; for Workday the career site URL, e.g. https://acme.wd5.myworkdayjobs.com/External


def load_boards(path: str | Path) -> list[Board]:
    path = Path(path)
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [Board(r["employer_key"], r["employer"], r["ats"], r["slug"]) for r in csv.DictReader(f)]


class CompanyBoardsSource(JobSource):
    """Openings on the target companies' Greenhouse, Lever, Ashby and Workday career sites."""

    name = "boards"

    def __init__(self, boards: list[Board], titles: list[str], delay: float = 0.3,
                 workday_max_pages: int = 3, **kwargs):
        super().__init__(**kwargs)
        self.boards = boards
        self.titles = titles
        self.delay = delay
        self.workday_max_pages = workday_max_pages

    def fetch(self, search: SearchConfig) -> list[JobListing]:
        since = datetime.now(timezone.utc) - timedelta(days=search.max_days_old)
        jobs: list[JobListing] = []
        failed = 0
        for board in self.boards:
            try:
                # list(): the parsers are generators, so request errors surface while iterating.
                postings = list(getattr(self, f"_fetch_{board.ats}")(board, search, since))
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                failed += 1
                self.logger.warning(f"{board.employer} ({board.ats}) failed: {e}")
                continue
            for job, country in postings:
                if not title_matches(job.title, self.titles) or job.job_type not in search.job_types:
                    continue
                if not is_us_location(job.location, country):
                    continue
                if job.date_posted and job.date_posted < since:
                    continue
                jobs.append(job)
            time.sleep(self.delay)
        self.logger.info(f"Company boards: {len(jobs)} matching jobs from {len(self.boards) - failed} "
                         f"boards ({failed} failed)")
        return jobs

    def _get(self, url: str, **params):
        resp = self.client.get(url, params=params)
        resp.raise_for_status()
        return resp.json()

    def _fetch_greenhouse(self, b: Board, search: SearchConfig, since: datetime):
        data = self._get(GREENHOUSE.format(slug=b.slug) + "/jobs", content="true")
        for j in data.get("jobs", []):
            posted = j.get("first_published") or j.get("updated_at")
            title = j.get("title", "").strip()
            yield JobListing(
                title=title, company=b.employer,
                location=(j.get("location") or {}).get("name", ""), job_url=j.get("absolute_url", ""),
                source="greenhouse", description=strip_html(html.unescape(j.get("content") or "")),
                date_posted=datetime.fromisoformat(posted) if posted else None,
                job_type=classify_job_type(title),
            ), ""

    def _fetch_lever(self, b: Board, search: SearchConfig, since: datetime):
        data = self._get(LEVER.format(slug=b.slug), mode="json")
        if not isinstance(data, list):
            raise ValueError("board not found")
        for j in data:
            cat = j.get("categories") or {}
            created = j.get("createdAt")
            title = j.get("text", "").strip()
            yield JobListing(
                title=title, company=b.employer,
                location=" / ".join(cat.get("allLocations") or [cat.get("location") or ""]),
                job_url=j.get("hostedUrl", ""), source="lever",
                description=j.get("descriptionPlain") or "",
                date_posted=datetime.fromtimestamp(created / 1000, timezone.utc) if created else None,
                job_type=classify_job_type(title, cat.get("commitment", "")),
            ), j.get("country") or ""

    def _fetch_ashby(self, b: Board, search: SearchConfig, since: datetime):
        data = self._get(ASHBY.format(slug=b.slug))
        for j in data.get("jobs", []):
            if j.get("isListed") is False:
                continue
            locations = [j.get("location") or ""] + [s.get("location", "") for s in j.get("secondaryLocations") or []]
            country = ((j.get("address") or {}).get("postalAddress") or {}).get("addressCountry", "")
            posted = j.get("publishedAt")
            title = j.get("title", "").strip()
            yield JobListing(
                title=title, company=b.employer,
                location=" / ".join(x for x in locations if x) + (" (Remote)" if j.get("isRemote") else ""),
                job_url=j.get("jobUrl", ""), source="ashby", description=j.get("descriptionPlain") or "",
                date_posted=datetime.fromisoformat(posted) if posted else None,
                job_type=classify_job_type(title, j.get("employmentType") or ""),
            ), "" if len(locations) > 1 else country

    def _fetch_workday(self, b: Board, search: SearchConfig, since: datetime):
        """Workday career sites load postings from a JSON endpoint (/wday/cxs/...). Search it with
        each of your queries, keep recent postings whose title matches, then read each one's
        detail for the description, country and exact posting date."""
        site = WorkdaySite.parse(b.slug)
        max_age = (datetime.now(timezone.utc) - since).days
        seen: set[str] = set()
        for query in search.all_queries:
            for page in range(self.workday_max_pages):
                body = {"appliedFacets": {}, "limit": 20, "offset": page * 20, "searchText": query}
                resp = self.client.post(site.api + "/jobs", json=body)
                resp.raise_for_status()
                data = resp.json()
                postings = data.get("jobPostings") or []
                for p in postings:
                    path = p.get("externalPath", "")
                    if not path or path in seen:
                        continue
                    seen.add(path)
                    age = workday_age_days(p.get("postedOn", ""))
                    title = p.get("title", "")
                    if (age is not None and age > max_age) or not title_matches(title, self.titles):
                        continue
                    if any(w.lower() in title.lower() for w in search.exclude_keywords):
                        continue  # e.g. senior roles: don't spend a detail request on them
                    yield self._workday_detail(b, site, p)
                    time.sleep(self.delay)
                if (page + 1) * 20 >= (data.get("total") or 0) or not postings:
                    break
                time.sleep(self.delay)

    def _workday_detail(self, b: Board, site: "WorkdaySite", p: dict):
        resp = self.client.get(site.api + p["externalPath"])
        resp.raise_for_status()
        info = resp.json().get("jobPostingInfo", {})
        title = (info.get("title") or p.get("title", "")).strip()
        country = (info.get("country") or {}).get("descriptor", "")
        start = info.get("startDate")
        posted = (datetime.fromisoformat(start).replace(tzinfo=timezone.utc) if start
                  else _days_ago(workday_age_days(p.get("postedOn", ""))))
        return JobListing(
            title=title, company=b.employer,
            location=info.get("location") or p.get("locationsText", ""),
            job_url=info.get("externalUrl") or site.url + p["externalPath"], source="workday",
            description=strip_html(info.get("jobDescription") or ""), date_posted=posted,
            job_type=classify_job_type(title, info.get("timeType") or p.get("timeType", "")),
        ), ("US" if country == "United States of America" else "XX") if country else ""


@dataclass(frozen=True)
class WorkdaySite:
    url: str  # https://acme.wd5.myworkdayjobs.com/External
    api: str  # https://acme.wd5.myworkdayjobs.com/wday/cxs/acme/External

    @classmethod
    def parse(cls, url: str) -> "WorkdaySite":
        m = re.match(r"(https://([^./]+)\.(wd\d+)\.myworkdayjobs\.com)/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)", url.strip())
        if not m:
            raise ValueError(f"not a Workday career site URL: {url}")
        host, tenant, _, site = m.groups()
        return cls(url=f"{host}/{site}", api=f"{host}/wday/cxs/{tenant}/{site}")


def workday_age_days(posted_on: str) -> int | None:
    """'Posted Today' -> 0, 'Posted Yesterday' -> 1, 'Posted 5 Days Ago' -> 5, 'Posted 30+ Days Ago' -> 30."""
    text = (posted_on or "").lower()
    if "today" in text:
        return 0
    if "yesterday" in text:
        return 1
    m = re.search(r"(\d+)\+?\s*day", text)
    return int(m.group(1)) if m else None


def _days_ago(days: int | None) -> datetime | None:
    return None if days is None else datetime.now(timezone.utc) - timedelta(days=days)


# ----------------------------------------------------------------------------- discovery

_GENERIC_TAIL = {
    "SERVICES", "SERVICE", "TECHNOLOGIES", "TECHNOLOGY", "SOLUTIONS", "GROUP", "HOLDINGS", "USA", "US",
    "U S", "AMERICA", "AMERICAS", "NATIONAL ASSOCIATION", "N A", "NA", "PLATFORMS", "ENTERPRISE",
    "INTERNATIONAL", "GLOBAL", "SYSTEMS", "LABS", "COM", "INFORMATION", "MANAGEMENT", "AND",
    "MARKETS", "FINANCIAL", "FINANCE", "INTERACTIVE", "WHOLESALE", "REWARDS", "SOFTWARE",
}


def slug_candidates(employer_key: str) -> list[str]:
    """Likely board names: 'CAPITAL ONE SERVICES' -> capitalone, capital-one, ..."""
    key = normalize_name(employer_key.split(" D B A ")[-1])
    words = key.split()
    while len(words) > 1 and (words[-1] in _GENERIC_TAIL or " ".join(words[-2:]) in _GENERIC_TAIL):
        words = words[:-2] if " ".join(words[-2:]) in _GENERIC_TAIL else words[:-1]
    out = []
    for ws in (words, key.split()):
        if ws:
            out += ["".join(ws).lower(), "-".join(ws).lower()]
    return list(dict.fromkeys(s for s in out if len(s) >= 3))


def _same_company(board_name: str, employer_key: str) -> bool:
    """The board's display name starts with the employer's whole brand, not just its first word:
    "Robinhood" ~ "ROBINHOOD MARKETS" (brand ROBINHOOD), but "General Interest" !~ "GENERAL MOTORS"
    and "charles" !~ "CHARLES SCHWAB AND"."""
    board = normalize_name(board_name).replace(" ", "")
    brand = slug_candidates(employer_key)[0].replace("-", "").upper() if slug_candidates(employer_key) else ""
    return bool(board and brand) and board.startswith(brand)


def find_board(client: httpx.Client, employer_key: str, delay: float = 0.2) -> tuple[str, str, str] | None:
    """(ats, slug, board name) for the first public board found for this employer, or None."""
    for slug in slug_candidates(employer_key):
        try:
            r = client.get(GREENHOUSE.format(slug=slug))
            if r.status_code == 200 and _same_company(r.json().get("name", ""), employer_key):
                return "greenhouse", slug, r.json().get("name", "")
            time.sleep(delay)
            r = client.get(ASHBY.format(slug=slug))
            if r.status_code == 200 and r.json().get("jobs") is not None:
                return "ashby", slug, slug
            time.sleep(delay)
            r = client.get(LEVER.format(slug=slug), params={"mode": "json", "limit": 1})
            if r.status_code == 200 and isinstance(r.json(), list):
                return "lever", slug, slug
            time.sleep(delay)
        except (httpx.HTTPError, ValueError):
            continue
    # "ROBINHOOD MARKETS" -> "robinhood": only on Greenhouse, where the board's name is checked.
    first = normalize_name(employer_key.split(" D B A ")[-1]).split()[0].lower()
    if len(first) >= 5 and first not in slug_candidates(employer_key):
        try:
            r = client.get(GREENHOUSE.format(slug=first))
            if r.status_code == 200 and _same_company(r.json().get("name", ""), employer_key):
                return "greenhouse", first, r.json().get("name", "")
        except (httpx.HTTPError, ValueError):
            pass
    return None


def write_boards(path: str | Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=BOARD_COLUMNS)
        w.writeheader()
        w.writerows(rows)
