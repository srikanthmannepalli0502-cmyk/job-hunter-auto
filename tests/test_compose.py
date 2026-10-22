import json

from jobhunter.resume.compose import Master, ResumeComposer

BASE = """JANE DOE
Town, ST | +1-555-123-4567 | jane@example.com | LinkedIn

SUMMARY
Data analyst with 3 years of experience in banking data, SQL and Tableau dashboards.

EDUCATION
State University  May 2026
M.S., Data Science

SKILLS
- Tools: SQL, Python, Tableau, Excel

EXPERIENCE
Bank Co | Analyst  Jan 2020 - Dec 2023
- Built 10+ Tableau dashboards tracking transaction volume and error rates for executives.
- Wrote SQL queries to validate ledger data across 5 source systems every month.
- Automated reporting with Python, cutting manual effort by 60%.

PROJECTS
Dash | Python | https://github.com/jane/dash
- Built a Python dashboard over 2M rows of public transit data.
Model | scikit-learn
- Trained a regression model reaching 87% accuracy on delays.
Lake | Snowflake
- Designed a Snowflake star schema that cut query time by 70%.

CERTIFICATIONS
- Cert One
- Cert Two
- Cert Three
"""


class FakeLLM:
    def __init__(self, reply):
        self.reply = reply
        self.prompts = []

    def generate(self, prompt):
        self.prompts.append(prompt)
        return self.reply if "Return JSON only" in prompt else ""


def draft_reply(**over):
    data = {
        "summary": "Data analyst with 3 years of experience in banking data, building SQL and Tableau dashboards.",
        "skills": [{"category": "BI", "items": ["Tableau", "SQL", "Power BI"]}],
        "experience": [{"id": "E1", "bullets": [
            {"source": "E1-2", "text": "Validated ledger data with SQL queries across 5 source systems every month."},
            {"source": "E1-1", "text": "Built 10+ Tableau dashboards for executives, managing vendor procurement budgets."},
            {"source": "E1-3", "text": "Automated reporting with Python, cutting manual effort by 70%."},
        ]}],
        "projects": [{"id": "P3", "bullets": [{"source": "P3-1", "text": "Designed a Snowflake star schema that cut query time by 70%."}]},
                     {"id": "P1", "bullets": [{"source": "P1-1", "text": "Built a Python dashboard over 2M rows of transit data."}]}],
    }
    data.update(over)
    return json.dumps(data)


def test_parse_gives_ids():
    m = Master.parse(BASE)
    assert [e.id for e in m.jobs] == ["E1"] and [e.id for e in m.projects] == ["P1", "P2", "P3"]
    assert m.jobs[0].source("E1-3").startswith("Automated reporting")
    assert "E1-2: Wrote SQL queries" in m.catalog()


def test_rewrites_are_checked_against_their_own_source():
    c = ResumeComposer(BASE, FakeLLM(draft_reply()))
    d = c.draft("Data analyst: SQL, Tableau, dashboards")
    bullets = dict(d.jobs[0][1])
    assert bullets["E1-2"].startswith("Validated ledger data")  # a true rewording is kept
    assert bullets["E1-1"].startswith("Built 10+ Tableau dashboards tracking")  # added "procurement": original
    assert "60%" in bullets["E1-3"]  # moved a project's 70% into the job: original
    assert [sid for sid, _ in d.jobs[0][1]] == ["E1-2", "E1-1", "E1-3"]  # the model's order is kept


def test_projects_chosen_by_the_model_and_skills_checked():
    c = ResumeComposer(BASE, FakeLLM(draft_reply()))
    d = c.draft("jd")
    assert [e.id for e, _ in d.projects][:2] == ["P3", "P1"]
    assert d.skills == ["- BI: Tableau, SQL"]  # Power BI is not in the master resume


def test_bad_json_falls_back_to_the_master_resume():
    c = ResumeComposer(BASE, FakeLLM("not json"))
    text = c.tailor_text("jd")
    assert "Data analyst with 3 years of experience in banking data" in text
    assert text.count("Built 10+ Tableau dashboards") == 1


def test_no_bullet_appears_twice_and_page_fits():
    reply = json.loads(draft_reply())
    reply["experience"][0]["bullets"].append(dict(reply["experience"][0]["bullets"][0]))  # duplicate source
    c = ResumeComposer(BASE, FakeLLM(json.dumps(reply)))
    text = c.tailor_text("jd")
    assert text.count("Validated ledger data") == 1
    assert "https://github.com/jane/dash" in text  # project header and link kept
