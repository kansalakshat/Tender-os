from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.db import get_db
from app.models import Source, Tender

SOON = date.today() + timedelta(days=30)

PROFILE = {
    "name": "Acme Electricals",
    "contact_email": "ops@acme.invalid",
    "sectors": ["electrical_power"],
    "keywords": ["transformer"],
    "districts": ["Mandsaur"],
    "min_lead_days": 7,
}


@pytest.fixture
def client(session_factory):
    with session_factory() as db:
        src = Source(name="MP eProcurement", base_url="https://mptenders.gov.in")
        db.add(src)
        db.flush()
        db.add_all([
            Tender(source_id=src.id, external_ref="a",
                   title="Supply of 11 kv transformer at Mandsaur",
                   organization="MPPKVVCL", department="EE, Mandsaur STC Division",
                   deadline=SOON, status="open",
                   source_url="https://mptenders.gov.in/a"),
            Tender(source_id=src.id, external_ref="b", title="Tender for medicine items",
                   organization="Directorate of Health", deadline=SOON, status="open",
                   source_url="https://mptenders.gov.in/b"),
        ])
        db.commit()
    app.dependency_overrides[get_db] = lambda: session_factory()
    c = TestClient(app)
    # Saving a profile now requires an account: an unowned row could not be
    # protected by anything and holds a company name and contact address.
    c.post("/auth/signup", json={"email": "owner@acme.invalid",
                                 "password": "a good long password"})
    yield c
    app.dependency_overrides.clear()


def create(client, **overrides):
    return client.post("/companies", json={**PROFILE, **overrides})


def test_questionnaire_lists_sectors_and_districts(client):
    body = client.get("/questionnaire").json()
    assert {"key": "electrical_power",
            "label": "Electrical & power infrastructure"} in body["sectors"]
    assert "Mandsaur" in body["districts"]


def test_create_then_fetch_profile(client):
    created = create(client)
    assert created.status_code == 201
    company_id = created.json()["id"]

    got = client.get(f"/companies/{company_id}").json()
    assert got["name"] == "Acme Electricals"
    assert got["sectors"] == ["electrical_power"]


def test_matches_are_scored_ranked_and_explained(client):
    company_id = create(client).json()["id"]
    matches = client.get(f"/companies/{company_id}/matches").json()

    assert len(matches) == 1                     # the medicine tender is filtered out
    assert "transformer" in matches[0]["tender"]["title"]
    assert matches[0]["score"] > 0
    assert any("Electrical" in r for r in matches[0]["reasons"])


def test_match_preview_saves_nothing(client):
    preview = client.post("/match", json=PROFILE)
    assert preview.status_code == 200
    assert len(preview.json()) == 1
    assert client.get("/companies/1").status_code == 404


def test_update_profile_changes_matches(client):
    company_id = create(client).json()["id"]
    assert len(client.get(f"/companies/{company_id}/matches").json()) == 1

    client.put(f"/companies/{company_id}",
               json={**PROFILE, "sectors": ["medical_pharma"], "keywords": [],
                     "districts": []})
    matches = client.get(f"/companies/{company_id}/matches").json()
    assert len(matches) == 1
    assert "medicine" in matches[0]["tender"]["title"]


def test_unknown_sector_is_rejected_loudly(client):
    resp = create(client, sectors=["space_elevators"])
    assert resp.status_code == 422
    assert "space_elevators" in str(resp.json())


def test_profile_with_no_sector_or_keyword_is_rejected(client):
    assert create(client, sectors=[], keywords=[]).status_code == 422


def test_missing_company_is_404(client):
    assert client.get("/companies/999").status_code == 404
    assert client.get("/companies/999/matches").status_code == 404


# ---- the HTML pages ----

def test_questionnaire_renders_on_both_pages_that_ask_it(client):
    """Signup and edit share one markup string; if they ever diverge, so does the
    questionnaire a new user answers and the one they later edit."""
    for path in ("/signup", "/profile"):
        html = client.get(path).text
        assert "Electrical &amp; power infrastructure" in html, path
        assert html.count("type=checkbox name=sectors") == 11, path
        assert "name=states" in html and "name=exclude_buyers" in html, path


def test_signed_out_landing_offers_signup_not_the_form(client):
    client.post("/auth/logout")          # the fixture signs in; this test must not be
    html = client.get("/").text
    assert "/signup" in html
    assert "type=checkbox name=sectors" not in html


def test_results_page_lists_matches(client):
    company_id = create(client).json()["id"]
    html = client.get(f"/c/{company_id}").text
    assert "Acme Electricals" in html
    assert "transformer" in html
    assert "medicine" not in html


def test_results_page_escapes_scraped_titles(client, session_factory):
    """Titles come off a scraped page; they must not be able to inject markup."""
    with session_factory() as db:
        src = db.query(Source).first()
        db.add(Tender(source_id=src.id, external_ref="xss",
                      title="<script>alert(1)</script> transformer supply",
                      deadline=SOON, status="open",
                      source_url="https://mptenders.gov.in/x"))
        db.commit()
    company_id = create(client).json()["id"]
    html = client.get(f"/c/{company_id}").text
    assert "<script>alert(1)</script>" not in html
    assert "&lt;script&gt;" in html


def test_results_page_for_unknown_company_is_404(client):
    assert client.get("/c/999").status_code == 404
