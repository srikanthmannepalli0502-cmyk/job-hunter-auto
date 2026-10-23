import pytest

from jobhunter.tracker import Tracker
from tests.conftest import make_job


@pytest.fixture
def tracker():
    return Tracker("sqlite:///:memory:")


def test_save_new_skips_known_jobs(tracker):
    first = tracker.save_new([make_job(company="A"), make_job(company="B")])
    second = tracker.save_new([make_job(company="B"), make_job(company="C")])
    assert len(first) == 2
    assert [j.company for j in second] == ["C"]


def test_save_new_dedupes_within_batch(tracker):
    assert len(tracker.save_new([make_job(), make_job()])) == 1


def test_top_jobs_hides_applied(tracker):
    a, b = tracker.save_new([make_job(company="A", match_score=90), make_job(company="B", match_score=50)])
    assert [j.company for j in tracker.top_jobs()] == ["A", "B"]

    tracker.log_application(a.id, resume_path="r.pdf")
    assert [j.company for j in tracker.top_jobs()] == ["B"]


def test_application_lifecycle(tracker):
    (job,) = tracker.save_new([make_job()])
    app = tracker.log_application(job.id)
    tracker.update_status(app.id, "interview", notes="Phone screen Monday")

    ((saved, saved_job),) = tracker.applications()
    assert saved.status == "interview"
    assert saved.notes == "Phone screen Monday"
    assert saved_job.title == "Data Analyst"


def test_invalid_status_and_ids(tracker):
    (job,) = tracker.save_new([make_job()])
    app = tracker.log_application(job.id)
    with pytest.raises(ValueError):
        tracker.update_status(app.id, "ghosted")
    with pytest.raises(ValueError):
        tracker.log_application(9999)


def test_interview_stages(tracker):
    from datetime import datetime, timezone

    import pytest

    tracker.save_new([make_job()])
    job = tracker.top_jobs(1)[0]
    app = tracker.log_application(job.id)
    st = tracker.add_stage(app.id, "recruiter screen", datetime(2026, 10, 1, tzinfo=timezone.utc), "phone",
                           "Pat (recruiter)", notes="Asked about visa timeline")
    tracker.add_stage(app.id, "technical")
    assert [s.kind for s in tracker.stages(app.id)] == ["recruiter screen", "technical"]  # dated first
    assert tracker.get_application(app.id)[0].status == "interview"  # moved on from "applied"
    assert tracker.stage_counts() == {app.id: 2}
    assert tracker.set_stage_outcome(st.id, "passed").outcome == "passed"
    with pytest.raises(ValueError):
        tracker.add_stage(app.id, "coffee chat")
