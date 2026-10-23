"""Profile fields the browser extension can fill, and how to recognise them on a form.

Each field has words that appear in the label of the matching form question. Free-form
answers you save (field empty) are matched by question text instead. Sensitive identifiers
(SSN, passport, bank, date of birth) are never stored or filled.
"""

import re

# (key, label shown on the Profile page, label keywords; any matching keyword identifies the field)
FIELDS = [
    ("first_name", "First name", ["first name", "given name", "legal first"]),
    ("last_name", "Last name", ["last name", "family name", "surname", "legal last"]),
    ("full_name", "Full name", ["full name", "your name", "legal name"]),
    ("email", "Email", ["email", "e-mail"]),
    ("phone", "Phone", ["phone", "mobile", "telephone", "cell"]),
    ("city", "City", ["city", "town"]),
    ("state", "State", ["state", "province", "region"]),
    ("country", "Country", ["country"]),
    ("address_line1", "Street address", ["address line 1", "street address", "address 1", "street"]),
    ("postal_code", "ZIP / postal code", ["postal code", "zip code", "zip", "postcode"]),
    ("linkedin", "LinkedIn URL", ["linkedin"]),
    ("github", "GitHub URL", ["github"]),
    ("website", "Portfolio / website", ["website", "portfolio", "personal site", "other url"]),
    ("school", "University", ["school", "university", "college", "institution"]),
    ("degree", "Degree", ["degree", "level of education", "highest education"]),
    ("major", "Field of study", ["major", "field of study", "discipline", "area of study"]),
    ("gpa", "GPA", ["gpa", "grade point"]),
    ("graduation", "Graduation date", ["graduation", "expected graduation", "end date of study"]),
    ("work_authorized", "Authorized to work in the U.S.?", ["authorized to work", "legally authorized",
                                                           "eligible to work", "work authorization"]),
    ("needs_sponsorship", "Will you need visa sponsorship (now or later)?", ["sponsorship", "sponsor",
                                                                              "visa status", "require a visa"]),
    ("work_status", "Current work status (e.g. F-1, OPT)", ["immigration status", "current visa", "work permit"]),
    ("relocate", "Willing to relocate?", ["relocate", "relocation"]),
    ("salary", "Salary expectation", ["salary", "compensation expectation", "desired pay"]),
    ("start_date", "Earliest start date", ["start date", "available to start", "availability"]),
    ("how_heard", "How did you hear about us?", ["how did you hear", "source", "referred by"]),
]
FIELD_KEYS = {k for k, _, _ in FIELDS}

# Words that mean a label is NOT this field even though a keyword matches,
# e.g. "Phone Extension" or "Country Phone Code" are not the phone number or the country.
EXCLUDE = {
    "phone": ["extension", "ext", "device", "type", "country", "code"],
    "country": ["phone", "code", "citizenship"],
    "state": ["statement", "status"],
    "city": ["citizenship", "ethnicity"],
    "school": ["high school"],
    "start_date": ["from", "to"],
    "email": ["confirm", "re-enter", "retype"],
}


_SUFFIX = re.compile(r"[,.]?\s+(inc|llc|l\.l\.c|n\.a|corp|corporation|co|ltd|lp|llp|l\.p|plc|group)\.?$", re.I)
JOB_BOARD_SOURCES = {"greenhouse", "lever", "ashby", "workday", "oracle", "smartrecruiters", "amazon"}


def company_names(company: str) -> list[str]:
    """'Comcast Cable Communications, LLC' -> ['Comcast Cable Communications', 'Comcast'] (as matching.js does)."""
    name = (company or "").strip()
    while _SUFFIX.search(name):
        name = _SUFFIX.sub("", name).strip()
    first = name.split()[0] if name.split() else ""
    return [n for n in dict.fromkeys([name, first if len(first) >= 4 else ""]) if len(n) >= 3]


def generalize(question: str, company: str) -> str:
    """The company's name in a question replaced by 'the company', so a saved answer to "Have you
    worked at Comcast before?" also answers it at the next company."""
    for n in company_names(company):
        question = re.sub(rf"\b{re.escape(n)}\b", "the company", question, flags=re.I)
    return question


def found_via(source: str, company: str) -> dict:
    """Where Job Hunter found a job, for "How did you hear about us?"."""
    if source in JOB_BOARD_SOURCES:
        return {"kind": "company_site", "name": (company_names(company) or [company])[0]}
    names = {"hackernews": "Hacker News", "remotive": "Remotive", "adzuna": "Adzuna"}
    return {"kind": "job_board", "name": names.get(source, source.title())}


def work_history(resume_text: str) -> dict:
    """Jobs and education from the master resume, for forms with repeated 'Work Experience' and
    'Education' sections: [{title, company, start: 'MM/YYYY', end, current, description}]."""
    from jobhunter.resume.compose import Master

    months = {m: i for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}

    def mm_yyyy(text: str) -> str:
        m = re.match(r"([A-Za-z]{3})[a-z]*\.?\s+(\d{4})", text.strip())
        return f"{months.get(m.group(1).lower(), 1):02d}/{m.group(2)}" if m else ""

    jobs = []
    for e in Master.parse(resume_text).jobs:
        m = re.match(r"(.+?)\s*\|\s*(.+?)\s{2,}(.+)$", e.header) or re.match(
            r"(.+?)\s*\|\s*(.+?)\s+((?:[A-Z][a-z]{2}\s+\d{4}).*)$", e.header)
        if not m:
            continue
        company, title, dates = m.group(1).strip(), m.group(2).strip(), m.group(3)
        start, _, end = dates.partition("-")
        current = "present" in end.lower()
        jobs.append({"title": title, "company": company.replace(", TCS", "").strip(), "start": mm_yyyy(start),
                     "end": "" if current else mm_yyyy(end), "current": current,
                     "description": "\n".join(f"- {b}" for b in e.bullets)})
    return {"jobs": jobs, "education": education(resume_text)}


def education(resume_text: str) -> list[dict]:
    """Degrees under EDUCATION: 'Syracuse University  Dec 2026' then "Master's, Applied Data Science | GPA: 3.67/4.0"
    -> [{school, degree, major, gpa, end}]."""
    lines = resume_text.splitlines()
    start = next((i for i, l in enumerate(lines) if l.strip().upper() == "EDUCATION"), None)
    if start is None:
        return []
    out = []
    for line in lines[start + 1:]:
        line = line.strip()
        if not line or line.lower().startswith("coursework"):
            continue
        if line.isupper() and len(line.split()) <= 4:  # next section heading
            break
        deg = re.match(r"(.+?),\s*([^|]+?)(?:\s*\|\s*GPA:?\s*([\d.]+))?(?:/[\d.]+)?$", line)
        if out and not out[-1]["degree"] and deg:
            out[-1].update(degree=deg.group(1).strip(), major=deg.group(2).strip(), gpa=deg.group(3) or "")
            continue
        school = re.match(r"(.+?)\s{2,}(.+)$", line)
        if school:
            out.append({"school": school.group(1).strip(), "end": school.group(2).strip(),
                        "degree": "", "major": "", "gpa": ""})
    return out

# Form questions the extension must never fill or learn.
SENSITIVE = re.compile(
    r"social security|\bssn\b|passport|bank|routing|account number|credit card|date of birth|\bdob\b|"
    r"birth date|driver'?s license|password|tax id|\bitin\b|alien registration|a-number",
    re.I,
)


def defaults_from_profile(profile, resume_text: str) -> dict[str, str]:
    """Starting values taken from your profile and master resume (you can change them all)."""
    out = {}
    name = (profile.name or "").split()
    if name:
        out["first_name"], out["last_name"], out["full_name"] = name[0], name[-1], " ".join(name)
    if profile.linkedin_url:
        out["linkedin"] = profile.linkedin_url
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", resume_text)
    phone = re.search(r"\+?1?[-. ]?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}", resume_text)
    github = re.search(r"github\.com/[\w-]+", resume_text, re.I)
    if email:
        out["email"] = email.group(0)
    if phone:
        out["phone"] = phone.group(0).strip()
    if github:
        out["github"] = "https://" + github.group(0)
    return out


def normalize_question(text: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", " ", (text or "").lower()).strip()
