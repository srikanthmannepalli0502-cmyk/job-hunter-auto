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


# ---- Azure AI Document Intelligence: the layout reader a commercial parser uses, not just text.
# It splits the page into paragraphs and marks section headings, so it also shows whether each
# heading is recognised as one and whether an entry's dates stay next to it.

LAYOUT_API = "/documentintelligence/documentModels/prebuilt-layout:analyze?api-version=2024-11-30"
_DATE_TAIL = re.compile(r"\s{2,}((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{4}.*)$")


def azure_layout(pdf_path: str | Path, endpoint: str, client=None, token: str = "", wait_s: float = 60) -> dict:
    """Document Intelligence's prebuilt-layout result for a PDF, signed in with Entra ID (no key)."""
    import time

    import httpx

    if not token:
        from azure.identity import DefaultAzureCredential

        token = DefaultAzureCredential().get_token("https://cognitiveservices.azure.com/.default").token
    headers = {"Authorization": f"Bearer {token}"}
    http = client or httpx.Client(timeout=60)
    r = http.post(endpoint.rstrip("/") + LAYOUT_API, content=Path(pdf_path).read_bytes(),
                  headers={**headers, "Content-Type": "application/pdf"})
    r.raise_for_status()
    poll, deadline = r.headers["operation-location"], time.monotonic() + wait_s
    while True:
        result = http.get(poll, headers=headers).json()
        if result.get("status") == "succeeded":
            return result.get("analyzeResult", {})
        if result.get("status") == "failed" or time.monotonic() > deadline:
            raise RuntimeError(f"layout analysis {result.get('status', 'timed out')}")
        time.sleep(1.5)


def layout_problems(layout: dict, resume_text: str) -> list[str]:
    """What the layout reader got wrong compared with the resume text (empty when it reads cleanly)."""
    source = sanitize_text(resume_text)
    lines = [line.strip() for line in source.splitlines() if line.strip()]
    paras = [(_flat(p.get("content", "")), p.get("role", "")) for p in layout.get("paragraphs", [])]
    problems = []
    if len(layout.get("pages", [])) > 1:
        problems.append(f"{len(layout['pages'])} pages")
    headings = [line.upper() for line in lines if line.upper() in HEADINGS]
    recognised = {text for text, role in paras if role in ("sectionHeading", "title")}
    missed = [h for h in headings if _flat(h) not in recognised]
    if missed:
        problems.append("not recognised as section headings: " + ", ".join(missed))

    def where(fragment: str) -> list[int]:
        return [i for i, (text, _) in enumerate(paras) if fragment and fragment in text]

    for line in lines:
        m = _DATE_TAIL.search(line)
        if not m:
            continue
        entry, dates = line[: m.start()], m.group(1)
        # The same words can appear elsewhere ("Syracuse University" in a job and the degree), so
        # pair this entry with its nearest copy of the dates.
        at_entry, at_dates = where(" ".join(_flat(entry).split()[:4])), where(_flat(dates))
        if not at_dates:
            problems.append(f"dates \"{dates.strip()}\" don't read back")
        elif at_entry and min(abs(d - e) for d in at_dates for e in at_entry) > 1:
            problems.append(f"\"{dates.strip()}\" reads away from \"{entry.strip()[:40]}\"")
    all_text = " ".join(text for text, _ in paras)
    bullets = [" ".join(_flat(line[2:]).split()[:6]) for line in lines if line.startswith("- ")]
    lost = [b for b in bullets if b and b not in all_text]
    if lost:
        problems.append(f"{len(lost)} of {len(bullets)} bullets not read")
    return problems


def azure_summary(pdf_path: str | Path, resume_text: str, endpoint: str, **kwargs) -> str:
    """One note for the job page. A failed call is reported, never raised: the resume is still fine."""
    try:
        layout = azure_layout(pdf_path, endpoint, **kwargs)
    except Exception as e:  # noqa: BLE001 - network, sign-in or service problems must not stop a resume
        return f"Azure layout read-back skipped ({type(e).__name__})"
    problems = layout_problems(layout, resume_text)
    sections = sum(1 for p in layout.get("paragraphs", []) if p.get("role") == "sectionHeading")
    if not problems:
        return (f"Azure layout read-back: {sections} section headings recognised; every entry's dates and "
                "bullets read in place")
    return "Azure layout read-back: " + "; ".join(problems)
