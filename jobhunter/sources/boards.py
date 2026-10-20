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
SMARTRECRUITERS = "https://api.smartrecruiters.com/v1/companies/{slug}/postings"
AMAZON = "https://www.amazon.jobs/en/search.json"
BOARD_COLUMNS = ["employer_key", "employer", "ats", "slug", "board_name"]
OFFLINE_AFTER = 4  # network errors on this many sites in a row = the internet is down

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
    ats: str   # greenhouse, lever, ashby, workday, smartrecruiters, amazon or oracle
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
        self.retry_pause = 5 if delay else 0
        self.workday_max_pages = workday_max_pages
        self.failures: list[str] = []
        self.offline = False

    def fetch(self, search: SearchConfig) -> list[JobListing]:
        since = datetime.now(timezone.utc) - timedelta(days=search.max_days_old)
        jobs: list[JobListing] = []
        failed = 0
        self.failures: list[str] = []
        self.offline = False
        network_errors_in_a_row = 0
        for n, board in enumerate(self.boards):
            try:
                postings = self._fetch_board(board, search, since)
                network_errors_in_a_row = 0
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as e:
                failed += 1
                network_errors_in_a_row = network_errors_in_a_row + 1 if isinstance(e, httpx.TransportError) else 0
                if network_errors_in_a_row >= OFFLINE_AFTER:
                    # The internet is gone (PC asleep, Wi-Fi dropped): stop instead of failing every board.
                    self.offline = True
                    skipped = len(self.boards) - n - 1
                    self.failures = self.failures[: -(OFFLINE_AFTER - 1)] + [
                        f"offline: lost the internet connection; stopped with {skipped + OFFLINE_AFTER} sites unchecked"]
                    self.logger.error(self.failures[-1])
                    break
                self.failures.append(f"{board.employer} ({board.ats}): {str(e)[:150]}")
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

    def _fetch_board(self, board: Board, search: SearchConfig, since: datetime) -> list:
        """One site's postings; a network hiccup gets one retry after a short pause."""
        fetch = getattr(self, f"_fetch_{board.ats}")
        try:
            # list(): the parsers are generators, so request errors surface while iterating.
            return list(fetch(board, search, since))
        except httpx.TransportError:
            time.sleep(self.retry_pause)
            return list(fetch(board, search, since))

    def _request(self, method: str, url: str, **kwargs) -> httpx.Response:
        """A request that waits and tries again (twice) when the site says we're going too fast."""
        for attempt in range(3):
            resp = self.client.request(method, url, **kwargs)
            if resp.status_code != 429 or attempt == 2:
                break
            wait = resp.headers.get("Retry-After", "")
            time.sleep(min(float(wait) if wait.isdigit() else 20.0, 60.0) if self.retry_pause else 0)
        resp.raise_for_status()
        return resp

    def _get(self, url: str, **params):
        return self._request("GET", url, params=params).json()

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
                data = self._request("POST", site.api + "/jobs", json=body).json()
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
        info = self._request("GET", site.api + p["externalPath"]).json().get("jobPostingInfo", {})
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


    def _fetch_smartrecruiters(self, b: Board, search: SearchConfig, since: datetime):
        """SmartRecruiters' public Posting API: search by keyword, then read matching postings."""
        seen: set[str] = set()
        for query in search.all_queries:
            data = self._get(SMARTRECRUITERS.format(slug=b.slug), q=query, limit=100, country="us")
            for p in data.get("content") or []:
                if p.get("id") in seen:
                    continue
                seen.add(p.get("id"))
                posted = p.get("releasedDate")
                posted_at = datetime.fromisoformat(posted.replace("Z", "+00:00")) if posted else None
                title = (p.get("name") or "").strip()
                if (posted_at and posted_at < since) or not title_matches(title, self.titles) \
                        or any(w.lower() in title.lower() for w in search.exclude_keywords):
                    continue
                detail = self._get(p["ref"])
                sections = (detail.get("jobAd") or {}).get("sections") or {}
                text = _join(strip_html((sections.get(k) or {}).get("text") or "")
                                   for k in ("jobDescription", "qualifications", "additionalInformation"))
                loc = p.get("location") or {}
                yield JobListing(
                    title=title, company=b.employer,
                    location=loc.get("fullLocation") or ", ".join(x for x in (loc.get("city"), loc.get("region")) if x),
                    job_url=detail.get("postingUrl") or "", source="smartrecruiters", description=text,
                    date_posted=posted_at,
                    job_type=classify_job_type(title, (p.get("typeOfEmployment") or {}).get("label", "")),
                ), (loc.get("country") or "").upper()
                time.sleep(self.delay)

    def _fetch_amazon(self, b: Board, search: SearchConfig, since: datetime):
        """amazon.jobs search feed (the JSON behind its search page), newest first, US only."""
        seen: set[str] = set()
        for query in search.all_queries:
            data = self._get(AMAZON, base_query=query, country="USA", result_limit=100, offset=0, sort="recent")
            for j in data.get("jobs") or []:
                if j.get("id") in seen:
                    continue
                seen.add(j.get("id"))
                try:
                    posted = datetime.strptime(j.get("posted_date", ""), "%B %d, %Y").replace(tzinfo=timezone.utc)
                except ValueError:
                    posted = None
                title = (j.get("title") or "").strip()
                text = _join(strip_html(j.get(k) or "") for k in
                                   ("description", "basic_qualifications", "preferred_qualifications"))
                yield JobListing(
                    title=title, company=b.employer, location=j.get("normalized_location") or j.get("location", ""),
                    job_url="https://www.amazon.jobs" + (j.get("job_path") or ""), source="amazon",
                    description=text, date_posted=posted,
                    job_type="intern" if j.get("is_intern") else classify_job_type(title, j.get("job_schedule_type") or ""),
                ), "US" if j.get("country_code") == "USA" else (j.get("country_code") or "")

    def _fetch_oracle(self, b: Board, search: SearchConfig, since: datetime):
        """Oracle Recruiting Cloud career sites (the REST feed behind their search page), newest first."""
        site = OracleSite.parse(b.slug)
        seen: set[str] = set()
        for query in search.all_queries:
            for page in range(self.workday_max_pages):
                finder = (f"findReqs;siteNumber={site.site},keyword={query},limit=25,offset={page * 25},"
                          "sortBy=POSTING_DATES_DESC")
                data = self._get(site.api, onlyData="true", expand="requisitionList.secondaryLocations", finder=finder)
                items = (data.get("items") or [{}])[0].get("requisitionList") or []
                older = False
                for r in items:
                    posted = datetime.fromisoformat(r["PostedDate"]).replace(tzinfo=timezone.utc) if r.get("PostedDate") else None
                    if posted and posted < since:
                        older = True
                        continue
                    if r.get("Id") in seen:
                        continue
                    seen.add(r.get("Id"))
                    title = (r.get("Title") or "").strip()
                    text = _join(strip_html(r.get(k) or "") for k in
                                       ("ShortDescriptionStr", "ExternalResponsibilitiesStr", "ExternalQualificationsStr"))
                    yield JobListing(
                        title=title, company=b.employer, location=r.get("PrimaryLocation") or "",
                        job_url=f"{site.url}/job/{r.get('Id')}", source="oracle", description=text,
                        date_posted=posted,
                        job_type=classify_job_type(title, r.get("JobSchedule") or "", r.get("WorkerType") or ""),
                    ), r.get("PrimaryLocationCountry") or ""
                if older or len(items) < 25:
                    break  # sorted newest first: the rest are older than the window
                time.sleep(self.delay)


@dataclass(frozen=True)
class OracleSite:
    url: str  # https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001
    api: str  # https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions
    site: str  # CX_1001

    @classmethod
    def parse(cls, url: str) -> "OracleSite":
        m = re.match(r"(https://[^/]+)/hcmUI/CandidateExperience/[a-z]{2}(?:-[A-Z]{2})?/sites/([^/?#]+)", url.strip())
        if not m:
            raise ValueError(f"not an Oracle Recruiting Cloud site URL: {url}")
        host, site = m.groups()
        return cls(url=f"{host}/hcmUI/CandidateExperience/en/sites/{site}",
                   api=f"{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions", site=site)


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


def _join(parts) -> str:
    """Non-empty description sections, separated by blank lines."""
    return "\n\n".join(x for x in parts if x and x.strip())


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
