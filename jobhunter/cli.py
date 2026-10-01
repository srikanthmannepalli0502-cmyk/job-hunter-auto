"""Command-line interface.

    python -m jobhunter search                 fetch, score and store new jobs
    python -m jobhunter list                   show top unapplied jobs
    python -m jobhunter tailor JOB_ID          tailor your resume to one job
    python -m jobhunter open JOB_ID            open the posting, then log that you applied
    python -m jobhunter status APP_ID STATUS   update an application (interview, offer, ...)
    python -m jobhunter applications           show your application history
"""

import argparse
import sys
import webbrowser
from pathlib import Path

from loguru import logger

from jobhunter.config import DEFAULT_PROFILE_PATH, load_profile, settings
from jobhunter.scoring import rank_jobs
from jobhunter.sources import AdzunaSource, RemotiveSource, dedupe
from jobhunter.sources.base import is_excluded
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
        print(f"[{job.id:>4}] {job.match_score:5.1f}  {job.title} @ {job.company}")
        details = " | ".join(x for x in (job.location, job.source, _salary(job)) if x)
        print(f"        {details}")


def cmd_search(args, profile, tracker) -> None:
    jobs = []
    for source in (AdzunaSource(), RemotiveSource()):
        try:
            jobs.extend(source.fetch(profile.search))
        except Exception as e:  # one failing source shouldn't stop the run
            logger.error(f"{source.name} failed: {e}")

    jobs = [j for j in dedupe(jobs) if not is_excluded(j, profile.search.exclude_keywords)]
    ranked = rank_jobs(jobs, profile.scoring)
    relevant = [j for j in ranked if j.match_score >= profile.search.min_score]
    new = tracker.save_new(relevant)
    print(f"\nFetched {len(jobs)} jobs, {len(relevant)} scored >= {profile.search.min_score}, {len(new)} new.\n")
    print_jobs(sorted(new, key=lambda j: j.match_score, reverse=True)[: args.top])


def cmd_list(args, profile, tracker) -> None:
    print_jobs(tracker.top_jobs(limit=args.top))


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


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="jobhunter", description="Find, score and tailor for jobs.")
    p.add_argument("--profile", type=Path, default=DEFAULT_PROFILE_PATH, help="path to profile.toml")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("search", help="fetch, score and store new jobs")
    s.add_argument("--top", type=int, default=15)
    s.set_defaults(func=cmd_search)

    s = sub.add_parser("list", help="show top unapplied jobs")
    s.add_argument("--top", type=int, default=15)
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
    return p


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    profile = load_profile(args.profile)
    tracker = Tracker(settings.database_url)
    args.func(args, profile, tracker)
