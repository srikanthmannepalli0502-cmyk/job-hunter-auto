import json

import pytest

from jobhunter.connections import parse_connections_csv, same_company
from jobhunter.resume.compose import ResumeComposer
from jobhunter.tracker import Tracker
from tests.test_compose import BASE, FakeLLM, draft_reply

EXPORT = """Notes:
"When exporting your connection data, you may notice that some of the email addresses are missing."

First Name,Last Name,URL,Email Address,Company,Position,Connected On
Ana,Lee,https://www.linkedin.com/in/analee,,"Capital One",Data Engineer,01 Mar 2025
Raj,Kumar,https://www.linkedin.com/in/rajk,,Metadata Inc,Analyst,02 Mar 2025
Sam,,,,,Student,03 Mar 2025
"""


@pytest.fixture
def tracker(tmp_path):
    return Tracker(f"sqlite:///{tmp_path / 'db.sqlite'}")


def test_parse_linkedin_export():
    rows = parse_connections_csv("﻿" + EXPORT)
    assert [r["name"] for r in rows] == ["Ana Lee", "Raj Kumar"]  # no company: left out
    assert rows[0]["url"] == "https://www.linkedin.com/in/analee"
    with pytest.raises(ValueError):
        parse_connections_csv("name,company\nx,y")


def test_company_matching():
    assert same_company("Capital One", "Capital One Services, LLC")
    assert same_company("CAPITAL ONE, N.A.", "capital one")
    assert not same_company("Meta", "Metadata Inc")
    assert not same_company("", "Acme")


def test_connections_at_company(tracker):
    assert tracker.replace_connections(parse_connections_csv(EXPORT)) == 2
    assert [c.name for c in tracker.connections_at("Capital One Services, LLC")] == ["Ana Lee"]
    assert tracker.connections_at("Meta Platforms") == []
    tracker.replace_connections([])  # a new upload replaces the list
    assert tracker.connection_count() == 0


def test_confirmed_skills_store(tracker):
    tracker.confirm_skill("Power BI")
    tracker.confirm_skill("power bi")  # once, whatever the case
    tracker.confirm_skill("  ")
    assert tracker.confirmed_skills() == ["Power BI"]
    tracker.unconfirm_skill("POWER BI")
    assert tracker.confirmed_skills() == []


def test_confirmed_skill_goes_on_the_skills_line_only():
    job = "Data Analyst - Acme\nNYC\n\nSQL, Tableau and Power BI dashboards for executives."
    reply = json.loads(draft_reply())
    reply["experience"][0]["bullets"][0]["text"] = "Built 10+ Power BI and Tableau dashboards for executives."
    without = ResumeComposer(BASE, FakeLLM(json.dumps(reply))).tailor_text(job)
    assert "Power BI" not in without

    c = ResumeComposer(BASE, FakeLLM(json.dumps(reply)), confirmed=["Power BI"])
    text = c.tailor_text(job)
    skills = text.split("SKILLS", 1)[1].split("EXPERIENCE", 1)[0]
    body = text.split("EXPERIENCE", 1)[1]
    assert "Power BI" in skills
    assert "Power BI" not in body  # a bullet still can't claim it
