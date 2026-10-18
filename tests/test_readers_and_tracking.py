from datetime import datetime, timedelta, timezone

import pytest

from jobhunter.config import SearchConfig, SponsorshipConfig
from jobhunter.sources.boards import Board, CompanyBoardsSource, OracleSite
from jobhunter.sponsors import WARNING_PENALTY, apply_sponsorship, sponsorship_warning
from jobhunter.tracker import Tracker
from tests.conftest import make_job
from tests.test_boards import routed_client

NOW = datetime.now(timezone.utc)
TITLES = ["data analyst", "data scientist"]


def run(board, routes):
    src = CompanyBoardsSource([board], TITLES, delay=0, client=routed_client(routes))
    return src.fetch(SearchConfig(max_days_old=14, queries=["data analyst"], exclude_keywords=["senior"]))


def test_smartrecruiters():
    listing = {"content": [
        {"id": "1", "name": "Data Analyst", "releasedDate": (NOW - timedelta(days=1)).isoformat(),
         "location": {"city": "Austin", "region": "TX", "country": "us"},
         "typeOfEmployment": {"label": "Full-time"}, "ref": "https://api.smartrecruiters.com/v1/companies/Acme/postings/1"},
        {"id": "2", "name": "Senior Data Analyst", "releasedDate": NOW.isoformat(), "location": {"country": "us"},
         "ref": "https://api.smartrecruiters.com/v1/companies/Acme/postings/2"},
    ]}
    detail = {"postingUrl": "https://jobs.smartrecruiters.com/Acme/1-data-analyst",
              "jobAd": {"sections": {"jobDescription": {"text": "<p>SQL daily</p>"}}}}
    jobs = run(Board("ACME", "Acme", "smartrecruiters", "Acme"), {
        "companies/Acme/postings/1": (200, detail), "companies/Acme/postings": (200, listing)})
    assert [(j.title, j.job_url, j.location) for j in jobs] == [
        ("Data Analyst", "https://jobs.smartrecruiters.com/Acme/1-data-analyst", "Austin, TX")]
    assert "SQL daily" in jobs[0].description


def test_amazon():
    feed = {"jobs": [
        {"id": "1", "title": "Data Analyst", "posted_date": NOW.strftime("%B %d, %Y"), "job_path": "/en/jobs/1/x",
         "normalized_location": "Seattle, Washington, USA", "country_code": "USA", "job_schedule_type": "full-time",
         "description": "SQL"},
        {"id": "2", "title": "Data Analyst Intern", "posted_date": NOW.strftime("%B %d, %Y"), "job_path": "/en/jobs/2/y",
         "country_code": "USA", "is_intern": True},
        {"id": "3", "title": "Data Analyst", "posted_date": NOW.strftime("%B %d, %Y"), "job_path": "/en/jobs/3/z",
         "country_code": "IND"},
        {"id": "4", "title": "Data Analyst", "posted_date": (NOW - timedelta(days=40)).strftime("%B %d, %Y"),
         "job_path": "/en/jobs/4/w", "country_code": "USA"},
    ]}
    jobs = run(Board("AMAZON", "Amazon", "amazon", "amazon"), {"amazon.jobs/en/search.json": (200, feed)})
    assert sorted((j.job_url, j.job_type) for j in jobs) == [
        ("https://www.amazon.jobs/en/jobs/1/x", "full_time"), ("https://www.amazon.jobs/en/jobs/2/y", "intern")]


def test_oracle_stops_at_older_postings():
    site = "https://acme.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
    reqs = [{"Id": "10", "Title": "Data Analyst", "PostedDate": NOW.date().isoformat(), "PrimaryLocation": "New York, NY",
             "PrimaryLocationCountry": "US", "ShortDescriptionStr": "SQL"},
            {"Id": "11", "Title": "Data Analyst", "PostedDate": NOW.date().isoformat(), "PrimaryLocation": "Pune, India",
             "PrimaryLocationCountry": "IN"},
            {"Id": "12", "Title": "Data Analyst", "PostedDate": (NOW - timedelta(days=30)).date().isoformat(),
             "PrimaryLocationCountry": "US"}]
    seen = []
    src = CompanyBoardsSource([Board("ACME", "Acme", "oracle", site)], TITLES, delay=0,
                              client=routed_client({"recruitingCEJobRequisitions": (200, {"items": [{"requisitionList": reqs}]})}, seen))
    jobs = src.fetch(SearchConfig(max_days_old=14, queries=["data analyst"]))
    assert [(j.job_url, j.description) for j in jobs] == [(f"{site}/job/10", "SQL")]
    assert len(seen) == 1  # an older posting on page 1 means no further pages


def test_oracle_site_parse():
    s = OracleSite.parse("https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/jobs?x=1")
    assert s.site == "CX_1001"
    assert s.api == "https://jpmc.fa.oraclecloud.com/hcmRestApi/resources/latest/recruitingCEJobRequisitions"
    with pytest.raises(ValueError):
        OracleSite.parse("https://example.com/careers")


@pytest.mark.parametrize("text, flagged", [
    ("Applicants must be authorized to work in the U.S. without current or future sponsorship.", True),
    ("We are unable to provide visa sponsorship for this position.", True),
    ("This role does not offer immigration sponsorship.", True),
    ("Must be a U.S. citizen with an active Secret clearance.", True),
    ("We sponsor H-1B visas.", False),
    ("Build dashboards on sponsor data.", False),
    ("Acme will consider sponsoring a new qualified applicant.", False),
])
def test_sponsorship_warning(text, flagged):
    assert bool(sponsorship_warning(text)) is flagged


def test_warning_lowers_rank():
    ok, flagged = make_job(company="A", description="SQL"), make_job(company="B", description="No sponsorship available.")
    ok.match_score = flagged.match_score = 80
    ranked = apply_sponsorship([flagged, ok], None, SponsorshipConfig())
    assert ranked[0] is ok and flagged.overall_score == pytest.approx(80 * WARNING_PENALTY)
    assert "sponsorship" in flagged.sponsorship_warning.lower()


@pytest.fixture
def tracker(tmp_path):
    return Tracker(f"sqlite:///{tmp_path / 't.db'}")


def test_same_job_tomorrow_is_not_a_duplicate(tracker):
    new, again = tracker.save_seen([make_job(company="A"), make_job(company="B")])
    assert len(new) == 2 and again == 0
    new, again = tracker.save_seen([make_job(company="A"), make_job(company="C")])
    assert [j.company for j in new] == ["C"] and again == 1
    assert len(tracker.top_jobs(limit=50)) == 3


def test_decisions_survive_rescans(tracker):
    [a] = tracker.save_new([make_job(company="A")])
    tracker.set_decision(a.id, "skipped")
    tracker.save_seen([make_job(company="A")])
    assert tracker.top_jobs(limit=50) == []
    assert tracker.get_job(a.id).decision == "skipped"
    with pytest.raises(ValueError):
        tracker.set_decision(a.id, "maybe")


def test_stale_jobs_close_and_reopen(tracker):
    [a] = tracker.save_new([make_job(company="A")])
    with tracker.Session() as s:
        s.get(type(a), a.id).last_seen = NOW - timedelta(days=5)
        s.commit()
    assert tracker.close_stale(days=3) == 1
    assert tracker.top_jobs(limit=50) == []
    tracker.save_seen([make_job(company="A")])  # reposted / seen again
    assert [j.company for j in tracker.top_jobs(limit=50)] == ["A"]


def test_application_records_resume_and_cover_note(tracker):
    [a] = tracker.save_new([make_job(company="A")])
    app = tracker.log_application(a.id, resume_path="r.pdf", cover_note_path="c.txt")
    [(row, job)] = tracker.applications()
    assert (row.resume_path, row.cover_note_path, job.company) == ("r.pdf", "c.txt", "A")
    assert tracker.top_jobs(limit=50) == []  # applied jobs leave the to-do list
    assert app.status == "applied"


def test_runs_are_recorded(tracker):
    run = tracker.start_run()
    tracker.finish_run(run, fetched=10, new=3, seen_again=7, problems="Acme (workday) failed")
    [r] = tracker.recent_runs()
    assert (r.fetched, r.new, r.seen_again, r.problems) == (10, 3, 7, "Acme (workday) failed") and r.finished


def _failing_client(fail_times: dict):
    """Greenhouse boards that raise a network error the first `n` times they're requested."""
    import httpx

    calls: dict[str, int] = {}

    def handler(request):
        slug = request.url.path.split("/")[3]  # /v1/boards/<slug>/jobs
        calls[slug] = calls.get(slug, 0) + 1
        if calls[slug] <= fail_times.get(slug, 0):
            raise httpx.ConnectError("getaddrinfo failed", request=request)
        return httpx.Response(200, json={"jobs": []})

    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_network_hiccup_is_retried_once():
    client, calls = _failing_client({"a": 1})
    src = CompanyBoardsSource([Board("A", "A", "greenhouse", "a")], TITLES, delay=0, client=client)
    src.fetch(SearchConfig())
    assert calls["a"] == 2 and src.failures == [] and not src.offline


def test_lost_connection_stops_the_run():
    boards = [Board(str(i), f"Co{i}", "greenhouse", f"s{i}") for i in range(10)]
    client, calls = _failing_client({f"s{i}": 99 for i in range(10)})
    src = CompanyBoardsSource(boards, TITLES, delay=0, client=client)
    src.fetch(SearchConfig())
    assert src.offline and len(calls) == 4  # stopped after 4 sites in a row
    assert len(src.failures) == 1 and src.failures[0].startswith("offline:")
