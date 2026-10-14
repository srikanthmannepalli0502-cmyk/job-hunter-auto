import csv

import pytest

from jobhunter.config import SponsorshipConfig
from jobhunter.sponsors import SponsorDB, apply_sponsorship, describe, normalize_name, sponsor_score
from jobhunter.tracker import Tracker
from tests.conftest import make_job

COLUMNS = ["employer_key", "employer", "filings_all", "filings_analyst", "filings_data_science",
           "filings_ai", "filings_data_engineering", "filings_software", "active_months",
           "new_hires_latest_fy", "new_hire_share", "median_wage_data", "last_filing",
           "naics_sector", "months_covered", "uscis_new_approved", "uscis_new_denied",
           "possibly_cap_exempt"]


def row(key, employer, filings_all, analyst=0, ds=0, ai=0, months=21, new_hires=0,
        approved=0, denied=0, cap_exempt=False):
    return [key, employer, filings_all, analyst, ds, ai, 0, 0, months, new_hires, 0.3, 120000,
            "2026-06-30", "52", 21, approved, denied, str(cap_exempt).lower()]


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "sponsors.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerow(row("JPMORGAN CHASE AND", "JPMorgan Chase & Co.", 6208, 367, 155, 95, new_hires=790,
                       approved=553, denied=2))
        w.writerow(row("CAPITAL ONE SERVICES", "Capital One Services, LLC", 2000, 300, 100, new_hires=100,
                       approved=130, denied=0))
        w.writerow(row("CAPITAL ONE NATIONAL ASSOCIATION", "Capital One, National Association", 1500, 200))
        w.writerow(row("CAPITAL RESEARCH GROUP", "Capital Research Group", 4))
        w.writerow(row("WAL MART ASSOCIATES", "WAL-MART ASSOCIATES, INC.", 5000, 600))
        w.writerow(row("META PLATFORMS", "Meta Platforms, Inc", 4000, 50, 900))
        w.writerow(row("JOHNS HOPKINS UNIVERSITY", "Johns Hopkins University", 803, 47, cap_exempt=True))
        w.writerow(row("TINY SHOP", "Tiny Shop LLC", 3, months=1))
    return SponsorDB.load(path)


@pytest.mark.parametrize("raw, key", [
    ("JPMorgan Chase & Co.", "JPMORGAN CHASE AND"),
    ("Amazon.com Services, LLC", "AMAZON COM SERVICES"),
    ("The Home Depot, Inc.", "HOME DEPOT"),
    ("  meta platforms inc ", "META PLATFORMS"),
])
def test_normalize_matches_warehouse_keys(raw, key):
    assert normalize_name(raw) == key


def test_exact_match(db):
    m = db.match("JPMorgan Chase & Co.")
    assert m.exact and m.sponsor.employer == "JPMorgan Chase & Co."


def test_brand_name_picks_largest_entity(db):
    m = db.match("Capital One")
    assert not m.exact and m.sponsor.key == "CAPITAL ONE SERVICES"


def test_trailing_words_are_dropped(db):
    assert db.match("Capital One, N.A.").sponsor.key == "CAPITAL ONE SERVICES"


def test_does_not_fall_back_to_a_single_shared_word(db):
    # "Capital Rx" must not become Capital One just because both start with "Capital".
    assert db.match("Capital Rx") is None


def test_spacing_variants(db):
    assert db.match("Walmart").sponsor.key == "WAL MART ASSOCIATES"
    assert db.match("Meta").sponsor.key == "META PLATFORMS"


def test_unknown_company(db):
    assert db.match("Some Startup Nobody Heard Of") is None
    assert db.match("") is None


def test_score_ranks_active_sponsors_higher(db):
    roles = ["analyst", "data_science", "ai"]
    big = sponsor_score(db.match("JPMorgan Chase & Co.").sponsor, roles)
    tiny = sponsor_score(db.match("Tiny Shop").sponsor, roles)
    assert 0 <= tiny < big <= 100


def test_role_filter_changes_score(db):
    meta = db.match("Meta").sponsor
    assert sponsor_score(meta, ["data_science"]) > sponsor_score(meta, ["analyst"])


def test_describe(db):
    note = describe(db.match("Johns Hopkins University"), ["analyst"])
    assert "47 H-1B in your roles" in note and "possibly cap-exempt" in note
    assert "matched" in describe(db.match("Capital One"), ["analyst"])
    assert describe(None, ["analyst"]) == "no H-1B filings found"


def test_apply_sponsorship_ranks_and_filters(db):
    jobs = [make_job(company="Some Startup", description="sql python"),
            make_job(company="Capital One", description="sql python")]
    for j in jobs:
        j.match_score = 80
    ranked = apply_sponsorship(jobs, db, SponsorshipConfig(weight=35))
    assert ranked[0].company == "Capital One"
    assert ranked[1].sponsor_score == 0 and ranked[1].overall_score == pytest.approx(80 * 0.65)

    only = apply_sponsorship(jobs, db, SponsorshipConfig(hide_non_sponsors=True))
    assert [j.company for j in only] == ["Capital One"]


def test_without_data_overall_equals_fit():
    job = make_job()
    job.match_score = 70
    assert apply_sponsorship([job], None, SponsorshipConfig())[0].overall_score == 70


def test_missing_file_returns_none(tmp_path):
    assert SponsorDB.load(tmp_path / "nope.csv") is None


def test_tracker_adds_new_columns_to_old_database(tmp_path):
    import sqlite3

    path = tmp_path / "old.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE jobs (id INTEGER PRIMARY KEY, dedupe_key VARCHAR(512) UNIQUE, title VARCHAR(255),"
                " company VARCHAR(255), location VARCHAR(255), job_url TEXT, source VARCHAR(50),"
                " description TEXT, salary_min FLOAT, salary_max FLOAT, match_score FLOAT,"
                " date_posted DATETIME, date_fetched DATETIME)")
    con.execute("INSERT INTO jobs (dedupe_key, title, company, source, match_score, date_fetched)"
                " VALUES ('a|b', 'Data Analyst', 'Acme', 'test', 55, '2026-10-01')")
    con.commit()
    con.close()

    tracker = Tracker(f"sqlite:///{path}")
    [job] = tracker.top_jobs()
    assert job.overall_score == 55 and job.sponsor_score == 0


@pytest.fixture
def family_db(tmp_path):
    path = tmp_path / "family.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        w.writerow(row("DELOITTE", "Deloitte", 22))
        w.writerow(row("DELOITTE CONSULTING", "Deloitte Consulting LLP", 6856, 300))
        w.writerow(row("ERNST AND YOUNG U S", "Ernst & Young U.S. LLP", 15165, 400))
        w.writerow(row("FIDELITY TECHNOLOGY GROUP LLC D B A FIDELITY INVESTMENTS",
                       "Fidelity Technology Group, LLC d/b/a Fidelity Investments", 3876, 400))
        w.writerow(row("FIDELITY INVESTMENTS INSTITUTIONAL OPERATIONS", "Fidelity Investments Inst. Ops", 53, 12))
        w.writerow(row("THE HOME DEPOT", "The Home Depot", 10))
    return SponsorDB.load(path)


def test_main_entity_beats_small_exact_namesake(family_db):
    m = family_db.match("Deloitte")
    assert m.sponsor.key == "DELOITTE CONSULTING" and not m.exact


def test_abbreviation_and_dba(family_db):
    assert family_db.match("EY").sponsor.key == "ERNST AND YOUNG U S"
    assert family_db.match("Fidelity Investments").sponsor.filings_all == 3876


def test_leading_the_is_ignored(family_db):
    m = family_db.match("Home Depot")
    assert m.exact and m.sponsor.employer == "The Home Depot"


def test_generic_single_word_is_not_expanded(db):
    assert db.match("Capital") is None
