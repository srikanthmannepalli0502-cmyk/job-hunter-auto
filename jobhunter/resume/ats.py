"""ATS check: how a tailored resume scores against a job description.

Applicant tracking systems (Workday, Taleo, Greenhouse, iCIMS) parse the resume into text, then
rank it by how many of the posting's skills and terms it contains, usually as exact words. This
module does the same scan, and also separates the gaps you can close (terms your master resume
supports but the tailored one left out) from the ones you can't honestly claim.
"""

import re
from dataclasses import dataclass, field

# Terms ATS searches commonly score for data, analytics and AI roles. Each entry is
# (term shown to you, regex). Patterns use word boundaries so "R" doesn't match "Rust".
_B = chr(92) + "b"  # \b, kept out of string escapes
TERMS: list[tuple[str, str]] = [(name, pattern) for name, pattern in [
    ("SQL", r"sql"), ("PL/SQL", r"pl/sql"), ("Python", r"python"), ("R", r"r(?!\w)(?![-/&])"),
    ("SAS", r"sas"), ("VBA", r"vba"), ("Scala", r"scala"), ("Java", r"java(?!script)"),
    ("COBOL", r"cobol"), ("Excel", r"excel"), ("Tableau", r"tableau"), ("Power BI", r"power ?bi"),
    ("Looker", r"looker"), ("Qlik", r"qlik"), ("Alteryx", r"alteryx"), ("Snowflake", r"snowflake"),
    ("Databricks", r"databricks"), ("Spark", r"(?:py)?spark"), ("Hadoop", r"hadoop"), ("Hive", r"hive"),
    ("Airflow", r"airflow"), ("dbt", r"dbt"), ("Kafka", r"kafka"), ("Oracle", r"oracle"),
    ("MySQL", r"mysql"), ("PostgreSQL", r"postgres(?:ql)?"), ("SQL Server", r"sql server"),
    ("NoSQL", r"nosql"), ("MongoDB", r"mongo(?:db)?"), ("AWS", r"aws|amazon web services"),
    ("Azure", r"azure"), ("GCP", r"gcp|google cloud"), ("Linux", r"linux|unix"), ("Git", r"git(?!hub)"),
    ("REST APIs", r"rest(?:ful)? apis?"), ("Jira", r"jira"), ("Agile", r"agile|scrum"),
    ("ETL", r"etl|elt"), ("data pipelines", r"data pipelines?|pipelines?"), ("data warehouse", r"data warehous\w*"),
    ("data modeling", r"data model(?:l)?ing|star schema"), ("data quality", r"data quality|data integrity"),
    ("data validation", r"data validation|validat\w+"), ("data governance", r"data governance"),
    ("data lineage", r"lineage"), ("data analysis", r"data analy\w+|analy[sz]\w* data"),
    ("data visualization", r"data visuali[sz]ation|visuali[sz]ation"), ("dashboards", r"dashboards?"),
    ("reporting", r"report(?:ing|s)"), ("KPIs", r"kpis?"), ("ad hoc analysis", r"ad hoc"),
    ("root cause analysis", r"root[- ]cause"), ("reconciliation", r"reconcil\w+"),
    ("statistics", r"statistic\w*"), ("A/B testing", r"a/b test\w*"), ("machine learning", r"machine learning|\bml\b"),
    ("deep learning", r"deep learning"), ("NLP", r"nlp|natural language"), ("LLMs", r"llms?|large language models?"),
    ("generative AI", r"generative ai|genai"), ("scikit-learn", r"scikit-learn|sklearn"),
    ("TensorFlow", r"tensorflow"), ("PyTorch", r"pytorch"), ("pandas", r"pandas"), ("NumPy", r"numpy"),
    ("forecasting", r"forecast\w*"), ("predictive modeling", r"predictive model\w*"), ("regression", r"regression"),
    ("Terraform", r"terraform"), ("CI/CD", r"ci/cd|github actions|continuous integration"), ("Docker", r"docker"),
    ("Kubernetes", r"kubernetes|k8s"), ("risk management", r"risk management"), ("credit risk", r"credit risk"),
    ("counterparty risk", r"counterparty"), ("regulatory reporting", r"regulatory"), ("compliance", r"compliance"),
    ("financial data", r"financial data|financial reporting|financial services"), ("banking", r"bank\w*"),
    ("stakeholders", r"stakeholders?"), ("cross-functional", r"cross[- ]functional"),
    ("communication", r"communicat\w+"), ("problem solving", r"problem[- ]solving"),
    ("automation", r"automat\w+"), ("documentation", r"document\w+"),
]]
_COMPILED = [(name, re.compile(_B + f"(?:{pattern})(?![a-z])", re.I)) for name, pattern in TERMS]

# Section headings ATS parsers look for to split a resume into fields.
STANDARD_HEADINGS = ("SUMMARY", "EDUCATION", "SKILLS", "EXPERIENCE", "PROJECTS")


def terms_in(text: str) -> set[str]:
    return {name for name, rx in _COMPILED if rx.search(text or "")}


_BY_NAME = {name: rx for name, rx in _COMPILED}


def term_rx(term: str) -> re.Pattern:
    """How to find a term: the built-in pattern for known terms, else the exact phrase (words may be
    joined by spaces, hyphens or slashes; the last word may be plural)."""
    words = [re.escape(w) for w in re.split(r"[\s/-]+", term.strip()) if w]
    if not words:
        return re.compile(r"(?!x)x")
    words[-1] += r"(?:s|es)?"
    phrase = _B + r"[\s/-]+".join(words) + r"(?![a-z])"
    if term in _BY_NAME:  # the built-in pattern (with its synonyms) or the phrase itself
        return re.compile(f"(?:{_BY_NAME[term].pattern})|(?:{phrase})", re.I)
    return re.compile(phrase, re.I)


def found(term: str, text: str) -> bool:
    return bool(term_rx(term).search(text or ""))


@dataclass
class ATSReport:
    score: int  # % of the posting's terms found in the resume
    matched: list[str] = field(default_factory=list)
    add_these: list[str] = field(default_factory=list)  # in your master resume, missing here
    gaps: list[str] = field(default_factory=list)  # wanted by the job, not in your record
    format_problems: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"ATS keyword match {self.score}% ({len(self.matched)} of "
                 f"{len(self.matched) + len(self.add_these) + len(self.gaps)} job terms)"]
        if self.add_these:
            parts.append("supported but missing: " + ", ".join(self.add_these))
        if self.gaps:
            parts.append("not in your record (don't claim): " + ", ".join(self.gaps))
        return "; ".join(parts + self.format_problems)


def format_problems(resume_text: str) -> list[str]:
    """Things that make ATS parsers misread a resume."""
    problems = []
    upper = {line.strip().upper() for line in resume_text.split("\n")}
    missing = [h for h in STANDARD_HEADINGS if h not in upper]
    if missing:
        problems.append("missing standard headings: " + ", ".join(missing))
    if not re.search(r"[\w.+-]+@[\w-]+\.\w+", resume_text):
        problems.append("no email address")
    if not re.search(r"\+?\d[\d\s().-]{8,}\d", resume_text):
        problems.append("no phone number")
    return problems


def score(resume_text: str, job_description: str, base_resume: str,
          keywords: list[str] | None = None) -> ATSReport:
    """keywords: the posting's own terms (picked by the model, each found in the posting); they are
    scored next to the built-in list. A term is "supported" when the master resume contains it."""
    wanted = terms_in(job_description) | {k for k in keywords or [] if found(k, job_description)}
    have = {t for t in wanted if found(t, resume_text)}
    supported = {t for t in wanted if found(t, base_resume)}
    matched = sorted(wanted & have, key=str.lower)
    add_these = sorted((wanted - have) & supported, key=str.lower)
    gaps = sorted(wanted - have - supported, key=str.lower)
    pct = round(100 * len(matched) / len(wanted)) if wanted else 100
    return ATSReport(pct, matched, add_these, gaps, format_problems(resume_text))


def unsupported_claims(resume_text: str, base_resume: str) -> list[str]:
    """Terms the tailored resume uses that the master resume never mentions, e.g. 'credit risk'
    copied from the posting into a bullet. Only the summary may name the target role."""
    body = re.split(r"\n\s*EDUCATION\s*\n", resume_text, maxsplit=1)
    after_summary = body[1] if len(body) == 2 else resume_text
    return sorted(terms_in(after_summary) - terms_in(base_resume))
