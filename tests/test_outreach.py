import json

from jobhunter.outreach import company_insight, draft_messages, short_company
from jobhunter.sponsors import Sponsor, SponsorDB


def sponsor_db():
    s = Sponsor(key="ACME", employer="Acme Corp, LLC", filings_all=120,
                role_filings={"analyst": 10, "data_science": 5}, active_months=21, months_covered=21,
                new_hires_latest_fy=30, new_hire_share=0.25, median_wage_data=None,
                uscis_new_approved=48, uscis_new_denied=2, possibly_cap_exempt=False)
    return SponsorDB([s])


def test_company_insight_has_exact_numbers_and_a_short_name():
    line, _ = company_insight(sponsor_db(), "Acme Corp, LLC", ["analyst", "data_science"])
    assert line.startswith("Acme filed 120 H-1B petitions over the last 21 months, 15 of them for data and AI roles")
    assert "96% of new-hire petitions approved" in line
    assert company_insight(sponsor_db(), "Unknown Startup", ["analyst"]) == ("", "")
    assert short_company("Citibank, N.A.") == "Citibank"
    assert short_company("Comcast Cable Communications, LLC") == "Comcast Cable Communications"


class LLM:
    def __init__(self, reply):
        self.reply = reply

    def generate(self, prompt):
        return json.dumps(self.reply)


def test_drafts_with_made_up_numbers_are_dropped():
    reply = {"alumni": "Syracuse alum here. Could I get 10 minutes about the Data Analyst role? (120 chars)",
             "recruiter": "I applied. I cut reporting effort by 60%.",
             "hiring_manager": "I grew revenue 40% at TCS.", "subject": "Data Analyst application"}
    out = draft_messages(LLM(reply), title="Data Analyst", company="Acme", url="", name="Jane", headline="",
                         highlights="", insight="", dashboard="", portfolio="", school="Syracuse University",
                         resume="- Automated reporting, cutting manual effort by 60%.")
    assert out["alumni"].endswith("role?")  # the model's "(120 chars)" removed; "10 minutes" is fine
    assert "recruiter" in out and "hiring_manager" not in out  # 40% is not in the resume
