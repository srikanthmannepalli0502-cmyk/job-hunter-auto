from loguru import logger

from jobhunter.config import ScoringConfig
from jobhunter.models import JobListing


def score_job(job: JobListing, cfg: ScoringConfig) -> float:
    """Score a job from 0-100 against your profile.

    - Keyword match: 0-50 points (your skills found in the title/description)
    - Title match:   0-30 points (earlier entries in preferred_titles score higher)
    - Location:      0-20 points (preferred locations bonus)
    """
    title = job.title.lower()
    text = f"{title} {(job.description or '').lower()}"
    location = (job.location or "").lower()

    # 1. Keywords (0-50)
    max_keyword_score = sum(cfg.keywords.values()) or 1
    keyword_score = sum(w for kw, w in cfg.keywords.items() if kw.lower() in text)
    keyword_points = min(50, keyword_score / max_keyword_score * 100)

    # 2. Title (0-30)
    title_points = 0
    for i, preferred in enumerate(cfg.preferred_titles):
        if preferred.lower() in title:
            title_points = max(0, 30 - i * 3)
            break

    # 3. Location (0-20)
    location_points = 0
    for loc, points in cfg.locations.items():
        if loc.lower() in location:
            location_points = min(20, points * 2)
            break

    total = min(100, round(keyword_points + title_points + location_points, 1))
    logger.debug(
        f"{total}/100 '{job.title}': keywords {keyword_points:.1f}/50, "
        f"title {title_points}/30, location {location_points}/20"
    )
    return total


def rank_jobs(jobs: list[JobListing], cfg: ScoringConfig) -> list[JobListing]:
    """Set match_score on each job and return them best first."""
    for job in jobs:
        job.match_score = score_job(job, cfg)
    return sorted(jobs, key=lambda j: j.match_score, reverse=True)
