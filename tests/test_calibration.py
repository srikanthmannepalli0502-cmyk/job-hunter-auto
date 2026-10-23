from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from jobhunter.calibration import band_of, calibrate, outcome

NOW = datetime(2026, 10, 22, tzinfo=timezone.utc)


def pair(status, days_ago, overall):
    return (SimpleNamespace(status=status, applied_date=NOW - timedelta(days=days_ago)),
            SimpleNamespace(overall_score=overall, match_score=overall, sponsor_score=0))


def test_outcomes():
    assert outcome("applied", NOW - timedelta(days=3), NOW) == "waiting"
    assert outcome("applied", (NOW - timedelta(days=30)).replace(tzinfo=None), NOW) == "none"
    assert outcome("rejected", NOW, NOW) == "none"
    assert outcome("offer", NOW, NOW) == "offer"
    assert outcome("withdrawn", NOW, NOW) is None


def test_bands():
    assert [band_of(s) for s in (95, 80, 79.9, 50, 12, None)] == ["80+", "80+", "65-79", "50-64", "under 50", "under 50"]


def test_rates_need_enough_results():
    apps = ([pair("interview", 40, 85)] * 2 + [pair("offer", 40, 90)] + [pair("rejected", 40, 82)] * 2
            + [pair("applied", 2, 88)] + [pair("withdrawn", 40, 88)] + [pair("rejected", 40, 55)] * 2)
    top, mid, low, bottom = calibrate(apps, now=NOW)
    assert (top.results, top.interviews, top.offers, top.waiting) == (5, 3, 1, 1)
    assert (top.rate(top.interviews), top.rate(top.offers)) == ("60%", "20%")
    assert low.results == 2 and low.rate(low.interviews) == "too few"
    assert mid.results == bottom.results == 0
