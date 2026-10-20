"""Application assistant: answers your questions while you fill in an application.

It sees the job (title, company, description), your master resume, your saved answers, and the
questions visible on the page you're on (sent by the browser extension when you ask), and
answers only from those, so suggested answers stay true to your record.
"""

PROMPT = """You help a job seeker fill in an online job application. Answer their question.

Rules:
- Use only facts from the RESUME and SAVED ANSWERS below. Never invent experience, numbers,
  employers, dates, degrees, skills or personal details.
- If a field asks for something not in them (e.g. salary expectation, start date, a personal
  detail), say what's missing and suggest how to decide; don't make it up.
- For a form question, give the answer ready to paste, then at most one line of why.
- For "why this company / role" style questions, write 80-150 words that connect the job
  description to the most relevant resume facts.
- Questions about visas or work authorization: use the SAVED ANSWERS exactly; if they don't
  cover it, say so (this is not legal advice).
- Plain text, concise. No markdown headings.

JOB: {title} at {company}
JOB DESCRIPTION (excerpt):
{description}

QUESTIONS VISIBLE ON THE PAGE:
{page_questions}

SAVED ANSWERS:
{answers}

RESUME:
{resume}

THE USER ASKS: {question}"""


def build_assist_prompt(question: str, resume: str, answers: list[tuple[str, str]], title: str = "",
                        company: str = "", description: str = "", page_questions: list[str] | None = None) -> str:
    saved = "\n".join(f"- {q}: {a}" for q, a in answers) or "(none yet)"
    visible = "\n".join(f"- {q}" for q in (page_questions or [])[:60]) or "(not sent)"
    return PROMPT.format(question=question.strip()[:2000], resume=resume, answers=saved, title=title or "unknown role",
                         company=company or "unknown company", description=(description or "")[:5000],
                         page_questions=visible)


def assist(llm, question: str, resume: str, answers: list[tuple[str, str]], **job) -> str:
    return (llm.generate(build_assist_prompt(question, resume, answers, **job)) or "").strip()
