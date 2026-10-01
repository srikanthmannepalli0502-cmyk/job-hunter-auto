import pytest

from jobhunter.config import ScoringConfig, SearchConfig
from jobhunter.models import JobListing


@pytest.fixture
def scoring():
    return ScoringConfig(
        keywords={"sql": 5, "python": 5, "tableau": 4, "snowflake": 4},
        preferred_titles=["data analyst", "data engineer"],
        locations={"remote": 8, "new york": 6},
    )


@pytest.fixture
def search():
    return SearchConfig(query="data analyst", location="New York, NY", max_results=10)


def make_job(title="Data Analyst", company="Acme", location="New York, NY",
             description="SQL and Python", **kw) -> JobListing:
    return JobListing(title=title, company=company, location=location,
                      job_url=f"https://example.com/{title}-{company}", source="test",
                      description=description, **kw)
