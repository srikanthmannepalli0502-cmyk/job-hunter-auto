"""Command-line interface.

    python -m jobhunter run                    gather, rank and store jobs (what the schedule runs)
    python -m jobhunter review                 open the review page in your browser
    python -m jobhunter list                   show top open jobs you haven't decided on or applied to
    python -m jobhunter shortlist JOB_ID       mark a job to apply to (skip JOB_ID hides it)
    python -m jobhunter kit JOB_ID             tailored resume + cover note for one job
    python -m jobhunter open JOB_ID            open the posting, then log that you applied
    python -m jobhunter applied JOB_ID         log an application (records the kit you sent)
    python -m jobhunter status APP_ID STATUS   update an application (interview, offer, ...)
    python -m jobhunter applications           show your application history
    python -m jobhunter runs                   show recent runs and any source problems
    python -m jobhunter sponsor "COMPANY"      look up a company's H-1B sponsorship
    python -m jobhunter discover-boards        find target sponsors' Greenhouse/Lever/Ashby boards
"""

import argparse
import os
import sys
import webbrowser
from datetime import datetime
from pathlib import Path

from loguru import logger

from jobhunter.config import DEFAULT_PROFILE_PATH, load_profile, settings
from jobhunter.pipeline import gather, prepare_kit, push, run_search
from jobhunter.sources.base import USER_AGENT
from jobhunter.sources.boards import find_board, suggest_board, write_boards
from jobhunter.sponsors import SponsorDB, describe, sponsor_score
from jobhunter.tracker import STATUSES, Tracker


def _salary(job) -> str:
    if job.salary_min and job.salary_max:
        return f"${job.salary_min:,.0f}-${job.salary_max:,.0f}"
    return ""


def print_jobs(jobs) -> None:
    if not jobs:
        print("No jobs to show.")
        return
    for job in jobs:
        mark = "*" if job.decision == "shortlisted" else " "
        print(f"[{job.id:>4}]{mark}{job.overall_score:5.1f}  {job.title} @ {job.company}")
        kind = (job.job_type or "").replace("_", "-") if job.job_type not in (None, "full_time") else ""
        details = " | ".join(x for x in (kind.upper(), job.location, job.source, _salary(job)) if x)
        print(f"        {details}")
        if job.sponsor_note:
            badge = "H-1B ok " if job.sponsor_score > 0 else "NO H-1B "
            print(f"        {badge}{job.sponsor_score:5.1f}: {job.sponsor_note}  (fit {job.match_score:.0f})")
        if job.sponsorship_warning:
            print(f"        WARNING posting may not sponsor: {job.sponsorship_warning}")


def cmd_run(args, profile, tracker) -> None:
    if args.log:
        log_dir = Path("logs")
        log_dir.mkdir(exist_ok=True)
        logger.add(log_dir / f"run_{datetime.now():%Y-%m-%d}.log", level="INFO", encoding="utf-8")
    if args.push:
        # The hosted app owns the database: gather here, let it store.
        token = os.environ.get("JOBHUNTER_API_TOKEN", "")
        if not token:
            sys.exit("JOBHUNTER_API_TOKEN is not set")
        gathered = gather(profile)
        result = push(gathered, args.push, token)
        print(f"\nSent {len(gathered.jobs)} jobs: {result.get('summary', result)}")
        for p in gathered.problems:
            print(f"  problem: {p}")
        return
    summary = run_search(profile, tracker)
    print(f"\n{summary.line()}\n")
    print_jobs(sorted(summary.new, key=lambda j: j.overall_score, reverse=True)[: args.top])
    for p in summary.problems:
        print(f"  problem: {p}")


def cmd_list(args, profile, tracker) -> None:
    decisions = ("shortlisted",) if args.shortlisted else ("new", "shortlisted")
    print_jobs(tracker.top_jobs(limit=args.top, sponsors_only=args.sponsors_only, decisions=decisions))


def _decide(decision):
    def cmd(args, profile, tracker) -> None:
        for job_id in args.job_ids:
            job = tracker.set_decision(job_id, decision)
            print(f"[{job.id}] {job.title} @ {job.company}: {decision}")
    return cmd


def cmd_kit(args, profile, tracker) -> None:
    jd = Path(args.jd_file).read_text(encoding="utf-8") if args.jd_file else ""
    job = tracker.get_job(args.job_id)
    if job is None:
        sys.exit(f"No job with id {args.job_id}")
    print(f"Preparing a kit for: {job.title} @ {job.company} (about a minute)...")
    resume, cover, warnings = prepare_kit(profile, tracker, args.job_id, jd)
    print(f"  resume:     {Path(resume).resolve() if resume else 'FAILED (see log)'}")
    print(f"  cover note: {Path(cover).resolve() if cover else 'FAILED (see log)'}")
    for w in warnings:
        print(f"  CHECK: {w}")
    print("Review both before sending - the LLM can make mistakes.")


def cmd_tailor(args, profile, tracker) -> None:
    """Resume only (kept for compatibility; `kit` also writes a cover note)."""
    from jobhunter.llm import LLMClient
    from jobhunter.resume.parser import parse_resume
    from jobhunter.resume.compose import ResumeComposer

    job = tracker.get_job(args.job_id)
    if job is None:
        sys.exit(f"No job with id {args.job_id}")
    description = Path(args.jd_file).read_text(encoding="utf-8") if args.jd_file else job.description or ""
    base_text = parse_resume(profile.base_resume)
    if not base_text:
        sys.exit(f"Could not read your base resume at {profile.base_resume}")
    tailor = ResumeComposer(base_text, LLMClient(), profile.must_keep, profile.linkedin_url, profile.output_dir)
    path = tailor.tailor(f"{job.title} - {job.company}\n{job.location}\n\n{description}", job.company, job.title)
    if not path:
        sys.exit("Could not produce a one-page resume. Try again or shorten your base resume.")
    tracker.set_kit(job.id, resume_path=path, notes="\n".join(tailor.warnings))
    for w in tailor.warnings:
        print(f"CHECK: {w}")
    print(f"\nTailored resume: {Path(path).resolve()}\nReview it before sending - the LLM can make mistakes.")


def cmd_open(args, profile, tracker) -> None:
    job = tracker.get_job(args.job_id)
    if job is None:
        sys.exit(f"No job with id {args.job_id}")
    print(f"Opening: {job.title} @ {job.company}\n{job.job_url}")
    webbrowser.open(job.job_url)
    if job.kit_resume_path:
        print(f"Your kit: {job.kit_resume_path}" + (f"\n          {job.kit_cover_path}" if job.kit_cover_path else ""))
    answer = input("\nDid you submit an application? [y/N] ").strip().lower()
    if answer == "y":
        app = tracker.log_application(job.id, resume_path=args.resume or "")
        print(f"Logged application #{app.id} (resume: {app.resume_path or 'not recorded'}).")


def cmd_applied(args, profile, tracker) -> None:
    app = tracker.log_application(args.job_id, resume_path=args.resume or "", notes=args.notes or "")
    print(f"Logged application #{app.id} (resume: {app.resume_path or 'not recorded'}, "
          f"cover note: {app.cover_note_path or 'none'}).")


def cmd_status(args, profile, tracker) -> None:
    app = tracker.update_status(args.app_id, args.status, args.notes or "")
    print(f"Application #{app.id} is now '{app.status}'.")


def cmd_applications(args, profile, tracker) -> None:
    rows = tracker.applications()
    if not rows:
        print("No applications logged yet.")
    for app, job in rows:
        print(f"#{app.id:<4} {app.status:<10} {app.applied_date:%Y-%m-%d}  {job.title} @ {job.company}")
        if app.resume_path:
            print(f"      resume: {app.resume_path}")


def cmd_runs(args, profile, tracker) -> None:
    for r in tracker.recent_runs(args.top):
        took = f"{(r.finished - r.started).seconds // 60} min" if r.finished else "unfinished"
        print(f"#{r.id:<4} {r.started:%Y-%m-%d %H:%M} UTC  {took:>10}  fetched {r.fetched}, new {r.new}, "
              f"seen again {r.seen_again}, closed {r.closed}")
        for line in (r.problems or "").splitlines():
            print(f"        problem: {line}")


def cmd_review(args, profile, tracker) -> None:
    from jobhunter.web import serve

    serve(profile, tracker, host=args.host, port=args.port, open_browser=not args.no_browser)


def cmd_sponsor(args, profile, tracker) -> None:
    db = SponsorDB.load(profile.sponsorship.data_file)
    if db is None:
        sys.exit("No sponsor data; see README (H-1B sponsorship).")
    match = db.match(args.company)
    if match is None:
        print(f"{args.company}: no H-1B filings found.")
        return
    s, roles = match.sponsor, profile.sponsorship.roles
    print(f"{s.employer}  (sponsor score {sponsor_score(s, roles)}/100)")
    print(f"  {describe(match, roles)}")
    print(f"  by role group: " + ", ".join(f"{k} {v:,}" for k, v in s.role_filings.items()))
    print(f"  new H-1B hires in the latest fiscal year: {s.new_hires_latest_fy:,}; "
          f"new-hire share {s.new_hire_share:.0%}")
    if s.median_wage_data:
        print(f"  median offered wage, data roles: ${s.median_wage_data:,.0f}")


def cmd_discover_boards(args, profile, tracker) -> None:
    import httpx

    db = SponsorDB.load(profile.sponsorship.data_file)
    if db is None:
        sys.exit("No sponsor data; see README (H-1B sponsorship).")
    import csv

    roles = profile.sponsorship.roles
    targets = sorted(db.by_key.values(), key=lambda s: s.filings_in(roles), reverse=True)
    targets = [s for s in targets if s.filings_in(roles) >= args.min_filings]
    existing: list[dict] = []
    checked: set[str] = set()
    if args.add:  # grow the list: keep every board you have, skip sponsors already checked
        path = Path(profile.boards.file)
        if path.exists():
            with path.open(encoding="utf-8", newline="") as f:
                existing = list(csv.DictReader(f))
        if args.checked and Path(args.checked).exists():
            checked = set(Path(args.checked).read_text(encoding="utf-8").split("\n"))
        have = {r["employer_key"] for r in existing}
        targets = [s for s in targets if s.key not in have and s.key not in checked]
    targets = targets[: args.top]
    print(f"Checking {len(targets)} sponsors with {args.min_filings}+ H-1B filings in your roles...")

    rows = []
    llm = None
    if args.ask_llm:
        from jobhunter.llm import LLMClient

        llm = LLMClient()
    with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        for i, s in enumerate(targets, 1):
            found = find_board(client, s.key)
            if not found and llm is not None:
                try:
                    found = suggest_board(llm, client, s.employer, s.key)
                except Exception as e:  # noqa: BLE001 - one sponsor's lookup failing shouldn't stop the run
                    print(f"  [{i}/{len(targets)}] {s.employer}: lookup failed ({e})")
            if found:
                ats, slug, name = found
                rows.append({"employer_key": s.key, "employer": s.employer, "ats": ats, "slug": slug,
                             "board_name": name})
                print(f"  [{i}/{len(targets)}] {s.employer}: {ats}/{slug}")
    write_boards(profile.boards.file, existing + rows)
    if args.checked:
        done = sorted(checked | {s.key for s in targets} - {""})
        Path(args.checked).write_text("\n".join(done) + "\n", encoding="utf-8")
    print(f"\nFound {len(rows)} new boards ({len(existing) + len(rows)} in all); wrote {profile.boards.file}. "
          f"Review it: remove any row that points at a different company with a similar name.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobhunter", description="Find, rank and apply to jobs.")
    p.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH, help="path to profile.toml")
    sub = p.add_subparsers(dest="command", required=True)

    for name in ("run", "search"):
        s = sub.add_parser(name, help="gather, rank and store jobs from every source")
        s.add_argument("--top", type=int, default=15)
        s.add_argument("--log", action="store_true", help="also write logs/run_<date>.log")
        s.add_argument("--push", metavar="APP_URL", help="send results to the hosted review app instead of "
                                                         "storing them here (token: JOBHUNTER_API_TOKEN)")
        s.set_defaults(func=cmd_run)

    s = sub.add_parser("review", help="open the review page in your browser")
    s.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8765)))
    s.add_argument("--host", default="127.0.0.1", help="0.0.0.0 when hosted (e.g. Azure App Service)")
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(func=cmd_review)

    s = sub.add_parser("list", help="show top open jobs")
    s.add_argument("--top", type=int, default=15)
    s.add_argument("--sponsors-only", action="store_true", help="hide companies with no H-1B filings")
    s.add_argument("--shortlisted", action="store_true", help="only jobs you shortlisted")
    s.set_defaults(func=cmd_list)

    for name, decision in (("shortlist", "shortlisted"), ("skip", "skipped"), ("unskip", "new")):
        s = sub.add_parser(name, help=f"mark jobs as {decision}")
        s.add_argument("job_ids", type=int, nargs="+")
        s.set_defaults(func=_decide(decision))

    s = sub.add_parser("kit", help="tailored resume + cover note for a job")
    s.add_argument("job_id", type=int)
    s.add_argument("--jd-file", help="text file with the full job description")
    s.set_defaults(func=cmd_kit)

    s = sub.add_parser("tailor", help="tailored resume only")
    s.add_argument("job_id", type=int)
    s.add_argument("--jd-file", help="text file with the full job description")
    s.set_defaults(func=cmd_tailor)

    s = sub.add_parser("open", help="open a posting in your browser and log the application")
    s.add_argument("job_id", type=int)
    s.add_argument("--resume", help="path of the resume you sent (default: the job's kit)")
    s.set_defaults(func=cmd_open)

    s = sub.add_parser("applied", help="log that you applied to a job")
    s.add_argument("job_id", type=int)
    s.add_argument("--resume", help="path of the resume you sent (default: the job's kit)")
    s.add_argument("--notes")
    s.set_defaults(func=cmd_applied)

    s = sub.add_parser("status", help="update an application's status")
    s.add_argument("app_id", type=int)
    s.add_argument("status", choices=STATUSES)
    s.add_argument("--notes")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("applications", help="show your application history")
    s.set_defaults(func=cmd_applications)

    s = sub.add_parser("runs", help="show recent runs and source problems")
    s.add_argument("--top", type=int, default=10)
    s.set_defaults(func=cmd_runs)

    s = sub.add_parser("discover-boards", help="find target sponsors' Greenhouse/Lever/Ashby boards")
    s.add_argument("--top", type=int, default=300, help="how many top sponsors to check")
    s.add_argument("--min-filings", type=int, default=10, help="minimum H-1B filings in your roles")
    s.add_argument("--add", action="store_true", help="keep the boards you have and add new ones")
    s.add_argument("--checked", metavar="FILE", help="sponsors already checked (skipped, then updated)")
    s.add_argument("--ask-llm", action="store_true",
                   help="also ask the LLM for each sponsor's careers site (Workday, Oracle...); every "
                        "suggestion is checked against the live site before it's kept")
    s.set_defaults(func=cmd_discover_boards)

    s = sub.add_parser("sponsor", help="look up a company's H-1B sponsorship")
    s.add_argument("company")
    s.set_defaults(func=cmd_sponsor)
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    profile = load_profile(args.profile)
    tracker = Tracker(settings.database_url)
    args.func(args, profile, tracker)
