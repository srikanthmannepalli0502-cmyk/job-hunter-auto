from jobhunter.sources.hackernews import parse_post

TITLES = ["data analyst", "data scientist", "machine learning", "analytics"]


def post(text, id_=1):
    return {"id": id_, "created_at": "2026-10-02T10:00:00Z", "text": text}


def test_role_from_first_line_in_the_us():
    job = parse_post(post("Middesk | Data Scientist ($175K) | NYC / SF | Full-time | VISA<p>We build ..."), TITLES)
    assert (job.company, job.title, job.source) == ("Middesk", "Data Scientist", "hackernews")
    assert job.job_url == "https://news.ycombinator.com/item?id=1"


def test_role_named_further_down():
    text = "Coast | Hybrid in New York City, NY | Full-time<p>Roles: Senior Data Analyst, Backend Engineer"
    assert parse_post(post(text), TITLES).title == "Senior Data Analyst"


def test_sentences_and_non_us_posts_are_skipped():
    assert parse_post(post("JustPark | Hybrid: Dallas, TX<p>You will improve our analytics pipelines."), TITLES) is None
    assert parse_post(post("G-Research | Machine Learning Engineer | London, UK"), TITLES) is None
    assert parse_post(post("Axmed | AI Engineer | REMOTE (preferred CET +-2)"), TITLES + ["ai engineer"]) is None
    assert parse_post(post("Kaavio | Data Scientist | REMOTE (US or Canada)"), TITLES) is not None
    assert parse_post(post("Snout https://snout.com/ | Data Analyst | Remote US"), TITLES).company == "Snout"
