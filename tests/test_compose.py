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
    assert d.skills == ["- Tools: Tableau, SQL"]  # master category name; Power BI is not in the master resume


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


def test_rewrites_with_new_shorthand_fall_back():
    reply = json.loads(draft_reply())
    reply["experience"][0]["bullets"][0]["text"] = "Validated ledger data w/ SQL across 5 source systems & DBs monthly."
    d = ResumeComposer(BASE, FakeLLM(json.dumps(reply))).draft("jd")
    assert dict(d.jobs[0][1])["E1-2"].startswith("Wrote SQL queries")


class KeywordLLM(FakeLLM):
    def generate(self, prompt):
        self.prompts.append(prompt)
        if "applicant tracking system would scan" in prompt:
            return json.dumps({"keywords": ["error rates", "ledger data", "Kubernetes"]})
        return self.reply if "Return JSON only" in prompt else ""


def test_supported_job_keywords_reach_the_page():
    reply = json.loads(draft_reply())
    reply["skills"] = [{"category": "Tools", "items": ["SQL"]}]
    c = ResumeComposer(BASE, KeywordLLM(json.dumps(reply)))
    text = c.tailor_text("Analyst: SQL, ledger data, error rates, Kubernetes")
    assert c.wanted >= {"ledger data", "error rates"} and "Kubernetes" not in c.wanted  # not in the master
    assert "ledger data" in text.lower() and "error rates" in text.lower()
    assert "kubernetes" not in text.lower()
    assert "ledger data" in c.llm.prompts[1]  # the draft was told which keywords it may use


def test_feedback_goes_into_the_draft_prompt():
    c = ResumeComposer(BASE, KeywordLLM(draft_reply()))
    c.tailor_text("jd", feedback="Put the Lake project first")
    assert "ASKS FOR THESE CHANGES" in c.llm.prompts[1] and "Lake project first" in c.llm.prompts[1]


def test_helpers_for_titles_concepts_and_casing():
    from jobhunter.resume.compose import clean_title, concept_key, lower_term

    assert clean_title("Data Scientist 3") == "Data Scientist"
    assert clean_title("Data Analyst, Assistant Vice President") == "Data Analyst"
    assert not (concept_key("SQL") & concept_key("PL/SQL"))
    assert concept_key("LLMs") & concept_key("large language models")
    assert lower_term("Data integration") == "data integration" and lower_term("ETL pipelines") == "ETL pipelines"


class EvidenceLLM(FakeLLM):
    def generate(self, prompt):
        self.prompts.append(prompt)
        if "applicant tracking system would scan" in prompt:
            return json.dumps({"keywords": ["SQL", "data integration", "Databricks", "regression analysis"]})
        if "don't appear word for word" in prompt:
            return json.dumps({"claims": [
                {"term": "data integration", "kind": "skill", "entry": "E1", "evidence": "validate ledger data across 5 source systems"},
                {"term": "Databricks", "kind": "tool", "entry": "E1", "evidence": "Wrote SQL queries to validate ledger data"},
                {"term": "regression analysis", "kind": "skill", "entry": "P2", "evidence": "a sentence that is not in the resume"}]})
        return self.reply if "Return JSON only" in prompt else ""


def test_evidence_claims_need_a_real_quote_and_never_name_a_tool():
    c = ResumeComposer(BASE, EvidenceLLM(draft_reply()))
    c.draft("Analyst: SQL, data integration, Databricks, regression analysis")
    assert set(c.claims) == {"data integration"}  # Databricks is a tool; the regression quote isn't real
    assert c.claims["data integration"][0] == "E1"
