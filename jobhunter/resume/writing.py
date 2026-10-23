"""A writing score for resume bullets, from fixed rules (no model): weak openers, first person,
long bullets, no number, and the same opening verb used again and again in one entry. It only
points things out; nothing is rewritten. The rules follow Resume-Matcher's writing check (Apache-2.0).
"""

import re
from collections import Counter

WEAK_OPENERS = ("responsible for", "helped", "worked on", "assisted", "involved in", "tasked with",
                "duties included", "participated in", "in charge of", "was part of")
FIRST_PERSON = re.compile(r"\b(i|me|my|we|our|us)\b", re.I)
MAX_WORDS = 35
PENALTY = {"weak": 40, "first_person": 10, "long": 10, "repeated": 10, "no_number": 5}


def _entries(resume_text: str) -> list[list[str]]:
    """Bullets grouped by the job or project they belong to (a run of '- ' lines)."""
    groups, current = [], []
    for line in resume_text.splitlines():
        if line.strip().startswith("- "):
            current.append(line.strip()[2:].strip())
        elif current:
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return groups


def _opener(bullet: str) -> str:
    words = re.findall(r"[A-Za-z]+", bullet)
    return words[0].lower() if words else ""


def bullet_issues(bullet: str, repeated: bool = False) -> list[str]:
    low = bullet.lower()
    issues = []
    if low.startswith(WEAK_OPENERS):
        issues.append("weak")
    if FIRST_PERSON.search(bullet):
        issues.append("first_person")
    if len(bullet.split()) > MAX_WORDS:
        issues.append("long")
    if repeated:
        issues.append("repeated")
    if not re.search(r"\d", bullet):
        issues.append("no_number")
    return issues


LABELS = {"weak": "starts with a weak verb", "first_person": "uses I/my/we",
          "long": f"over {MAX_WORDS} words", "repeated": "same opening verb as 2+ other bullets here",
          "no_number": "no number"}


def report(resume_text: str) -> tuple[int, list[tuple[str, list[str]]]]:
    """(score 0-100, [(bullet, issues)] for bullets with any issue other than only 'no number')."""
    scores, flagged = [], []
    for group in _entries(resume_text):
        counts = Counter(_opener(b) for b in group)
        for b in group:
            issues = bullet_issues(b, repeated=counts[_opener(b)] >= 3)
            scores.append(max(0, 100 - sum(PENALTY[i] for i in issues)))
            if set(issues) - {"no_number"}:
                flagged.append((b, issues))
    return (round(sum(scores) / len(scores)) if scores else 100), flagged


def summary(resume_text: str, show: int = 3) -> str:
    score, flagged = report(resume_text)
    text = f"Writing score {score}/100"
    if flagged:
        text += f"; {len(flagged)} bullet(s) to check: " + "; ".join(
            f"\"{b[:50]}...\" ({', '.join(LABELS[i] for i in issues)})" for b, issues in flagged[:show])
    return text
