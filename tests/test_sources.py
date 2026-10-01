import httpx

from jobhunter.sources import AdzunaSource, RemotiveSource
from jobhunter.sources.remotive import strip_html

ADZUNA_RESPONSE = {
    "results": [
        {
            "title": "Data Analyst ",
            "company": {"display_name": "Acme Corp"},
            "location": {"display_name": "New York, NY"},
            "redirect_url": "https://www.adzuna.com/details/1",
            "description": "Use SQL and Tableau...",
            "salary_min": 80000,
            "salary_max": 95000,
            "created": "2026-09-28T10:00:00Z",
        }
    ]
}

REMOTIVE_RESPONSE = {
    "jobs": [
        {
            "title": "Analytics Engineer",
            "company_name": "Remote Co",
            "candidate_required_location": "USA",
            "url": "https://remotive.com/remote-jobs/1",
            "description": "<p>Build <b>dbt</b> models</p><ul><li>SQL</li></ul>",
            "publication_date": "2026-09-27T08:00:00",
        }
    ]
}


def client_returning(payload, seen: list):
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_adzuna_parses_results_and_sends_params(search):
    requests = []
    source = AdzunaSource(app_id="id", app_key="key", client=client_returning(ADZUNA_RESPONSE, requests))

    jobs = source.fetch(search)

    assert len(jobs) == 1
    job = jobs[0]
    assert (job.title, job.company, job.location) == ("Data Analyst", "Acme Corp", "New York, NY")
    assert (job.salary_min, job.salary_max) == (80000, 95000)
    assert job.date_posted.year == 2026
    params = requests[0].url.params
    assert params["what"] == "data analyst"
    assert params["where"] == "New York, NY"
    assert "/jobs/us/search/1" in str(requests[0].url)


def test_adzuna_without_keys_is_skipped(search):
    requests = []
    source = AdzunaSource(client=client_returning(ADZUNA_RESPONSE, requests))
    source.app_id = source.app_key = ""
    assert source.fetch(search) == []
    assert requests == []


def test_remotive_parses_and_strips_html(search):
    jobs = RemotiveSource(client=client_returning(REMOTIVE_RESPONSE, [])).fetch(search)
    assert jobs[0].company == "Remote Co"
    assert jobs[0].location == "Remote (USA)"
    assert "<" not in jobs[0].description
    assert "dbt" in jobs[0].description


def test_remotive_disabled(search):
    search.include_remote = False
    requests = []
    assert RemotiveSource(client=client_returning(REMOTIVE_RESPONSE, requests)).fetch(search) == []
    assert requests == []


def test_strip_html_keeps_line_breaks():
    assert strip_html("<p>One</p><p>Two &amp; three</p>") == "One\nTwo & three"
