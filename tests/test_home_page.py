"""The signed-out home page's buyer directory and the /buyers state filter it
links to. The sections themselves are covered in test_tenderleo.py."""
from datetime import date, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import web
from app.api import app, get_db
from app.models import Source, Tender

TODAY = date(2026, 11, 18)


@pytest.fixture
def db_with_buyers(session_factory):
    soon = date.today() + timedelta(days=9)
    with session_factory() as db:
        cppp = Source(name="CPPP", base_url="https://eprocure.gov.in", license="p",
                      robots_txt_allowed=True)
        db.add(cppp)
        db.flush()
        rows = [
            ("g", "District Office Gwalior", "Hiring of vehicles", soon, None),
            ("p", "PWD Division Jaipur Rajasthan", "Road repair", soon, None),
            # one closed tender: counted in activity, not in "open"
            ("x", "Ministry of Railways", "Old works", date.today() - timedelta(days=3), TODAY),
        ]
        # weekly buckets: today and six days back share the last bar; seven
        # days back is the bar before; 55 days back is the first bar; 56 is out
        for i, back in enumerate((0, 6, 7, 55, 56)):
            rows.append((f"r{i}", "Ministry of Railways", f"Track work {i}", soon,
                         TODAY - timedelta(days=back)))
        for ref, org, title, deadline, pub in rows:
            db.add(Tender(source_id=cppp.id, external_ref=ref, source_url="u" + ref,
                          title=title, organization=org, deadline=deadline,
                          published_date=pub))
        db.commit()
    return session_factory


def test_buyer_activity_is_eight_seven_day_weeks(db_with_buyers):
    with db_with_buyers() as db:
        cards = web._buyer_cards(db, TODAY)
    rail = next(c for c in cards if c["name"] == "Ministry of Railways")
    assert rail["weeks"] == [1, 0, 0, 0, 0, 0, 1, 3]
    assert rail["open"] == 5                 # the closed tender is not open
    assert rail["latest"] == TODAY


def test_buyers_page_filters_by_state(db_with_buyers):
    app.dependency_overrides[get_db] = lambda: db_with_buyers()
    try:
        client = TestClient(app)
        mp = client.get("/buyers", params={"state": "Madhya Pradesh"}).text
        assert "District Office Gwalior" in mp and "PWD Division Jaipur" not in mp
        assert "in Madhya Pradesh" in mp and "<option selected>Madhya Pradesh</option>" in mp
        # an unknown state is ignored, not an empty page
        every = client.get("/buyers", params={"state": "Atlantis"}).text
        assert "District Office Gwalior" in every and "PWD Division Jaipur" in every
    finally:
        app.dependency_overrides.clear()


def test_home_buyer_panel_and_demo_button(db_with_buyers, monkeypatch):
    app.dependency_overrides[get_db] = lambda: db_with_buyers()
    try:
        html = TestClient(app).get("/").text
    finally:
        app.dependency_overrides.clear()
    # the panel the script updates is the one the page draws
    js = (Path(__file__).parent.parent / "static" / "js" / "home.js").read_text(encoding="utf-8")
    assert "querySelector('.bxside')" in js and "class=bxside" in html
    assert "Open Tenders: <i id=bxopen>" in html
    # the buyer picker sends a state, and works without JavaScript
    assert "<select id=bpick name=state>" in html
    assert html.count("<noscript><button") == 2
    # no video configured: the hero offers to book a demo, not to watch one
    hero = html[html.index("class=hxctas"):html.index("class=hxart")]
    assert "Book a Demo" in hero and "Watch Demo" not in hero
