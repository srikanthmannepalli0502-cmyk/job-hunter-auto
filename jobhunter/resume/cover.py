"""Short cover note for a job: why this role, backed only by facts from your resume."""

import re
from pathlib import Path

PROMPT = """Write a short cover note (130-180 words, 3 short paragraphs, plain text) for this job.

Rules:
- Use ONLY facts from the resume below. Never invent experience, numbers, tools or employers.
- Paragraph 1: the role and one sentence on why this company or team specifically (use the job
  description, not generic praise).
- Paragraph 2: the 2 most relevant pieces of evidence from the resume, with their numbers.
- Paragraph 3: one sentence on what you'd contribute, and a polite close.
- Do not mention visas, sponsorship, salary, or that you are a student unless the job asks.
- No greeting line with a name you don't know: start with "Dear Hiring Team,".
- End with "Best regards," and the candidate's name: {name}.
- No markdown, no placeholders like [Company].

JOB: {title} at {company}
JOB DESCRIPTION:
{description}

RESUME:
{resume}

Return only the note."""


def build_cover_prompt(resume: str, title: str, company: str, description: str, name: str) -> str:
    return PROMPT.format(resume=resume, title=title, company=company, name=name or "the candidate",
                         description=(description or "")[:6000])


def clean_note(text: str) -> str:
    text = re.sub(r"```\w*", "", text or "").strip()
    # Sign-off on its own lines, even if the model ran it into the last paragraph.
    text = re.sub(r"\s*(Best regards,)\s*", r"\n\n\1\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def write_cover_note(llm, resume: str, title: str, company: str, description: str, name: str,
                     path: str | Path) -> str:
    """Generate the note, save it as a .txt file and return the path ('' if the LLM returned nothing)."""
    note = clean_note(llm.generate(build_cover_prompt(resume, title, company, description, name)))
    if not note:
        return ""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(note + "\n", encoding="utf-8")
    return str(path)
