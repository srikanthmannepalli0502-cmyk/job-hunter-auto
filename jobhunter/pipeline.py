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
from jobhunter.models import JobListing
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


@dataclass
class Gathered:
    """What one pass over the sources found, before anything is stored."""

    jobs: list[JobListing]
    fetched: int
    problems: list[str]
    offline: bool

    def to_dict(self) -> dict:
        return {"jobs": [j.to_dict() for j in self.jobs], "fetched": self.fetched,
                "problems": self.problems, "offline": self.offline}

    @classmethod
    def from_dict(cls, d: dict) -> "Gathered":
        return cls([JobListing.from_dict(j) for j in d.get("jobs", [])], int(d.get("fetched", 0)),
                   list(d.get("problems", [])), bool(d.get("offline", False)))


def gather(profile: Profile) -> Gathered:
    """Read every source, filter and score. Runs anywhere (your PC or GitHub Actions)."""
    jobs, problems = [], []
    sources = [AdzunaSource(), RemotiveSource()]
    boards = load_boards(profile.boards.file) if profile.boards.enabled else []
    if boards:
        sources.append(CompanyBoardsSource(boards, profile.scoring.preferred_titles))
    for source in sources:
        try:
            jobs.extend(source.fetch(profile.search))
        except Exception as e:  # one failing source shouldn't stop the run
            problems.append(f"{source.name}: {str(e)[:150]}")
            logger.error(f"{source.name} failed: {e}")
        problems.extend(getattr(source, "failures", []))

    jobs = [j for j in dedupe(jobs) if not is_excluded(j, profile.search.exclude_keywords)
            and j.job_type in profile.search.job_types]
    ranked = rank_jobs(jobs, profile.scoring)
    relevant = [j for j in ranked if j.match_score >= profile.search.min_score]
    relevant = apply_sponsorship(relevant, SponsorDB.load(profile.sponsorship.data_file), profile.sponsorship)
    return Gathered(relevant, len(jobs), problems, any(getattr(s, "offline", False) for s in sources))


def store(tracker: Tracker, gathered: Gathered) -> RunSummary:
    """Save a gathered result: new jobs, last_seen for known ones, close stale jobs, record the run."""
    run = tracker.start_run()
    summary = RunSummary(fetched=gathered.fetched, problems=list(gathered.problems))
    summary.new, summary.seen_again = tracker.save_seen(gathered.jobs)
    # An offline run didn't really check the sites, so it must not close anything.
    if not gathered.offline:
        summary.closed = tracker.close_stale(CLOSE_AFTER_DAYS)
    tracker.finish_run(run, fetched=summary.fetched, new=len(summary.new), seen_again=summary.seen_again,
                       closed=summary.closed, problems="\n".join(summary.problems) or None)
    logger.info(f"Run {run.id}: {summary.line()}")
    return summary


def run_search(profile: Profile, tracker: Tracker) -> RunSummary:
    """One full run on this machine: gather, then store in the local database."""
    return store(tracker, gather(profile))


def push(gathered: Gathered, url: str, token: str) -> dict:
    """Send a gathered result to the hosted review app, which stores it (the app owns the database)."""
    import httpx

    resp = httpx.post(url.rstrip("/") + "/api/ingest", json=gathered.to_dict(),
                      headers={"Authorization": f"Bearer {token}"}, timeout=120)
    resp.raise_for_status()
    return resp.json()


def _slug(*parts: str) -> str:
    cleaned = [re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_")[:30] for p in parts]
    return "_".join(cleaned + [datetime.now().strftime("%Y%m%d_%H%M")])


def make_resume(profile: Profile, tracker: Tracker, job_id: int, jd_text: str = "", llm=None) -> tuple[str, list[str]]:
    """Tailored one-page resume only. Returns (path or '', warnings); the path is saved on the job."""
    from jobhunter.llm import LLMClient
    from jobhunter.resume.parser import parse_resume
    from jobhunter.resume.tailor import ResumeTailor

    job = tracker.get_job(job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    base = parse_resume(profile.base_resume)
    if not base:
        raise RuntimeError(f"Could not read your base resume at {profile.base_resume}")
    tailor = ResumeTailor(base, llm or LLMClient(), profile.must_keep, profile.linkedin_url, profile.output_dir)
    path = tailor.tailor(f"{job.title} - {job.company}\n{job.location}\n\n{jd_text or job.description or ''}",
                         job.company, job.title) or ""
    if path:
        tracker.set_kit(job_id, resume_path=path)
    return path, tailor.warnings


def make_cover_note(profile: Profile, tracker: Tracker, job_id: int, jd_text: str = "", llm=None) -> str:
    """Cover note only. Returns its path or ''; the path is saved on the job."""
    from jobhunter.llm import LLMClient
    from jobhunter.resume.cover import write_cover_note
    from jobhunter.resume.parser import parse_resume

    job = tracker.get_job(job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    base = parse_resume(profile.base_resume)
    path = write_cover_note(llm or LLMClient(), base, job.title, job.company, jd_text or job.description or "",
                            profile.name, Path(profile.output_dir) / f"{_slug(job.company, job.title)}_cover.txt")
    if path:
        tracker.set_kit(job_id, cover_path=path)
    return path


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
