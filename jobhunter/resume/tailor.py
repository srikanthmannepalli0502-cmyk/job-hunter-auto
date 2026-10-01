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
}

PROMPT = """You are an expert resume writer and ATS optimization specialist.

CONTENT RULES:
1. Match keywords from the job description
2. Reorder bullets to put the most relevant experience first
3. Headings: SUMMARY, EDUCATION, SKILLS, EXPERIENCE, PROJECTS, CERTIFICATIONS
4. Rewrite SUMMARY to target this specific role
5. Prioritize skills that appear in the job description
6. Be truthful: reorganize and rephrase only, never invent experience, numbers or skills
7. Start each bullet with a strong action verb
8. Keep dates, names, titles and education EXACTLY as in the original
9. Keep the same number of experience bullets per job as the original
{must_keep_rules}
FORMATTING:
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
                 mode: str = "normal", hint: str = "") -> str:
    must_keep_rules = ""
    if must_keep:
        items = "\n".join(f"  - {item}" for item in must_keep)
        must_keep_rules = (
            f"\nMANDATORY - every one of these must appear in the output:\n{items}\n"
            "If any of them is missing, the output is INVALID.\n"
        )
    return PROMPT.format(
        must_keep_rules=must_keep_rules,
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


def trim_to_one_page(resume_text: str, linkedin_url: str = "") -> str:
    """Remove one wrapped line per round, always from the line that loses the fewest words,
    until the PDF fits on one page."""
    lines = [line.strip() for line in resume_text.strip().split("\n")]
    measure = _TextMeasure()

    for round_no in range(1, MAX_TRIM_ROUNDS + 1):
        best_idx, best_cut, best_removed = -1, None, None
        for i, line in enumerate(lines):
            if not _trimmable(line):
                continue
            cut = measure.cut_to_lose_one_line(line)
            if cut is None:
                continue
            removed = len(line.split()) - len(cut.split())
            if best_removed is None or removed < best_removed:
                best_idx, best_cut, best_removed = i, cut, removed
        if best_cut is None:
            break

        lines[best_idx] = best_cut
        text = "\n".join(lines)
        if check_resume_fit(text, linkedin_url)["status"] != "too_long":
            logger.info(f"Fits on one page after {round_no} trim rounds")
            return text

    logger.warning("Could not trim the resume to one page")
    return "\n".join(lines)


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

    def _generate(self, job_description: str, mode: str = "normal", hint: str = "") -> str:
        prompt = build_prompt(self.base_resume_text, job_description, self.must_keep, mode, hint)
        return self.llm.generate(prompt).strip()

    def tailor_text(self, job_description: str) -> str:
        text = self._generate(job_description)
        if not text:
            logger.error("Empty response from LLM")
            return ""

        if missing := missing_items(text, self.must_keep):
            logger.warning(f"LLM dropped {missing}; retrying once")
            text = self._generate(job_description) or text

        fit = check_resume_fit(text, self.linkedin_url)
        logger.info(f"First draft: {fit['status']} ({fit['fill_percent']}% of page)")

        if fit["status"] == "too_long":
            return trim_to_one_page(text, self.linkedin_url)

        if fit["status"] == "too_short":
            longer = self._generate(job_description, "longer",
                                    hint=f"The page was only {fit['fill_percent']}% full.")
            if longer:
                fit2 = check_resume_fit(longer, self.linkedin_url)
                logger.info(f"Longer draft: {fit2['status']} ({fit2['fill_percent']}% of page)")
                if fit2["status"] == "too_long":
                    return trim_to_one_page(longer, self.linkedin_url)
                if fit2["fill_percent"] > fit["fill_percent"]:
                    return longer
        return text

    def tailor(self, job_description: str, company: str, role: str) -> str:
        """Return the path of the saved PDF, or "" if it could not be made to fit."""
        text = self.tailor_text(job_description)
        if not text:
            return ""
        if missing := missing_items(text, self.must_keep):
            logger.warning(f"Tailored resume is missing: {missing}. Review it before sending.")

        path = self.output_dir / safe_filename(company, role)
        return format_to_pdf(text, str(path), linkedin_url=self.linkedin_url)
