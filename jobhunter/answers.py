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
