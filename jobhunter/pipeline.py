"""The steps shared by the command line, the scheduled runs and the review page.

run_search:  gather from every source -> filter -> score (fit + H-1B) -> store, refresh, close stale
prepare_kit: tailored one-page resume + cover note for one job, remembered on the job
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from jobhunter.config import Profile
from jobhunter.scoring import rank_jobs
from jobhunter.sources import AdzunaSource, RemotiveSource, dedupe
from jobhunter.sources.base import is_excluded
from jobhunter.sources.boards import CompanyBoardsSource, load_boards
from jobhunter.sponsors import SponsorDB, apply_sponsorship
from jobhunter.tracker import Job, Tracker

CLOSE_AFTER_DAYS = 3


@dataclass
class RunSummary:
    fetched: int = 0
    new: list[Job] = field(default_factory=list)
    seen_again: int = 0
    closed: int = 0
    problems: list[str] = field(default_factory=list)

    def line(self) -> str:
        return (f"fetched {self.fetched}, new {len(self.new)}, seen again {self.seen_again}, "
                f"closed {self.closed}, problems {len(self.problems)}")


def run_search(profile: Profile, tracker: Tracker) -> RunSummary:
    """One full run, recorded in the runs table."""
    run = tracker.start_run()
    summary = RunSummary()
    jobs = []
    sources = [AdzunaSource(), RemotiveSource()]
    boards = load_boards(profile.boards.file) if profile.boards.enabled else []
    if boards:
        sources.append(CompanyBoardsSource(boards, profile.scoring.preferred_titles))
    for source in sources:
        try:
            jobs.extend(source.fetch(profile.search))
        except Exception as e:  # one failing source shouldn't stop the run
            summary.problems.append(f"{source.name}: {str(e)[:150]}")
            logger.error(f"{source.name} failed: {e}")
        summary.problems.extend(getattr(source, "failures", []))

    jobs = [j for j in dedupe(jobs) if not is_excluded(j, profile.search.exclude_keywords)
            and j.job_type in profile.search.job_types]
    summary.fetched = len(jobs)
    ranked = rank_jobs(jobs, profile.scoring)
    relevant = [j for j in ranked if j.match_score >= profile.search.min_score]
    relevant = apply_sponsorship(relevant, SponsorDB.load(profile.sponsorship.data_file), profile.sponsorship)
    summary.new, summary.seen_again = tracker.save_seen(relevant)
    # An offline run didn't really check the sites, so it must not close anything.
    if not any(getattr(s, "offline", False) for s in sources):
        summary.closed = tracker.close_stale(CLOSE_AFTER_DAYS)
    tracker.finish_run(run, fetched=summary.fetched, new=len(summary.new), seen_again=summary.seen_again,
                       closed=summary.closed, problems="\n".join(summary.problems) or None)
    logger.info(f"Run {run.id}: {summary.line()}")
    return summary


def _slug(*parts: str) -> str:
    cleaned = [re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_")[:30] for p in parts]
    return "_".join(cleaned + [datetime.now().strftime("%Y%m%d_%H%M")])


def prepare_kit(profile: Profile, tracker: Tracker, job_id: int, jd_text: str = "",
                llm=None) -> tuple[str, str, list[str]]:
    """Tailored resume PDF + cover note for one job. Returns (resume_path, cover_path, warnings);
    '' for a part that failed. Both paths are saved on the job and the job is shortlisted."""
    from jobhunter.llm import LLMClient
    from jobhunter.resume.cover import write_cover_note
    from jobhunter.resume.parser import parse_resume
    from jobhunter.resume.tailor import ResumeTailor

    job = tracker.get_job(job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    base = parse_resume(profile.base_resume)
    if not base:
        raise RuntimeError(f"Could not read your base resume at {profile.base_resume}")

    llm = llm or LLMClient()
    description = jd_text or job.description or ""
    posting = f"{job.title} - {job.company}\n{job.location}\n\n{description}"
    tailor = ResumeTailor(base, llm, profile.must_keep, profile.linkedin_url, profile.output_dir)
    resume_path = tailor.tailor(posting, job.company, job.title) or ""

    cover_path = ""
    try:
        cover_path = write_cover_note(llm, base, job.title, job.company, description, profile.name,
                                      Path(profile.output_dir) / f"{_slug(job.company, job.title)}_cover.txt")
    except Exception as e:  # the resume is still useful without a note
        logger.error(f"Cover note failed: {e}")

    tracker.set_kit(job_id, resume_path, cover_path)
    return resume_path, cover_path, tailor.warnings
