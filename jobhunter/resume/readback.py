"""Read a finished resume PDF back as an ATS parser would, and check that it reads like the text it
was built from: the name, email and phone, every section heading in order, and the start of every
bullet. Text a parser can't read back (garbled characters, a second page, lost lines) is text the
employer never sees. The idea follows Resume-Matcher's ATS round-trip check (Apache-2.0).
"""

import re
from pathlib import Path

from jobhunter.resume.pdf import sanitize_text

HEADINGS = {"SUMMARY", "EDUCATION", "SKILLS", "EXPERIENCE", "PROJECTS", "CERTIFICATIONS",
            "PROFESSIONAL EXPERIENCE", "WORK EXPERIENCE", "TECHNICAL SKILLS", "ACHIEVEMENTS", "AWARDS"}


def _flat(text: str) -> str:
    """Lower-case words only, so line wraps, spacing and punctuation don't matter."""
    return " ".join(re.findall(r"[a-z0-9@.+]+", text.lower().replace("­", "")))


def pdf_text(path: str | Path) -> tuple[str, int]:
    try:
        import pymupdf
    except ImportError:  # older PyMuPDF
        import fitz as pymupdf

    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc), doc.page_count


def read_back(pdf_path: str | Path, resume_text: str) -> list[str]:
    """Problems reading the PDF back (empty when it reads back whole and in order)."""
    try:
        text, pages = pdf_text(pdf_path)
    except Exception as e:  # noqa: BLE001 - a PDF we can't open is itself the finding
        return [f"PDF read-back failed: {e}"]
    source = sanitize_text(resume_text)
    flat = _flat(text)
    problems = []
    if pages > 1:
        problems.append(f"the PDF has {pages} pages; many ATS read only the first")
    if "�" in text or re.search(r"\(cid:\d+\)", text):
        problems.append("the PDF text has unreadable characters")

    lines = [line.strip() for line in source.splitlines() if line.strip()]
    if lines and _flat(lines[0]) not in flat:
        problems.append("your name doesn't read back")
    email = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", source)
    if email and email.group(0).lower().rstrip(".") not in flat:
        problems.append("your email doesn't read back")
    phone = re.search(r"\+?\d[\d\s().-]{8,}\d", source)
    if phone and re.sub(r"\D", "", phone.group(0)) not in re.sub(r"\D", "", text):
        problems.append("your phone number doesn't read back")

    headings = [line.upper() for line in lines if line.upper() in HEADINGS]
    # A heading reads back as a line of its own; the word alone ("skills") also appears in sentences.
    pdf_lines = [line.strip().upper() for line in text.splitlines()]
    at = [pdf_lines.index(h) if h in pdf_lines else -1 for h in headings]
    lost = [h for h, i in zip(headings, at) if i < 0]
    if lost:
        problems.append("headings that don't read back: " + ", ".join(lost))
    elif at != sorted(at):
        problems.append("the sections read back in a different order")

    bullets = [line[2:] for line in lines if line.startswith("- ")]
    starts = [" ".join(_flat(b).split()[:6]) for b in bullets]
    missing = [b for b, s in zip(bullets, starts) if s and s not in flat]
    if missing:
        problems.append(f"{len(missing)} of {len(bullets)} bullets don't read back, e.g. \"{missing[0][:60]}\"")
    return problems


def summary(pdf_path: str | Path, resume_text: str) -> str:
    problems = read_back(pdf_path, resume_text)
    if not problems:
        return "PDF read-back: name, contact, sections and every bullet read back in order"
    return "PDF read-back: " + "; ".join(problems)
