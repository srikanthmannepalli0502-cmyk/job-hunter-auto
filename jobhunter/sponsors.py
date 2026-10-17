"""H-1B sponsorship signal for job postings.

Matches a posting's company name to the sponsor table exported by the H-1B Tech Jobs
Tracker (data/h1b_sponsors.csv: certified H-1B LCAs from the U.S. Department of Labor
plus USCIS new-hire decisions) and scores how actively the company sponsors the roles
you target, from 0 (no H-1B filings found) to 100.

Score parts:
- Filings in your target roles: 0-40 (log scale, 100+ filings = full points)
- All H-1B filings:             0-15 (log scale, 1,000+ = full points)
- Consistency:                  0-15 (months with filings / months covered)
- New hires in the latest year: 0-15 (new H-1B petitions, 20+ = full points)
- USCIS approval:               0-15 (new-hire denial rate; 10%+ scores 0; under 20 decisions = half)
"""

import bisect
import csv
import math
import re
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

from jobhunter.config import SponsorshipConfig
from jobhunter.models import JobListing

# Same rules as the tracker's normalize_employer dbt macro, so keys line up.
_SUFFIXES = re.compile(
    r"(\s+(L\s?L\s?C|L\s?L\s?P|L\s?P|INC|INCORPORATED|CORP|CORPORATION|CO|COMPANY|LTD|LIMITED|PC|PLLC|P\s?C))+\s*$"
)
# Prefix matches on very short keys ("AI", "THE") would match unrelated companies.
_MIN_PREFIX_CHARS = 4
# One-word names too generic to expand by prefix ("Capital" must not become Capital One).
_GENERIC_WORDS = {
    "ADVANCED", "AMERICAN", "CAPITAL", "DATA", "DIGITAL", "FIRST", "GENERAL", "GLOBAL",
    "INTERNATIONAL", "NATIONAL", "NEW", "SMART", "UNITED", "US", "USA",
}
# Names job boards use that don't share a prefix with the legal name on the filings.
ABBREVIATIONS = {
    "EY": "ERNST AND YOUNG",
    "PWC": "PRICEWATERHOUSECOOPERS",
    "BOFA": "BANK OF AMERICA",
    "BANK OF AMERICA": "BANK OF AMERICA N A",
    "JP MORGAN": "JPMORGAN CHASE",
    "J P MORGAN": "JPMORGAN CHASE",
    "JPMORGAN": "JPMORGAN CHASE",
}


def normalize_name(name: str) -> str:
    key = re.sub(r"[^A-Z0-9]+", " ", (name or "").upper().replace("&", " AND "))
    key = _SUFFIXES.sub("", key)
    key = re.sub(r"\s+", " ", key).strip()
    return key.removeprefix("THE ").strip()


@dataclass(frozen=True)
class Sponsor:
    key: str
    employer: str
    filings_all: int
    role_filings: dict[str, int]
    active_months: int
    months_covered: int
    new_hires_latest_fy: int
    new_hire_share: float
    median_wage_data: float | None
    uscis_new_approved: int
    uscis_new_denied: int
    possibly_cap_exempt: bool

    def filings_in(self, roles: list[str]) -> int:
        return sum(self.role_filings.get(r, 0) for r in roles)

    @property
    def new_hire_denial_rate(self) -> float | None:
        decisions = self.uscis_new_approved + self.uscis_new_denied
        return self.uscis_new_denied / decisions if decisions >= 20 else None


@dataclass(frozen=True)
class SponsorMatch:
    sponsor: Sponsor
    exact: bool  # False when matched by company-name prefix (e.g. "Capital One" -> "Capital One Services")


def _int(value: str) -> int:
    return int(float(value)) if value else 0


class SponsorDB:
    def __init__(self, sponsors: list[Sponsor]):
        # Re-normalize so keys match posting names exactly (this also drops a leading "THE").
        self.by_key: dict[str, Sponsor] = {}
        for s in sponsors:
            key = normalize_name(s.key)
            if key and (key not in self.by_key or s.filings_all > self.by_key[key].filings_all):
                self.by_key[key] = s
        self.keys = sorted(self.by_key)
        # "FIDELITY TECHNOLOGY GROUP LLC D B A FIDELITY INVESTMENTS" is also "FIDELITY INVESTMENTS".
        self.aliases: dict[str, list[Sponsor]] = {}
        for key, sponsor in self.by_key.items():
            if " D B A " in key:
                alias = normalize_name(key.split(" D B A ", 1)[1])
                self.aliases.setdefault(alias, []).append(sponsor)
        self.compact = sorted((k.replace(" ", ""), k) for k in self.by_key)

    @classmethod
    def load(cls, path: str | Path) -> "SponsorDB | None":
        path = Path(path)
        if not path.exists():
            logger.warning(f"{path} not found; jobs will not get an H-1B sponsorship score.")
            return None
        sponsors = []
        with path.open(encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f):
                roles = {k.removeprefix("filings_"): _int(v) for k, v in row.items()
                         if k.startswith("filings_") and k != "filings_all"}
                sponsors.append(Sponsor(
                    key=row["employer_key"],
                    employer=row["employer"],
                    filings_all=_int(row["filings_all"]),
                    role_filings=roles,
                    active_months=_int(row["active_months"]),
                    months_covered=max(1, _int(row["months_covered"])),
                    new_hires_latest_fy=_int(row["new_hires_latest_fy"]),
                    new_hire_share=float(row["new_hire_share"] or 0),
                    median_wage_data=float(row["median_wage_data"]) if row["median_wage_data"] else None,
                    uscis_new_approved=_int(row["uscis_new_approved"]),
                    uscis_new_denied=_int(row["uscis_new_denied"]),
                    possibly_cap_exempt=row["possibly_cap_exempt"].lower() == "true",
                ))
        logger.info(f"Loaded {len(sponsors):,} H-1B sponsors from {path}")
        return cls(sponsors)

    def _with_prefix(self, prefix: str) -> list[Sponsor]:
        """Sponsors whose key is `prefix` followed by more words (subsidiaries, legal variants)."""
        start = prefix + " "
        i = bisect.bisect_left(self.keys, start)
        found = []
        while i < len(self.keys) and self.keys[i].startswith(start):
            found.append(self.by_key[self.keys[i]])
            i += 1
        return found

    def _with_compact_prefix(self, compact: str) -> list[Sponsor]:
        i = bisect.bisect_left(self.compact, (compact, ""))
        found = []
        while i < len(self.compact) and self.compact[i][0].startswith(compact):
            found.append(self.by_key[self.compact[i][1]])
            i += 1
        return found

    def match(self, company: str) -> SponsorMatch | None:
        """Best sponsor record for a posting's company name, or None.

        Candidates, in order (the first step that finds any wins):
        1. The exact key, "d/b/a" names, well-known abbreviations, and longer keys starting
           with the name ("Capital One" -> "Capital One Services, LLC"; "Deloitte" ->
           "Deloitte Consulting LLP").
        2. The name with trailing words dropped, keeping at least two words
           ("Capital One, N.A." -> "Capital One").
        3. The name without spaces ("Walmart" -> "WAL MART ASSOCIATES"), for 6+ letters.
        Within a step the employer with the most filings wins, so a small entity sharing
        the name can't outweigh the main one. Non-exact matches name the matched employer
        in the note so you can check them.
        """
        key = normalize_name(company)
        if not key:
            return None

        candidates = list(self.aliases.get(key, []))
        if key in self.by_key:
            candidates.append(self.by_key[key])
        for target in (ABBREVIATIONS.get(key), key):
            if target and len(target) >= _MIN_PREFIX_CHARS and target not in _GENERIC_WORDS:
                candidates += self._with_prefix(target)
                if target in self.by_key:
                    candidates.append(self.by_key[target])
        if candidates:
            best = max(candidates, key=lambda s: s.filings_all)
            return SponsorMatch(best, exact=best is self.by_key.get(key))

        words = key.split()[:-1]
        while len(words) >= 2:
            prefix = " ".join(words)
            candidates = self._with_prefix(prefix) + ([self.by_key[prefix]] if prefix in self.by_key else [])
            if candidates:
                return SponsorMatch(max(candidates, key=lambda s: s.filings_all), exact=False)
            words.pop()

        compact = key.replace(" ", "")
        if len(compact) >= 6 and key not in _GENERIC_WORDS:
            candidates = self._with_compact_prefix(compact)
            if candidates:
                return SponsorMatch(max(candidates, key=lambda s: s.filings_all), exact=False)
        return None


def _log_share(value: int, full_at: int) -> float:
    return min(1.0, math.log1p(value) / math.log1p(full_at))


def sponsor_score(sponsor: Sponsor, roles: list[str]) -> float:
    role_filings = sponsor.filings_in(roles)
    denial = sponsor.new_hire_denial_rate
    approval = 0.5 if denial is None else max(0.0, 1 - denial / 0.10)
    points = (
        40 * _log_share(role_filings, 100)
        + 15 * _log_share(sponsor.filings_all, 1000)
        + 15 * min(1.0, sponsor.active_months / sponsor.months_covered)
        + 15 * min(1.0, sponsor.new_hires_latest_fy / 20)
        + 15 * approval
    )
    return round(points, 1)


def describe(match: SponsorMatch | None, roles: list[str]) -> str:
    if match is None:
        return "no H-1B filings found"
    s = match.sponsor
    role_filings = s.filings_in(roles)
    parts = [f"{role_filings:,} H-1B in your roles" if role_filings else "H-1B sponsor, none in your roles",
             f"{s.filings_all:,} total",
             f"{s.active_months}/{s.months_covered} months"]
    if s.new_hire_denial_rate is not None:
        parts.append(f"{s.new_hire_denial_rate:.1%} new-hire denial")
    if s.possibly_cap_exempt:
        parts.append("possibly cap-exempt")
    note = ", ".join(parts)
    return note if match.exact else f"{note} (matched {s.employer})"


# Phrases in a posting that rule out (or very likely rule out) visa sponsorship for that role,
# even at a company that sponsors others.
_NO_SPONSORSHIP = re.compile(
    r"(?:not|unable to|cannot|can't|won't|will not|do not|does not|don't|is not able to)\s+"
    r"(?:\w+\s+){0,3}sponsor"
    r"|without\s+(?:the\s+need\s+for\s+)?(?:current\s+or\s+future\s+|future\s+)?(?:visa\s+|employer\s+)?sponsorship"
    r"|sponsorship\s+(?:is\s+)?(?:not\s+available|unavailable|not\s+offered|not\s+provided)"
    r"|no\s+(?:visa\s+)?sponsorship"
    r"|u\.?s\.?\s+citizens?\s+only|must\s+be\s+(?:a\s+)?u\.?s\.?\s+citizen|u\.?s\.?\s+citizenship\s+(?:is\s+)?required"
    r"|(?:active|current|ability to obtain)\s+(?:\w+\s+){0,2}(?:security\s+)?clearance",
    re.I,
)
# Down-weight instead of hiding: the phrase can be boilerplate or about a different visa type.
WARNING_PENALTY = 0.4


def sponsorship_warning(description: str) -> str:
    """The first phrase suggesting this posting won't sponsor a visa, or ''."""
    m = _NO_SPONSORSHIP.search(description or "")
    if not m:
        return ""
    start, end = max(0, m.start() - 40), min(len(description), m.end() + 40)
    return "..." + re.sub(r"\s+", " ", description[start:end]).strip() + "..."


def apply_sponsorship(jobs: list[JobListing], db: SponsorDB | None, cfg: SponsorshipConfig) -> list[JobListing]:
    """Set sponsor_score, sponsor_note, sponsorship_warning and overall_score; return jobs best first.

    overall = fit score x (1 - weight) + sponsor score x weight. Without sponsor data the
    overall score is the fit score. A posting that says it won't sponsor keeps only
    WARNING_PENALTY of its overall score.
    """
    weight = max(0.0, min(1.0, cfg.weight / 100))
    for job in jobs:
        job.sponsorship_warning = sponsorship_warning(job.description)
        if db is None:
            job.overall_score = job.match_score
        else:
            match = db.match(job.company)
            job.sponsor_score = sponsor_score(match.sponsor, cfg.roles) if match else 0.0
            job.sponsor_note = describe(match, cfg.roles)
            job.overall_score = round(job.match_score * (1 - weight) + job.sponsor_score * weight, 1)
        if job.sponsorship_warning:
            job.overall_score = round(job.overall_score * WARNING_PENALTY, 1)
    if db is not None and cfg.hide_non_sponsors:
        jobs = [j for j in jobs if j.sponsor_score > 0]
    return sorted(jobs, key=lambda j: j.overall_score, reverse=True)
