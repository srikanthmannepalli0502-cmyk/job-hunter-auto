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
    short_last_lines,
)
from jobhunter.resume import ats

MAX_TRIM_ROUNDS = 40

LENGTH_RULES = {
    "normal": """
LENGTH: Fill the whole page (97-100%) - dense, no empty space at the bottom.
- SUMMARY: 3-4 sentences, 60-80 words: years and domain, tools, strongest results, education and target role
- Current role: 3-4 bullets; longest past job: 6-7 bullets
- A printed bullet line holds about {cpl} characters. Write every bullet as either ONE full line
  ({one_low}-{one_high} characters) or TWO full lines ({two_low}-{two_high} characters), never in between
- Exactly 3 projects with 2 bullets each
- Skills: 6 lines, each "Category: item, item, ...", the job's supported keywords first
- Every bullet ends close to the end of its last line: never leave a line holding only a few words""",
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
1. ATS: applicant tracking systems rank resumes by the job description's exact terms. Use the
   posting's own words (e.g. "data quality", "stakeholders", "dashboards") wherever the base
   resume supports the same fact, and put the most important supported terms in SKILLS and in
   the first bullet of each job
2. Rewrite each experience bullet in the language of this job: same facts and numbers, framed
   for what the role needs; put the most relevant bullets first
3. Headings: SUMMARY, EDUCATION, SKILLS, EXPERIENCE, PROJECTS, CERTIFICATIONS
4. Rewrite SUMMARY to target this specific role
5. Prioritize skills that appear in the job description
6. Be truthful: reorganize and rephrase only, never invent experience, numbers, duties, tools or
   outcomes (no "procurement", "runbooks", "led", "audits" unless the base resume says so).
   SKILLS may only list tools and skills that appear in the base resume: never add one just
   because the job description mentions it
7. Start each bullet with a strong action verb; every bullet is a complete sentence
8. Keep dates, names, titles and education EXACTLY as in the original (coursework on one line)
9. Write numbers as digits exactly as in the base resume (2M+, 60%, 4 hours), never as words
10. Choose the 3 projects whose tools and results best match this job's duties and required skills
    (e.g. dashboards and SQL for analyst roles, models for data science, LLMs for AI roles),
    always including the mandatory items below; drop the rest. Keep each chosen project's header
    line exactly as in the base resume, including its "| https://..." link. Keep at least 3 certifications
11. Every number and claim stays with the job or project it belongs to in the base resume:
    never move a metric from a project into a job, or between jobs
12. Past tense for past jobs and finished projects; present tense only for a current role (Present)
13. A domain or tool that appears only in the job description (e.g. "credit risk", "Power BI") may be
    named only in the SUMMARY as the role you're targeting, never as something you did or used
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


RESIZE_PROMPT = """Rewrite this resume text so it is between {low} and {high} characters long
(it is {now} now). Keep its meaning, its numbers and its tense. Write plain, complete sentences
like the original: no "&", "<", "/" or other shortcuts, a space after every comma, third person
(no "I"), ending with a period. Add no fact, tool, duty or result
that isn't in the bullet or in these original bullets from the same job:
{sources}

Bullet: {bullet}

Return only the rewritten bullet text, without a leading dash."""


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
        length_rules=LENGTH_RULES[mode].format(hint=hint, **_budgets()),
        base_resume=base_resume,
        job_description=job_description,
    )


def _budgets() -> dict:
    cpl = round(_TextMeasure().chars_per_line("- " + "Collected, cleaned and transformed operational and "
                                              "financial data from 5+ source systems; built and maintained "
                                              "automated ETL pipelines with initial and incremental load strategies."))
    return {"cpl": cpl, "one_low": int(cpl * 0.85), "one_high": int(cpl * 0.97),
            "two_low": int(cpl * 1.8), "two_high": int(cpl * 1.96)}


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

    def printed(self, line: str) -> list[str]:
        bullet = line.startswith(("- ", "* "))
        body = sanitize_text(line[2:].strip() if bullet else line)
        width = self.bullet_width if bullet else self.text_width
        return self.pdf.multi_cell(width, 4.2, body, dry_run=True, output="LINES")

    def last_share(self, line: str) -> float:
        """How full the last printed line of `line` is (1.0 = full)."""
        rows = self.printed(line)
        width = self.bullet_width if line.startswith(("- ", "* ")) else self.text_width
        return self.pdf.get_string_width(rows[-1]) / width if rows else 1.0

    def chars_per_line(self, line: str) -> float:
        rows = self.printed(line)
        return sum(len(r) for r in rows[:-1]) / (len(rows) - 1) if len(rows) > 1 else len(line)

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


# Words a rewrite may add without adding a fact (connectives and plain resume verbs).
_FREE_WORDS = set("""
about above across after along also among another around based because before being below between both
built create created delivering deliver delivered during each either enable enabled enabling ensure
ensuring every first from further have having into itself make making more most other over same
several since still such than that their them then there these they this those through throughout
under until using utilize upon various where which while whose with within without would your
support supported supporting help helped helping work worked working provide provided providing
improve improved improving include included including team teams across monthly weekly daily
""".split())


def _stem(word: str) -> str:
    w = word.lower()
    for suffix in ("ations", "ation", "ings", "ing", "ers", "ies", "ied", "ed", "es", "er", "ly", "s"):
        if w.endswith(suffix) and len(w) - len(suffix) >= 4:
            return w[: -len(suffix)]
    return w


_WORD = re.compile(r"[A-Za-z][A-Za-z+#/-]{4,}")


def _stems(text: str) -> set[str]:
    """Stems of a text's words, with hyphenated words also split (twice-daily -> twice, daily)."""
    words = _WORD.findall(text)
    words += [x for w in words for x in re.split(r"[-/]", w) if len(x) > 2]
    words += [w.replace("-", "") for w in words if "-" in w]
    return {_stem(w) for w in words}


def _known(word: str, stems: set[str], prefixes: set[str]) -> bool:
    s = _stem(word)
    return word.lower() in _FREE_WORDS or s in stems or s[:6] in prefixes  # troubleshot ~ troubleshoot


def invented_words(line: str, base: str) -> list[str]:
    """Content words in a tailored line that the master resume never uses, e.g. 'procurement'
    or 'runbooks': a sign the rewrite added a fact."""
    stems = _stems(base)
    prefixes = {s[:6] for s in stems if len(s) >= 6} | {s[:5] for s in stems if len(s) >= 5}
    out = []
    for w in _WORD.findall(line):
        if _known(w, stems, prefixes) or _known(w.replace("-", ""), stems, prefixes):
            continue
        parts = [x for x in re.split(r"[-/]", w) if len(x) > 2]
        if len(parts) > 1 and all(_known(x, stems, prefixes) for x in parts):
            continue
        out.append(w)
    return list(dict.fromkeys(out))


def same_fact(a: str, b: str) -> bool:
    """Two bullets state the same thing: they share a metric (e.g. 15+, 70%) or most words."""
    na, nb = _numbers(a), _numbers(b)
    if na & nb:
        return True
    sa, sb = _stems(a), _stems(b)
    return len(sa & sb) / max(1, min(len(sa), len(sb))) >= 0.45


def restore_invented_bullets(text: str, base: str, limit: int = 2) -> tuple[str, list[str]]:
    """Replace each EXPERIENCE / PROJECTS bullet that still adds facts with the master resume's
    closest original bullet. Returns (text, notes)."""
    originals = [line for line in base.split("\n") if line.startswith(("- ", "* "))]
    lines, notes, used, section = text.split("\n"), [], set(), ""
    for i, line in enumerate(lines):
        upper = line.strip().upper()
        if upper in _HEADINGS:
            section = upper
            continue
        if section not in ("EXPERIENCE", "PROJECTS") or not line.startswith(("- ", "* ")):
            continue
        extra = invented_words(line, base)
        if len(extra) < limit:
            continue
        mine = _stems(line)
        others = [l for j, l in enumerate(lines) if j != i and l.startswith(("- ", "* "))]
        on_page = {o for o in originals if any(same_fact(o, other) for other in others)}
        best = max((o for o in originals if o not in used and o not in on_page),
                   key=lambda o: len(mine & _stems(o)), default=None)
        if best:
            used.add(best)
            lines[i] = best
            notes.append(f"restored your original bullet in place of one that added: {', '.join(extra)}")
    return "\n".join(lines), notes


def bullet_skills(text: str) -> str:
    """'Category: a, b' lines in SKILLS get a leading '- ' so they render as bullets."""
    out, in_skills = [], False
    for line in text.split("\n"):
        upper = line.strip().upper()
        if upper in _HEADINGS:
            in_skills = upper == "SKILLS"
        elif in_skills and ":" in line and not line.startswith(("- ", "* ")):
            line = "- " + line.strip()
        out.append(line)
    return "\n".join(out)


def drop_unsupported_skills(text: str, base: str) -> str:
    """Remove SKILLS items with no support in the base resume (e.g. a tool the LLM copied from
    the job description, or "credit reporting"). Every word of the item (outside parentheses)
    must appear in the base resume."""
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
                for item in re.split(r",(?![^(]*\))", items):  # commas inside (...) stay with their item
                    # the item's own words must all be supported; a "(qualifier)" may rephrase
                    words = re.findall(r"[A-Za-z][A-Za-z+#.]{3,}", re.sub(r"\(.*?\)", "", item))
                    if not words or all(w.lower().rstrip(".") in base_lower or _stem(w) in _stems(base)
                                        or w.lower() in _FREE_WORDS for w in words):
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
        if hasattr(llm, "reasoning_effort"):
            llm.reasoning_effort = "medium"  # following the truthfulness rules matters more than speed

    def _generate(self, job_description: str, mode: str = "normal", hint: str = "",
                  corrections: list[str] | None = None) -> str:
        prompt = build_prompt(self.base_resume_text, job_description, self.must_keep, mode, hint, corrections)
        text = (self.llm.generate(prompt) or "").strip()
        if not text:
            return ""
        text = bullet_skills(ensure_header(text, self.base_resume_text))
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

    def problems(self, text: str, job_description: str) -> list[str]:
        """What an ATS or a careful reader would flag, worded as fixes for the LLM."""
        out = []
        report = ats.score(text, job_description, self.base_resume_text)
        if report.add_these:
            out.append("The job asks for these and the base resume supports them, but your version leaves "
                       "them out; work them in truthfully: " + ", ".join(report.add_these))
        if claims := ats.unsupported_claims(text, self.base_resume_text):
            out.append("These terms are not in the base resume; remove them outside the SUMMARY: " + ", ".join(claims))
        section = ""
        for line in text.split("\n"):
            upper = line.strip().upper()
            if upper in _HEADINGS:
                section = upper
            elif section in ("SUMMARY", "EXPERIENCE", "PROJECTS") and line.strip():
                extra = [w for w in invented_words(line, self.base_resume_text)
                         if section != "SUMMARY" or w.lower() not in job_description.lower()]
                if len(extra) >= (3 if section == "SUMMARY" else 2):
                    out.append(f'"{line.strip()[:70]}..." adds facts the base resume does not have '
                               f"({', '.join(extra)}); rephrase using only the base resume's facts")
        for s in short_last_lines(text, 0.4):
            out.append(f"{s} - shorten that bullet by a few words or add real detail so its last line is fuller")
        return out + misplaced_claims(text, self.base_resume_text)

    def _entry_sources(self, text: str, index: int) -> list[str]:
        """The master resume's bullets for the job or project that line `index` belongs to."""
        lines = text.split("\n")
        header = next((lines[i] for i in range(index, -1, -1)
                       if lines[i].strip() and not lines[i].startswith(("- ", "* "))), "")
        base_lines = self.base_resume_text.split("\n")
        for section in ("EXPERIENCE", "PROJECTS"):
            for h, bullets in _entries(base_lines, section):
                if _entry_key(base_lines[h]) and _entry_key(base_lines[h])[:15] == _entry_key(header)[:15]:
                    return [base_lines[i] for i in bullets]
        return []

    def _base_summary(self) -> str:
        lines = self.base_resume_text.split("\n")
        start = next((i for i, l in enumerate(lines) if l.strip().upper() == "SUMMARY"), None)
        if start is None:
            return ""
        out = []
        for l in lines[start + 1:]:
            if l.strip().upper() in _HEADINGS:
                break
            out.append(l)
        return " ".join(x.strip() for x in out if x.strip())

    def fix_short_lines(self, text: str, min_share: float = 0.45) -> str:
        """Rewrite each bullet whose last printed line holds only a few words to a measured length
        (one line fewer), and keep the rewrite only if it really fits and adds nothing."""
        measure = _TextMeasure()
        lines = text.split("\n")
        section = ""
        for i, line in enumerate(lines):
            if line.strip().upper() in _HEADINGS:
                section = line.strip().upper()
                continue
            summary = section == "SUMMARY" and len(line) > 120
            if not (line.startswith(("- ", "* ")) or summary):
                continue
            rows = measure.printed(line)
            if len(rows) < 2 or measure.last_share(line) >= min_share:
                continue
            per_line = measure.chars_per_line(line)
            if summary:  # keep the summary's length: fill its last line instead
                low, high = int(per_line * (len(rows) - 0.25)), int(per_line * (len(rows) - 0.03))
            else:
                low, high = int(per_line * (len(rows) - 1) * 0.86), int(per_line * (len(rows) - 1) * 0.97)
            sources = [self._base_summary()] if summary else self._entry_sources(text, i)
            body = line.strip() if summary else line[2:].strip()
            for _ in range(2):
                prompt = RESIZE_PROMPT.format(low=low, high=high, now=len(body), bullet=body,
                                              sources="\n".join(sources) or "(none)")
                reply = (self.llm.generate(prompt) or "").strip().lstrip("-* ").strip()
                candidate = reply if summary else "- " + reply
                rows_ok = (len(measure.printed(candidate)) == len(rows)) if summary else (len(measure.printed(candidate)) < len(rows))
                plain = (candidate.rstrip().endswith(".") and not re.search(r"[&<>]|,\S|\bI\b", candidate))
                if (rows_ok and plain and measure.last_share(candidate) >= 0.6
                        and len(invented_words(candidate, self.base_resume_text)) <= len(invented_words(line, self.base_resume_text))
                        and _numbers(candidate) <= _numbers(line) | _numbers(" ".join(sources))):
                    lines[i] = candidate
                    break
        return "\n".join(lines)

    def fill_page(self, text: str, job_description: str, target: float = 99.0) -> str:
        """Add the master resume's own bullets (most job keywords first) to the jobs and projects
        already on the page, while it stays on one page."""
        wanted = ats.terms_in(job_description)
        for _ in range(12):
            fit = check_resume_fit(text, self.linkedin_url)
            if fit["status"] == "too_long" or fit["fill_percent"] >= target - 2:
                break
            lines = text.split("\n")
            measure = _TextMeasure()
            options = []
            for section in ("EXPERIENCE", "PROJECTS"):
                for header, bullets in _entries(lines, section):
                    for source in self._entry_sources(text, header):
                        if any(same_fact(source, lines[b]) for b in bullets):
                            continue  # already on the page, maybe reworded
                        if len(measure.printed(source)) > 1 and measure.last_share(source) < 0.45:
                            continue  # would add a near-empty line
                        gain = len(ats.terms_in(source) & wanted)
                        where = (bullets[-1] if bullets else header) + 1
                        options.append((gain, -len(source), where, source))
            added = False
            for gain, _, where, source in sorted(options, reverse=True):
                candidate = "\n".join(lines[:where] + [source] + lines[where:])
                cfit = check_resume_fit(candidate, self.linkedin_url)
                if cfit["status"] != "too_long" and cfit["fill_percent"] <= target:
                    text, added = candidate, True
                    break
            if not added:
                break
        return text

    def polish(self, job_description: str, text: str) -> str:
        """One rewrite that fixes ATS gaps, unsupported terms and near-empty lines; kept only if
        it still fits on one page and has fewer problems."""
        problems = self.problems(text, job_description)
        if not problems:
            return text
        logger.info(f"Polishing: {problems}")
        retry = self._generate(job_description, corrections=problems)
        if not retry or missing_items(retry, self.must_keep):
            return text
        if check_resume_fit(retry, self.linkedin_url)["status"] == "too_long":
            retry = self._trim(retry)
            if check_resume_fit(retry, self.linkedin_url)["status"] == "too_long":
                return text
        before = ats.score(text, job_description, self.base_resume_text).score
        after = ats.score(retry, job_description, self.base_resume_text).score
        if len(self.problems(retry, job_description)) < len(problems) and after >= before:
            return retry
        return text

    def tailor(self, job_description: str, company: str, role: str) -> str:
        """Return the path of the saved PDF, or "" if it could not be made to fit."""
        text = self.tailor_text(job_description)
        if not text:
            return ""
        text = self.polish(job_description, text)
        text, restored = restore_invented_bullets(text, self.base_resume_text)
        if restored and check_resume_fit(text, self.linkedin_url)["status"] == "too_long":
            text = self._trim(text)
        text = self.fix_short_lines(text)
        text = self.fill_page(text, job_description)
        text = self.fix_short_lines(text)  # bullets just added from the master resume, too
        text = self.fill_page(text, job_description)
        text, restored_late = restore_invented_bullets(text, self.base_resume_text)  # resizes are checked too
        restored += restored_late
        if restored_late:
            text = self.fill_page(text, job_description)
        if check_resume_fit(text, self.linkedin_url)["status"] == "too_long":
            text = self._trim(text)  # a resize or restore can tip it just over one page
        self.ats_report = ats.score(text, job_description, self.base_resume_text)
        logger.info(self.ats_report.summary())
        self.warnings = [f"missing: {m}" for m in missing_items(text, self.must_keep)]
        self.warnings.append(self.ats_report.summary())
        if claims := ats.unsupported_claims(text, self.base_resume_text):
            self.warnings.append("not in your master resume, check the wording: " + ", ".join(claims))
        self.warnings += misplaced_claims(text, self.base_resume_text) + restored
        for w in self.warnings:
            logger.warning(f"Check before sending: {w}")

        path = self.output_dir / safe_filename(company, role)
        return format_to_pdf(text, str(path), linkedin_url=self.linkedin_url)
