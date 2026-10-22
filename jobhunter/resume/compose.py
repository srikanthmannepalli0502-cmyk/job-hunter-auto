"""Resume composer: tailor the master resume to a job, one checked bullet at a time.

The model never writes the resume as free text. It gets every master-resume bullet with an id
(E1-3 = job 1, bullet 3; P2-1 = project 2, bullet 1) and returns JSON: a summary, skills, and for
each job and each of the 3 projects it picks, its bullets reworded for the job, each naming the
bullet it came from, most relevant first. Then, in code:

- a reworded bullet is kept only if its numbers come from its own source bullet and it adds no
  words its job or project doesn't support; otherwise the original bullet is used,
- skills must all appear in the master resume,
- the page is filled by keeping the most relevant bullets that fit on one page,
- bullets that end with a few words on their last line are resized to an exact length,
- the result is scored the way an ATS keyword scan would (ats.py).
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from loguru import logger

from jobhunter.resume import ats
from jobhunter.resume.pdf import check_resume_fit, format_to_pdf
from jobhunter.resume.tailor import (
    RESIZE_PROMPT,
    _HEADINGS,
    _TextMeasure,
    _budgets,
    _numbers,
    drop_unsupported_skills,
    invented_words,
    safe_filename,
)

# Cut order when the page overflows: first down to the "full" counts (projects and certificates
# give way before experience), then the third project, then down to the minimums.
FULL_BULLETS = {"current": 3, "job": 5, "project": 2}
MIN_BULLETS = {"current": 2, "job": 3, "project": 1}
FULL_CERTIFICATIONS = 3


@dataclass
class Entry:
    id: str  # E1, P2
    header: str
    bullets: list[str]  # master text, without "- "
    kind: str = "job"  # job / current / project

    def source(self, bullet_id: str) -> str | None:
        n = bullet_id.split("-")[-1]
        return self.bullets[int(n) - 1] if n.isdigit() and 0 < int(n) <= len(self.bullets) else None


@dataclass
class Master:
    head: list[str]
    summary: str
    education: list[str]
    skills: list[str]
    jobs: list[Entry]
    projects: list[Entry]
    certifications: list[str]
    text: str = ""

    @classmethod
    def parse(cls, text: str) -> "Master":
        lines = [l.rstrip() for l in text.strip().split("\n")]
        head = [l for l in lines[:3] if l.strip()][:2]
        sections: dict[str, list[str]] = {}
        current = None
        for line in lines:
            if line.strip().upper() in _HEADINGS:
                current = line.strip().upper()
                sections[current] = []
            elif current and line.strip():
                sections[current].append(line)

        def entries(section: str, prefix: str) -> list[Entry]:
            out: list[Entry] = []
            for line in sections.get(section, []):
                if line.startswith(("- ", "* ")):
                    if out:
                        out[-1].bullets.append(line[2:].strip())
                else:
                    out.append(Entry(f"{prefix}{len(out) + 1}", line.strip(), [],
                                     "project" if prefix == "P" else ("current" if "present" in line.lower() else "job")))
            return out

        return cls(head, " ".join(sections.get("SUMMARY", [])), sections.get("EDUCATION", []),
                   sections.get("SKILLS", []), entries("EXPERIENCE", "E"), entries("PROJECTS", "P"),
                   [l[2:].strip() if l.startswith("- ") else l.strip() for l in sections.get("CERTIFICATIONS", [])],
                   text)

    def catalog(self) -> str:
        """The master resume with ids, as the model sees it."""
        out = []
        for e in self.jobs + self.projects:
            out.append(f"{e.id}: {e.header}")
            out += [f"  {e.id}-{i}: {b}" for i, b in enumerate(e.bullets, 1)]
        return "\n".join(out)


PROMPT = """You tailor a resume to a job so it passes applicant tracking systems (ATS) and reads
well to a recruiter. ATS rank resumes by the posting's exact terms, so reuse the job's wording
wherever the candidate's own facts support it.

Return JSON only, in this shape:
{{
  "summary": "3-4 sentences, {summary_low}-{summary_high} characters: years and domain, tools, strongest results, education, target role",
  "skills": [{{"category": "Data Analysis & BI", "items": ["SQL", "..."]}}],
  "experience": [{{"id": "E1", "bullets": [{{"source": "E1-2", "text": "..."}}]}}],
  "projects": [{{"id": "P3", "bullets": [{{"source": "P3-1", "text": "..."}}]}}]
}}

Rules:
- experience: every job below, and for each job EVERY one of its bullets, reworded for this job and
  ordered most relevant first. Each bullet names the source bullet it rewrites.
- projects: exactly the 3 projects whose tools and results best fit this job's duties and skills
  {must_projects}, each with all of its bullets reworded, most relevant first.
- Rewording keeps the source bullet's facts, numbers and tense: change emphasis and use the job's
  terms for the same thing; never add a duty, tool, result, number or domain the source bullet
  (or another bullet of the same job) doesn't state. Past tense for past jobs, present for current.
- Each bullet: either one full printed line ({one_low}-{one_high} characters) or two full lines
  ({two_low}-{two_high} characters). Plain sentences. Copy every number exactly as the source
  writes it, digits and symbols included (2M+, 15+, ~60%, 98%, 4 hours), never spelled out. Don't
  shorten names into abbreviations the source doesn't use (write "Saudi National Bank", not "SNB").
- skills: use the candidate's own SKILLS categories (same names), items only from those lines,
  the job's terms first:
  {wanted}
- A domain that appears only in the job description (e.g. "credit risk") may appear only in the
  summary, as the role being targeted.

CANDIDATE RESUME (ids in front):
SUMMARY: {summary}
SKILLS:
{skills}
{catalog}

JOB DESCRIPTION:
{job}"""


def new_shorthand(text: str, support: str) -> list[str]:
    """Short tokens (2-4 letters, e.g. 'SNB', 'pbp', 'DB') and symbols ('&', '<') that the
    rewrite uses but its source never does: shorthand a recruiter would trip over."""
    known = {w.lower() for w in re.findall(r"[A-Za-z][A-Za-z0-9+#.]*", support)}
    out = [w for w in re.findall(r"\b[A-Za-z]{2,4}\b", text)
           if w.lower() not in known and w.lower() not in _SMALL_WORDS
           and (w.isupper() or not re.search(r"[aeiouy]", w, re.I))]  # SNB, DB, pbp; not "time"
    out += [s for s in ("&", "<", ">") if s in text and s not in support]
    return list(dict.fromkeys(out))


_SMALL_WORDS = set("""a an and are as at be by for from has have in into is it its of on or our over per
the to up via was were with all also both each more most than that this then them they used use uses
using new key top data team teams work help led ran run set made make via end""".split())


def _json(reply: str) -> dict | None:
    reply = re.sub(r"^```(?:json)?|```$", "", (reply or "").strip(), flags=re.M).strip()
    start, end = reply.find("{"), reply.rfind("}")
    try:
        return json.loads(reply[start:end + 1]) if start >= 0 else None
    except ValueError:
        return None


@dataclass
class Draft:
    summary: str
    skills: list[str]
    jobs: list[tuple[Entry, list[tuple[str, str]]]]  # (entry, [(source id, text)]) in relevance order
    projects: list[tuple[Entry, list[tuple[str, str]]]]
    certifications: list[str]
    keep: dict[str, int] = field(default_factory=dict)  # entry id -> bullets shown

    def render(self, master: Master) -> str:
        out = master.head + ["", "SUMMARY", self.summary, "", "EDUCATION", *master.education, "", "SKILLS"]
        out += self.skills + ["", "EXPERIENCE"]
        for entry, bullets in self.jobs:
            out += [entry.header] + [f"- {t}" for _, t in bullets[: self.keep.get(entry.id, len(bullets))]] + [""]
        out += ["PROJECTS"]
        for entry, bullets in self.projects:
            out += [entry.header] + [f"- {t}" for _, t in bullets[: self.keep.get(entry.id, len(bullets))]]
        out += ["", "CERTIFICATIONS"] + [f"- {c}" for c in self.certifications]
        return "\n".join(out)


class ResumeComposer:
    """Same interface as ResumeTailor: tailor(job, company, role) -> saved PDF path."""

    def __init__(self, base_resume_text: str, llm, must_keep: list[str] | None = None,
                 linkedin_url: str = "", output_dir: str = "resumes/tailored"):
        self.master = Master.parse(base_resume_text)
        self.base = base_resume_text
        self.llm = llm
        self.must_keep = must_keep or []
        self.linkedin_url = linkedin_url
        self.output_dir = Path(output_dir)
        self.warnings: list[str] = []
        self.ats_report: ats.ATSReport | None = None
        self.wanted: set[str] = set()  # job terms the master resume supports

    # ---- model
    def _ask(self, prompt: str, effort: str) -> str:
        if hasattr(self.llm, "reasoning_effort"):
            self.llm.reasoning_effort = effort
        return self.llm.generate(prompt) or ""

    def _draft_json(self, job: str) -> dict | None:
        wanted = sorted(ats.terms_in(job) & ats.terms_in(self.base))
        must = [e.id for e in self.master.projects if any(m.lower()[:25] in e.header.lower() for m in self.must_keep)]
        b = _budgets()
        prompt = PROMPT.format(
            summary_low=int(b["cpl"] * 1.05 * 3.6), summary_high=int(b["cpl"] * 1.05 * 3.95),
            must_projects=f"(always include {', '.join(must)})" if must else "",
            wanted=", ".join(wanted) or "(none)", summary=self.master.summary,
            skills="\n".join(self.master.skills), catalog=self.master.catalog(), job=job[:9000], **b)
        for _ in range(2):
            data = _json(self._ask(prompt, "low"))
            if data and data.get("experience"):
                return data
            logger.warning("Resume draft was not valid JSON; asking again")
        return None

    # ---- checks
    def _checked(self, entry: Entry, source_id: str, text: str) -> str | None:
        """The reworded bullet if it's true to its source, else the source text."""
        source = entry.source(source_id)
        if source is None:
            return None
        text = re.sub(r"\s+", " ", (text or "").strip().lstrip("-* ")).strip()
        support = " ".join([entry.header, *entry.bullets])
        added = invented_words(text, support)
        shorthand = new_shorthand(text, support)
        lost = (ats.terms_in(source) & self.wanted) - ats.terms_in(text)  # e.g. dropped "regulatory reporting"
        # numbers must be exactly the source's: none added, none dropped (a "70% faster" is the point)
        if not text or _numbers(text) != _numbers(source) or len(added) > 1 or shorthand or lost:
            if text:
                why = "; ".join(x for x in (
                    f"added words {added}" if len(added) > 1 else "",
                    f"shorthand {shorthand}" if shorthand else "",
                    f"dropped job terms {sorted(lost)}" if lost else "",
                    "changed or dropped a number" if _numbers(text) != _numbers(source) else "") if x)
                logger.info(f"{source_id}: kept the original (rewrite: {why})")
            return source
        return text

    def _bullets(self, entry: Entry, items: list) -> list[tuple[str, str]]:
        out, seen = [], set()
        for item in items or []:
            sid = str(item.get("source", "")) if isinstance(item, dict) else ""
            if not sid.startswith(entry.id + "-") or sid in seen:
                continue
            text = self._checked(entry, sid, item.get("text", ""))
            if text:
                out.append((sid, text))
                seen.add(sid)
        # any master bullet the model left out goes last, as written
        out += [(f"{entry.id}-{i}", b) for i, b in enumerate(entry.bullets, 1) if f"{entry.id}-{i}" not in seen]
        return out

    def _summary(self, text: str, job: str) -> str:
        text = re.sub(r"\s+", " ", (text or "").strip())
        extra = [w for w in invented_words(text, self.base) if w.lower() not in job.lower()]
        if not text or not (_numbers(text) <= _numbers(self.base)) or len(extra) > 2 or re.search(r"\bI\b", text):
            return self.master.summary
        return text

    def _skills(self, groups: list) -> list[str]:
        """Each group under one of the master resume's own categories, with items that appear in its
        SKILLS section (so 'stakeholders' or a made-up 'Risk' category can't sneak in)."""
        skills_text = "\n".join(self.master.skills)
        categories = [l.lstrip("-* ").split(":", 1)[0].strip() for l in self.master.skills if ":" in l]
        merged: dict[str, list[str]] = {}
        for g in groups or []:
            if not (isinstance(g, dict) and g.get("items")):
                continue
            items = [str(i).strip() for i in g["items"] if str(i).strip()]
            name = str(g.get("category", "")).strip()
            if name.lower() not in {c.lower() for c in categories} and categories:
                # the master category whose line shares most of these items
                name = max(categories, key=lambda c: sum(
                    i.lower() in next(l for l in self.master.skills if c in l).lower() for i in items))
            name = next((c for c in categories if c.lower() == name.lower()), name)
            merged.setdefault(name, [])
            merged[name] += [i for i in items if i not in merged[name]]
        lines = [f"- {c}: {', '.join(items)}" for c, items in merged.items()]
        checked = drop_unsupported_skills("\n".join(["SKILLS", *lines]), skills_text).split("\n")[1:]
        checked = [l for l in checked if l.split(":", 1)[-1].strip()]
        return checked or self.master.skills

    def draft(self, job: str) -> Draft:
        self.wanted = ats.terms_in(job) & ats.terms_in(self.base)
        data = self._draft_json(job) or {}
        by_id = {e.id: e for e in self.master.jobs + self.master.projects}
        jobs = []
        given = {str(x.get("id")): x.get("bullets") for x in data.get("experience", []) if isinstance(x, dict)}
        for e in self.master.jobs:
            jobs.append((e, self._bullets(e, given.get(e.id))))
        projects = []
        for x in data.get("projects", [])[:3]:
            e = by_id.get(str(x.get("id"))) if isinstance(x, dict) else None
            if e and e.kind == "project" and all(e is not p for p, _ in projects):
                projects.append((e, self._bullets(e, x.get("bullets"))))
        for e in self.master.projects:  # must-keep projects, then fill up to 3 in master order
            must = any(m.lower()[:25] in e.header.lower() for m in self.must_keep)
            if (must or len(projects) < 3) and all(e is not p for p, _ in projects):
                projects.append((e, self._bullets(e, [])))
        return Draft(self._summary(data.get("summary", ""), job), self._skills(data.get("skills")),
                     jobs, projects[:3] if len(projects) > 3 else projects, list(self.master.certifications))

    # ---- layout
    def _fits(self, d: Draft) -> dict:
        return check_resume_fit(d.render(self.master), self.linkedin_url)

    def _minimum(self, entry: Entry) -> int:
        return min(len(entry.bullets), MIN_BULLETS[entry.kind])

    def _carries(self, d: Draft, entry: Entry, k: int) -> int:
        """How many job terms only bullet k of this entry puts on the page (cut those last)."""
        bullets = next(b for e, b in d.jobs + d.projects if e.id == entry.id)
        text = d.render(self.master)
        mine = ats.terms_in(bullets[k][1]) & self.wanted
        rest = text.replace(bullets[k][1], "")
        return len({term for term in mine if term not in ats.terms_in(rest)})

    def _cut_one(self, d: Draft, floor: dict) -> bool:
        """Drop one bullet from an entry above its floor: the most over it, projects first, and
        never the only carrier of a job term if another bullet can go instead."""
        options = []
        for entry, _ in d.projects + d.jobs:
            n = d.keep[entry.id]
            low = min(len(entry.bullets), floor[entry.kind])
            if n > low:
                options.append((self._carries(d, entry, n - 1), -(n - low), entry.kind != "project", entry.id))
        if not options:
            return False
        d.keep[min(options)[3]] -= 1
        return True

    def fit(self, d: Draft) -> Draft:
        """Drop the least relevant bullets until the page fits (see FULL_BULLETS for the order)."""
        d.keep = {e.id: len(b) for e, b in d.jobs + d.projects}
        while self._fits(d)["status"] == "too_long":
            if self._cut_one(d, FULL_BULLETS):
                continue
            if len(d.certifications) > FULL_CERTIFICATIONS:
                d.certifications.pop()
            elif len(d.projects) > 2:
                d.projects.pop()
            elif not self._cut_one(d, MIN_BULLETS):
                break
        return d

    def fix_short_lines(self, d: Draft) -> Draft:
        """Resize bullets whose last printed line holds only a few words; checked like any rewrite."""
        measure = _TextMeasure()
        for entry, bullets in d.jobs + d.projects:
            for k in range(d.keep.get(entry.id, len(bullets))):
                sid, text = bullets[k]
                line = f"- {text}"
                rows = measure.printed(line)
                if len(rows) < 2 or measure.last_share(line) >= 0.45:
                    continue
                per = measure.chars_per_line(line)
                low, high = int(per * (len(rows) - 1) * 0.86), int(per * (len(rows) - 1) * 0.97)
                for _ in range(2):
                    reply = self._ask(RESIZE_PROMPT.format(low=low, high=high, now=len(text), bullet=text,
                                                           sources="\n".join(entry.bullets)), "low")
                    reply = reply.strip().lstrip("-* ").strip()
                    new = self._checked(entry, sid, reply)
                    cand = f"- {new}"
                    if (new == reply and len(measure.printed(cand)) < len(rows) and measure.last_share(cand) >= 0.6
                            and new.endswith(".") and not re.search(r"[&<>]|,\S", new)):
                        bullets[k] = (sid, new)
                        break
        return d

    def grow(self, d: Draft, target: float = 99.0) -> Draft:
        """Put back the next most relevant bullet anywhere it still fits (after resizing freed space)."""
        changed = True
        while changed:
            changed = False
            for entry, bullets in sorted(d.jobs + d.projects, key=lambda eb: d.keep[eb[0].id]):
                if d.keep[entry.id] < len(bullets):
                    d.keep[entry.id] += 1
                    fit = self._fits(d)
                    if fit["status"] == "too_long" or fit["fill_percent"] > target:
                        d.keep[entry.id] -= 1
                    else:
                        changed = True
        return d

    # ---- public
    def tailor_text(self, job_description: str) -> str:
        d = self.fit(self.draft(job_description))
        d = self.grow(self.fix_short_lines(d))
        d = self.fit(d)
        return d.render(self.master)

    def tailor(self, job_description: str, company: str, role: str) -> str:
        text = self.tailor_text(job_description)
        self.ats_report = ats.score(text, job_description, self.base)
        fit = check_resume_fit(text, self.linkedin_url)
        logger.info(f"{self.ats_report.summary()}; page {fit['fill_percent']}% full")
        self.warnings = [self.ats_report.summary()]
        if claims := ats.unsupported_claims(text, self.base):
            self.warnings.append("not in your master resume, check the wording: " + ", ".join(claims))
        missing = [m for m in self.must_keep if m.upper()[:30] not in text.upper()]
        self.warnings += [f"missing: {m}" for m in missing]
        for w in self.warnings:
            logger.warning(f"Check before sending: {w}")
        return format_to_pdf(text, str(self.output_dir / safe_filename(company, role)), linkedin_url=self.linkedin_url)
