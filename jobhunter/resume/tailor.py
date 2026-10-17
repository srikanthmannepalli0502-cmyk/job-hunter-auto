import math
import re
from datetime import datetime
from pathlib import Path

from fpdf import FPDF
from loguru import logger

from jobhunter.resume.pdf import (
    BODY_FONT_SIZE,
    MARGIN_LEFT,
    MARGIN_RIGHT,
    PAGE_W,
    check_resume_fit,
    format_to_pdf,
    sanitize_text,
)

MAX_TRIM_ROUNDS = 40

LENGTH_RULES = {
    "normal": """
LENGTH: Fill exactly 1 page - dense, no empty space.
- SUMMARY: 3-4 detailed sentences: tools, domain, education, target role
- Each bullet: 20-30 words with specifics
- Each project: 1-2 bullet points
- Skills: comprehensive, with relevant keywords""",
    "longer": """
LENGTH: THE PREVIOUS VERSION LEFT EMPTY SPACE AT THE BOTTOM - ADD DETAIL.
{hint}
- Keep the same structure; expand content slightly
- SUMMARY: add 1 sentence about specific tools and the target role
- Each experience bullet: add 3-5 words of detail or metrics
- Each project with 1 bullet: add a second one
- Skills: add 2-3 relevant keywords per category""",
    "shorter": """
LENGTH: THE PREVIOUS VERSION WAS TOO LONG FOR ONE PAGE - MAKE IT SHORTER.
{hint}
- Drop the least relevant project first (keep the mandatory items)
- Keep 3-4 bullets for the longest job and 2 for shorter ones; drop the least relevant bullets
- Tighten bullets to 15-22 words, but every bullet must stay a complete sentence with its numbers
- SUMMARY: 2-3 sentences; Skills: 4-5 lines""",
}

PROMPT = """You are an expert resume writer and ATS optimization specialist.

CONTENT RULES:
1. Match keywords from the job description
2. Reorder bullets to put the most relevant experience first
3. Headings: SUMMARY, EDUCATION, SKILLS, EXPERIENCE, PROJECTS, CERTIFICATIONS
4. Rewrite SUMMARY to target this specific role
5. Prioritize skills that appear in the job description
6. Be truthful: reorganize and rephrase only, never invent experience, numbers or skills.
   SKILLS may only list tools and skills that appear in the base resume: never add one just
   because the job description mentions it
7. Start each bullet with a strong action verb; every bullet is a complete sentence
8. Keep dates, names, titles and education EXACTLY as in the original (coursework on one line)
9. Write numbers as digits exactly as in the base resume (2M+, 60%, 4 hours), never as words
10. Select what fits one page: keep the most relevant bullets per job (fewer if needed), and if
    the base resume lists more projects than fit, keep the 3-4 most relevant to this job
    (always including the mandatory items below) and drop the rest. Keep at least 3 certifications
11. Every number and claim stays with the job or project it belongs to in the base resume:
    never move a metric from a project into a job, or between jobs
12. Past tense for past jobs and finished projects; present tense only for a current role (Present)
{must_keep_rules}{corrections}
FORMATTING:
- First two lines: the name and the contact line, exactly as in the base resume
- Experience header on ONE line: Company Name | Role  Mon YYYY - Mon YYYY
- Education header on ONE line: School Name  Mon YYYY
- Never put dates on a separate line
- Start bullets with "- " (dash space)
{length_rules}

BASE RESUME:
{base_resume}

JOB DESCRIPTION:
{job_description}

Return ONLY the resume text. No markdown, no explanations, no backticks."""


def build_prompt(base_resume: str, job_description: str, must_keep: list[str],
                 mode: str = "normal", hint: str = "", corrections: list[str] | None = None) -> str:
    must_keep_rules = ""
    if must_keep:
        items = "\n".join(f"  - {item}" for item in must_keep)
        must_keep_rules = (
            f"\nMANDATORY - every one of these must appear in the output:\n{items}\n"
            "If any of them is missing, the output is INVALID.\n"
        )
    fix = ""
    if corrections:
        fix = "\nFIX THESE PROBLEMS FROM YOUR PREVIOUS VERSION:\n" + "\n".join(f"  - {c}" for c in corrections) + "\n"
    return PROMPT.format(
        must_keep_rules=must_keep_rules,
        corrections=fix,
        length_rules=LENGTH_RULES[mode].format(hint=hint),
        base_resume=base_resume,
        job_description=job_description,
    )


def missing_items(text: str, must_keep: list[str]) -> list[str]:
    """Return must-keep items the tailored text dropped (compares the first 30 chars, case-insensitive)."""
    upper = text.upper()
    return [item for item in must_keep if item.upper()[:30] not in upper]


class _TextMeasure:
    """Measures wrapped line counts with the same font and widths the PDF renderer uses."""

    def __init__(self):
        self.pdf = FPDF(format="letter")
        self.pdf.add_page()
        self.pdf.set_font("Helvetica", "", BODY_FONT_SIZE)
        content = PAGE_W - MARGIN_LEFT - MARGIN_RIGHT
        self.bullet_width = content - 7  # matches the bullet indent in pdf.py
        self.text_width = content

    def width_for(self, line: str) -> float:
        cell = self.bullet_width if line.startswith(("- ", "* ")) else self.text_width
        # multi_cell wraps inside its cell margins; keep a 2% buffer for word-wrap slack.
        return (cell - 2 * self.pdf.c_margin) * 0.98

    def lines(self, line: str) -> int:
        body = sanitize_text(line[2:] if line.startswith(("- ", "* ")) else line)
        return max(1, math.ceil(self.pdf.get_string_width(body) / self.width_for(line)))

    def cut_to_lose_one_line(self, line: str) -> str | None:
        """Shortest word-boundary cut that makes `line` wrap onto one fewer line."""
        n = self.lines(line)
        if n < 2:
            return None
        words = line.split(" ")
        for end in range(len(words) - 1, 2, -1):
            candidate = " ".join(words[:end]).rstrip(",;:-") + "."
            if self.lines(candidate) < n:
                return candidate
        return None


def _trimmable(line: str) -> bool:
    stripped = line.strip()
    if stripped.isupper() and len(stripped) < 30:  # headings, name
        return False
    if "|" in stripped and ("@" in stripped or "+1" in stripped):  # contact line
        return False
    return len(stripped) > 50


_HEADINGS = ("SUMMARY", "EDUCATION", "SKILLS", "EXPERIENCE", "PROJECTS", "CERTIFICATIONS")


def _entries(lines: list[str], section: str) -> list[tuple[int, list[int]]]:
    """(header line index, bullet line indexes) for each job or project in a section."""
    out: list[tuple[int, list[int]]] = []
    inside = False
    for i, line in enumerate(lines):
        upper = line.strip().upper()
        if upper in _HEADINGS:
            inside = upper == section
            continue
        if not inside or not line.strip():
            continue
        if line.startswith(("- ", "* ")):
            if out:
                out[-1][1].append(i)
        else:
            out.append((i, []))
    return out


def _next_removal(lines: list[str], must_keep: list[str]) -> list[int] | None:
    """Line indexes of the next whole piece to drop, least important first:
    extra project bullets, then experience bullets beyond 2 per job (longest job first),
    then whole projects from the bottom that aren't must-keep items."""
    keep = [m.upper()[:30] for m in must_keep]
    projects = _entries(lines, "PROJECTS")
    for _, bullets in reversed(projects):
        if len(bullets) > 1:
            return [bullets[-1]]
    jobs = sorted(_entries(lines, "EXPERIENCE"), key=lambda e: len(e[1]), reverse=True)
    for _, bullets in jobs:
        if len(bullets) > 2:
            return [bullets[-1]]
    for header, bullets in reversed(projects):
        if not any(k in lines[header].upper() for k in keep):
            return [header, *bullets]
    return None


def trim_to_one_page(resume_text: str, linkedin_url: str = "", must_keep: list[str] | None = None) -> str:
    """Last resort when the LLM's shorter rewrite still overflows: drop whole bullets (never part
    of a sentence), least important first, until the PDF fits on one page."""
    lines = [line.rstrip() for line in resume_text.strip().split("\n")]
    for round_no in range(1, MAX_TRIM_ROUNDS + 1):
        if check_resume_fit("\n".join(lines), linkedin_url)["status"] != "too_long":
            if round_no > 1:
                logger.info(f"Fits on one page after dropping {round_no - 1} bullet(s)")
            return "\n".join(lines)
        drop = _next_removal(lines, must_keep or [])
        if drop is None:
            break
        lines = [line for i, line in enumerate(lines) if i not in set(drop)]
    logger.warning("Could not trim the resume to one page")
    return "\n".join(lines)


def ensure_header(text: str, base: str) -> str:
    """Put the base resume's name and contact lines back on top if the LLM dropped them."""
    head = [line.strip() for line in base.strip().split("\n") if line.strip()][:2]
    lines = text.strip().split("\n")
    if not head or (lines and lines[0].strip().upper() == head[0].upper()):
        return text.strip()
    body = [line for line in lines if line.strip() not in head]
    return "\n".join(head + body)


def _entry_key(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.split("|")[0].lower())[:25]


def _numbers(text: str) -> set[str]:
    """Digits of the metrics in a piece of text, ignoring years."""
    found = {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}
    return {n for n in found if not re.fullmatch(r"(19|20)\d\d", n)}


def misplaced_claims(text: str, base: str) -> list[str]:
    """Bullets whose numbers don't appear in the same job or project of the base resume,
    e.g. a project's '32 data tests' moved under a job."""
    base_lines = base.split("\n")
    base_entries = {}
    for section in ("EXPERIENCE", "PROJECTS"):
        for header, bullets in _entries(base_lines, section):
            body = " ".join([base_lines[header]] + [base_lines[i] for i in bullets])
            base_entries[_entry_key(base_lines[header])] = _numbers(body)
    lines = text.split("\n")
    problems = []
    for section in ("EXPERIENCE", "PROJECTS"):
        for header, bullets in _entries(lines, section):
            key = _entry_key(lines[header])
            match = next((v for k, v in base_entries.items() if k and key and (k.startswith(key) or key.startswith(k))), None)
            if match is None:
                continue
            for i in bullets:
                extra = _numbers(lines[i]) - match
                if extra:
                    problems.append(f'In "{lines[header].split("|")[0].strip()}", the bullet "{lines[i][2:60]}..." uses '
                                    f"{', '.join(sorted(extra))}, which the base resume doesn't have for that entry")
    return problems


def drop_unsupported_skills(text: str, base: str) -> str:
    """Remove SKILLS items with no support in the base resume (e.g. a tool the LLM copied from
    the job description). An item is kept if any of its words (4+ letters) appears in the base."""
    base_lower = base.lower()
    out, in_skills = [], False
    for line in text.split("\n"):
        upper = line.strip().upper()
        if upper in _HEADINGS:
            in_skills = upper == "SKILLS"
        elif in_skills and line.strip():
            prefix, sep, items = line.partition(":")
            if sep:
                kept = []
                for item in items.split(","):
                    words = re.findall(r"[A-Za-z][A-Za-z+#.]{3,}", item)
                    if not words or any(w.lower().rstrip(".") in base_lower for w in words):
                        kept.append(item.strip())
                    else:
                        logger.info(f"Dropped unsupported skill: {item.strip()}")
                line = f"{prefix}: {', '.join(k for k in kept if k)}"
        out.append(line)
    return "\n".join(out)


def safe_filename(*parts: str) -> str:
    cleaned = [re.sub(r"[^A-Za-z0-9]+", "_", p).strip("_")[:30] for p in parts]
    return "_".join(cleaned + [datetime.now().strftime("%Y%m%d_%H%M%S")]) + ".pdf"


class ResumeTailor:
    """Tailor a base resume to a job description and save a one-page ATS-friendly PDF."""

    def __init__(self, base_resume_text: str, llm, must_keep: list[str] | None = None,
                 linkedin_url: str = "", output_dir: str = "resumes/tailored"):
        self.base_resume_text = base_resume_text
        self.llm = llm
        self.must_keep = must_keep or []
        self.linkedin_url = linkedin_url
        self.output_dir = Path(output_dir)
        self.warnings: list[str] = []  # problems left in the last tailored resume

    def _generate(self, job_description: str, mode: str = "normal", hint: str = "",
                  corrections: list[str] | None = None) -> str:
        prompt = build_prompt(self.base_resume_text, job_description, self.must_keep, mode, hint, corrections)
        text = (self.llm.generate(prompt) or "").strip()
        if not text:
            return ""
        text = ensure_header(text, self.base_resume_text)
        return drop_unsupported_skills(text, self.base_resume_text)

    def _trim(self, text: str) -> str:
        return trim_to_one_page(text, self.linkedin_url, self.must_keep)

    def _shorten(self, job_description: str, text: str, fill: float, attempts: int = 2) -> str:
        """Ask the LLM for shorter rewrites (it chooses what to drop); trim whole bullets only if
        it still overflows."""
        if fill <= 110:  # slightly over: dropping a bullet or two keeps the page full
            trimmed = self._trim(text)
            trimmed_fit = check_resume_fit(trimmed, self.linkedin_url)
            if trimmed_fit["status"] != "too_long" and trimmed_fit["fill_percent"] >= 88:
                return trimmed
        best, best_fill = text, fill
        for attempt in range(1, attempts + 1):
            shorter = self._generate(job_description, "shorter",
                                     hint=f"It filled {best_fill:.0f}% of the page; cut about {best_fill - 96:.0f}% of it.")
            if not shorter or missing_items(shorter, self.must_keep):
                continue
            fit = check_resume_fit(shorter, self.linkedin_url)
            logger.info(f"Shorter draft {attempt}: {fit['status']} ({fit['fill_percent']}% of page)")
            if fit["status"] != "too_long":
                return shorter
            if fit["fill_percent"] < best_fill:
                best, best_fill = shorter, fit["fill_percent"]
        return self._trim(best)

    def tailor_text(self, job_description: str) -> str:
        text = self._generate(job_description)
        if not text:
            logger.error("Empty response from LLM")
            return ""

        if missing := missing_items(text, self.must_keep):
            logger.warning(f"LLM dropped {missing}; retrying once")
            text = self._generate(job_description) or text

        if problems := misplaced_claims(text, self.base_resume_text):
            logger.warning(f"Misplaced claims {problems}; retrying once")
            retry = self._generate(job_description, corrections=problems)
            if retry and len(misplaced_claims(retry, self.base_resume_text)) < len(problems):
                text = retry

        fit = check_resume_fit(text, self.linkedin_url)
        logger.info(f"First draft: {fit['status']} ({fit['fill_percent']}% of page)")

        if fit["status"] == "too_long":
            return self._shorten(job_description, text, fit["fill_percent"])

        if fit["status"] == "too_short":
            longer = self._generate(job_description, "longer",
                                    hint=f"The page was only {fit['fill_percent']}% full.")
            if longer:
                fit2 = check_resume_fit(longer, self.linkedin_url)
                logger.info(f"Longer draft: {fit2['status']} ({fit2['fill_percent']}% of page)")
                if fit2["status"] == "too_long":
                    return self._trim(longer)
                if fit2["fill_percent"] > fit["fill_percent"]:
                    return longer
        return text

    def tailor(self, job_description: str, company: str, role: str) -> str:
        """Return the path of the saved PDF, or "" if it could not be made to fit."""
        text = self.tailor_text(job_description)
        if not text:
            return ""
        self.warnings = [f"missing: {m}" for m in missing_items(text, self.must_keep)]
        self.warnings += misplaced_claims(text, self.base_resume_text)
        for w in self.warnings:
            logger.warning(f"Check before sending: {w}")

        path = self.output_dir / safe_filename(company, role)
        return format_to_pdf(text, str(path), linkedin_url=self.linkedin_url)
