"""Messages you send to people at a company (you send them; nothing is sent from here).

Two things make them stand out from "I applied, please look at my resume":

1. A fact about the company from the H-1B data, with exact numbers ("Comcast filed 1,039 H-1B
   petitions over 21 months, 53 for data and AI roles"), plus a link to your live dashboard. It
   shows the skill they're hiring for before they open your resume.
2. One concrete result from your resume that matches the job.

Three drafts per job: a LinkedIn connection note to a Syracuse alum (LinkedIn allows 300
characters), a note to a recruiter, and one to the hiring manager. Every number in a draft must
come from your resume or the company fact; a draft that adds any is rejected.
"""

import json
import re

from jobhunter.resume.tailor import _numbers

ROLE_NAMES = {"analyst": "analyst", "data_science": "data science", "ai": "AI",
              "data_engineering": "data engineering", "software": "software"}

PROMPT = """Write three short outreach messages for a job seeker. They send these themselves on
LinkedIn or by email. Tone: warm, specific, confident, plain English; no flattery, no clichés ("I hope
this finds you well", "passionate"), no numbered lists, no pasted resume lines: say the result in your
own short words with its number. Call the company "{short}" and the role "{role}". Never claim a
referral, a past conversation, or anything not below.

JOB: {title} at {company} ({url})
CANDIDATE: {name}, {headline}
STRONGEST MATCH from their resume (use one, with its number): {highlights}
COMPANY FACT from a public-data project the candidate built (a tracker of US Department of Labor
H-1B filings). Use it only in the hiring-manager note, framed as what the candidate's project found
("my H-1B hiring tracker shows {short} filed ..."), numbers exactly as written: {insight}
LINKS: dashboard {dashboard} | portfolio {portfolio}

Return JSON only:
{{"alumni": "LinkedIn connection note to a {school} alum who works at {company}: at most 250
characters (count them), mention {school}, the role, and ask for 10 minutes or a referral",
  "recruiter": "message to a recruiter at {company}, 50-80 words: applied for the role, one matching
result, the portfolio link, ask about next steps",
  "hiring_manager": "message to the hiring manager, 70-100 words: why this team's work fits them, one
result, the company fact as proof of how they work with data, offer a short call",
  "subject": "email subject line, under 70 characters"}}"""


def company_insight(sponsor_db, company: str, roles: list[str]) -> tuple[str, str]:
    """(one sentence with the company's H-1B record, its numbers as text), or ('', '')."""
    match = sponsor_db.match(company) if sponsor_db else None
    if match is None or match.sponsor.filings_all < 5:
        return "", ""
    s = match.sponsor
    role_n = s.filings_in(roles)
    label = "data and AI" if set(roles) & {"analyst", "data_science", "ai"} else " and ".join(
        ROLE_NAMES.get(r, r) for r in roles[:2])
    line = f"{short_company(s.employer)} filed {s.filings_all:,} H-1B petitions over the last {s.months_covered} months"
    if role_n:
        line += f", {role_n:,} of them for {label} roles"
    extras = []
    if s.months_covered and s.active_months == s.months_covered:
        extras.append(f"filing in every one of those {s.months_covered} months")
    decided = s.uscis_new_approved + s.uscis_new_denied
    if decided >= 20:
        extras.append(f"with {round(100 * s.uscis_new_approved / decided)}% of new-hire petitions approved by USCIS")
    if extras:
        line += ", " + " and ".join(extras) if len(extras) < 2 else ", " + extras[0] + ", " + extras[1]
    return line + ".", line


def _json(reply: str) -> dict:
    m = re.search(r"\{.*\}", reply or "", re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def short_company(company: str) -> str:
    """'Comcast Cable Communications, LLC' -> 'Comcast Cable Communications'; 'Citibank, N.A.' -> 'Citibank'."""
    name, suffix = company.strip(), re.compile(r"[,\s]+(?:inc|llc|l\.l\.c|n\.a|corp|corporation|co|ltd|lp|llp|l\.p)\.?$", re.I)
    while suffix.search(name):
        name = suffix.sub("", name).strip()
    return name or company


def draft_messages(llm, *, title: str, company: str, url: str, name: str, headline: str, highlights: str,
                   insight: str, dashboard: str, portfolio: str, school: str, resume: str) -> dict:
    """The three drafts, each checked: its numbers must all come from the resume, the company fact or
    the job title; the alumni note must fit LinkedIn's 300 characters. Failing drafts are left out."""
    from jobhunter.resume.compose import clean_title

    prompt = PROMPT.format(title=title, company=company, url=url, name=name, headline=headline,
                           highlights=highlights, insight=insight or "(no H-1B data for this company)",
                           dashboard=dashboard, portfolio=portfolio, school=school,
                           short=short_company(company), role=clean_title(title.split(" - ")[0]))
    data = _json(llm.generate(prompt))
    if len(str(data.get("alumni", ""))) > 300:  # one more try for LinkedIn's limit
        retry = _json(llm.generate(prompt + "\n\nYour alumni note was too long. Keep it under 250 characters."))
        if retry.get("alumni"):
            data["alumni"] = retry["alumni"]
    allowed = _numbers(" ".join([resume, insight, title, url, dashboard, portfolio]))
    out = {}
    for key in ("alumni", "recruiter", "hiring_manager", "subject"):
        text = re.sub(r"[ \t]+", " ", str(data.get(key, ""))).strip()
        text = re.sub(r"\s*\(\d+\s*(?:characters|chars)\)\s*$", "", text)  # "(180 chars)" the model adds
        facts = re.sub(r"\b\d+[- ]?(?:minutes?|mins?)\b", "", text)  # "10 minutes" is a request, not a claim
        if not text or not (_numbers(facts) <= allowed):
            continue
        if key == "alumni" and len(text) > 300:
            continue  # LinkedIn's connection-note limit
        out[key] = text
    return out
