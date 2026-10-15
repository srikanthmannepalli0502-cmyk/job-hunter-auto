"""Command-line interface.

    python -m jobhunter search                 fetch, score and store new jobs
    python -m jobhunter list                   show top unapplied jobs
    python -m jobhunter tailor JOB_ID          tailor your resume to one job
    python -m jobhunter open JOB_ID            open the posting, then log that you applied
    python -m jobhunter status APP_ID STATUS   update an application (interview, offer, ...)
    python -m jobhunter applications           show your application history
    python -m jobhunter sponsor "COMPANY"      look up a company's H-1B sponsorship
    python -m jobhunter discover-boards        find target sponsors' Greenhouse/Lever/Ashby boards
"""

import argparse
import sys
import webbrowser
from pathlib import Path

from loguru import logger

from jobhunter.config import DEFAULT_PROFILE_PATH, load_profile, settings
from jobhunter.scoring import rank_jobs
from jobhunter.sponsors import SponsorDB, apply_sponsorship, describe, sponsor_score
from jobhunter.sources import AdzunaSource, RemotiveSource, dedupe
from jobhunter.sources.base import USER_AGENT
from jobhunter.sources.base import is_excluded
from jobhunter.sources.boards import CompanyBoardsSource, find_board, load_boards, write_boards
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
        print(f"[{job.id:>4}] {job.overall_score:5.1f}  {job.title} @ {job.company}")
        details = " | ".join(x for x in (job.location, job.source, _salary(job)) if x)
        print(f"        {details}")
        if job.sponsor_note:
            badge = "H-1B ok " if job.sponsor_score > 0 else "NO H-1B "
            print(f"        {badge}{job.sponsor_score:5.1f}: {job.sponsor_note}  (fit {job.match_score:.0f})")


def cmd_search(args, profile, tracker) -> None:
    jobs = []
    sources = [AdzunaSource(), RemotiveSource()]
    boards = load_boards(profile.boards.file) if profile.boards.enabled else []
    if boards:
        sources.append(CompanyBoardsSource(boards, profile.scoring.preferred_titles))
    for source in sources:
        try:
            jobs.extend(source.fetch(profile.search))
        except Exception as e:  # one failing source shouldn't stop the run
            logger.error(f"{source.name} failed: {e}")

    jobs = [j for j in dedupe(jobs) if not is_excluded(j, profile.search.exclude_keywords)]
    ranked = rank_jobs(jobs, profile.scoring)
    relevant = [j for j in ranked if j.match_score >= profile.search.min_score]
    relevant = apply_sponsorship(relevant, SponsorDB.load(profile.sponsorship.data_file), profile.sponsorship)
    new = tracker.save_new(relevant)
    print(f"\nFetched {len(jobs)} jobs, {len(relevant)} scored >= {profile.search.min_score}, {len(new)} new.\n")
    print_jobs(sorted(new, key=lambda j: j.overall_score, reverse=True)[: args.top])


def cmd_list(args, profile, tracker) -> None:
    print_jobs(tracker.top_jobs(limit=args.top, sponsors_only=args.sponsors_only))


def cmd_tailor(args, profile, tracker) -> None:
    from jobhunter.llm import LLMClient
    from jobhunter.resume.parser import parse_resume
    from jobhunter.resume.tailor import ResumeTailor

    job = tracker.get_job(args.job_id)
    if job is None:
        sys.exit(f"No job with id {args.job_id}")

    if args.jd_file:
        description = Path(args.jd_file).read_text(encoding="utf-8")
    else:
        description = job.description or ""
        if len(description) < 800:
            print("Note: this source only provides a short description. For a better result,")
            print("paste the full posting into a text file and pass --jd-file FILE.\n")

    base_text = parse_resume(profile.base_resume)
    if not base_text:
        sys.exit(f"Could not read your base resume at {profile.base_resume}")

    tailor = ResumeTailor(base_text, LLMClient(), profile.must_keep,
                          profile.linkedin_url, profile.output_dir)
    path = tailor.tailor(f"{job.title} - {job.company}\n{job.location}\n\n{description}",
                         job.company, job.title)
    if not path:
        sys.exit("Could not produce a one-page resume. Try again or shorten your base resume.")
    print(f"\nTailored resume: {Path(path).resolve()}")
    print("Review it before sending - the LLM can make mistakes.")


def cmd_open(args, profile, tracker) -> None:
    job = tracker.get_job(args.job_id)
    if job is None:
        sys.exit(f"No job with id {args.job_id}")
    print(f"Opening: {job.title} @ {job.company}\n{job.job_url}")
    webbrowser.open(job.job_url)

    answer = input("\nDid you submit an application? [y/N] ").strip().lower()
    if answer == "y":
        app = tracker.log_application(job.id, resume_path=args.resume or "")
        print(f"Logged application #{app.id}.")


def cmd_status(args, profile, tracker) -> None:
    app = tracker.update_status(args.app_id, args.status, args.notes or "")
    print(f"Application #{app.id} is now '{app.status}'.")


def cmd_applications(args, profile, tracker) -> None:
    rows = tracker.applications()
    if not rows:
        print("No applications logged yet.")
    for app, job in rows:
        print(f"#{app.id:<4} {app.status:<10} {app.applied_date:%Y-%m-%d}  {job.title} @ {job.company}")


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
    roles = profile.sponsorship.roles
    targets = sorted(db.by_key.values(), key=lambda s: s.filings_in(roles), reverse=True)
    targets = [s for s in targets if s.filings_in(roles) >= args.min_filings][: args.top]
    print(f"Checking {len(targets)} sponsors with {args.min_filings}+ H-1B filings in your roles...")

    rows = []
    with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        for i, s in enumerate(targets, 1):
            found = find_board(client, s.key)
            if found:
                ats, slug, name = found
                rows.append({"employer_key": s.key, "employer": s.employer, "ats": ats, "slug": slug,
                             "board_name": name})
                print(f"  [{i}/{len(targets)}] {s.employer}: {ats}/{slug}")
    write_boards(profile.boards.file, rows)
    print(f"\nFound {len(rows)} boards; wrote {profile.boards.file}. Review it: remove any row that "
          f"points at a different company with a similar name.")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobhunter", description="Find, score and tailor for jobs.")
    p.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH, help="path to profile.toml")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="fetch, score and store new jobs")
    s.add_argument("--top", type=int, default=15)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("list", help="show top unapplied jobs")
    s.add_argument("--top", type=int, default=15)
    s.add_argument("--sponsors-only", action="store_true", help="hide companies with no H-1B filings")
    s.set_defaults(func=cmd_list)

    s = sub.add_parser("tailor", help="tailor your resume to a job")
    s.add_argument("job_id", type=int)
    s.add_argument("--jd-file", help="text file with the full job description")
    s.set_defaults(func=cmd_tailor)

    s = sub.add_parser("open", help="open a posting in your browser and log the application")
    s.add_argument("job_id", type=int)
    s.add_argument("--resume", help="path of the resume you sent")
    s.set_defaults(func=cmd_open)

    s = sub.add_parser("status", help="update an application's status")
    s.add_argument("app_id", type=int)
    s.add_argument("status", choices=STATUSES)
    s.add_argument("--notes")
    s.set_defaults(func=cmd_status)

    s = sub.add_parser("applications", help="show your application history")
    s.set_defaults(func=cmd_applications)

    s = sub.add_parser("discover-boards", help="find target sponsors' Greenhouse/Lever/Ashby boards")
    s.add_argument("--top", type=int, default=300, help="how many top sponsors to check")
    s.add_argument("--min-filings", type=int, default=10, help="minimum H-1B filings in your roles")
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
