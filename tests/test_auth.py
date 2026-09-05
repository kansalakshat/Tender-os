from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.auth import (
    SESSION_COOKIE,
    hash_password,
    make_session,
    read_session,
    verify_password,
)
from app.db import get_db
from app.models import Source, Tender

SOON = date.today() + timedelta(days=30)
PROFILE = {
    "name": "Acme Electricals",
    "sectors": ["electrical_power"],
    "keywords": ["transformer"],
    "min_lead_days": 7,
}
CREDS = {"email": "ops@acme.invalid", "password": "correct horse battery"}


@pytest.fixture
def client(session_factory):
    with session_factory() as db:
        src = Source(name="MP eProcurement", base_url="https://mptenders.gov.in")
        db.add(src)
        db.flush()
        db.add(
            Tender(
                source_id=src.id, external_ref="a",
                title="Supply of 11 kv transformer at Mandsaur",
                organization="MPPKVVCL", deadline=SOON, status="open",
                source_url="https://mptenders.gov.in/a",
            )
        )
        db.commit()
    app.dependency_overrides[get_db] = lambda: session_factory()
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---- password hashing ----

def test_password_round_trip_and_rejection():
    stored = hash_password("correct horse battery")
    assert verify_password("correct horse battery", stored)
    assert not verify_password("Correct horse battery", stored)


def test_hash_is_salted_and_stores_no_plaintext():
    a, b = hash_password("same pw here"), hash_password("same pw here")
    assert a != b                      # distinct salts
    assert "same pw here" not in a


def test_garbage_hash_is_rejected_not_crashed():
    for junk in ("", "nonsense", "scrypt$bad", "md5$1$2$3$4$5"):
        assert verify_password("x", junk) is False


# ---- session cookie ----

def test_session_round_trip():
    assert read_session(make_session(7)) == 7


def test_tampered_or_missing_session_is_rejected():
    token = make_session(7)
    assert read_session(token[:-3] + "aaa") is None      # broken signature
    assert read_session("9." + token.split(".", 1)[1]) is None  # swapped user id
    assert read_session(None) is None
    assert read_session("nonsense") is None


def test_expired_session_is_rejected(monkeypatch):
    import app.auth as auth

    token = make_session(7)
    monkeypatch.setattr(auth, "SESSION_DAYS", -1)
    assert read_session(auth.make_session(7)) is None
    assert read_session(token) == 7      # the unexpired one still works


# ---- endpoints ----

def test_signup_logs_you_in(client):
    r = client.post("/auth/signup", json=CREDS)
    assert r.status_code == 201
    assert SESSION_COOKIE in r.cookies
    assert client.get("/me").json()["user"]["email"] == "ops@acme.invalid"


def test_short_password_is_refused(client):
    r = client.post("/auth/signup", json={**CREDS, "password": "short"})
    assert r.status_code == 400
    assert "10 characters" in r.json()["detail"]


def test_duplicate_email_is_refused_case_insensitively(client):
    client.post("/auth/signup", json=CREDS)
    r = client.post("/auth/signup", json={**CREDS, "email": "OPS@Acme.invalid"})
    assert r.status_code == 400
    assert "already registered" in r.json()["detail"]


def test_login_logout_cycle(client):
    client.post("/auth/signup", json=CREDS)
    client.post("/auth/logout")
    assert client.get("/me").json()["user"] is None
    assert client.post("/auth/login", json=CREDS).status_code == 200
    assert client.get("/me").json()["user"]["email"] == "ops@acme.invalid"


def test_wrong_password_gives_nothing_away(client):
    client.post("/auth/signup", json=CREDS)
    bad_pw = client.post("/auth/login", json={**CREDS, "password": "wrong pw here"})
    no_user = client.post(
        "/auth/login", json={"email": "nobody@acme.invalid", "password": "wrong pw here"}
    )
    assert bad_pw.status_code == no_user.status_code == 401
    # Identical wording, or the response tells an attacker which emails exist.
    assert bad_pw.json()["detail"] == no_user.json()["detail"]


# ---- profiles belong to accounts ----

def test_saving_while_signed_in_overwrites_instead_of_piling_up(client):
    client.post("/auth/signup", json=CREDS)
    first = client.post("/companies", json=PROFILE).json()
    second = client.post("/companies", json={**PROFILE, "name": "Acme Renamed"}).json()
    assert first["id"] == second["id"]
    assert client.get("/me").json()["company"]["name"] == "Acme Renamed"


def test_another_account_cannot_read_my_profile(client):
    client.post("/auth/signup", json=CREDS)
    mine = client.post("/companies", json=PROFILE).json()["id"]
    client.post("/auth/logout")
    client.post("/auth/signup", json={"email": "rival@x.invalid", "password": "another long pw"})
    # 404 not 403: confirming the id exists would itself leak something.
    assert client.get(f"/companies/{mine}").status_code == 404
    assert client.get(f"/companies/{mine}/matches").status_code == 404
    assert client.put(f"/companies/{mine}", json=PROFILE).status_code == 404


def test_signed_out_visitor_cannot_save_a_profile(client):
    """Profiles used to be creatable without an account and readable by anyone
    who guessed the id. Ids are sequential, so walking /companies/1,2,3 handed
    out company names and contact email addresses."""
    refused = client.post("/companies", json=PROFILE)
    assert refused.status_code == 401
    assert "match" in refused.json()["detail"]


def test_signed_out_preview_works_and_stores_nothing(client, session_factory):
    """The no-account path is POST /match, which scores without persisting."""
    from app.models import Company

    scored = client.post("/match", json=PROFILE)
    assert scored.status_code == 200
    assert scored.json(), "expected at least one match"
    with session_factory() as db:
        assert db.query(Company).count() == 0


def test_profiles_are_not_enumerable_by_id(client):
    """Walking the id space must reveal nothing, whether or not a row exists."""
    client.post("/auth/signup", json=CREDS)
    mine = client.post("/companies", json=PROFILE).json()["id"]
    client.post("/auth/logout")
    for probe in (mine, mine + 1, mine + 2, 1, 2, 3):
        assert client.get(f"/companies/{probe}").status_code == 404
        assert client.get(f"/c/{probe}").status_code == 404


def test_request_body_cannot_claim_ownership(client):
    """user_id is not a CompanyIn field, so it must be ignored, not honoured."""
    client.post("/auth/signup", json=CREDS)
    victim = client.get("/me").json()["user"]["id"]
    client.post("/auth/logout")
    client.post("/auth/signup", json={"email": "other@acme.invalid",
                                      "password": "another long pw"})
    created = client.post("/companies", json={**PROFILE, "user_id": victim})
    assert created.status_code == 201
    # It belongs to the caller, not to the id smuggled in the body.
    assert client.get("/me").json()["company"]["id"] == created.json()["id"]
    client.post("/auth/logout")
    client.post("/auth/login", json=CREDS)
    assert client.get(f"/companies/{created.json()['id']}").status_code == 404


# ---- the questions are asked once, at signup ----

def test_signup_page_asks_the_questionnaire_too(client):
    """The whole point: account details and company details on one page."""
    html = client.get("/signup").text
    assert "name=email" in html and "name=password" in html
    assert "type=checkbox name=sectors" in html
    # Contact email is not asked twice -- the sign-in address is reused.
    assert "name=contact_email" not in html


def test_returning_user_lands_on_matches_not_the_form(client):
    client.post("/auth/signup", json=CREDS)
    company_id = client.post("/companies", json=PROFILE).json()["id"]
    client.post("/auth/logout")
    client.post("/auth/login", json=CREDS)

    landing = client.get("/", follow_redirects=False)
    assert landing.status_code == 303
    assert landing.headers["location"] == f"/c/{company_id}"
    # And the questionnaire is nowhere on the page they actually land on.
    assert "type=checkbox name=sectors" not in client.get("/").text


def test_account_without_answers_is_sent_to_finish_them(client):
    """If the profile save failed during signup, ask for it -- do not show an
    empty matches page."""
    client.post("/auth/signup", json=CREDS)
    landing = client.get("/", follow_redirects=False)
    assert landing.status_code == 303
    assert landing.headers["location"] == "/profile"


def test_signed_out_visitor_can_still_preview_without_an_account(client):
    html = client.get("/profile").text
    assert "type=checkbox name=sectors" in html
    assert "without an account" in html


def test_edit_page_is_reachable_and_prefills_from_me(client):
    client.post("/auth/signup", json=CREDS)
    client.post("/companies", json=PROFILE)
    html = client.get("/profile").text
    assert "Edit your answers" in html
    assert "fetch('/me')" in html
