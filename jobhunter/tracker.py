"""SQLite (or any SQLAlchemy URL) store for fetched jobs and the applications you send."""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from jobhunter.models import JobListing

STATUSES = ("applied", "interview", "rejected", "offer", "withdrawn")


def _now() -> datetime:
    return datetime.now(timezone.utc)


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
    date_posted: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    date_fetched: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class Application(Base):
    __tablename__ = "applications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    job_id: Mapped[int] = mapped_column(ForeignKey("jobs.id"))
    status: Mapped[str] = mapped_column(String(50), default="applied")
    resume_path: Mapped[str | None] = mapped_column(String(500))
    applied_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    notes: Mapped[str | None] = mapped_column(Text)


class Tracker:
    def __init__(self, database_url: str):
        self.engine = create_engine(database_url)
        Base.metadata.create_all(self.engine)
        self.Session = sessionmaker(self.engine, expire_on_commit=False)

    def save_new(self, jobs: list[JobListing]) -> list[Job]:
        """Store jobs not seen before; return only the newly stored ones."""
        with self.Session() as s:
            keys = {j.dedupe_key for j in jobs}
            known = set(s.scalars(select(Job.dedupe_key).where(Job.dedupe_key.in_(keys))))
            new = []
            for j in jobs:
                if j.dedupe_key in known:
                    continue
                known.add(j.dedupe_key)
                row = Job(
                    dedupe_key=j.dedupe_key, title=j.title, company=j.company, location=j.location,
                    job_url=j.job_url, source=j.source, description=j.description,
                    salary_min=j.salary_min, salary_max=j.salary_max, match_score=j.match_score,
                    date_posted=j.date_posted, date_fetched=j.date_fetched,
                )
                s.add(row)
                new.append(row)
            s.commit()
            return new

    def get_job(self, job_id: int) -> Job | None:
        with self.Session() as s:
            return s.get(Job, job_id)

    def top_jobs(self, limit: int = 10, unapplied_only: bool = True) -> list[Job]:
        with self.Session() as s:
            q = select(Job).order_by(Job.match_score.desc(), Job.date_fetched.desc()).limit(limit)
            if unapplied_only:
                q = q.where(~Job.id.in_(select(Application.job_id)))
            return list(s.scalars(q))

    def log_application(self, job_id: int, resume_path: str = "", notes: str = "") -> Application:
        with self.Session() as s:
            if s.get(Job, job_id) is None:
                raise ValueError(f"No job with id {job_id}")
            app = Application(job_id=job_id, resume_path=resume_path or None, notes=notes or None)
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

    def applications(self) -> list[tuple[Application, Job]]:
        with self.Session() as s:
            q = select(Application, Job).join(Job, Application.job_id == Job.id)
            return [(app, job) for app, job in s.execute(q.order_by(Application.applied_date.desc()))]
