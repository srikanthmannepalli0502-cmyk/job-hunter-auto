"""Your LinkedIn connections at the companies you're looking at, from LinkedIn's own export.

LinkedIn lets you download your data (Settings > Data privacy > Get a copy of your data >
Connections); the export has a Connections.csv. Nothing here signs in to or reads LinkedIn: you
upload the file, and each job page shows who you know at that company. The idea follows
career-ops' linkedin-join (MIT).
"""

import csv
import io

from jobhunter.sponsors import normalize_name


def parse_connections_csv(text: str) -> list[dict]:
    """Rows of LinkedIn's Connections.csv: {name, company, position, url}. The export starts with a
    few "Notes:" lines before its header row."""
    lines = (text or "").lstrip("﻿").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith("First Name")), None)
    if start is None:
        raise ValueError("this doesn't look like LinkedIn's Connections.csv (no 'First Name' header)")
    out = []
    for row in csv.DictReader(io.StringIO("\n".join(lines[start:]))):
        name = " ".join(x for x in (row.get("First Name", "").strip(), row.get("Last Name", "").strip()) if x)
        company = (row.get("Company") or "").strip()
        if name and company:
            out.append({"name": name[:200], "company": company[:255],
                        "position": (row.get("Position") or "").strip()[:255], "url": (row.get("URL") or "").strip()[:500]})
    return out


def company_key(company: str) -> str:
    return normalize_name(company)


def same_company(a: str, b: str) -> bool:
    """'Capital One' and 'Capital One Services, LLC' are the same; 'Meta' and 'Metadata Inc' are not."""
    ka, kb = company_key(a), company_key(b)
    if not ka or not kb:
        return False
    short, long_ = sorted((ka, kb), key=len)
    return long_ == short or (long_.startswith(short + " ") and len(short) >= 4)
