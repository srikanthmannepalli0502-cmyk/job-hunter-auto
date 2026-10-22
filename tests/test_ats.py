from jobhunter.resume import ats
from jobhunter.resume.pdf import short_last_lines, split_link

BASE = """JANE DOE
Town | +1-555-123-4567 | jane@example.com

SUMMARY
Analyst.

EDUCATION
State University  May 2026

SKILLS
- Tools: SQL, Python, Tableau, Excel

EXPERIENCE
Bank Co | Analyst  Jan 2020 - Dec 2023
- Built Tableau dashboards and validated data integrity for 3 teams.

PROJECTS
Dash | Python | https://github.com/jane/dash
- Wrote Python to reconcile ledgers.
"""

JD = "Data Analyst, Credit Risk. Requires SQL, Python, Tableau, Power BI and data quality skills; dashboards for stakeholders."


def test_score_separates_fixable_gaps_from_real_ones():
    tailored = (BASE.replace("- Tools: SQL, Python, Tableau, Excel", "- Tools: SQL, Excel")
                .replace("Built Tableau dashboards", "Built dashboards").replace("Wrote Python to", "Wrote code to")
                .replace("Dash | Python |", "Dash | SQL |"))
    report = ats.score(tailored, JD, BASE)
    assert "SQL" in report.matched and "dashboards" in report.matched and "data quality" in report.matched
    assert set(report.add_these) == {"Python", "Tableau"}  # in the master resume: put them back
    assert "Power BI" in report.gaps and "credit risk" in report.gaps  # never claim these
    assert report.format_problems == []
    assert 0 < report.score < 100


def test_unsupported_claims_allow_the_target_role_in_the_summary_only():
    ok = BASE.replace("Analyst.\n", "Analyst targeting credit risk roles.\n")
    assert ats.unsupported_claims(ok, BASE) == []
    bad = ok.replace("validated data integrity", "managed credit risk")
    assert ats.unsupported_claims(bad, BASE) == ["credit risk"]


def test_word_boundaries():
    assert "R" not in ats.terms_in("Rust and R&D")
    assert "R" in ats.terms_in("Python, R, SQL")
    assert "Excel" not in ats.terms_in("excellent communication")


def test_format_problems():
    assert "no email address" in ats.format_problems("SUMMARY\nno contact")


def test_project_link_and_short_lines():
    assert split_link("Dash | Python | https://github.com/jane/dash") == ("Dash | Python", "https://github.com/jane/dash")
    assert split_link("Dash | Python") == ("Dash | Python", "")
    long_bullet = "- " + "word " * 23 + "end."
    assert short_last_lines(long_bullet)  # spills two words onto a new line
    assert short_last_lines("- short and sweet.") == []


def test_job_keywords_are_scored_as_exact_phrases():
    jd = "Data Analyst: SQL, data-quality checks, regulatory reporting and Basel III."
    base = "Built SQL checks for data quality and regulatory reports."
    report = ats.score("SQL only", jd, base, keywords=["regulatory reporting", "Basel III", "invented term"])
    assert "regulatory reporting" in report.add_these  # in the master resume ("regulatory reports")
    assert "Basel III" in report.gaps  # in the posting, not in your record
    assert "invented term" not in report.add_these + report.gaps + report.matched  # not in the posting
    assert ats.found("data quality", "improving data-quality checks")
