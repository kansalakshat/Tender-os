"""The dashboard's figures, trend and upcoming deadlines. Saving, bidding and
saved searches themselves are tested in test_tenderleo.py and test_wishlist.py."""
from datetime import date, timedelta

from fastapi.testclient import TestClient

from app import web
from app.api import app, get_db


def test_change_badge():
    assert web._change(5, 0) == ("+5 new", "up")
    assert web._change(0, 0) == ("No change", "flat")
    assert web._change(15, 10) == ("+50%", "up")
    assert web._change(5, 10) == ("-50%", "down")
    assert web._change(3, 3) == ("+0%", "flat")


def test_per_day_and_weekly():
    start = date(2026, 11, 1)
    pairs = [(start, 2), (start + timedelta(days=3), 1), (start - timedelta(days=1), 9), (None, 4)]
    days = web._per_day(pairs, start, 7)
    assert days == [2, 0, 0, 1, 0, 0, 0]          # outside the window and undated are left out
    assert web._weekly(list(range(14))) == [21, 70]


def test_dashboard_shows_figures_trend_and_deadlines(session_factory):
    app.dependency_overrides[get_db] = lambda: session_factory()
    try:
        c = TestClient(app)
        c.post("/auth/signup", json={"email": "dx@example.invalid", "password": "coconut-husk-2026"})
        html = c.get("/dashboard", params={"days": 7}).text
        for label in ("Matched Tenders", "Saved Tenders", "Bids in Progress", "Upcoming Deadlines",
                      "Tender Opportunities Trend"):
            assert label in html, label
        assert '<option value="7" selected>Last 7 Days</option>' in html
        # an unknown range falls back to 30 days rather than failing
        assert '<option value="30" selected>' in c.get("/dashboard", params={"days": 5}).text
        # the sections the rest of the page relies on stay, in this order
        assert html.index("id=searches") < html.index("id=bidding") < html.index("id=saved")
    finally:
        app.dependency_overrides.clear()
