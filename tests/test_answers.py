from jobhunter.answers import work_history

RESUME = """EXPERIENCE
Syracuse University iSchool | Student Technology Support | Syracuse, NY  Apr 2026 - Present
- Resolved 30+ tickets per week.

Tata Consultancy Services, TCS | System Engineer  Dec 2021 - Dec 2024
- Maintained transaction systems.
"""


def test_work_history_reads_the_location_when_the_header_has_one():
    a, b = work_history(RESUME)["jobs"]
    assert (a["company"], a["title"], a["location"], a["start"], a["current"]) == \
        ("Syracuse University iSchool", "Student Technology Support", "Syracuse, NY", "04/2026", True)
    assert (b["company"], b["title"], b["location"], b["start"], b["end"]) == \
        ("Tata Consultancy Services", "System Engineer", "", "12/2021", "12/2024")
