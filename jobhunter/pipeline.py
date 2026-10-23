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
from jobhunter.liveness import closed_phrase
from jobhunter.scoring import rank_jobs
from jobhunter.sources import AdzunaSource, RemotiveSource, dedupe
from jobhunter.sources.hackernews import HackerNewsSource
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
    sources = [AdzunaSource(), RemotiveSource(), HackerNewsSource(profile.scoring.preferred_titles)]
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
    # Still listed, but the posting itself says the role is filled.
    summary.closed += tracker.close_jobs([j.dedupe_key for j in gathered.jobs if closed_phrase(j.description or "")])
    tracker.finish_run(run, fetched=summary.fetched, new=len(summary.new), seen_again=summary.seen_again,
                       closed=summary.closed, problems="\n".join(summary.problems) or None)
    logger.info(f"Run {run.id}: {summary.line()}")
    return summary


PAGE_CHECK_SOURCES = ("hackernews", "remotive", "adzuna")


def check_shortlist(tracker: Tracker, delay: float = 1.0) -> list[tuple[Job, str, str]]:
    """Check the pages of your shortlisted jobs from sources that never say when a job is taken
    down; close the ones that are gone. Returns (job, verdict, why) for each job checked."""
    import time

    import httpx

    from jobhunter.liveness import check_page
    from jobhunter.sources.base import USER_AGENT

    out = []
    with httpx.Client(timeout=15, headers={"User-Agent": USER_AGENT}) as client:
        for job in tracker.open_shortlist(PAGE_CHECK_SOURCES):
            verdict, why = check_page(client, job.job_url or "")
            out.append((job, verdict, why))
            time.sleep(delay)
    tracker.close_jobs(job_ids=[j.id for j, verdict, _ in out if verdict == "closed"])
    return out


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
    from jobhunter.resume.compose import ResumeComposer
    from jobhunter.resume.parser import parse_resume

    job = tracker.get_job(job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    base = parse_resume(profile.base_resume)
    if not base:
        raise RuntimeError(f"Could not read your base resume at {profile.base_resume}")
    tailor = ResumeComposer(base, llm or LLMClient(), profile.must_keep, profile.linkedin_url, profile.output_dir,
                            confirmed=tracker.confirmed_skills())
    path = tailor.tailor(f"{job.title} - {job.company}\n{job.location}\n\n{jd_text or job.description or ''}",
                         job.company, job.title, feedback=job.kit_feedback or "") or ""
    if path:
        tailor.warnings += quality_notes(path)
        tracker.set_kit(job_id, resume_path=path, notes="\n".join(tailor.warnings))
    return path, tailor.warnings


def save_edited_resume(profile: Profile, tracker: Tracker, job_id: int, text: str) -> tuple[str, list[str]]:
    """Your own edit of a tailored resume's text: rebuild the PDF from it as written, with the same
    checks shown as notes (an edit may say anything; the notes point out what your master resume
    doesn't support). Returns (path or '', notes); '' when it no longer fits on one page."""
    from jobhunter.resume import ats
    from jobhunter.resume.parser import parse_resume
    from jobhunter.resume.pdf import check_resume_fit, format_to_pdf
    from jobhunter.resume.tailor import invented_words, safe_filename

    job = tracker.get_job(job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    text = text.replace("\r\n", "\n").strip()
    fit = check_resume_fit(text, profile.linkedin_url)
    if fit["status"] == "too_long":
        return "", [f"Your edit fills {fit['fill_percent']}% of the page; shorten it to fit one page."]
    base = parse_resume(profile.base_resume) or ""
    report = ats.score(text, f"{job.title}\n{job.description or ''}", base)
    notes = [report.summary() + f"; page {fit['fill_percent']}% full", "edited by you"]
    new = [w for line in text.splitlines() for w in invented_words(line, base)]
    if new:
        notes.append("words not in your master resume (fine if true): " + ", ".join(dict.fromkeys(new)))
    path = format_to_pdf(text, str(Path(profile.output_dir) / safe_filename(job.company, job.title)),
                         linkedin_url=profile.linkedin_url)
    if path:
        Path(path).with_suffix(".txt").write_text(text, encoding="utf-8")
        notes += quality_notes(path)
        tracker.set_kit(job_id, resume_path=path, notes="\n".join(notes))
    return path, notes


def company_fact(profile: Profile, company: str) -> str:
    """One sentence about the company's H-1B record, from your sponsor data ('' if none)."""
    from jobhunter.outreach import company_insight

    return company_insight(_sponsors(profile.sponsorship.data_file), company, profile.sponsorship.roles)[0]


_SPONSOR_CACHE: dict = {}


def _sponsors(path: str):
    """The sponsor data, loaded once per process (30,000 rows; job pages ask for it every view)."""
    if path not in _SPONSOR_CACHE:
        _SPONSOR_CACHE[path] = SponsorDB.load(path)
    return _SPONSOR_CACHE[path]


def make_outreach(profile: Profile, tracker: Tracker, job_id: int, llm=None) -> dict:
    """Drafts of a referral ask (alum), a recruiter note and a hiring-manager note for one job;
    saved on the job. You send them yourself."""
    from jobhunter.llm import LLMClient
    from jobhunter.outreach import draft_messages
    from jobhunter.resume.parser import parse_resume

    job = tracker.get_job(job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    resume = resume_text_for(job) or parse_resume(profile.base_resume) or ""
    # The results that match this job best: bullets with a number, ranked by the job's terms they share.
    from jobhunter.resume import ats

    wanted = ats.terms_in(f"{job.title}\n{job.description or ''}")
    bullets = [line[2:] for line in resume.splitlines() if line.startswith("- ") and re.search(r"\d", line)]
    bullets.sort(key=lambda b: len(ats.terms_in(b) & wanted), reverse=True)
    summary = next((l for l in resume.split("SUMMARY", 1)[-1].splitlines() if l.strip()), "")
    drafts = draft_messages(
        llm or LLMClient(), title=job.title, company=job.company, url=job.job_url or "",
        name=profile.name, headline=summary[:300], highlights=" | ".join(bullets[:6]),
        insight=company_fact(profile, job.company), dashboard=profile.dashboard_url,
        portfolio=profile.portfolio_url, school=profile.school or "my university", resume=resume)
    tracker.set_outreach(job_id, drafts)
    return drafts


def quality_notes(resume_path: str) -> list[str]:
    """How the finished PDF reads back and how its bullets are written (none if no text was saved)."""
    from jobhunter.resume import readback, writing

    txt = Path(resume_path).with_suffix(".txt") if resume_path else None
    if not txt or not txt.exists():
        return []
    text = txt.read_text(encoding="utf-8")
    return [readback.summary(resume_path, text), writing.summary(text)]


def resume_text_for(job) -> str:
    """The text of a job's tailored resume (saved next to its PDF), or '' for older resumes."""
    if not job or not job.kit_resume_path:
        return ""
    txt = Path(job.kit_resume_path).with_suffix(".txt")
    return txt.read_text(encoding="utf-8") if txt.exists() else ""


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
    from jobhunter.resume.compose import ResumeComposer
    from jobhunter.resume.parser import parse_resume

    job = tracker.get_job(job_id)
    if job is None:
        raise ValueError(f"No job with id {job_id}")
    base = parse_resume(profile.base_resume)
    if not base:
        raise RuntimeError(f"Could not read your base resume at {profile.base_resume}")

    llm = llm or LLMClient()
    description = jd_text or job.description or ""
    posting = f"{job.title} - {job.company}\n{job.location}\n\n{description}"
    tailor = ResumeComposer(base, llm, profile.must_keep, profile.linkedin_url, profile.output_dir,
                            confirmed=tracker.confirmed_skills())
    resume_path = tailor.tailor(posting, job.company, job.title) or ""
    tailor.warnings += quality_notes(resume_path)

    cover_path = ""
    try:
        cover_path = write_cover_note(llm, base, job.title, job.company, description, profile.name,
                                      Path(profile.output_dir) / f"{_slug(job.company, job.title)}_cover.txt")
    except Exception as e:  # the resume is still useful without a note
        logger.error(f"Cover note failed: {e}")

    tracker.set_kit(job_id, resume_path, cover_path, notes="\n".join(tailor.warnings) if resume_path else None)
    return resume_path, cover_path, tailor.warnings
