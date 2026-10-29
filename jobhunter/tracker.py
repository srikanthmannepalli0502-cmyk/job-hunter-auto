"""SQLite (or any SQLAlchemy URL) store for fetched jobs, your decisions, applications and runs.

A job is identified by title + company (JobListing.dedupe_key), so the same posting found
again tomorrow, or on another source, is the same row: it gets a fresh last_seen date instead
of a duplicate. Jobs not seen for a few days are marked closed.
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from jobhunter.models import JobListing

STATUSES = ("applied", "interview", "rejected", "offer", "withdrawn")
DECISIONS = ("new", "shortlisted", "skipped")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; treat them as UTC."""
    return dt.replace(tzinfo=timezone.utc) if dt is not None and dt.tzinfo is None else dt


REPOST_GAP_DAYS = 7
REPOST_ABSENT_DAYS = 10


def _reposted(row: "Job", listing: JobListing) -> bool:
    """A known job is back after being gone 10+ days (a board that failed for a day or two closes
    jobs too, so a short absence doesn't count), or carries a posting date a week or more newer than
    the one stored. Same-day listings of one role in several cities share a row and a date."""
    last = _aware(row.last_seen)
    if row.closed and last and _now() - last >= timedelta(days=REPOST_ABSENT_DAYS):
        return True
    old, new = _aware(row.date_posted), _aware(listing.date_posted)
    return bool(old and new and new - old >= timedelta(days=REPOST_GAP_DAYS))


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dedupe_key: Mapped[str] = mapped_column(String(512), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(255))
    company: Mapped[str] = mapped_column(String(255))
    location: Mapped[str | None] = mapped_column(String(255))
    job_url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(50))
    description: Mapped[str | None] = mapped_column(Text)
    salary_min: Mapped[float | None] = mapped_column(Float)
    salary_max: Mapped[float | None] = mapped_column(Float)
    match_score: Mapped[float] = mapped_column(Float, default=0.0)
    sponsor_score: Mapped[float] = mapped_column(Float, default=0.0)
    sponsor_note: Mapped[str | None] = mapped_column(String(500))
    sponsorship_warning: Mapped[str | None] = mapped_column(String(300))
    overall_score: Mapped[float] = mapped_column(Float, default=0.0)
    job_type: Mapped[str | None] = mapped_column(String(20))
    date_posted: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_fetched: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    decision: Mapped[str] = mapped_column(String(20), default="new")
    kit_resume_path: Mapped[str | None] = mapped_column(String(500))
    kit_cover_path: Mapped[str | None] = mapped_column(String(500))
    kit_notes: Mapped[str | None] = mapped_column(Text)  # ATS score and checks for the tailored resume
    kit_feedback: Mapped[str | None] = mapped_column(Text)  # your change requests, one per line, reused on regenerate
    outreach: Mapped[str | None] = mapped_column(Text)  # JSON: drafts of messages to people at the company
    # JSON from the extension's Fill to Review: {state: "review" | "needs_you", step, note, at, url}
    apply_progress: Mapped[str | None] = mapped_column(Text)
    # Shortlisted in the app: the extension opens the application once the tailored resume is ready.
    open_when_ready: Mapped[bool] = mapped_column(Boolean, default=False)
    open_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # when you shortlisted it
    # Times the posting came back after it was taken down, or was re-posted with a newer date: a role
    # that's hard to fill, or an old opening kept alive.
    reposts: Mapped[int] = mapped_column(Integer, default=0)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    status: Mapped[str] = mapped_column(String(50), default="applied")
    resume_path: Mapped[str | None] = mapped_column(String(500))
    cover_note_path: Mapped[str | None] = mapped_column(String(500))
    applied_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    notes: Mapped[str | None] = mapped_column(Text)


STAGE_KINDS = ("recruiter screen", "hiring manager", "technical", "take-home", "panel / onsite", "final", "other")
STAGE_OUTCOMES = ("pending", "passed", "rejected", "withdrew")


class InterviewStage(Base):
    """One step of an application's interviews: what it was, when, with whom, and how it went.
    The idea follows jobsync's job stages (MIT)."""

    __tablename__ = "interview_stages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    application_id: Mapped[int] = mapped_column(ForeignKey("applications.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30), default="other")
    happened: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    format: Mapped[str | None] = mapped_column(String(100))  # phone, video, onsite...
    interviewers: Mapped[str | None] = mapped_column(String(300))
    outcome: Mapped[str] = mapped_column(String(20), default="pending")
    notes: Mapped[str | None] = mapped_column(Text)  # questions asked, what to prepare next
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Run(Base):
    """One scheduled or manual search run, so you can see what each run found or failed on."""

    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    finished: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched: Mapped[int] = mapped_column(Integer, default=0)
    new: Mapped[int] = mapped_column(Integer, default=0)
    seen_again: Mapped[int] = mapped_column(Integer, default=0)
    closed: Mapped[int] = mapped_column(Integer, default=0)
    problems: Mapped[str | None] = mapped_column(Text)


class Answer(Base):
    """Your answers for application forms: profile basics (field set, e.g. 'email') and answers
    to questions you've seen before (field empty), reused by the browser extension."""

    __tablename__ = "answers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    field: Mapped[str | None] = mapped_column(String(50), index=True)
    question: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text)
    updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class ConfirmedSkill(Base):
    """A skill you confirmed you have ("I have this") that your master resume doesn't name; resumes
    may list it under SKILLS, and you'll want to be ready to talk about it."""

    __tablename__ = "confirmed_skills"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    term: Mapped[str] = mapped_column(String(100), unique=True)
    created: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Connection(Base):
    """Someone you're connected to on LinkedIn, from the Connections.csv you uploaded."""

    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    company: Mapped[str] = mapped_column(String(255))
    company_key: Mapped[str] = mapped_column(String(255), index=True)
    position: Mapped[str | None] = mapped_column(String(255))
    url: Mapped[str | None] = mapped_column(String(500))


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


# Columns added after the first release, so older databases can be upgraded in place.
_ADDED_COLUMNS = {
    "jobs": {
        "sponsor_score": "FLOAT DEFAULT 0", "sponsor_note": "VARCHAR(500)", "overall_score": "FLOAT DEFAULT 0",
        "job_type": "VARCHAR(20)", "sponsorship_warning": "VARCHAR(300)", "last_seen": "DATETIME",
        "closed": "BOOLEAN DEFAULT 0", "decision": "VARCHAR(20) DEFAULT 'new'",
        "kit_resume_path": "VARCHAR(500)", "kit_cover_path": "VARCHAR(500)", "kit_notes": "TEXT", "kit_feedback": "TEXT", "outreach": "TEXT",
        "apply_progress": "TEXT", "open_when_ready": "BOOLEAN DEFAULT 0", "open_requested_at": "DATETIME", "reposts": "INTEGER DEFAULT 0",
    },
    "applications": {"cover_note_path": "VARCHAR(500)"},
}


class Tracker:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        Base.metadata.create_all(self.engine)
        self._add_missing_columns()
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def _add_missing_columns(self) -> None:
        """create_all() doesn't alter existing tables; add columns introduced after a database was created."""
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table, columns in _ADDED_COLUMNS.items():
                existing = {c["name"] for c in insp.get_columns(table)}
                for name, ddl in columns.items():
                    if name not in existing:
                        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"))
                if table == "jobs" and "overall_score" not in existing:
                    conn.execute(text("UPDATE jobs SET overall_score = match_score"))  # rank old rows by fit
                if table == "jobs" and "last_seen" not in existing:
                    conn.execute(text("UPDATE jobs SET last_seen = date_fetched"))

    def save_new(self, jobs: list[JobListing]) -> list[Job]:
        """Store jobs not seen before and return them. Jobs already stored get a fresh last_seen
        (and are reopened if they had been marked closed); your decisions on them are kept."""
        new, _ = self.save_seen(jobs)
        return new

    def save_seen(self, jobs: list[JobListing]) -> tuple[list[Job], int]:
        """(newly stored jobs, number of known jobs seen again)."""
        now = _now()
        with self.Session() as s:
            keys = {j.dedupe_key for j in jobs}
            known = {row.dedupe_key: row for row in s.scalars(select(Job).where(Job.dedupe_key.in_(keys)))}
            new, seen_again = [], 0
            fresh: set[str] = set()
            for j in jobs:
                row = known.get(j.dedupe_key)
                if row is not None:
                    if j.dedupe_key not in fresh:
                        seen_again += 1
                        if _reposted(row, j):
                            row.reposts = (row.reposts or 0) + 1
                            row.date_posted = j.date_posted or row.date_posted
                    row.last_seen, row.closed = now, False
                    # Scores can change (new sponsor data, profile edits); keep the latest.
                    row.match_score, row.sponsor_score = j.match_score, j.sponsor_score
                    row.sponsor_note, row.sponsorship_warning = j.sponsor_note or None, j.sponsorship_warning or None
                    row.overall_score = j.overall_score or j.match_score
                    continue
                row = Job(
                    dedupe_key=j.dedupe_key, title=j.title, company=j.company, location=j.location,
                    job_url=j.job_url, source=j.source, description=j.description,
                    salary_min=j.salary_min, salary_max=j.salary_max, match_score=j.match_score,
                    sponsor_score=j.sponsor_score, sponsor_note=j.sponsor_note or None,
                    sponsorship_warning=j.sponsorship_warning or None,
                    overall_score=j.overall_score or j.match_score, job_type=j.job_type,
                    date_posted=j.date_posted, date_fetched=j.date_fetched, last_seen=now,
                )
                s.add(row)
                known[j.dedupe_key] = row
                fresh.add(j.dedupe_key)
                new.append(row)
            s.commit()
            return new, seen_again

    def close_stale(self, days: int = 3) -> int:
        """Mark jobs not seen for `days` days as closed (taken down by the company). Returns how many."""
        cutoff = _now() - timedelta(days=days)
        with self.Session() as s:
            stale = list(s.scalars(select(Job).where(~Job.closed, Job.last_seen < cutoff)))
            for job in stale:
                job.closed = True
            s.commit()
            return len(stale)

    def close_jobs(self, dedupe_keys: list[str] | None = None, job_ids: list[int] | None = None) -> int:
        """Mark these jobs closed now (their posting says the role is filled, or its page is gone)."""
        if not dedupe_keys and not job_ids:
            return 0
        with self.Session() as s:
            q = select(Job).where(~Job.closed)
            q = q.where(Job.dedupe_key.in_(dedupe_keys)) if dedupe_keys else q.where(Job.id.in_(job_ids))
            rows = list(s.scalars(q))
            for job in rows:
                job.closed = True
            s.commit()
            return len(rows)

    def open_shortlist(self, sources: tuple[str, ...]) -> list[Job]:
        """Open shortlisted jobs you haven't applied to, from these sources."""
        with self.Session() as s:
            q = select(Job).where(~Job.closed, Job.decision == "shortlisted", Job.source.in_(sources),
                                  ~Job.id.in_(select(Application.job_id)))
            return list(s.scalars(q))

    def get_job(self, job_id: int) -> Job | None:
        with self.Session() as s:
            return s.get(Job, job_id)

    def top_jobs(self, limit: int = 10, unapplied_only: bool = True, sponsors_only: bool = False,
                 include_closed: bool = False, decisions: tuple[str, ...] = ("new", "shortlisted")) -> list[Job]:
        with self.Session() as s:
            q = select(Job).order_by(Job.overall_score.desc(), Job.date_fetched.desc()).limit(limit)
            if unapplied_only:
                q = q.where(~Job.id.in_(select(Application.job_id)))
            if sponsors_only:
                q = q.where(Job.sponsor_score > 0)
            if not include_closed:
                q = q.where(~Job.closed)
            if decisions:
                q = q.where(Job.decision.in_(decisions))
            return list(s.scalars(q))

    def set_decision(self, job_id: int, decision: str) -> Job:
        if decision not in DECISIONS:
            raise ValueError(f"decision must be one of {DECISIONS}")
        with self.Session() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise ValueError(f"No job with id {job_id}")
            job.decision = decision
            s.commit()
            return job

    def add_feedback(self, job_id: int, request: str) -> Job:
        """Remember a change you asked for on this job's resume (kept for later regenerations)."""
        request = " ".join((request or "").split())[:1000]
        with self.Session() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise ValueError(f"No job with id {job_id}")
            if request:
                job.kit_feedback = "\n".join([*(job.kit_feedback or "").splitlines(), request])[-4000:]
            s.commit()
            return job

    def set_outreach(self, job_id: int, drafts: dict) -> None:
        import json

        with self.Session() as s:
            job = s.get(Job, job_id)
            if job is not None:
                job.outreach = json.dumps(drafts)
                s.commit()

    def set_progress(self, job_id: int, progress: dict | None) -> None:
        """Where Fill to Review left an application: at Review for you to submit, or stopped on a
        question you need to answer. None clears it (you submitted)."""
        import json

        with self.Session() as s:
            job = s.get(Job, job_id)
            if job is not None:
                job.apply_progress = json.dumps(progress) if progress else None
                s.commit()

    def set_open_when_ready(self, job_id: int, value: bool) -> None:
        with self.Session() as s:
            job = s.get(Job, job_id)
            if job is not None:
                job.open_when_ready = value
                if value:
                    job.open_requested_at = _now()
                s.commit()

    def ready_to_open(self) -> list[Job]:
        """Shortlisted jobs whose tailored resume is ready and whose application hasn't been opened yet,
        the one you shortlisted last first."""
        applied = select(Application.job_id)
        with self.Session() as s:
            return list(s.scalars(select(Job).where(Job.open_when_ready, Job.kit_resume_path.is_not(None),
                                                    ~Job.closed, ~Job.id.in_(applied))
                                     .order_by(Job.open_requested_at.desc().nulls_last(), Job.id.desc())))

    def clear_feedback(self, job_id: int) -> None:
        with self.Session() as s:
            job = s.get(Job, job_id)
            if job is not None:
                job.kit_feedback = None
                s.commit()

    def set_kit(self, job_id: int, resume_path: str = "", cover_path: str = "", notes: str | None = None) -> Job:
        """Remember the tailored resume and cover note prepared for a job (also shortlists it)."""
        with self.Session() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise ValueError(f"No job with id {job_id}")
            job.kit_resume_path = resume_path or job.kit_resume_path
            job.kit_cover_path = cover_path or job.kit_cover_path
            if notes is not None:
                job.kit_notes = notes
            job.decision = "shortlisted"
            s.commit()
            return job

    def log_application(self, job_id: int, resume_path: str = "", notes: str = "",
                        cover_note_path: str = "") -> Application:
        """Record that you applied. Without explicit paths, the job's prepared kit is recorded."""
        with self.Session() as s:
            job = s.get(Job, job_id)
            if job is None:
                raise ValueError(f"No job with id {job_id}")
            app = Application(job_id=job_id, resume_path=resume_path or job.kit_resume_path, notes=notes or None,
                              cover_note_path=cover_note_path or job.kit_cover_path)
            job.apply_progress = None  # submitted: no longer waiting on you
            job.open_when_ready = False
            s.add(app)
            s.commit()
            return app

    def update_status(self, app_id: int, status: str, notes: str = "") -> Application:
        if status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}")
        with self.Session() as s:
            app = s.get(Application, app_id)
            if app is None:
                raise ValueError(f"No application with id {app_id}")
            app.status = status
            app.updated_date = _now()
            if notes:
                app.notes = f"{app.notes}\n{notes}" if app.notes else notes
            s.commit()
            return app

    # ---- interview stages
    def stages(self, app_id: int) -> list[InterviewStage]:
        with self.Session() as s:
            q = select(InterviewStage).where(InterviewStage.application_id == app_id)
            return list(s.scalars(q.order_by(InterviewStage.happened.is_(None), InterviewStage.happened,
                                             InterviewStage.id)))

    def stage_counts(self) -> dict[int, int]:
        from sqlalchemy import func

        with self.Session() as s:
            q = select(InterviewStage.application_id, func.count()).group_by(InterviewStage.application_id)
            return dict(s.execute(q).all())

    def add_stage(self, app_id: int, kind: str, happened: datetime | None = None, format: str = "",
                  interviewers: str = "", outcome: str = "pending", notes: str = "") -> InterviewStage:
        """Record an interview step. An application still at "applied" moves to "interview"."""
        if kind not in STAGE_KINDS:
            raise ValueError(f"kind must be one of {STAGE_KINDS}")
        if outcome not in STAGE_OUTCOMES:
            raise ValueError(f"outcome must be one of {STAGE_OUTCOMES}")
        with self.Session() as s:
            app = s.get(Application, app_id)
            if app is None:
                raise ValueError(f"No application with id {app_id}")
            stage = InterviewStage(application_id=app_id, kind=kind, happened=happened, format=format.strip()[:100] or None,
                                   interviewers=interviewers.strip()[:300] or None, outcome=outcome,
                                   notes=notes.strip()[:5000] or None)
            s.add(stage)
            if app.status == "applied":
                app.status, app.updated_date = "interview", _now()
            s.commit()
            return stage

    def set_stage_outcome(self, stage_id: int, outcome: str) -> InterviewStage:
        if outcome not in STAGE_OUTCOMES:
            raise ValueError(f"outcome must be one of {STAGE_OUTCOMES}")
        with self.Session() as s:
            stage = s.get(InterviewStage, stage_id)
            if stage is None:
                raise ValueError(f"No interview stage with id {stage_id}")
            stage.outcome = outcome
            s.commit()
            return stage

    def get_application(self, app_id: int) -> tuple[Application, Job] | None:
        with self.Session() as s:
            app = s.get(Application, app_id)
            return (app, s.get(Job, app.job_id)) if app else None

    def applications(self) -> list[tuple[Application, Job]]:
        with self.Session() as s:
            q = select(Application, Job).join(Job, Application.job_id == Job.id)
            return [(app, job) for app, job in s.execute(q.order_by(Application.applied_date.desc()))]

    def start_run(self) -> Run:
        with self.Session() as s:
            run = Run()
            s.add(run)
            s.commit()
            return run

    def finish_run(self, run: Run, **counts) -> Run:
        with self.Session() as s:
            row = s.get(Run, run.id)
            for k, v in counts.items():
                setattr(row, k, v)
            row.finished = _now()
            s.commit()
            return row

    # ---- answers for application forms
    def answers(self) -> list[Answer]:
        with self.Session() as s:
            return list(s.scalars(select(Answer).order_by(Answer.field.is_(None), Answer.field, Answer.question)))

    def save_answer(self, question: str, answer: str, field: str = "") -> Answer:
        """Insert or update (same field, or same question text for free-form answers)."""
        question, answer, field = question.strip(), answer.strip(), (field or "").strip()
        if not question or not answer:
            raise ValueError("question and answer are required")
        with self.Session() as s:
            q = select(Answer).where(Answer.field == field) if field else \
                select(Answer).where(Answer.field.is_(None), Answer.question == question)
            row = s.scalars(q).first()
            if row is None:
                row = Answer(field=field or None, question=question, answer=answer)
                s.add(row)
            else:
                row.question, row.answer, row.updated = question, answer, _now()
            s.commit()
            return row

    def delete_answer(self, answer_id: int) -> None:
        with self.Session() as s:
            row = s.get(Answer, answer_id)
            if row is not None:
                s.delete(row)
                s.commit()

    # ---- your LinkedIn connections
    def replace_connections(self, rows: list[dict]) -> int:
        """Replace the stored connections with a fresh export. Returns how many were stored."""
        from jobhunter.connections import company_key

        with self.Session() as s:
            s.query(Connection).delete()
            s.add_all(Connection(company_key=company_key(r["company"]), **r) for r in rows)
            s.commit()
        return len(rows)

    def connection_count(self) -> int:
        from sqlalchemy import func

        with self.Session() as s:
            return s.scalar(select(func.count()).select_from(Connection)) or 0

    def connections_at(self, company: str) -> list[Connection]:
        from jobhunter.connections import company_key, same_company

        key = company_key(company)
        if not key:
            return []
        first = key.split()[0]
        with self.Session() as s:
            q = select(Connection).where(Connection.company_key.like(first + "%")).order_by(Connection.name)
            return [c for c in s.scalars(q) if same_company(c.company, company)]

    # ---- skills you confirmed
    def confirmed_skills(self) -> list[str]:
        with self.Session() as s:
            return [r.term for r in s.scalars(select(ConfirmedSkill).order_by(ConfirmedSkill.term))]

    def confirm_skill(self, term: str) -> None:
        term = " ".join((term or "").split())[:100]
        if not term:
            return
        with self.Session() as s:
            if not any(t.lower() == term.lower() for t in s.scalars(select(ConfirmedSkill.term))):
                s.add(ConfirmedSkill(term=term))
                s.commit()

    def unconfirm_skill(self, term: str) -> None:
        with self.Session() as s:
            for row in s.scalars(select(ConfirmedSkill)):
                if row.term.lower() == (term or "").strip().lower():
                    s.delete(row)
            s.commit()

    def setting(self, key: str, default: str = "") -> str:
        with self.Session() as s:
            row = s.get(Setting, key)
            return row.value if row else default

    def set_setting(self, key: str, value: str) -> None:
        with self.Session() as s:
            row = s.get(Setting, key)
            if row is None:
                s.add(Setting(key=key, value=value))
            else:
                row.value = value
            s.commit()

    def recent_runs(self, limit: int = 10) -> list[Run]:
        with self.Session() as s:
            return list(s.scalars(select(Run).order_by(Run.started.desc()).limit(limit)))
