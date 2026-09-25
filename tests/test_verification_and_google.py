"""Email verification and Google sign-in.

Google is exercised by faking only the two network calls (`oauth.exchange_code`),
so the rest of the flow -- state check, cookie pairing, account linking, session
issue -- runs for real. Nothing here touches accounts.google.com.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import mailer, oauth
from app.api import app
from app.auth import (
    make_session,
    make_verification_token,
    unsign,
    user_from_verification_token,
)
from app.db import get_db
from app.models import Source, Tender, User

SOON = date.today() + timedelta(days=30)
CREDS = {"email": "ops@acme.invalid", "password": "correct horse battery"}


@pytest.fixture
def client(session_factory, monkeypatch, tmp_path):
    # Never write to the real outbox, and never try to reach a mail server.
    monkeypatch.setattr(mailer, "OUTBOX", tmp_path / "outbox.log")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    with session_factory() as db:
        src = Source(name="MP", base_url="https://mptenders.gov.in")
        db.add(src)
        db.flush()
        db.add(
            Tender(
                source_id=src.id, external_ref="a",
                title="Supply of 11 kv transformer", organization="MPPKVVCL",
                deadline=SOON, status="open", source_url="https://mptenders.gov.in/a",
            )
        )
        db.commit()
    app.dependency_overrides[get_db] = lambda: session_factory()
    yield TestClient(app)
    app.dependency_overrides.clear()


def _user(session_factory, email=CREDS["email"]):
    with session_factory() as db:
        return db.query(User).filter(User.email == email).one()


# ---- token separation ----

ONE = User(id=1, email="one@acme.invalid")


def test_a_session_cookie_is_not_a_verification_token(db):
    """Without a purpose in the signed payload, being logged in for 30 days would
    also be a standing licence to mark yourself verified."""
    db.add(User(email=ONE.email))
    db.commit()
    assert user_from_verification_token(db, make_session(ONE)) is None
    assert user_from_verification_token(db, make_verification_token(ONE)).id == 1
    assert unsign("oauth", make_verification_token(ONE)) is None


# ---- verification flow ----

def test_signup_is_unverified_and_writes_a_link_to_the_outbox(client, session_factory):
    r = client.post("/auth/signup", json=CREDS)
    assert r.status_code == 201
    assert r.json()["email_verified"] is False
    assert client.get("/me").json()["user"]["email_verified"] is False
    assert "/auth/verify?token=" in mailer.OUTBOX.read_text(encoding="utf-8")


def test_clicking_the_link_verifies_and_signs_in(client, session_factory):
    client.post("/auth/signup", json=CREDS)
    token = make_verification_token(_user(session_factory))
    client.post("/auth/logout")

    r = client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"] == "/?verify=ok"
    # Signed in by the click, so a link opened on a phone does not dead-end.
    me = client.get("/me").json()
    assert me["user"]["email_verified"] is True

    # Once only: a second click is not a standing 24-hour login.
    client.post("/auth/logout")
    client.get(f"/auth/verify?token={token}", follow_redirects=False)
    assert client.get("/me").json()["user"] is None


def test_forged_or_expired_link_is_refused(client, session_factory):
    client.post("/auth/signup", json=CREDS)
    me = _user(session_factory)
    stranger = User(id=me.id, email="stranger@x.invalid")    # same id, other account
    for bad in ("nonsense", make_session(me), make_verification_token(me)[:-3] + "aaa",
                make_verification_token(stranger)):
        r = client.get(f"/auth/verify?token={bad}", follow_redirects=False)
        assert r.headers["location"] == "/?verify=invalid"
    assert client.get("/me").json()["user"]["email_verified"] is False


def test_unverified_user_is_nagged_but_not_blocked(client):
    """The whole product is behind this; an unsent email must not lock anyone out."""
    client.post("/auth/signup", json=CREDS)
    created = client.post(
        "/companies",
        json={"name": "Acme", "sectors": ["electrical_power"], "min_lead_days": 7},
    )
    assert created.status_code == 201
    assert client.get(f"/c/{created.json()['id']}").status_code == 200


def test_resend_requires_a_session_and_reports_honestly(client):
    assert client.post("/auth/resend-verification").status_code == 401
    client.post("/auth/signup", json=CREDS)
    body = client.post("/auth/resend-verification").json()
    # No SMTP configured in tests, so it must NOT claim the mail was delivered.
    assert body["delivered"] is False


# ---- Google sign-in ----

def test_button_and_route_are_absent_until_configured(client, monkeypatch):
    monkeypatch.delenv("GOOGLE_CLIENT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLIENT_SECRET", raising=False)
    assert oauth.configured() is False
    assert "Continue with Google" not in client.get("/login").text
    assert client.get("/auth/google", follow_redirects=False).status_code == 404


@pytest.fixture
def google(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "test-secret")
    return monkeypatch


def test_button_appears_once_configured(client, google):
    assert "Continue with Google" in client.get("/login").text
    assert "Continue with Google" in client.get("/signup").text


def test_start_redirects_to_google_with_a_signed_state(client, google):
    r = client.get("/auth/google", follow_redirects=False)
    assert r.status_code == 303
    assert r.headers["location"].startswith(oauth.AUTH_ENDPOINT)
    assert "oauth_state" in r.cookies
    assert oauth.check_state(r.cookies["oauth_state"])


def test_callback_rejects_a_state_that_does_not_match_the_cookie(client, google):
    """A forged callback link carries a state but not the matching cookie."""
    client.get("/auth/google")                      # sets a real cookie
    r = client.get(
        f"/auth/google/callback?code=x&state={oauth.make_state()}",
        follow_redirects=False,
    )
    assert r.headers["location"] == "/login?error=state"


def test_callback_rejects_an_unsigned_state(client, google):
    r = client.get("/auth/google/callback?code=x&state=forged", follow_redirects=False)
    assert r.headers["location"] == "/login?error=state"


def test_cancelling_consent_is_not_an_error_page(client, google):
    r = client.get("/auth/google/callback?error=access_denied", follow_redirects=False)
    assert r.headers["location"] == "/login?error=cancelled"


def _complete_google(client, monkeypatch, sub, email, verified=True):
    """Drive the callback with the network call stubbed out."""
    start = client.get("/auth/google", follow_redirects=False)
    state = start.cookies["oauth_state"]
    monkeypatch.setattr(
        oauth, "exchange_code",
        lambda code, origin=None: {"sub": sub, "email": email, "email_verified": verified},
    )
    return client.get(
        f"/auth/google/callback?code=abc&state={state}", follow_redirects=False
    )


def test_google_creates_a_verified_account_and_signs_in(client, google):
    r = _complete_google(client, google, "sub-1", "New.User@gmail.com")
    assert r.status_code == 303 and r.headers["location"] == "/"
    me = client.get("/me").json()["user"]
    assert me["email"] == "new.user@gmail.com"      # normalised
    assert me["email_verified"] is True
    assert me["via_google"] is True


def test_google_links_to_an_existing_password_account(client, google, session_factory):
    client.post("/auth/signup", json=CREDS)
    existing_id = client.get("/me").json()["user"]["id"]
    client.post("/auth/logout")

    _complete_google(client, google, "sub-2", CREDS["email"])
    me = client.get("/me").json()["user"]
    assert me["id"] == existing_id       # linked, not a duplicate account
    assert me["email_verified"] is True
    # The address was never confirmed, so that password may be a squatter's.
    # Google proved ownership; the unproven password is gone.
    client.post("/auth/logout")
    assert client.post("/auth/login", json=CREDS).status_code == 401


def test_a_squatter_is_locked_out_when_the_owner_uses_google(client, google):
    """Pre-registration takeover: sign up with someone's address, keep the
    session, wait for them to arrive through Google."""
    squatter = TestClient(app)
    squatter.post("/auth/signup", json=CREDS)
    assert squatter.get("/me").json()["user"] is not None

    _complete_google(client, google, "sub-owner", CREDS["email"])
    assert client.get("/me").json()["user"]["email"] == CREDS["email"]
    assert squatter.get("/me").json()["user"] is None       # old session dead
    assert squatter.post("/auth/login", json=CREDS).status_code == 401


def test_a_verified_password_survives_linking_google(client, google, session_factory):
    client.post("/auth/signup", json=CREDS)
    with session_factory() as db:
        db.query(User).one().email_verified = True
        db.commit()
    client.post("/auth/logout")
    _complete_google(client, google, "sub-2b", CREDS["email"])
    client.post("/auth/logout")
    assert client.post("/auth/login", json=CREDS).status_code == 200


def test_unverified_google_email_cannot_take_over_an_account(client, google):
    """Linking on an address Google has not confirmed would be account takeover."""
    client.post("/auth/signup", json=CREDS)
    client.post("/auth/logout")
    r = _complete_google(client, google, "sub-3", CREDS["email"], verified=False)
    assert r.headers["location"] == "/login?error=google"
    assert client.get("/me").json()["user"] is None


def test_same_google_identity_signs_into_the_same_account(client, google):
    _complete_google(client, google, "sub-4", "repeat@gmail.com")
    first = client.get("/me").json()["user"]["id"]
    client.post("/auth/logout")
    _complete_google(client, google, "sub-4", "repeat@gmail.com")
    assert client.get("/me").json()["user"]["id"] == first


def test_google_only_account_cannot_be_password_guessed(client, google):
    _complete_google(client, google, "sub-5", "nopw@gmail.com")
    client.post("/auth/logout")
    r = client.post("/auth/login", json={"email": "nopw@gmail.com", "password": "anything at all"})
    assert r.status_code == 401
    assert "Google" in r.json()["detail"]


def test_google_returns_you_to_the_origin_you_started_on(monkeypatch):
    """Started on the local dashboard, PUBLIC_BASE_URL on Vercel: Google sent
    the visitor to Vercel, where the state cookie does not exist."""
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://tender-0s.vercel.app")
    cb = "/auth/google/callback"
    assert oauth.redirect_uri("http://127.0.0.1:8000") == "http://127.0.0.1:8000" + cb
    assert oauth.redirect_uri("http://localhost:8000") == "http://localhost:8000" + cb
    assert oauth.redirect_uri("https://tender-0s.vercel.app") == "https://tender-0s.vercel.app" + cb
    # A spoofed Host header cannot pick where Google sends the code.
    for evil in ("https://evil.example", "http://127.0.0.1.evil.example",
                 "https://127.0.0.1:8000", None):
        assert oauth.redirect_uri(evil) == "https://tender-0s.vercel.app" + cb
