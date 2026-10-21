"""Application assistant: answers your questions while you fill in an application.

It sees the job (title, company, description), your master resume, your saved answers, and the
questions visible on the page you're on (sent by the browser extension when you ask), and
answers only from those, so suggested answers stay true to your record.
"""

PROMPT = """You help a job seeker fill in an online job application. Answer their question.

Rules:
- Start with the answer itself. For a pick-one question (dropdown, radio, list of options),
  start with "Choose: <exact option text>", then one line of why. Map the user's real situation
  to the options using what such options normally mean (e.g. "sourcer" / "sourcing program"
  options mean a recruiter contacted you; a job-board name means you found it on that board).
  If none of the listed options fits, say "None of these fit" and name the kind of option to
  look for instead (e.g. "Company website" / "Career site"). Don't list every option.
- Use only facts from HOW THIS JOB WAS FOUND, the RESUME and SAVED ANSWERS below. Never invent
  experience, numbers, employers, dates, degrees, skills or personal details.
- If a field asks for something not in them (e.g. salary expectation, a personal detail),
  say in one line what's missing and give a sensible default; don't make up facts.
- For "why this company / role" style questions, write 80-150 words that connect the job
  description to the most relevant resume facts, ready to paste.
- Questions about visas or work authorization: use the SAVED ANSWERS exactly; if they don't
  cover it, say so (this is not legal advice).
- Plain text, concise. No markdown headings.

JOB: {title} at {company}
HOW THIS JOB WAS FOUND: {found}
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
                        company: str = "", description: str = "", page_questions: list[str] | None = None,
                        found: str = "") -> str:
    saved = "\n".join(f"- {q}: {a}" for q, a in answers) or "(none yet)"
    visible = "\n".join(f"- {q}" for q in (page_questions or [])[:60]) or "(not sent)"
    return PROMPT.format(question=question.strip()[:2000], resume=resume, answers=saved, title=title or "unknown role",
                         company=company or "unknown company", description=(description or "")[:5000],
                         page_questions=visible, found=found or "unknown")


def assist(llm, question: str, resume: str, answers: list[tuple[str, str]], **job) -> str:
    return (llm.generate(build_assist_prompt(question, resume, answers, **job)) or "").strip()
