"""The Tenderleo changes: state on every card, the browse filters behind it, the
new pages, and the dashboard (bids in progress, saved searches)."""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import app, get_db
from app.models import Source, Tender
from app.web import clean_query


@pytest.fixture
def client(session_factory):
    soon = date.today() + timedelta(days=9)
    with session_factory() as db:
        cppp = Source(name="CPPP", base_url="https://eprocure.gov.in", license="p",
                      robots_txt_allowed=True)
        raj = Source(name="Rajasthan eProcurement", base_url="https://eproc.rajasthan.gov.in",
                     license="p", robots_txt_allowed=True)
        db.add_all([cppp, raj])
        db.flush()
        db.add_all([
            Tender(source_id=cppp.id, external_ref="a", deadline=soon, source_url="u1",
                   title="Construction of civil works at 132 KV GSS Katrathal, Sikar",
                   organization="Rajasthan Rajya Vidyut Prasaran Nigam Limited"),
            Tender(source_id=raj.id, external_ref="b", deadline=soon, source_url="u2",
                   title="Supply of office chairs", organization="PWD Division"),
            Tender(source_id=cppp.id, external_ref="c", deadline=soon, source_url="u3",
                   title="Hiring of vehicles", organization="District Office Gwalior"),
            Tender(source_id=cppp.id, external_ref="d", deadline=soon, source_url="u4",
                   title="Annual maintenance of lifts", organization="Ministry of Railways"),
        ])
        db.commit()
    app.dependency_overrides[get_db] = lambda: session_factory()
    yield TestClient(app)
    app.dependency_overrides.clear()


def refs(client, **params):
    return sorted(t["external_ref"] for t in client.get("/tenders", params=params).json()["items"])


def test_state_city_and_category_filters(client):
    # by name in the buyer, and every tender from the state's own portal
    assert refs(client, state="Rajasthan") == ["a", "b"]
    # a district places a tender in its state
    assert refs(client, state="Madhya Pradesh") == ["c"]
    assert refs(client, state="Rajasthan", city="Sikar") == ["a"]
    assert refs(client, sector="vehicle_hire") == ["c"]
    assert refs(client, sector="civil_construction", state="Rajasthan") == ["a"]
    for bad in ({"state": "Atlantis"}, {"city": "Gotham"}, {"sector": "x"}):
        assert client.get("/tenders", params=bad).status_code == 422


def test_every_card_leads_with_its_state(client):
    items = {t["external_ref"]: t for t in client.get("/tenders").json()["items"]}
    assert (items["a"]["state"], items["a"]["city"]) == ("Rajasthan", "Sikar")
    assert items["b"]["state"] == "Rajasthan"          # from the portal alone
    assert items["d"]["state"] == ""                   # a national buyer names none
    home_signed_out = client.get("/buyer", params={"name": "District Office Gwalior"}).text
    assert "<span class=st>Madhya Pradesh</span>" in home_signed_out


@pytest.mark.parametrize("path", ["/", "/about", "/buyers", "/buyers?q=rail", "/browse"])
def test_new_pages_render_without_em_dashes(client, path):
    r = client.get(path)
    assert r.status_code == 200
    assert "—" not in r.text and "&mdash;" not in r.text


def test_home_has_the_new_sections(client):
    html = client.get("/").text
    for text in ("Stop searching for tenders", "How Tenderleo works", "Find tenders by company",
                 "Find tenders by state", "Why Tenderleo", "AI-powered tender analysis",
                 "/browse?state=Rajasthan"):
        assert text in html, text
    assert "Closing soonest" not in html


def test_tender_page(client):
    tid = client.get("/tenders", params={"q": "Katrathal"}).json()["items"][0]["id"]
    html = client.get(f"/t/{tid}").text
    assert "Ready to fill this tender" in html and "https://wa.me/?text=" in html
    assert 'href="/browse?state=Rajasthan"' in html          # the breadcrumb


def test_dashboard_bids_and_saved_searches(client, session_factory):
    assert client.get("/dashboard", follow_redirects=False).status_code == 303
    assert client.post("/participate/1").status_code == 401
    assert client.post("/searches", json={"query": "state=Kerala"}).status_code == 401

    client.post("/auth/signup", json={"email": "dash@example.invalid", "password": "coconut-husk-2026"})
    assert client.post("/participate/1").json() == {"participating": True}
    assert client.post("/wishlist/2").status_code == 200
    r = client.post("/searches", json={"name": "Kerala civil",
                                       "query": "?state=Kerala&sector=civil_construction&evil=1"})
    assert r.status_code == 201 and r.json()["query"] == "state=Kerala&sector=civil_construction"
    assert client.post("/searches", json={"query": "evil=1"}).status_code == 422

    html = client.get("/dashboard").text
    bidding = html[html.index('id=bidding'):html.index('id=saved')]
    assert "Katrathal" in bidding and "office chairs" not in bidding
    assert "office chairs" in html[html.index('id=saved'):]
    assert 'href="/browse?state=Kerala&amp;sector=civil_construction"' in html

    # Someone else cannot delete it.
    sid = r.json()["id"]
    other = TestClient(app)
    other.post("/auth/signup", json={"email": "other@example.invalid", "password": "coconut-husk-2026"})
    assert other.delete(f"/searches/{sid}").status_code == 404
    assert client.delete(f"/searches/{sid}").status_code == 200

    assert client.delete("/participate/1").json() == {"participating": False}
    assert "Katrathal" not in client.get("/dashboard").text.split("id=saved")[0].split("id=bidding")[1]


def test_clean_query_keeps_only_browse_parameters():
    assert clean_query("q=pump&x=<script>&state=Goa") == "q=pump&state=Goa"
    assert clean_query("") == ""


def test_logos_match_every_spelling_of_a_buyer():
    from app.web import logo_url
    for name in ("Bharat Heavy Electricals Limited bhel", "Bharat Heavy Electricals Limited"):
        assert "/static/logos/bhel.png" in logo_url(name)
    assert "ntpc.png" in logo_url("Ntpc Limited") and "ntpc.png" in logo_url("NTPC Limited")
    # the longer phrase wins: a Coal India subsidiary, not Eastern Coalfields
    assert "coalindia.png" in logo_url("South Eastern Coalfields Limited")
    assert "ecl.png" not in logo_url("South Eastern Coalfields Limited")
    assert "ecl.jpg" in logo_url("Eastern Coalfields Limited")
    assert "railways.jpg" in logo_url("Western Railway")
    # whole words only
    assert logo_url("Gailpur Panchayat") == ""
    # the Army's units and every ministry are covered too, by the longest phrase
    assert "indianarmy.png" in logo_url("E-IN-C BRANCH - MILITARY ENGINEER SERVICES")
    assert "emblem.png" in logo_url("Ministry of Defence")
    assert "indianrailways.jpg" in logo_url("Ministry of Railways")
    # every file the index names exists
    import json
    from tests.conftest import STATIC
    index = json.loads((STATIC / "logos" / "logos.json").read_text(encoding="utf-8"))
    assert all((STATIC / "logos" / f).is_file() for f in index)


def test_filter_and_card_read_the_buyers_office_alike(client, session_factory):
    with session_factory() as db:
        src = db.query(Source).filter_by(name="CPPP").one()
        db.add(Tender(source_id=src.id, external_ref="e", deadline=date.today() + timedelta(days=5),
                      source_url="u5", title="Office chairs", organization="Department of Health",
                      raw_payload={"office": "CMHO Office Jabalpur"}))
        db.add(Tender(source_id=src.id, external_ref="f", deadline=date.today() + timedelta(days=5),
                      source_url="u6", title="Shed at Krishi Upaj Mandi", organization="Mandi Board"))
        db.commit()
    assert refs(client, state="Madhya Pradesh", city="Jabalpur") == ["e"]
    item = next(t for t in client.get("/tenders").json()["items"] if t["external_ref"] == "e")
    assert (item["state"], item["city"]) == ("Madhya Pradesh", "Jabalpur")
    # "Mandi" here is a market, not the Himachal district
    assert "f" not in refs(client, state="Himachal Pradesh")
