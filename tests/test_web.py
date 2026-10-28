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
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/"
    login = httpx.get(f"{base}/login?next=/runs").text
    assert "/.auth/login/google?post_login_redirect_uri=/runs" in login and "/.auth/login/aad" in login
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


def test_answers_page_saves_profile_and_answers(server):
    base, app = server()
    r = httpx.post(f"{base}/answers/save", data={"f_email": "me@example.com", "f_phone": ""}, follow_redirects=False)
    assert r.status_code == 303
    httpx.post(f"{base}/answers/add", data={"question": "Why us?", "answer": "Because."})
    fields = {a.field: a.answer for a in app.tracker.answers() if a.field}
    assert fields == {"email": "me@example.com"}  # empty values are not saved
    free = [a for a in app.tracker.answers() if not a.field]
    assert [(a.question, a.answer) for a in free] == [("Why us?", "Because.")]
    httpx.post(f"{base}/answers/delete/{free[0].id}")
    assert [a for a in app.tracker.answers() if not a.field] == []
    assert httpx.get(f"{base}/answers").status_code == 200


def test_extension_api(server, tmp_path):
    base, app = server()
    ingest(base, ["Acme"])
    job = app.tracker.top_jobs(limit=5)[0]
    assert httpx.get(f"{base}/api/x/context?url=x").status_code == 401
    key = app.extension_key()
    h = {"X-Extension-Key": key}
    assert httpx.get(f"{base}/api/x/context?url=x", headers={"X-Extension-Key": "wrong"}).status_code == 401

    app.tracker.save_answer("Email", "me@example.com", field="email")
    ctx = httpx.get(f"{base}/api/x/context", params={"url": job.job_url}, headers=h).json()
    assert ctx["job"]["id"] == job.id and ctx["job"]["has_resume"] is False
    assert [f["key"] for f in ctx["fields"]] == ["email"]
    assert httpx.get(f"{base}/api/x/context", params={"url": "https://other.example/x"}, headers=h).json()["job"] is None

    # Resume download: 404 until there is one, then the file with its name.
    assert httpx.get(f"{base}/api/x/resume/{job.id}", headers=h).status_code == 404
    out = tmp_path / "tailored"
    out.mkdir(exist_ok=True)
    pdf = out / "acme.pdf"
    pdf.write_bytes(b"%PDF-1.4 test")
    app.tracker.set_kit(job.id, resume_path=str(pdf))
    r = httpx.get(f"{base}/api/x/resume/{job.id}", headers=h)
    assert r.status_code == 200 and r.content == b"%PDF-1.4 test" and r.headers["X-Filename"].endswith("_Resume.pdf")

    # Saving answers skips sensitive questions.
    items = [{"question": "Years of SQL?", "answer": "2"}, {"question": "Social Security Number", "answer": "123"}]
    assert httpx.post(f"{base}/api/x/answers", json={"items": items}, headers=h).json() == {"saved": 1}
    assert [a.question for a in app.tracker.answers() if not a.field] == ["Years of SQL?"]

    r = httpx.post(f"{base}/api/x/applied/{job.id}", json={}, headers=h).json()
    assert r["resume"] == str(pdf) and app.tracker.applications()[0][1].id == job.id


def test_extension_assist_uses_the_job(server, monkeypatch):
    seen = {}

    def fake_assist(llm, question, resume, answers, **job):
        seen.update(job, question=question)
        return "Say yes."

    monkeypatch.setattr("jobhunter.assist.assist", fake_assist)
    monkeypatch.setattr("jobhunter.llm.LLMClient", lambda: None)
    base, app = server()
    ingest(base, ["Acme"])
    job = app.tracker.top_jobs(limit=5)[0]
    h = {"X-Extension-Key": app.extension_key()}
    r = httpx.post(f"{base}/api/x/assist", json={"job_id": job.id, "question": "Relocate?", "page_questions": ["Q1"]}, headers=h)
    assert r.json() == {"answer": "Say yes."}
    assert seen["company"] == "Acme" and seen["page_questions"] == ["Q1"] and seen["question"] == "Relocate?"


def test_google_sign_in_is_matched_by_email_claim(server):
    import base64

    base, _ = server(allowed_users="me@gmail.com, me@example.com")
    principal = {"auth_typ": "google", "claims": [{"typ": "name", "val": "Me"},
                 {"typ": "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress", "val": "Me@Gmail.com"}]}
    headers = {"X-MS-CLIENT-PRINCIPAL-NAME": "Me",
               "X-MS-CLIENT-PRINCIPAL": base64.b64encode(json.dumps(principal).encode()).decode()}
    r = httpx.get(f"{base}/", headers=headers)
    assert r.status_code == 200 and "me@gmail.com" in r.text
    principal["claims"][1]["val"] = "someone@gmail.com"
    headers["X-MS-CLIENT-PRINCIPAL"] = base64.b64encode(json.dumps(principal).encode()).decode()
    assert httpx.get(f"{base}/", headers=headers).status_code == 403


def test_change_requests_are_kept_and_rebuild_the_resume(server, monkeypatch):
    seen = []
    monkeypatch.setattr("jobhunter.pipeline.make_resume",
                        lambda profile, tracker, job_id: (seen.append(tracker.get_job(job_id).kit_feedback) or ("r.pdf", [])))
    base, app = server()
    ingest(base, ["Acme"])
    job = app.tracker.top_jobs(limit=5)[0]
    app.tracker.set_kit(job.id, resume_path="old.pdf")
    r = httpx.post(f"{base}/revise/{job.id}", data={"feedback": "Put the Lake project first"}, follow_redirects=False)
    assert r.status_code == 303
    for _ in range(50):
        if seen:
            break
        time.sleep(0.05)
    assert seen == ["Put the Lake project first"]
    h = {"X-Extension-Key": app.extension_key()}
    assert httpx.post(f"{base}/api/x/revise/{job.id}", json={"instructions": "Shorter summary"}, headers=h).json() == {"queued": True}
    assert app.tracker.get_job(job.id).kit_feedback.splitlines() == ["Put the Lake project first", "Shorter summary"]
    httpx.post(f"{base}/feedback/clear/{job.id}")
    assert app.tracker.get_job(job.id).kit_feedback is None


def test_editing_the_resume_text_rebuilds_the_pdf(server, tmp_path):
    base, app = server()
    ingest(base, ["Acme"])
    job = app.tracker.top_jobs(limit=5)[0]
    assert "Regenerate resume" in httpx.get(f"{base}/edit/{job.id}").text  # no text saved yet
    out = tmp_path / "tailored"
    out.mkdir(exist_ok=True)
    (out / "acme.pdf").write_bytes(b"%PDF")
    (out / "acme.txt").write_text("JANE DOE\nTown | jane@example.com | +1-555-123-4567\n\nSUMMARY\nAnalyst.", encoding="utf-8")
    app.tracker.set_kit(job.id, resume_path=str(out / "acme.pdf"))
    assert "Analyst." in httpx.get(f"{base}/edit/{job.id}").text
    text = "JANE DOE\nTown | jane@example.com | +1-555-123-4567\n\nSUMMARY\nAnalyst who loves SQL.\n\nSKILLS\n- Tools: SQL"
    r = httpx.post(f"{base}/edit/{job.id}", data={"text": text}, follow_redirects=False)
    assert r.status_code == 303 and "Saved" in r.headers["location"].replace("%20", " ")
    saved = app.tracker.get_job(job.id)
    assert saved.kit_resume_path.endswith(".pdf") and "edited by you" in saved.kit_notes
    assert "Analyst who loves SQL." in open(saved.kit_resume_path[:-4] + ".txt", encoding="utf-8").read()


def test_follow_ups_and_people_links(server):
    from datetime import datetime, timedelta, timezone

    from jobhunter.tracker import Application

    base, app = server()
    ingest(base, ["Acme, Inc."])
    job = app.tracker.top_jobs(limit=5)[0]
    a = app.tracker.log_application(job.id)
    with app.tracker.Session() as s:
        s.get(Application, a.id).applied_date = datetime.now(timezone.utc) - timedelta(days=9)
        s.commit()
    page = httpx.get(f"{base}/applications").text
    assert "Follow up" in page and "9 days" in page and "Acme+Syracuse+University" in page
    assert "Syracuse alumni" in httpx.get(f"{base}/job/{job.id}").text


def test_fill_to_review_progress_shows_until_you_submit(server):
    base, app = server()
    ingest(base, ["Acme", "Beta"])
    acme, beta = sorted(app.tracker.top_jobs(limit=5), key=lambda j: j.company)
    h = {"X-Extension-Key": app.extension_key()}
    assert httpx.post(f"{base}/api/x/progress/{acme.id}", json={"state": "submitted"}, headers=h).status_code == 400
    httpx.post(f"{base}/api/x/progress/{acme.id}", json={"state": "review", "step": "Review", "url": "https://x.test/apply"}, headers=h)
    httpx.post(f"{base}/api/x/progress/{beta.id}", json={"state": "needs_you", "step": "Application Questions",
                                                         "note": "Salary expectation"}, headers=h)
    waiting = httpx.get(f"{base}/?view=waiting").text
    assert "at Review: submit it" in waiting and "needs you: Application Questions" in waiting
    assert "Needs you at Application Questions:</b> Salary expectation" in httpx.get(f"{base}/job/{beta.id}").text
    httpx.post(f"{base}/api/x/applied/{acme.id}", json={}, headers=h)  # submitted: no longer waiting
    waiting = httpx.get(f"{base}/?view=waiting").text
    assert "at Review" not in waiting and "needs you" in waiting
    httpx.post(f"{base}/api/x/progress/{beta.id}", json={"state": "clear"}, headers=h)
    assert app.tracker.get_job(beta.id).apply_progress is None


def test_shortlisting_opens_the_application_once_the_resume_is_ready(server, monkeypatch):
    monkeypatch.setattr("jobhunter.pipeline.make_resume",
                        lambda profile, tracker, job_id: (tracker.set_kit(job_id, resume_path="r.pdf"), ("r.pdf", []))[1])
    base, app = server()
    ingest(base, ["Acme", "Beta"])
    acme, beta = sorted(app.tracker.top_jobs(limit=5), key=lambda j: j.company)
    h = {"X-Extension-Key": app.extension_key()}
    app.tracker.set_kit(beta.id, resume_path="old.pdf")  # shortlisted before: not opened by surprise
    httpx.post(f"{base}/decide/{acme.id}", data={"decision": "shortlisted"}, follow_redirects=False)
    for _ in range(100):
        if app.tracker.get_job(acme.id).kit_resume_path:
            break
        time.sleep(0.05)
    ready = httpx.get(f"{base}/api/x/ready", headers=h).json()["jobs"]
    assert [j["id"] for j in ready] == [acme.id]
    httpx.post(f"{base}/api/x/opened/{acme.id}", json={}, headers=h)
    assert httpx.get(f"{base}/api/x/ready", headers=h).json()["jobs"] == []
    # The autopilot asks for the job it opened by id, whatever page the sign-in moved to.
    ctx = httpx.get(f"{base}/api/x/context", params={"job_id": acme.id, "url": "https://example.com/login"}, headers=h).json()
    assert ctx["job"]["id"] == acme.id
    ctx = httpx.get(f"{base}/api/x/context", params={"job_id": 99999, "url": "https://example.com/login"}, headers=h).json()
    assert ctx["job"] is None
