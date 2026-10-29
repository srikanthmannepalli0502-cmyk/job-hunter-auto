"""Is a posting still open? Closure wording in its text ("this position has been filled"), and a
check of the posting's own page for the jobs you shortlisted from sources that aren't company
boards (Hacker News, Remotive, Adzuna), which never tell us when a job is taken down. Board jobs
already close when their board stops listing them. The phrases follow career-ops' liveness check (MIT).
"""

import re
import unicodedata

import httpx

CLOSED = re.compile(
    r"\b(?:this|the)\s+(?:position|role|job|opening|posting|requisition)\s+(?:has\s+been|was|is)\s+"
    r"(?:filled|closed|cancel+ed|removed|expired)"
    r"|\bno\s+longer\s+(?:accepting|taking|available|open|being\s+accepted)"
    r"|\b(?:position|role|job|posting)\s+(?:is\s+)?no\s+longer\s+(?:available|open|active)"
    r"|\bapplications?\s+(?:are|is)\s+(?:now\s+)?closed\b"
    r"|\bthis\s+job\s+(?:has\s+)?expired\b"
    r"|\b(?:we|we've|we\s+have)\s+(?:filled|closed)\s+(?:this|the)\s+(?:position|role)"
    # amazon.jobs: "Sorry, the job you're looking for isn't available."
    r"|\b(?:job|position|role|posting|opening|page)\s+(?:you're|you\s+are)\s+looking\s+for\s+(?:isn't|is\s+not|is\s+no\s+longer)\s+available",
    re.I)

# Sites we never fetch: their terms forbid automated access, or the page needs a sign-in.
NEVER_FETCH = re.compile(r"linkedin\.com|indeed\.com|glassdoor\.|ziprecruiter\.|google\.com", re.I)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").replace("’", "'")
    return re.sub(r"\s+", " ", "".join(c for c in text if not unicodedata.combining(c)))


def closed_phrase(text: str) -> str:
    """The closure wording in a posting's text, or ''."""
    m = CLOSED.search(_plain(text))
    return m.group(0) if m else ""


def check_page(client: httpx.Client, url: str) -> tuple[str, str]:
    """('closed' | 'open' | 'unknown', why) for a posting's own page."""
    if not url or NEVER_FETCH.search(url):
        return "unknown", "not checked"
    try:
        r = client.get(url, follow_redirects=True)
    except httpx.HTTPError as e:
        return "unknown", f"request failed ({type(e).__name__})"
    if r.status_code in (404, 410):
        return "closed", f"page gone (HTTP {r.status_code})"
    if r.status_code != 200:
        return "unknown", f"HTTP {r.status_code}"
    phrase = closed_phrase(re.sub(r"<[^>]+>", " ", r.text))
    if phrase:
        return "closed", f'the page says "{phrase}"'
    if len(re.sub(r"<[^>]+>|\s+", "", r.text)) < 300:
        return "unknown", "page too short to tell"
    return "open", "page is up"
