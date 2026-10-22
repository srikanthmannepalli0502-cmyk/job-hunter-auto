"""Review app over the tracker database: runs on your PC or hosted (e.g. Azure App Service).

Ranked open jobs -> Shortlist (a tailored resume is generated in the background) or Skip ->
Apply (opens the posting) -> Mark applied (records the resume and cover note you sent) ->
update status later. A cover note is written only when you ask for one.

Local:  python -m jobhunter review                 (127.0.0.1 only, no sign-in)
Hosted: python -m jobhunter review --host 0.0.0.0 --no-browser, behind the platform's sign-in
        (Azure App Service authentication). Set JOBHUNTER_ALLOWED_USERS to the email(s) allowed
        in; the platform passes the signed-in user in the X-MS-CLIENT-PRINCIPAL-NAME header and
        strips that header from incoming requests, so it can't be forged.
API (Bearer JOBHUNTER_API_TOKEN): POST /api/ingest (a gathered run), GET /api/backup (the
database), POST /api/restore (load a database into an empty app).
"""

import base64
import hmac
import html
import json
import os
import queue
import sqlite3
import tempfile
import threading
import webbrowser
from contextlib import closing
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

from loguru import logger
from sqlalchemy import or_, select

from jobhunter.config import Profile
from jobhunter.tracker import STATUSES, Application, Job, Tracker

VIEWS = {
    "todo": "To review",
    "shortlisted": "Shortlisted",
    "applied": "Applied",
    "skipped": "Skipped",
    "closed": "Closed",
}
PAGE_SIZE = 200

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--accent:#2457c5;--ok:#1b7f3b;--warn:#a15c00;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--card:#1f1f22;--fg:#ececee;--muted:#a0a0a8;--line:#333338;--accent:#7aa2ff;--ok:#5fc27e;--warn:#e0a24a;--bad:#ff7a6e}}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;gap:16px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line);background:var(--card);flex-wrap:wrap}
header b{font-size:16px}nav a{margin-right:12px;color:var(--muted);text-decoration:none}nav a.on{color:var(--accent);font-weight:600}
header .who{margin-left:auto;color:var(--muted);font-size:12px}
main{padding:16px 20px;max-width:1300px;margin:auto}a{color:var(--accent)}
.tablewrap{overflow-x:auto}table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}.muted{color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.tag{display:inline-block;padding:1px 6px;border-radius:9px;font-size:11px;border:1px solid var(--line);color:var(--muted);margin-left:4px}
form.inline{display:inline}button,.btn{font:inherit;padding:3px 9px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer;text-decoration:none;display:inline-block}
button.primary,.btn.primary{background:var(--accent);border-color:var(--accent);color:#fff}input,select{font:inherit;padding:4px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:12px}
pre{white-space:pre-wrap;font:13px/1.5 system-ui,sans-serif;margin:0}.filters{margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.flash{padding:8px 12px;border-radius:6px;background:var(--card);border:1px solid var(--line);margin-bottom:12px}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin:8px 0}
"""


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - dt).days
    return "today" if days <= 0 else f"{days}d ago"


class KitWorker:
    """Generates resumes / cover notes one at a time in the background (keeps within the LLM's rate limit)."""

    def __init__(self, app: "App"):
        self.app = app
        self.jobs: queue.Queue = queue.Queue()
        self.status: dict[int, str] = {}  # job id -> queued / generating / failed: ... / done note
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, job_id: int, what: str = "resume") -> None:
        if self.status.get(job_id) in ("queued", "generating"):
            return
        self.status[job_id] = "queued"
        self.jobs.put((job_id, what))

    def _loop(self) -> None:
        from jobhunter.pipeline import make_cover_note, make_resume

        while True:
            job_id, what = self.jobs.get()
            self.status[job_id] = "generating"
            try:
                if what == "cover":
                    ok = make_cover_note(self.app.profile, self.app.tracker, job_id)
                    self.status[job_id] = "Cover note ready." if ok else "failed: the LLM returned nothing"
                else:
                    path, warnings = make_resume(self.app.profile, self.app.tracker, job_id)
                    self.status[job_id] = ("Resume ready." + (" Check: " + "; ".join(warnings) if warnings else "")
                                           if path else "failed: could not fit the resume on one page")
            except Exception as e:  # noqa: BLE001 - shown on the job page
                logger.exception(f"kit for job {job_id} failed")
                self.status[job_id] = f"failed: {e}"


class App:
    def __init__(self, profile: Profile, tracker: Tracker):
        self.profile = profile
        self.tracker = tracker
        self.files_root = Path(profile.output_dir).resolve()
        self.allowed_users = {u.strip().lower() for u in os.environ.get("JOBHUNTER_ALLOWED_USERS", "").split(",") if u.strip()}
        self.api_token = os.environ.get("JOBHUNTER_API_TOKEN", "")
        self.worker = KitWorker(self)

    # ------------------------------------------------------------------ pages
    def page(self, title: str, body: str, view: str = "", flash: str = "", user: str = "") -> str:
        nav = "".join(f'<a href="/?view={k}" class="{"on" if k == view else ""}">{v}</a>' for k, v in VIEWS.items())
        nav += f'<a href="/applications" class="{"on" if view == "apps" else ""}">Applications</a>'
        nav += f'<a href="/runs" class="{"on" if view == "runs" else ""}">Runs</a>'
        nav += f'<a href="/answers" class="{"on" if view == "answers" else ""}">Profile &amp; answers</a>'
        who = f'<span class="who">{_e(user)}</span>' if user else ""
        flash_html = f'<div class="flash">{_e(flash)}</div>' if flash else ""
        return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{_e(title)} - Job Hunter</title><style>{CSS}</style></head><body>"
                f"<header><b>Job Hunter</b><nav>{nav}</nav>{who}</header><main>{flash_html}{body}</main></body></html>")

    def jobs_query(self, view: str, q: str, job_type: str):
        applied = select(Application.job_id)
        stmt = select(Job)
        if view == "applied":
            stmt = stmt.where(Job.id.in_(applied))
        elif view == "closed":
            stmt = stmt.where(Job.closed, ~Job.id.in_(applied))
        else:
            decisions = {"todo": ("new", "shortlisted"), "shortlisted": ("shortlisted",), "skipped": ("skipped",)}[view]
            stmt = stmt.where(~Job.closed, ~Job.id.in_(applied), Job.decision.in_(decisions))
        if q:
            like = f"%{q}%"
            stmt = stmt.where(or_(Job.title.ilike(like), Job.company.ilike(like), Job.location.ilike(like)))
        if job_type:
            stmt = stmt.where(Job.job_type == job_type)
        return stmt.order_by(Job.overall_score.desc(), Job.date_fetched.desc()).limit(PAGE_SIZE)

    def resume_state(self, j: Job) -> str:
        state = self.worker.status.get(j.id, "")
        if state in ("queued", "generating"):
            return '<span class="tag">resume: generating&hellip;</span>'
        if state.startswith("failed"):
            return '<span class="tag bad">resume failed</span>'
        return '<span class="tag">resume ready</span>' if j.kit_resume_path else ""

    def list_page(self, params: dict, flash: str = "", user: str = "") -> str:
        view = params.get("view", "todo") if params.get("view") in VIEWS else "todo"
        q, job_type = params.get("q", ""), params.get("type", "")
        with self.tracker.Session() as s:
            jobs = list(s.scalars(self.jobs_query(view, q, job_type)))
        types = "".join(f'<option value="{t}" {"selected" if t == job_type else ""}>{t.replace("_", "-") or "all types"}</option>'
                        for t in ("", "full_time", "intern", "contract", "part_time"))
        filters = (f'<form class="filters" method="get"><input type="hidden" name="view" value="{view}">'
                   f'<input name="q" placeholder="title, company or place" value="{_e(q)}">'
                   f'<select name="type">{types}</select><button>Filter</button>'
                   f'<span class="muted">{len(jobs)} jobs{" (first " + str(PAGE_SIZE) + ")" if len(jobs) == PAGE_SIZE else ""}</span></form>')
        rows = []
        for i, j in enumerate(jobs, 1):
            star = " &#9733;" if j.decision == "shortlisted" else ""
            sponsor = (f'<span class="ok" title="{_e(j.sponsor_note)}">{j.sponsor_score:.0f}</span>' if j.sponsor_score
                       else '<span class="bad" title="no H-1B filings found">none</span>')
            warn = f' <span class="warn" title="{_e(j.sponsorship_warning)}">&#9888; may not sponsor</span>' if j.sponsorship_warning else ""
            kind = f'<span class="tag">{_e(j.job_type.replace("_", "-"))}</span>' if j.job_type and j.job_type != "full_time" else ""
            actions = self.decision_buttons(j, view)
            if j.decision == "shortlisted" and view in ("todo", "shortlisted"):
                actions = f"<a class='btn primary' href='{_e(j.job_url)}' target='_blank' rel='noopener'>Apply</a> " + actions
            rows.append(
                f"<tr><td class='num muted'>{i}</td><td class='num'><b>{j.overall_score:.0f}</b></td>"
                f"<td><a href='/job/{j.id}'>{_e(j.title)}</a>{star}{kind}{self.resume_state(j)}{warn}</td><td>{_e(j.company)}</td>"
                f"<td class='muted'>{_e(j.location)}</td><td class='muted'>{_ago(j.date_posted)}</td>"
                f"<td class='num'>{sponsor}</td><td class='num muted'>{j.match_score:.0f}</td><td>{actions}</td></tr>")
        table = ("<div class='tablewrap'><table><tr><th>#</th><th>Score</th><th>Job</th><th>Company</th><th>Location</th><th>Posted</th>"
                 "<th>H-1B</th><th>Fit</th><th></th></tr>" + "".join(rows) + "</table></div>") if rows else "<p class='muted'>Nothing here.</p>"
        return self.page(VIEWS[view], filters + table, view, flash, user)

    def decision_buttons(self, j: Job, view: str) -> str:
        back = f"/?view={view}"
        def btn(decision, label, primary=False):
            cls = " class='primary'" if primary else ""
            return (f"<form class='inline' method='post' action='/decide/{j.id}'><input type='hidden' name='decision' value='{decision}'>"
                    f"<input type='hidden' name='back' value='{back}'><button{cls}>{label}</button></form> ")
        if view in ("applied", "closed"):
            return ""
        out = btn("shortlisted", "Shortlist", True) if j.decision != "shortlisted" else ""
        out += btn("skipped", "Skip") if j.decision != "skipped" else btn("new", "Restore")
        return out

    def job_page(self, job_id: int, flash: str = "", user: str = "") -> str:
        with self.tracker.Session() as s:
            j = s.get(Job, job_id)
            if j is None:
                return self.page("Not found", "<p>No such job.</p>", user=user)
            apps = list(s.scalars(select(Application).where(Application.job_id == job_id)))
        state = self.worker.status.get(j.id, "")
        busy = state in ("queued", "generating")
        files = ""
        for label, path in (("Tailored resume", j.kit_resume_path), ("Cover note", j.kit_cover_path)):
            if path:
                files += f"<div>{label}: <a href='/file?path={quote(path)}' target='_blank'>{_e(Path(path).name)}</a></div>"
        if j.kit_resume_path and j.kit_notes:
            files += ("<div class='muted' style='margin-top:6px'>Resume check:<ul>"
                      + "".join(f"<li>{_e(n)}</li>" for n in j.kit_notes.splitlines() if n.strip()) + "</ul></div>")
        if j.kit_resume_path and not busy:
            asked = "".join(f"<li>{_e(r)}</li>" for r in (j.kit_feedback or "").splitlines() if r.strip())
            files += (
                "<form method='post' action='/revise/" + str(j.id) + "' style='margin-top:10px'>"
                "<label for='fb'><b>Change something?</b> Say what to change; the resume is rebuilt with your "
                "request (still fact-checked against your master resume).</label>"
                "<textarea id='fb' name='feedback' rows='3' style='width:100%' placeholder='e.g. Put the Snowflake "
                "project first; mention reconciliation in the summary; drop the NFL project'></textarea>"
                "<button class='primary'>Rebuild resume with my changes</button> "
                f"<a href='/edit/{j.id}'>or edit the text yourself</a></form>"
                + (f"<div class='muted'>Your requests for this job (used on every rebuild):<ul>{asked}</ul>"
                   f"<form class='inline' method='post' action='/feedback/clear/{j.id}'><button>Clear requests</button></form></div>"
                   if asked else ""))
        if busy:
            files += "<div class='muted'>Generating&hellip; a careful resume takes a few minutes; refresh later.</div>"
        elif state.startswith("failed"):
            files += f"<div class='bad'>{_e(state)}</div>"
        warn = (f"<p class='warn'>&#9888; This posting may not sponsor visas: <i>{_e(j.sponsorship_warning)}</i></p>"
                if j.sponsorship_warning else "")
        if apps:
            a = apps[0]
            applied = f"<p class='ok'>Applied {a.applied_date:%Y-%m-%d} &middot; status: <b>{_e(a.status)}</b></p>"
            apply_btns = ""
        else:
            applied = ""
            apply_btns = (f"<form class='inline' method='post' action='/applied/{j.id}'><button>Mark applied</button></form> "
                          "<span class='muted'>records the resume and cover note below as what you sent</span>")
        def post(action, label):
            disabled = " disabled" if busy else ""
            return f"<form class='inline' method='post' action='/{action}/{j.id}'><button{disabled}>{label}</button></form> "
        kit_btns = (post("resume", "Regenerate resume" if j.kit_resume_path else "Generate resume")
                    + post("cover", "Rewrite cover note" if j.kit_cover_path else "Write cover note"))
        body = (
            f"<div class='card'><h2 style='margin:0 0 4px'>{_e(j.title)}</h2>"
            f"<div><b>{_e(j.company)}</b> &middot; {_e(j.location)} &middot; {_e((j.job_type or '').replace('_', '-'))} "
            f"&middot; posted {_ago(j.date_posted)} &middot; source {_e(j.source)}</div>"
            f"<p>Score <b>{j.overall_score:.0f}</b> (fit {j.match_score:.0f}, H-1B {j.sponsor_score:.0f})<br>"
            f"<span class='muted'>H-1B: {_e(j.sponsor_note or 'no data')}</span></p>{warn}{applied}"
            f"<div class='row'><a class='btn primary' href='{_e(j.job_url)}' target='_blank' rel='noopener'>Apply</a> "
            f"{self.decision_buttons(j, 'todo')}</div>"
            f"<div class='row'>{kit_btns}</div>{files}<div class='row'>{apply_btns}</div></div>"
            f"<div class='card'><pre>{_e(j.description or 'No description from this source.')}</pre></div>")
        return self.page(j.title, body, "", flash, user)

    def edit_page(self, job_id: int, flash: str = "", user: str = "") -> str:
        from jobhunter.pipeline import resume_text_for

        j = self.tracker.get_job(job_id)
        if j is None:
            return self.page("Not found", "<p>No such job.</p>", user=user)
        text = resume_text_for(j)
        if not text:
            body = ("<p>This resume was made before editing existed. Click <b>Regenerate resume</b> on the "
                    f"<a href='/job/{j.id}'>job page</a> first.</p>")
        else:
            body = (f"<div class='card'><h2 style='margin:0'>Edit resume: {_e(j.title)} at {_e(j.company)}</h2>"
                    "<p class='muted'>Same layout rules as your master resume: headings in capitals, bullets start "
                    "with \"- \", a job line ends with its dates. Saving rebuilds the PDF exactly as written "
                    "(it must still fit on one page) and lists anything your master resume doesn't support.</p>"
                    f"<form method='post' action='/edit/{j.id}'><textarea name='text' rows='40' style='width:100%;"
                    f"font-family:monospace;font-size:12px'>{_e(text)}</textarea>"
                    "<p><button class='primary'>Save and rebuild PDF</button> "
                    f"<a href='/job/{j.id}'>Cancel</a></p></form></div>")
        return self.page("Edit resume", body, "", flash, user)

    def applications_page(self, flash: str = "", user: str = "") -> str:
        rows = []
        for a, j in self.tracker.applications():
            opts = "".join(f"<option {'selected' if s == a.status else ''}>{s}</option>" for s in STATUSES)
            resume = f"<a href='/file?path={quote(a.resume_path)}' target='_blank'>resume</a>" if a.resume_path else "<span class='muted'>no resume recorded</span>"
            cover = f" &middot; <a href='/file?path={quote(a.cover_note_path)}' target='_blank'>cover note</a>" if a.cover_note_path else ""
            rows.append(
                f"<tr><td>{a.applied_date:%Y-%m-%d}</td><td><a href='/job/{j.id}'>{_e(j.title)}</a></td><td>{_e(j.company)}</td>"
                f"<td>{resume}{cover}</td><td><form class='inline' method='post' action='/status/{a.id}'>"
                f"<select name='status'>{opts}</select> <button>Save</button></form></td></tr>")
        body = ("<div class='tablewrap'><table><tr><th>Applied</th><th>Job</th><th>Company</th><th>What you sent</th><th>Status</th></tr>"
                + "".join(rows) + "</table></div>") if rows else "<p class='muted'>No applications yet.</p>"
        return self.page("Applications", body, "apps", flash, user)

    def runs_page(self, user: str = "") -> str:
        rows = []
        for r in self.tracker.recent_runs(30):
            started = r.started if r.started.tzinfo else r.started.replace(tzinfo=timezone.utc)
            problems = "<br>".join(_e(p) for p in (r.problems or "").splitlines()) or "<span class='muted'>none</span>"
            rows.append(f"<tr><td>{started.astimezone():%Y-%m-%d %H:%M}</td><td class='num'>{r.fetched}</td><td class='num'>{r.new}</td>"
                        f"<td class='num'>{r.seen_again}</td><td class='num'>{r.closed}</td><td>{problems}</td></tr>")
        body = ("<div class='tablewrap'><table><tr><th>Started</th><th>Fetched</th><th>New</th><th>Seen again</th><th>Closed</th><th>Problems</th></tr>"
                + "".join(rows) + "</table></div>") if rows else "<p class='muted'>No runs yet.</p>"
        return self.page("Runs", body, "runs", user=user)

    # ------------------------------------------------------------------ profile & answers
    def resume_text(self) -> str:
        from jobhunter.resume.parser import parse_resume

        return parse_resume(self.profile.base_resume) or ""

    def field_values(self) -> dict[str, str]:
        from jobhunter.answers import defaults_from_profile

        values = defaults_from_profile(self.profile, self.resume_text())
        values.update({a.field: a.answer for a in self.tracker.answers() if a.field})
        return values

    def upload_name(self, kind: str, suffix: str) -> str:
        """The file name employers see: 'First_Last_Resume.pdf', not the internal name."""
        import re as _re

        first = next((line for line in self.resume_text().splitlines() if line.strip()), "")
        name = "_".join(w.capitalize() for w in _re.findall(r"[A-Za-z]+", first)[:3]) or "My"
        return f"{name}_{'Resume' if kind == 'resume' else 'Cover_Letter'}{suffix}"

    def extension_key(self, regenerate: bool = False) -> str:
        import secrets

        key = "" if regenerate else self.tracker.setting("extension_key")
        if not key:
            key = secrets.token_urlsafe(24)
            self.tracker.set_setting("extension_key", key)
        return key

    def answers_page(self, flash: str = "", user: str = "", show_key: bool = False) -> str:
        from jobhunter.answers import FIELDS

        values = self.field_values()
        saved = {a.field for a in self.tracker.answers() if a.field}
        rows = "".join(
            f"<tr><td><label for='f_{k}'>{_e(label)}</label>{'' if k in saved else ' <span class=tag>not saved yet</span>'}</td>"
            f"<td><input id='f_{k}' name='f_{k}' value='{_e(values.get(k, ''))}' style='width:100%'></td></tr>"
            for k, label, _ in FIELDS)
        basics = (f"<form method='post' action='/answers/save'><div class='tablewrap'><table>{rows}</table></div>"
                  "<p><button class='primary'>Save profile</button> <span class='muted'>Only saved values are used "
                  "to fill forms. Leave a field empty to never fill it.</span></p></form>")
        free = "".join(
            f"<tr><td>{_e(a.question)}</td><td><pre>{_e(a.answer)}</pre></td><td class='muted'>{a.updated:%Y-%m-%d}</td>"
            f"<td><form class='inline' method='post' action='/answers/delete/{a.id}'><button>Delete</button></form></td></tr>"
            for a in self.tracker.answers() if not a.field)
        free_html = ("<div class='tablewrap'><table><tr><th>Question</th><th>Your answer</th><th>Saved</th><th></th></tr>"
                     f"{free}</table></div>") if free else "<p class='muted'>None yet: the extension offers to save the answers you type on application forms.</p>"
        add = ("<form method='post' action='/answers/add' class='row'><input name='question' placeholder='Question' style='flex:1;min-width:200px'>"
               "<input name='answer' placeholder='Your answer' style='flex:2;min-width:200px'><button>Add</button></form>")
        key = self.extension_key() if show_key else ""
        key_html = (f"<p>Extension key: <code>{_e(key)}</code></p>" if key else
                    "<p><a class='btn' href='/answers?key=1'>Show extension key</a></p>")
        ext = (f"<div class='card'><h3 style='margin-top:0'>Chrome extension</h3>{key_html}"
               "<p class='muted'>Paste the app address and this key into the extension's settings. Regenerate it if you "
               "think someone else has seen it (the old key stops working).</p>"
               "<form method='post' action='/extension/regenerate'><button>Regenerate key</button></form></div>")
        body = (f"<div class='card'><h3 style='margin-top:0'>Profile</h3>{basics}</div>"
                f"<div class='card'><h3 style='margin-top:0'>Saved answers</h3>{free_html}{add}</div>{ext}")
        return self.page("Profile & answers", body, "answers", flash, user)

    def match_job(self, url: str, title: str = "") -> Job | None:
        """The job whose posting this page belongs to: same site and path (or requisition id)."""
        import re as _re

        page = urlparse(url)
        if not page.netloc:
            return None
        with self.tracker.Session() as s:
            candidates = list(s.scalars(select(Job).where(Job.job_url.ilike(f"%{page.netloc}%"))))
        best, best_score = None, 0
        for j in candidates:
            posting = urlparse(j.job_url or "")
            score = 0
            if posting.path and page.path.startswith(posting.path.rstrip("/")):
                score = 3
            else:
                ids = set(_re.findall(r"[A-Z]{0,4}\d{5,}|[0-9a-f]{8}-[0-9a-f-]{27}", f"{posting.path}?{posting.query}"))
                if ids and any(i in url for i in ids):
                    score = 2
            if score and j.decision == "shortlisted":
                score += 1
            if score > best_score:
                best, best_score = j, score
        return best

    # ------------------------------------------------------------------ helpers
    def file_path(self, raw: str) -> Path | None:
        """Only files inside the tailored-resume folder can be opened."""
        try:
            path = Path(raw).resolve()
        except (OSError, ValueError):
            return None
        return path if path.is_file() and self.files_root in path.parents else None

    def token_ok(self, header: str) -> bool:
        return bool(self.api_token) and hmac.compare_digest(header or "", f"Bearer {self.api_token}")

    def db_path(self) -> Path | None:
        url = str(self.tracker.engine.url)
        return Path(self.tracker.engine.url.database) if url.startswith("sqlite") and self.tracker.engine.url.database else None


def signed_in_emails(headers) -> list[str]:
    """Emails of the signed-in user, from the sign-in proxy's headers (Microsoft or Google)."""
    emails = [(headers.get("X-MS-CLIENT-PRINCIPAL-NAME") or "").strip().lower()]
    raw = headers.get("X-MS-CLIENT-PRINCIPAL")
    if raw:
        try:
            claims = json.loads(base64.b64decode(raw)).get("claims", [])
        except ValueError:
            claims = []
        emails += [str(c.get("val", "")).strip().lower() for c in claims
                   if c.get("typ") in _EMAIL_CLAIMS]
    return [e for e in emails if e]


_EMAIL_CLAIMS = {"email", "preferred_username", "upn",
                 "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress"}


_SOURCE_NAMES = {"greenhouse": "Greenhouse", "lever": "Lever", "ashby": "Ashby", "workday": "Workday",
                 "oracle": "Oracle", "smartrecruiters": "SmartRecruiters", "amazon": "amazon.jobs"}


def found_note(job: Job | None) -> str:
    """How you came across a job, for the assistant (e.g. 'How did you hear about us?')."""
    if job is None:
        return ""
    if job.source in _SOURCE_NAMES:
        how = f"on {job.company}'s own careers site ({_SOURCE_NAMES[job.source]}), not through a recruiter or referral"
    else:
        how = f"on the job board {job.source}, not through a recruiter or referral"
    return f"The user found this posting themselves {how}."


def login_page(next_path: str) -> str:
    to = quote(next_path if next_path.startswith("/") and not next_path.startswith("//") else "/")
    return (
        "<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width'>"
        "<title>Sign in</title><body style='font:15px system-ui;max-width:360px;margin:15vh auto;padding:0 16px'>"
        "<h2>Job Hunter</h2>"
        f"<p><a href='/.auth/login/google?post_login_redirect_uri={to}'>Sign in with Google</a></p>"
        f"<p><a href='/.auth/login/aad?post_login_redirect_uri={to}'>Sign in with Microsoft</a></p></body>"
    )


def make_handler(app: App, port: int):
    local_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet console
            pass

        # ---- responses
        def send(self, data: bytes, kind: str, status: int = 200, extra: dict | None = None):
            if self.command == "POST" and not getattr(self, "_body_read", False):
                self.body()  # read an unread upload first, or the client may see a reset connection
            self.send_response(status)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-Content-Type-Options", "nosniff")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(data)

        def send_html(self, text: str, status: int = 200):
            self.send(text.encode("utf-8"), "text/html; charset=utf-8", status)

        def send_json(self, obj, status: int = 200):
            self.send(json.dumps(obj).encode("utf-8"), "application/json", status)

        def redirect(self, to: str):
            self.send_response(303)
            self.send_header("Location", to)
            self.end_headers()

        # ---- access control
        def user(self) -> str | None:
            """Signed-in user's email, '' when running locally without sign-in, None if not allowed."""
            if not app.allowed_users:
                return ""
            for email in signed_in_emails(self.headers):
                if email in app.allowed_users:
                    return email
            return None

        def require_user(self) -> str | None:
            user = self.user()
            if user is None:
                if self.headers.get("X-MS-CLIENT-PRINCIPAL-NAME"):
                    self.send_html("Not allowed for this account. <a href='/.auth/logout'>Sign out</a>", 403)
                else:
                    self.redirect("/login?next=" + quote(self.path))
            return user

        def same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            host = self.headers.get("Host", "")
            return origin in local_origins or origin == f"https://{host}"

        def body(self) -> bytes:
            self._body_read = True
            return self.rfile.read(int(self.headers.get("Content-Length") or 0))

        # ---- routes
        def do_GET(self):
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            if url.path == "/api/backup":
                return self.api_backup()
            if url.path.startswith("/api/x/"):
                return self.api_extension("GET", url.path, params)
            if url.path == "/healthz":
                return self.send(b"ok", "text/plain")
            if url.path == "/login":
                return self.send_html(login_page(params.get("next", "/")))
            user = self.require_user()
            if user is None:
                return
            parts = url.path.strip("/").split("/")
            msg = params.get("msg", "")
            if url.path == "/":
                return self.send_html(app.list_page(params, msg, user))
            if parts[0] == "job" and len(parts) == 2 and parts[1].isdigit():
                return self.send_html(app.job_page(int(parts[1]), msg, user))
            if parts[0] == "edit" and len(parts) == 2 and parts[1].isdigit():
                return self.send_html(app.edit_page(int(parts[1]), msg, user))
            if url.path == "/applications":
                return self.send_html(app.applications_page(msg, user))
            if url.path == "/runs":
                return self.send_html(app.runs_page(user))
            if url.path == "/answers":
                return self.send_html(app.answers_page(msg, user, show_key=params.get("key") == "1"))
            if url.path == "/file":
                path = app.file_path(params.get("path", ""))
                if path is None:
                    return self.send_html("Not found", 404)
                kind = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain; charset=utf-8"
                return self.send(path.read_bytes(), kind, extra={"Content-Disposition": f'inline; filename="{path.name}"'})
            self.send_html("Not found", 404)

        def do_POST(self):
            path = urlparse(self.path).path
            if path == "/api/ingest":
                return self.api_ingest()
            if path == "/api/restore":
                return self.api_restore()
            if path.startswith("/api/x/"):
                return self.api_extension("POST", path, {})
            if not self.same_origin():  # block other websites posting to this app
                return self.send_html("Forbidden", 403)
            if self.require_user() is None:
                return
            form = {k: v[0] for k, v in parse_qs(self.body().decode("utf-8")).items()}
            if path == "/answers/save":
                from jobhunter.answers import FIELDS

                for key, label, _ in FIELDS:
                    value = form.get(f"f_{key}", "").strip()
                    if value:
                        app.tracker.save_answer(label, value, field=key)
                return self.redirect("/answers?msg=" + quote("Profile saved."))
            if path == "/answers/add":
                try:
                    app.tracker.save_answer(form.get("question", ""), form.get("answer", ""))
                except ValueError as e:
                    return self.redirect("/answers?msg=" + quote(str(e)))
                return self.redirect("/answers?msg=" + quote("Answer saved."))
            if path == "/extension/regenerate":
                app.extension_key(regenerate=True)
                return self.redirect("/answers?key=1&msg=" + quote("New key created; update it in the extension."))
            parts = path.strip("/").split("/")
            if len(parts) == 3 and parts[:2] == ["feedback", "clear"] and parts[2].isdigit():
                app.tracker.clear_feedback(int(parts[2]))
                return self.redirect(f"/job/{parts[2]}?msg=" + quote("Requests cleared."))
            if len(parts) == 2 and parts[0] == "revise" and parts[1].isdigit():
                app.tracker.add_feedback(int(parts[1]), form.get("feedback", ""))
                app.worker.submit(int(parts[1]), "resume")
                return self.redirect(f"/job/{parts[1]}?msg=" + quote("Rebuilding with your changes; refresh in a few minutes."))
            if len(parts) == 2 and parts[0] == "edit" and parts[1].isdigit():
                from jobhunter.pipeline import save_edited_resume

                path_saved, notes = save_edited_resume(app.profile, app.tracker, int(parts[1]), form.get("text", ""))
                if not path_saved:
                    return self.redirect(f"/edit/{parts[1]}?msg=" + quote(" ".join(notes)))
                return self.redirect(f"/job/{parts[1]}?msg=" + quote("Saved your edit and rebuilt the PDF."))
            if len(parts) == 3 and parts[:2] == ["answers", "delete"] and parts[2].isdigit():
                app.tracker.delete_answer(int(parts[2]))
                return self.redirect("/answers?msg=" + quote("Answer deleted."))
            if len(parts) != 2 or not parts[1].isdigit():
                return self.send_html("Not found", 404)
            action, item = parts[0], int(parts[1])
            try:
                if action == "decide":
                    decision = form.get("decision", "new")
                    job = app.tracker.set_decision(item, decision)
                    if decision == "shortlisted" and not job.kit_resume_path:
                        app.worker.submit(item, "resume")  # shortlisting generates the resume
                    back = form.get("back", "/")
                    return self.redirect(back if back.startswith("/") else "/")
                if action in ("resume", "cover"):
                    app.worker.submit(item, action)
                    note = "Generating the resume" if action == "resume" else "Writing the cover note"
                    return self.redirect(f"/job/{item}?msg={quote(note + '; refresh in a minute.')}")
                if action == "applied":
                    app.tracker.log_application(item)
                    return self.redirect(f"/job/{item}?msg={quote('Application recorded.')}")
                if action == "status":
                    app.tracker.update_status(item, form.get("status", "applied"))
                    return self.redirect("/applications?msg=" + quote("Status saved."))
            except ValueError as e:
                return self.send_html(app.page("Error", f"<p class='bad'>{_e(e)}</p>"), 400)
            self.send_html("Not found", 404)

        # ---- API for the Chrome extension (X-Extension-Key)
        def api_extension(self, method: str, path: str, params: dict):
            from jobhunter.answers import EXCLUDE, FIELDS, SENSITIVE, work_history

            key = app.tracker.setting("extension_key")
            if not key or not hmac.compare_digest(self.headers.get("X-Extension-Key", ""), key):
                return self.send_json({"error": "unauthorized: check the extension key"}, 401)
            parts = path.strip("/").split("/")[2:]  # after api/x
            data = {}
            if method == "POST":
                try:
                    data = json.loads(self.body() or b"{}")
                except ValueError:
                    return self.send_json({"error": "bad JSON"}, 400)
            try:
                if method == "GET" and parts == ["context"]:
                    job = app.match_job(params.get("url", ""), params.get("title", ""))
                    values = app.field_values()
                    saved = {a.field for a in app.tracker.answers() if a.field}
                    return self.send_json({
                        "job": None if job is None else {
                            "id": job.id, "title": job.title, "company": job.company, "url": job.job_url,
                            "decision": job.decision, "has_resume": bool(job.kit_resume_path),
                            "has_cover": bool(job.kit_cover_path),
                            "resume_version": Path(job.kit_resume_path).name if job.kit_resume_path else "",
                            "status": app.worker.status.get(job.id, ""),
                            "notes": (job.kit_notes or "").splitlines()},
                        "fields": [{"key": k, "label": label, "keywords": kw, "exclude": EXCLUDE.get(k, []),
                                    "value": values[k]}
                                   for k, label, kw in FIELDS if k in saved and values.get(k)],
                        "history": work_history(app.resume_text()),
                        "answers": [{"question": a.question, "answer": a.answer}
                                    for a in app.tracker.answers() if not a.field],
                        "sensitive": SENSITIVE.pattern,
                    })
                if method == "GET" and len(parts) == 2 and parts[0] in ("resume", "cover") and parts[1].isdigit():
                    job = app.tracker.get_job(int(parts[1]))
                    raw = (job.kit_resume_path if parts[0] == "resume" else job.kit_cover_path) if job else None
                    path_ok = app.file_path(raw or "")
                    if path_ok is None:
                        return self.send_json({"error": f"no {parts[0]} for this job yet"}, 404)
                    kind = "application/pdf" if path_ok.suffix.lower() == ".pdf" else "text/plain; charset=utf-8"
                    return self.send(path_ok.read_bytes(), kind,
                                     extra={"X-Filename": app.upload_name(parts[0], path_ok.suffix)})
                if method == "POST" and parts == ["answers"]:
                    saved = 0
                    for item in data.get("items", [])[:100]:
                        q, a = str(item.get("question", "")), str(item.get("answer", ""))
                        if q.strip() and a.strip() and not SENSITIVE.search(q):
                            app.tracker.save_answer(q[:500], a[:5000])
                            saved += 1
                    return self.send_json({"saved": saved})
                if method == "POST" and parts == ["assist"]:
                    from jobhunter.assist import assist
                    from jobhunter.llm import LLMClient

                    job = app.tracker.get_job(int(data["job_id"])) if data.get("job_id") else None
                    qa = [(a.question, a.answer) for a in app.tracker.answers()]
                    answer = assist(LLMClient(), str(data.get("question", "")), app.resume_text(), qa,
                                    title=job.title if job else "", company=job.company if job else "",
                                    description=job.description if job else "",
                                    page_questions=[str(q) for q in data.get("page_questions", [])],
                                    found=found_note(job))
                    return self.send_json({"answer": answer})
                if method == "POST" and len(parts) == 2 and parts[0] == "revise" and parts[1].isdigit():
                    app.tracker.add_feedback(int(parts[1]), str(data.get("instructions", "")))
                    app.worker.submit(int(parts[1]), "resume")
                    return self.send_json({"queued": True})
                if method == "POST" and len(parts) == 2 and parts[0] == "applied" and parts[1].isdigit():
                    a = app.tracker.log_application(int(parts[1]))
                    return self.send_json({"application_id": a.id, "resume": a.resume_path, "cover": a.cover_note_path})
            except (ValueError, KeyError) as e:
                return self.send_json({"error": str(e)}, 400)
            self.send_json({"error": "not found"}, 404)

        # ---- API (token)
        def api_ingest(self):
            from jobhunter.pipeline import Gathered, store

            if not app.token_ok(self.headers.get("Authorization", "")):
                return self.send_json({"error": "unauthorized"}, 401)
            try:
                gathered = Gathered.from_dict(json.loads(self.body()))
            except (ValueError, TypeError, KeyError) as e:
                return self.send_json({"error": f"bad payload: {e}"}, 400)
            summary = store(app.tracker, gathered)
            self.send_json({"summary": summary.line(), "new": len(summary.new)})

        def api_backup(self):
            if not app.token_ok(self.headers.get("Authorization", "")):
                return self.send_json({"error": "unauthorized"}, 401)
            src = app.db_path()
            if src is None:
                return self.send_json({"error": "backup needs a SQLite database"}, 400)
            with tempfile.TemporaryDirectory() as tmp:
                copy = Path(tmp) / "backup.db"
                # closing(): sqlite3's own context manager commits but doesn't close the file.
                with closing(sqlite3.connect(src)) as s, closing(sqlite3.connect(copy)) as d:
                    s.backup(d)  # consistent copy even while the app is writing
                data = copy.read_bytes()
            self.send(data, "application/octet-stream", extra={"Content-Disposition": 'attachment; filename="jobhunter.db"'})

        def api_restore(self):
            """Load a database into an app that has no jobs yet (first deployment)."""
            if not app.token_ok(self.headers.get("Authorization", "")):
                return self.send_json({"error": "unauthorized"}, 401)
            dest = app.db_path()
            with app.tracker.Session() as s:
                existing = s.query(Job).count()
            if dest is None or existing:
                return self.send_json({"error": f"refused: the app already has {existing} jobs"}, 409)
            with tempfile.TemporaryDirectory() as tmp:
                upload = Path(tmp) / "upload.db"
                upload.write_bytes(self.body())
                with closing(sqlite3.connect(upload)) as s, closing(sqlite3.connect(dest)) as d:
                    s.backup(d)
            app.tracker.engine.dispose()
            app.tracker._add_missing_columns()
            with app.tracker.Session() as s:
                self.send_json({"restored_jobs": s.query(Job).count()})

    return Handler


def serve(profile: Profile, tracker: Tracker, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    app = App(profile, tracker)
    if host != "127.0.0.1" and not app.allowed_users:
        raise SystemExit("Refusing to listen beyond this PC without JOBHUNTER_ALLOWED_USERS (sign-in) set.")
    server = ThreadingHTTPServer((host, port), make_handler(app, port))
    url = f"http://127.0.0.1:{port}/"
    print(f"Review page: {url if host == '127.0.0.1' else f'http://{host}:{port}/'}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
