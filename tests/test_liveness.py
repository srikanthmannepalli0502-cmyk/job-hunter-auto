from datetime import datetime, timedelta, timezone

import httpx

from jobhunter.liveness import check_page, closed_phrase
from jobhunter.models import JobListing
from jobhunter.tracker import Tracker

NOW = datetime.now(timezone.utc)


def listing(posted_days_ago=1, **kw):
    return JobListing(title="Data Analyst", company="Acme", location="NYC", job_url="https://x/1", source="greenhouse",
                      description=kw.get("description", ""), date_posted=NOW - timedelta(days=posted_days_ago))


def test_closed_wording():
    assert closed_phrase("Update: this position has been filled. Thanks all!")
    assert closed_phrase("We are no longer accepting applications for this role")
    assert closed_phrase("Applications are now closed.")
    assert closed_phrase("Sorry, the job you’re looking for isn’t available.")  # amazon.jobs, curly quotes
    assert not closed_phrase("We fill roles quickly; apply soon. No longer than 3 days to hear back.")
    assert not closed_phrase("")


def _client(status, body):
    return httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(status, text=body)))


def test_page_checks():
    long = "<p>" + "Responsibilities include SQL and dashboards. " * 20 + "</p>"
    assert check_page(_client(404, ""), "https://jobs.example.com/1")[0] == "closed"
    assert check_page(_client(200, long + "This job has expired."), "https://jobs.example.com/1")[0] == "closed"
    assert check_page(_client(200, long), "https://jobs.example.com/1")[0] == "open"
    assert check_page(_client(200, "<p>Loading</p>"), "https://jobs.example.com/1")[0] == "unknown"
    assert check_page(_client(200, long), "https://www.linkedin.com/jobs/view/1") == ("unknown", "not checked")


def test_repost_counted_after_long_absence_or_newer_date(tmp_path):
    t = Tracker(f"sqlite:///{tmp_path / 'db.sqlite'}")
    t.save_seen([listing(posted_days_ago=20)])
    job = t.top_jobs(5)[0]
    t.save_seen([listing(posted_days_ago=20)])  # seen again, same date: not a repost
    assert t.get_job(job.id).reposts == 0
    t.save_seen([listing(posted_days_ago=2)])  # posted again 18 days later
    assert t.get_job(job.id).reposts == 1

    # Closed after a short absence (a board failing for a day) and back: not a repost.
    t.close_jobs(job_ids=[job.id])
    t.save_seen([listing(posted_days_ago=2)])
    assert t.get_job(job.id).reposts == 1 and not t.get_job(job.id).closed

    # Gone for 10+ days, then back: a repost.
    with t.Session() as s:
        row = s.get(type(job), job.id)
        row.closed, row.last_seen = True, NOW - timedelta(days=12)
        s.commit()
    t.save_seen([listing(posted_days_ago=2)])
    assert t.get_job(job.id).reposts == 2


def test_listed_job_whose_text_says_filled_is_closed(tmp_path):
    from jobhunter.pipeline import Gathered, store

    t = Tracker(f"sqlite:///{tmp_path / 'db.sqlite'}")
    store(t, Gathered([listing(description="Edit: this role has been filled.")], 1, [], False))
    assert t.top_jobs(5) == []
