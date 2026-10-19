import json
import sqlite3
import threading
import time
from http.server import ThreadingHTTPServer

import httpx
import pytest

from jobhunter.config import Profile
from jobhunter.pipeline import Gathered
from jobhunter.tracker import Tracker
from jobhunter.web import App, make_handler
from tests.conftest import make_job

TOKEN = "test-token"


@pytest.fixture
def server(tmp_path, monkeypatch):
    def start(allowed_users=""):
        monkeypatch.setenv("JOBHUNTER_API_TOKEN", TOKEN)
        monkeypatch.setenv("JOBHUNTER_ALLOWED_USERS", allowed_users)
        tracker = Tracker(f"sqlite:///{tmp_path / 'app.db'}")
        app = App(Profile(output_dir=str(tmp_path / "tailored")), tracker)
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(app, 0))
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{httpd.server_address[1]}"
        started.append(httpd)
        return base, app

    started = []
    yield start
    for httpd in started:
        httpd.shutdown()


def ingest(base, jobs, token=TOKEN):
    payload = Gathered([make_job(company=c) for c in jobs], len(jobs), [], False).to_dict()
    return httpx.post(f"{base}/api/ingest", json=payload, headers={"Authorization": f"Bearer {token}"})


def test_gathered_round_trip():
    job = make_job(company="Acme")
    job.sponsor_note = "x"
    back = Gathered.from_dict(json.loads(json.dumps(Gathered([job], 3, ["p"], True).to_dict())))
    assert back.jobs[0].dedupe_key == job.dedupe_key and back.jobs[0].sponsor_note == "x"
    assert (back.fetched, back.problems, back.offline) == (3, ["p"], True)


def test_ingest_needs_the_token(server):
    base, app = server()
    assert ingest(base, ["A"], token="wrong").status_code == 401
    r = ingest(base, ["A", "B"])
    assert r.status_code == 200 and r.json()["new"] == 2
    assert ingest(base, ["A"]).json()["new"] == 0  # same job again: no duplicate


def test_hosted_pages_need_sign_in(server):
    base, _ = server(allowed_users="me@example.com")
    r = httpx.get(f"{base}/", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/.auth/login/aad")
    assert httpx.get(f"{base}/", headers={"X-MS-CLIENT-PRINCIPAL-NAME": "other@example.com"}).status_code == 403
    ok = httpx.get(f"{base}/", headers={"X-MS-CLIENT-PRINCIPAL-NAME": "Me@Example.com"})
    assert ok.status_code == 200 and "me@example.com" in ok.text


def test_shortlisting_generates_a_resume(server, monkeypatch):
    made = []
    monkeypatch.setattr("jobhunter.pipeline.make_resume", lambda profile, tracker, job_id: (made.append(job_id) or ("r.pdf", [])))
    base, app = server()
    ingest(base, ["A"])
    job = app.tracker.top_jobs(limit=5)[0]
    r = httpx.post(f"{base}/decide/{job.id}", data={"decision": "shortlisted", "back": "/"}, follow_redirects=False)
    assert r.status_code == 303
    for _ in range(50):
        if made:
            break
        time.sleep(0.05)
    assert made == [job.id] and app.tracker.get_job(job.id).decision == "shortlisted"


def test_other_websites_cannot_post(server):
    base, app = server()
    ingest(base, ["A"])
    job = app.tracker.top_jobs(limit=5)[0]
    r = httpx.post(f"{base}/decide/{job.id}", data={"decision": "skipped"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and app.tracker.get_job(job.id).decision == "new"


def test_backup_and_restore(server, tmp_path):
    base, app = server()
    assert httpx.get(f"{base}/api/backup").status_code == 401
    ingest(base, ["A", "B"])
    backup = httpx.get(f"{base}/api/backup", headers={"Authorization": f"Bearer {TOKEN}"})
    assert backup.status_code == 200
    copy = tmp_path / "copy.db"
    copy.write_bytes(backup.content)
    con = sqlite3.connect(copy)
    assert con.execute("select count(*) from jobs").fetchone()[0] == 2
    con.close()
    # Restore is refused once the app has jobs (it's only for the first deployment).
    r = httpx.post(f"{base}/api/restore", content=backup.content, headers={"Authorization": f"Bearer {TOKEN}"})
    assert r.status_code == 409
