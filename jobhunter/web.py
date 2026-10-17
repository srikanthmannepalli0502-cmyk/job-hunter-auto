"""Review page: a small local website (http://127.0.0.1:8765) over the tracker database.

Ranked open jobs -> Shortlist / Skip -> Prepare kit (tailored resume + cover note) -> open the
posting and apply yourself -> Mark applied (records the kit you sent) -> update status later.
Uses only the standard library and listens on 127.0.0.1, so it's reachable from this PC only.
"""

import html
import threading
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse

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
_kit_lock = threading.Lock()  # one LLM job at a time

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1f;--muted:#6b6b70;--line:#e3e3e0;--accent:#2457c5;--ok:#1b7f3b;--warn:#a15c00;--bad:#b42318}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--card:#1f1f22;--fg:#ececee;--muted:#a0a0a8;--line:#333338;--accent:#7aa2ff;--ok:#5fc27e;--warn:#e0a24a;--bad:#ff7a6e}}
*{box-sizing:border-box}body{margin:0;font:14px/1.45 system-ui,Segoe UI,sans-serif;background:var(--bg);color:var(--fg)}
header{display:flex;gap:16px;align-items:center;padding:12px 20px;border-bottom:1px solid var(--line);background:var(--card);flex-wrap:wrap}
header b{font-size:16px}nav a{margin-right:12px;color:var(--muted);text-decoration:none}nav a.on{color:var(--accent);font-weight:600}
main{padding:16px 20px;max-width:1300px;margin:auto}a{color:var(--accent)}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line)}
th,td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}th{font-size:12px;color:var(--muted);font-weight:600}
td.num{text-align:right;font-variant-numeric:tabular-nums}.muted{color:var(--muted)}.ok{color:var(--ok)}.warn{color:var(--warn)}.bad{color:var(--bad)}
.tag{display:inline-block;padding:1px 6px;border-radius:9px;font-size:11px;border:1px solid var(--line);color:var(--muted)}
form.inline{display:inline}button{font:inherit;padding:3px 9px;border-radius:6px;border:1px solid var(--line);background:var(--card);color:var(--fg);cursor:pointer}
button.primary{background:var(--accent);border-color:var(--accent);color:#fff}input,select{font:inherit;padding:4px 6px;border:1px solid var(--line);border-radius:6px;background:var(--card);color:var(--fg)}
.card{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:14px 16px;margin-bottom:12px}
pre{white-space:pre-wrap;font:13px/1.5 system-ui,sans-serif;margin:0}.filters{margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.flash{padding:8px 12px;border-radius:6px;background:var(--card);border:1px solid var(--line);margin-bottom:12px}
"""


def _e(x) -> str:
    return html.escape("" if x is None else str(x))


def _ago(dt: datetime | None) -> str:
    if not dt:
        return ""
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    days = (datetime.now(timezone.utc) - dt).days
    return "today" if days <= 0 else f"{days}d ago"


class App:
    def __init__(self, profile: Profile, tracker: Tracker):
        self.profile = profile
        self.tracker = tracker
        self.files_root = Path(profile.output_dir).resolve()

    # ------------------------------------------------------------------ pages
    def page(self, title: str, body: str, view: str = "", flash: str = "") -> str:
        nav = "".join(f'<a href="/?view={k}" class="{"on" if k == view else ""}">{v}</a>' for k, v in VIEWS.items())
        nav += f'<a href="/applications" class="{"on" if view == "apps" else ""}">Applications</a>'
        nav += f'<a href="/runs" class="{"on" if view == "runs" else ""}">Runs</a>'
        flash_html = f'<div class="flash">{_e(flash)}</div>' if flash else ""
        return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>{_e(title)} - Job Hunter</title><style>{CSS}</style></head><body>"
                f"<header><b>Job Hunter</b><nav>{nav}</nav></header><main>{flash_html}{body}</main></body></html>")

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

    def list_page(self, params: dict, flash: str = "") -> str:
        view = params.get("view", "todo") if params.get("view") in VIEWS else "todo"
        q, job_type = params.get("q", ""), params.get("type", "")
        with self.tracker.Session() as s:
            jobs = list(s.scalars(self.jobs_query(view, q, job_type)))
        types = "".join(f'<option value="{t}" {"selected" if t == job_type else ""}>{t.replace("_", "-")}</option>'
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
            kind = f' <span class="tag">{_e(j.job_type.replace("_", "-"))}</span>' if j.job_type and j.job_type != "full_time" else ""
            kit = ' <span class="tag">kit ready</span>' if j.kit_resume_path else ""
            actions = self.decision_buttons(j, view)
            rows.append(
                f"<tr><td class='num muted'>{i}</td><td class='num'><b>{j.overall_score:.0f}</b></td>"
                f"<td><a href='/job/{j.id}'>{_e(j.title)}</a>{star}{kind}{kit}{warn}</td><td>{_e(j.company)}</td>"
                f"<td class='muted'>{_e(j.location)}</td><td class='muted'>{_ago(j.date_posted)}</td>"
                f"<td class='num'>{sponsor}</td><td class='num muted'>{j.match_score:.0f}</td><td>{actions}</td></tr>")
        table = ("<table><tr><th>#</th><th>Score</th><th>Job</th><th>Company</th><th>Location</th><th>Posted</th>"
                 "<th>H-1B</th><th>Fit</th><th></th></tr>" + "".join(rows) + "</table>") if rows else "<p class='muted'>Nothing here.</p>"
        return self.page(VIEWS[view], filters + table, view, flash)

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

    def job_page(self, job_id: int, flash: str = "") -> str:
        with self.tracker.Session() as s:
            j = s.get(Job, job_id)
            if j is None:
                return self.page("Not found", "<p>No such job.</p>")
            apps = list(s.scalars(select(Application).where(Application.job_id == job_id)))
        files = ""
        for label, path in (("Tailored resume", j.kit_resume_path), ("Cover note", j.kit_cover_path)):
            if path:
                files += f"<div>{label}: <a href='/file?path={quote(path)}' target='_blank'>{_e(Path(path).name)}</a></div>"
        warn = (f"<p class='warn'>&#9888; This posting may not sponsor visas: <i>{_e(j.sponsorship_warning)}</i></p>"
                if j.sponsorship_warning else "")
        if apps:
            a = apps[0]
            state = f"<p class='ok'>Applied {a.applied_date:%Y-%m-%d} &middot; status: <b>{_e(a.status)}</b></p>"
            apply_form = ""
        else:
            state = ""
            apply_form = (f"<form class='inline' method='post' action='/applied/{j.id}'><button class='primary'>Mark applied</button></form> "
                          "<span class='muted'>records the kit below as what you sent</span>")
        kit_form = (f"<form class='inline' method='post' action='/kit/{j.id}'><button>{'Rebuild' if j.kit_resume_path else 'Prepare'} kit</button></form> "
                    "<span class='muted'>tailored resume + cover note, about a minute</span>")
        body = (
            f"<div class='card'><h2 style='margin:0 0 4px'>{_e(j.title)}</h2>"
            f"<div><b>{_e(j.company)}</b> &middot; {_e(j.location)} &middot; {_e((j.job_type or '').replace('_', '-'))} "
            f"&middot; posted {_ago(j.date_posted)} &middot; source {_e(j.source)}</div>"
            f"<p>Score <b>{j.overall_score:.0f}</b> (fit {j.match_score:.0f}, H-1B {j.sponsor_score:.0f})<br>"
            f"<span class='muted'>H-1B: {_e(j.sponsor_note or 'no data')}</span></p>{warn}{state}"
            f"<p><a href='{_e(j.job_url)}' target='_blank' rel='noopener'><button class='primary' type='button'>Open posting</button></a> "
            f"{self.decision_buttons(j, 'todo')}</p>"
            f"<p>{kit_form}</p>{files}<p>{apply_form}</p></div>"
            f"<div class='card'><pre>{_e(j.description or 'No description from this source.')}</pre></div>")
        return self.page(j.title, body, "", flash)

    def applications_page(self, flash: str = "") -> str:
        rows = []
        for a, j in self.tracker.applications():
            opts = "".join(f"<option {'selected' if s == a.status else ''}>{s}</option>" for s in STATUSES)
            resume = f"<a href='/file?path={quote(a.resume_path)}' target='_blank'>resume</a>" if a.resume_path else "<span class='muted'>no resume recorded</span>"
            cover = f" &middot; <a href='/file?path={quote(a.cover_note_path)}' target='_blank'>cover note</a>" if a.cover_note_path else ""
            rows.append(
                f"<tr><td>{a.applied_date:%Y-%m-%d}</td><td><a href='/job/{j.id}'>{_e(j.title)}</a></td><td>{_e(j.company)}</td>"
                f"<td>{resume}{cover}</td><td><form class='inline' method='post' action='/status/{a.id}'>"
                f"<select name='status'>{opts}</select> <button>Save</button></form></td></tr>")
        body = ("<table><tr><th>Applied</th><th>Job</th><th>Company</th><th>What you sent</th><th>Status</th></tr>"
                + "".join(rows) + "</table>") if rows else "<p class='muted'>No applications yet.</p>"
        return self.page("Applications", body, "apps", flash)

    def runs_page(self) -> str:
        rows = []
        for r in self.tracker.recent_runs(30):
            started = r.started if r.started.tzinfo else r.started.replace(tzinfo=timezone.utc)
            problems = "<br>".join(_e(p) for p in (r.problems or "").splitlines()) or "<span class='muted'>none</span>"
            rows.append(f"<tr><td>{started.astimezone():%Y-%m-%d %H:%M}</td><td class='num'>{r.fetched}</td><td class='num'>{r.new}</td>"
                        f"<td class='num'>{r.seen_again}</td><td class='num'>{r.closed}</td><td>{problems}</td></tr>")
        body = ("<table><tr><th>Started</th><th>Fetched</th><th>New</th><th>Seen again</th><th>Closed</th><th>Problems</th></tr>"
                + "".join(rows) + "</table>") if rows else "<p class='muted'>No runs yet.</p>"
        return self.page("Runs", body, "runs")

    # ------------------------------------------------------------------ actions
    def prepare_kit(self, job_id: int) -> str:
        from jobhunter.pipeline import prepare_kit

        if not _kit_lock.acquire(blocking=False):
            return "Another kit is being prepared; try again in a minute."
        try:
            resume, cover, warnings = prepare_kit(self.profile, self.tracker, job_id)
        except Exception as e:  # noqa: BLE001 - show the reason on the page
            return f"Kit failed: {e}"
        finally:
            _kit_lock.release()
        if resume and cover:
            check = (" Check: " + "; ".join(warnings)) if warnings else ""
            return "Kit ready. Review the resume and cover note before sending." + check
        return "Kit partly failed: " + ("no resume. " if not resume else "") + ("no cover note." if not cover else "")

    def file_path(self, raw: str) -> Path | None:
        """Only files inside the tailored-resume folder can be opened."""
        try:
            path = Path(raw).resolve()
        except (OSError, ValueError):
            return None
        return path if path.is_file() and self.files_root in path.parents else None


def make_handler(app: App, port: int):
    allowed_origins = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # quiet console
            pass

        def send_html(self, text: str, status: int = 200):
            data = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def redirect(self, to: str):
            self.send_response(303)
            self.send_header("Location", to)
            self.end_headers()

        def do_GET(self):
            url = urlparse(self.path)
            params = {k: v[0] for k, v in parse_qs(url.query).items()}
            parts = url.path.strip("/").split("/")
            if url.path == "/":
                return self.send_html(app.list_page(params, params.get("msg", "")))
            if parts[0] == "job" and len(parts) == 2 and parts[1].isdigit():
                return self.send_html(app.job_page(int(parts[1]), params.get("msg", "")))
            if url.path == "/applications":
                return self.send_html(app.applications_page(params.get("msg", "")))
            if url.path == "/runs":
                return self.send_html(app.runs_page())
            if url.path == "/file":
                path = app.file_path(params.get("path", ""))
                if path is None:
                    return self.send_html("Not found", 404)
                data = path.read_bytes()
                kind = "application/pdf" if path.suffix.lower() == ".pdf" else "text/plain; charset=utf-8"
                self.send_response(200)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                return self.wfile.write(data)
            self.send_html("Not found", 404)

        def do_POST(self):
            origin = self.headers.get("Origin")
            if origin and origin not in allowed_origins:  # block other websites posting to this page
                return self.send_html("Forbidden", 403)
            length = int(self.headers.get("Content-Length") or 0)
            form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
            parts = urlparse(self.path).path.strip("/").split("/")
            if len(parts) != 2 or not parts[1].isdigit():
                return self.send_html("Not found", 404)
            action, item = parts[0], int(parts[1])
            try:
                if action == "decide":
                    app.tracker.set_decision(item, form.get("decision", "new"))
                    back = form.get("back", "/")
                    return self.redirect(back if back.startswith("/") else "/")
                if action == "kit":
                    msg = app.prepare_kit(item)
                    return self.redirect(f"/job/{item}?msg={quote(msg)}")
                if action == "applied":
                    app.tracker.log_application(item)
                    return self.redirect(f"/job/{item}?msg={quote('Application recorded.')}")
                if action == "status":
                    app.tracker.update_status(item, form.get("status", "applied"))
                    return self.redirect("/applications?msg=" + quote("Status saved."))
            except ValueError as e:
                return self.send_html(app.page("Error", f"<p class='bad'>{_e(e)}</p>"), 400)
            self.send_html("Not found", 404)

    return Handler


def serve(profile: Profile, tracker: Tracker, port: int = 8765, open_browser: bool = True) -> None:
    app = App(profile, tracker)
    server = ThreadingHTTPServer(("127.0.0.1", port), make_handler(app, port))
    url = f"http://127.0.0.1:{port}/"
    print(f"Review page: {url}  (Ctrl+C to stop)")
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
