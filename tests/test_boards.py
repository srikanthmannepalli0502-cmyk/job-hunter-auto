from datetime import datetime, timedelta, timezone

import httpx
import pytest

from jobhunter.config import SearchConfig
from jobhunter.sources.boards import (
    Board,
    CompanyBoardsSource,
    WorkdaySite,
    find_board,
    is_us_location,
    load_boards,
    slug_candidates,
    title_matches,
    workday_age_days,
    write_boards,
)
from jobhunter.sources.base import classify_job_type

NOW = datetime.now(timezone.utc)
RECENT = (NOW - timedelta(days=2)).isoformat()
OLD = (NOW - timedelta(days=60)).isoformat()

GREENHOUSE_JOBS = {"jobs": [
    {"title": "Data Analyst", "absolute_url": "https://gh/1", "location": {"name": "New York, NY"},
     "content": "&lt;p&gt;SQL and &lt;b&gt;Python&lt;/b&gt;&lt;/p&gt;", "first_published": RECENT},
    {"title": "Data Analyst", "absolute_url": "https://gh/2", "location": {"name": "London, UK"},
     "content": "", "first_published": RECENT},
    {"title": "Data Scientist Intern", "absolute_url": "https://gh/3", "location": {"name": "Remote"},
     "content": "", "first_published": RECENT},
    {"title": "Data Scientist", "absolute_url": "https://gh/4", "location": {"name": "Austin, TX"},
     "content": "", "first_published": OLD},
    {"title": "Account Executive", "absolute_url": "https://gh/5", "location": {"name": "Remote"},
     "content": "", "first_published": RECENT},
]}
LEVER_JOBS = [
    {"text": "Machine Learning Engineer", "hostedUrl": "https://lever/1", "country": "US",
     "categories": {"commitment": "Full-time", "location": "Seattle"}, "descriptionPlain": "pytorch",
     "createdAt": int((NOW - timedelta(days=1)).timestamp() * 1000)},
    {"text": "Machine Learning Engineer", "hostedUrl": "https://lever/2", "country": "SG",
     "categories": {"commitment": "Full-time", "location": "Singapore"},
     "createdAt": int(NOW.timestamp() * 1000)},
    {"text": "Data Analyst", "hostedUrl": "https://lever/3", "country": "US",
     "categories": {"commitment": "Contract", "location": "Remote"}, "createdAt": int(NOW.timestamp() * 1000)},
]
ASHBY_JOBS = {"jobs": [
    {"title": "AI Engineer", "location": "New York, NY", "isRemote": False, "jobUrl": "https://ashby/1",
     "publishedAt": RECENT, "employmentType": "FullTime", "descriptionPlain": "LLM agents",
     "address": {"postalAddress": {"addressCountry": "USA"}}},
    {"title": "AI Engineer", "location": "Toronto", "jobUrl": "https://ashby/2", "publishedAt": RECENT,
     "employmentType": "FullTime", "address": {"postalAddress": {"addressCountry": "Canada"}}},
    {"title": "AI Engineer", "location": "Remote", "jobUrl": "https://ashby/3", "publishedAt": RECENT,
     "employmentType": "PartTime"},
    {"title": "AI Engineer", "location": "Remote", "jobUrl": "https://ashby/4", "publishedAt": RECENT,
     "isListed": False},
]}


def routed_client(routes: dict, seen: list | None = None):
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(str(request.url))
        for fragment, (status, payload) in routes.items():
            if fragment in str(request.url):
                return httpx.Response(status, json=payload)
        return httpx.Response(404, json={"ok": False})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def source():
    boards = [Board("ACME", "Acme Inc", "greenhouse", "acme"), Board("LEV", "Lev Co", "lever", "lev"),
              Board("ASH", "Ash Ltd", "ashby", "ash"), Board("GONE", "Gone", "greenhouse", "gone")]
    client = routed_client({
        "greenhouse.io/v1/boards/acme/jobs": (200, GREENHOUSE_JOBS),
        "lever.co/v0/postings/lev": (200, LEVER_JOBS),
        "ashbyhq.com/posting-api/job-board/ash": (200, ASHBY_JOBS),
    })
    return CompanyBoardsSource(boards, ["data analyst", "data scientist", "machine learning", "ai engineer"],
                               delay=0, client=client)


def test_keeps_only_recent_us_matching_jobs_of_wanted_types(source):
    full_time = source.fetch(SearchConfig(max_days_old=14, job_types=["full_time"]))
    assert sorted(j.job_url for j in full_time) == ["https://ashby/1", "https://gh/1", "https://lever/1"]

    every_type = {j.job_url: j.job_type for j in source.fetch(SearchConfig(max_days_old=14))}
    assert every_type == {"https://ashby/1": "full_time", "https://gh/1": "full_time",
                          "https://lever/1": "full_time", "https://gh/3": "intern",
                          "https://lever/3": "contract", "https://ashby/3": "part_time"}


def test_parsed_fields(source):
    jobs = {j.job_url: j for j in source.fetch(SearchConfig(max_days_old=14))}
    gh = jobs["https://gh/1"]
    assert gh.company == "Acme Inc" and gh.source == "greenhouse"
    assert gh.description == "SQL and Python"
    assert jobs["https://lever/1"].date_posted.tzinfo is not None


def test_failed_board_does_not_stop_others(source):
    assert len(source.fetch(SearchConfig(max_days_old=14, job_types=["full_time"]))) == 3


@pytest.mark.parametrize("location, country, expected", [
    ("New York, NY", "", True),
    ("Remote", "", True),
    ("Remote - Canada", "", False),
    ("London, UK / New York, NY", "", True),
    ("Bengaluru, India", "", False),
    ("Anywhere", "SG", False),
    ("Anywhere", "US", True),
])
def test_us_location(location, country, expected):
    assert is_us_location(location, country) is expected


def test_title_matches():
    assert title_matches("Senior Data Analyst II", ["data analyst"])
    assert not title_matches("Data Engineer", ["data analyst"])


@pytest.mark.parametrize("key, first", [
    ("CAPITAL ONE NATIONAL ASSOCIATION", "capitalone"),
    ("META PLATFORMS", "meta"),
    ("AMAZON COM SERVICES", "amazon"),
    ("JPMORGAN CHASE AND", "jpmorganchase"),
    ("PEOPLE CENTER INC D B A RIPPLING", "rippling"),
])
def test_slug_candidates(key, first):
    assert slug_candidates(key)[0] == first


def test_find_board_checks_greenhouse_name():
    # A Greenhouse board named after another company is rejected; Ashby is tried next.
    client = routed_client({
        "greenhouse.io/v1/boards/acme": (200, {"name": "Totally Different"}),
        "ashbyhq.com/posting-api/job-board/acme": (200, {"jobs": []}),
    })
    assert find_board(client, "ACME", delay=0) == ("ashby", "acme", "acme")

    client = routed_client({"greenhouse.io/v1/boards/acme": (200, {"name": "Acme"})})
    assert find_board(client, "ACME", delay=0) == ("greenhouse", "acme", "Acme")
    assert find_board(routed_client({}), "ACME", delay=0) is None


def test_boards_file_round_trip(tmp_path):
    path = tmp_path / "boards.csv"
    write_boards(path, [{"employer_key": "ACME", "employer": "Acme Inc", "ats": "lever", "slug": "acme",
                         "board_name": "acme"}])
    assert load_boards(path) == [Board("ACME", "Acme Inc", "lever", "acme")]
    assert load_boards(tmp_path / "missing.csv") == []


@pytest.mark.parametrize("board_name, key, same", [
    ("Robinhood", "ROBINHOOD MARKETS", True),
    ("Chime Financial, Inc", "CHIME FINANCIAL", True),
    ("84.51°", "84 51", True),
    ("General Interest", "GENERAL MOTORS", False),
    ("charles", "CHARLES SCHWAB AND", False),
])
def test_same_company(board_name, key, same):
    from jobhunter.sources.boards import _same_company
    assert _same_company(board_name, key) is same


@pytest.mark.parametrize("title, hint, expected", [
    ("Data Analyst", "", "full_time"),
    ("Data Science Intern, Summer 2027", "", "intern"),
    ("Machine Learning Co-op", "", "intern"),
    ("Data Analyst", "Intern", "intern"),
    ("BI Analyst (Contract)", "", "contract"),
    ("Data Analyst", "PartTime", "part_time"),
    ("Data Analyst", "Part time", "part_time"),
    ("Internal Audit Data Analyst", "Full time", "full_time"),
])
def test_classify_job_type(title, hint, expected):
    assert classify_job_type(title, hint) == expected


def test_workday_site_parse():
    site = WorkdaySite.parse("https://capitalone.wd12.myworkdayjobs.com/en-US/Capital_One/details/x")
    assert site.url == "https://capitalone.wd12.myworkdayjobs.com/Capital_One"
    assert site.api == "https://capitalone.wd12.myworkdayjobs.com/wday/cxs/capitalone/Capital_One"
    with pytest.raises(ValueError):
        WorkdaySite.parse("https://example.com/careers")


@pytest.mark.parametrize("text, days", [
    ("Posted Today", 0), ("Posted Yesterday", 1), ("Posted 5 Days Ago", 5), ("Posted 30+ Days Ago", 30), ("", None),
])
def test_workday_age(text, days):
    assert workday_age_days(text) == days


def test_workday_source():
    api = "acme.wd5.myworkdayjobs.com/wday/cxs/acme/External"
    listing = {"total": 3, "jobPostings": [
        {"title": "Data Analyst", "externalPath": "/job/NY/Data-Analyst_R1", "postedOn": "Posted Today",
         "locationsText": "New York, NY", "timeType": "Full time"},
        {"title": "Data Analyst", "externalPath": "/job/OLD/Data-Analyst_R2", "postedOn": "Posted 30+ Days Ago"},
        {"title": "Recruiter", "externalPath": "/job/NY/Recruiter_R3", "postedOn": "Posted Today"},
    ]}
    detail = {"jobPostingInfo": {"title": "Data Analyst", "location": "New York, NY", "timeType": "Full time",
                                 "startDate": NOW.date().isoformat(), "jobDescription": "<p>SQL</p>",
                                 "country": {"descriptor": "United States of America"},
                                 "externalUrl": "https://acme.wd5.myworkdayjobs.com/External/job/NY/Data-Analyst_R1"}}
    seen = []
    client = routed_client({f"{api}/jobs": (200, listing), f"{api}/job/NY/Data-Analyst_R1": (200, detail)}, seen)
    src = CompanyBoardsSource([Board("ACME", "Acme", "workday", "https://acme.wd5.myworkdayjobs.com/External")],
                              ["data analyst"], delay=0, client=client)
    jobs = src.fetch(SearchConfig(max_days_old=14, queries=["data analyst"]))
    assert [(j.title, j.source, j.description, j.job_type) for j in jobs] == [
        ("Data Analyst", "workday", "SQL", "full_time")]
    # Only the matching, recent posting's detail page was requested.
    assert sum("/job/" in u for u in seen) == 1
