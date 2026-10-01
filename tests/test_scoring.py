from jobhunter.scoring import rank_jobs, score_job
from jobhunter.sources.base import dedupe, is_excluded
from tests.conftest import make_job


def test_strong_match_score_breakdown(scoring):
    job = make_job(description="sql python tableau snowflake")
    # keywords 50 (capped) + title 30 + location 12
    assert score_job(job, scoring) == 92.0


def test_title_order_matters(scoring):
    analyst = score_job(make_job(title="Data Analyst", description=""), scoring)
    engineer = score_job(make_job(title="Data Engineer", description=""), scoring)
    assert analyst - engineer == 3


def test_unrelated_job_scores_zero(scoring):
    job = make_job(title="Chef", location="Paris", description="cooking")
    assert score_job(job, scoring) == 0


def test_empty_keywords_do_not_divide_by_zero(scoring):
    scoring.keywords = {}
    assert score_job(make_job(), scoring) >= 0


def test_rank_sorts_best_first(scoring):
    jobs = [make_job(title="Chef", description=""), make_job(description="sql python")]
    ranked = rank_jobs(jobs, scoring)
    assert ranked[0].title == "Data Analyst"
    assert ranked[0].match_score > ranked[1].match_score


def test_dedupe_ignores_case_and_whitespace():
    jobs = [make_job(company="Acme"), make_job(company=" acme "), make_job(company="Other")]
    assert [j.company for j in dedupe(jobs)] == ["Acme", "Other"]


def test_exclude_keywords_match_title_only():
    assert is_excluded(make_job(title="Senior Data Analyst"), ["senior"])
    assert not is_excluded(make_job(description="work with senior leaders"), ["senior"])
