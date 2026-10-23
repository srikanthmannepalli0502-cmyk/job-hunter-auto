from jobhunter.resume import readback, writing
from jobhunter.resume.pdf import format_to_pdf

RESUME = """Sam Doe
sam.doe@example.com | +1 315 555 0100 | Syracuse, NY

SUMMARY
Data analyst who builds reliable reporting with SQL, Python and Power BI for business teams.

SKILLS
SQL, Python, Power BI, Tableau, Excel, dbt, Azure

EXPERIENCE
Acme Corp | Data Analyst  Jan 2023 - Present
- Built 12 Power BI dashboards used weekly by 40 managers across sales and finance
- Cut a monthly reporting job from 6 hours to 20 minutes by moving it to dbt on Azure
- Automated data quality checks on 3 pipelines, catching 95% of bad loads before reports refreshed

PROJECTS
H-1B Tracker | Python, dbt, DuckDB
- Modeled 1.5M labor filings into tested marts and a public dashboard

EDUCATION
Syracuse University | MS Applied Data Science  Aug 2024 - May 2026
"""


def test_a_clean_pdf_reads_back_whole(tmp_path):
    pdf = format_to_pdf(RESUME, str(tmp_path / "resume"))
    assert pdf
    assert readback.read_back(pdf, RESUME) == []
    assert "every bullet read back" in readback.summary(pdf, RESUME)


def test_text_missing_from_the_pdf_is_reported(tmp_path):
    pdf = format_to_pdf(RESUME, str(tmp_path / "resume"))
    edited = RESUME.replace("EDUCATION", "CERTIFICATIONS\n- Passed a certification exam nobody printed\n\nEDUCATION")
    problems = readback.read_back(pdf, edited.replace("sam.doe@example.com", "other@example.com"))
    text = "; ".join(problems)
    assert "email" in text
    assert "CERTIFICATIONS" in text


def test_unreadable_pdf_is_a_finding(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"not a pdf")
    assert readback.read_back(bad, RESUME)[0].startswith("PDF read-back failed")


def test_writing_rules():
    assert writing.bullet_issues("Responsible for my team's 3 dashboards") == ["weak", "first_person"]
    assert writing.bullet_issues("Built a dashboard") == ["no_number"]
    assert "long" in writing.bullet_issues(" ".join(["word"] * 36) + " 5")
    assert writing.bullet_issues("Built 12 dashboards") == []


def test_writing_score_and_repeated_openers():
    text = "\n".join(["Job A", "- Built 1 thing", "- Built 2 things", "- Built 3 things",
                      "Job B", "- Built 4 things", "- Helped with reports"])
    score, flagged = writing.report(text)
    assert [b for b, _ in flagged] == ["Built 1 thing", "Built 2 things", "Built 3 things", "Helped with reports"]
    assert score == round((90 * 3 + 100 + 55) / 5)
    assert writing.summary(RESUME).startswith("Writing score 100/100")
