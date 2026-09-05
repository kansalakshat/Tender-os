"""Regression tests for findings from the 6 Sep 2026 hardening pass.

Each test names the hole it closes. If one of these ever fails, the vulnerability
is back, not merely the test.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import security
from app.api import app
from app.auth import email_problem
from app.db import get_db
from app.models import Source, Tender

SOON = date.today() + timedelta(days=30)
PROFILE = {"name": "Acme", "sectors": ["electrical_power"], "min_lead_days": 7}
CREDS = {"email": "ops@acme.invalid", "password": "correct horse battery"}


@pytest.fixture
def client(session_factory):
    with session_factory() as db:
        src = Source(name="MP", base_url="https://mptenders.gov.in")
        db.add(src)
        db.flush()
        db.add(Tender(
            source_id=src.id, external_ref="a", title="Supply of 11 kv transformer",
            organization="MPPKVVCL", deadline=SOON, status="open",
            source_url="https://mptenders.gov.in/a",
        ))
        db.commit()
    app.dependency_overrides[get_db] = lambda: session_factory()
    yield TestClient(app)
    app.dependency_overrides.clear()


# ---- stored XSS: markup in an email address reached innerHTML ----

@pytest.mark.parametrize("bad", [
    "<img src=x onerror=alert(1)>@evil.com",
    '"><script>alert(1)</script>@evil.com',
    "javascript:alert(1)@evil.com",
    "a@b",                      # no TLD
    "no-at-sign",
])
def test_markup_and_malformed_addresses_are_refused(bad):
    assert email_problem(bad) is not None


def test_signup_refuses_an_address_containing_markup(client):
    r = client.post("/auth/signup",
                    json={"email": "<img src=x onerror=alert(1)>@evil.com",
                          "password": "hackerpassword"})
    assert r.status_code == 400


def test_ordinary_addresses_still_work():
    assert email_problem("ops@acme.invalid") is None
    assert email_problem("first.last+tag@sub.example.co.uk") is None


# ---- IDOR: profiles were readable by anyone who guessed a sequential id ----

def test_saving_a_profile_requires_an_account(client):
    assert client.post("/companies", json=PROFILE).status_code == 401


def test_another_account_sees_404_not_403(client):
    client.post("/auth/signup", json=CREDS)
    mine = client.post("/companies", json=PROFILE).json()["id"]
    client.post("/auth/logout")
    client.post("/auth/signup", json={"email": "rival@x.invalid",
                                      "password": "another long pw"})
    for path in (f"/companies/{mine}", f"/companies/{mine}/matches", f"/c/{mine}"):
        assert client.get(path).status_code == 404, path


def test_preview_endpoint_persists_nothing(client, session_factory):
    from app.models import Company

    assert client.post("/match", json=PROFILE).status_code == 200
    with session_factory() as db:
        assert db.query(Company).count() == 0


# ---- brute force ----

def test_repeated_failed_logins_are_throttled(client):
    client.post("/auth/signup", json=CREDS)
    client.post("/auth/logout")
    codes = [
        client.post("/auth/login", json={**CREDS, "password": f"wrong guess {i}"}).status_code
        for i in range(security.LOGIN_LIMIT + 3)
    ]
    assert 429 in codes, "login is not rate limited"
    assert codes.count(401) <= security.LOGIN_LIMIT


def test_a_successful_login_clears_the_failure_budget(client):
    client.post("/auth/signup", json=CREDS)
    client.post("/auth/logout")
    for _ in range(security.LOGIN_LIMIT - 1):
        client.post("/auth/login", json={**CREDS, "password": "wrong one here"})
    assert client.post("/auth/login", json=CREDS).status_code == 200
    # Budget reset, so a later mistype is not instantly a lockout.
    assert client.post("/auth/login",
                       json={**CREDS, "password": "wrong one here"}).status_code == 401


def test_signup_is_rate_limited(client):
    codes = [
        client.post("/auth/signup", json={"email": f"user{i}@acme.invalid",
                                          "password": "a long enough password"}).status_code
        for i in range(security.SIGNUP_LIMIT + 2)
    ]
    assert 429 in codes


def test_verification_mail_is_rate_limited(client):
    client.post("/auth/signup", json=CREDS)
    codes = [client.post("/auth/resend-verification").status_code
             for _ in range(security.MAIL_LIMIT + 2)]
    assert 429 in codes, "an account could be used as a mail cannon"


def test_rate_limit_table_cannot_grow_without_bound():
    """Rotating keys must not be a way to exhaust memory."""
    security.reset()
    for i in range(security._MAX_BUCKETS + 50):
        security.hit(f"k{i}", 5, 60)
    assert len(security._BUCKETS) <= security._MAX_BUCKETS + 1


# ---- response hardening ----

def test_security_headers_are_present(client):
    h = client.get("/").headers
    assert h["X-Content-Type-Options"] == "nosniff"
    assert h["X-Frame-Options"] == "DENY"
    assert h["Referrer-Policy"] == "no-referrer"
    assert "Permissions-Policy" in h


def test_csp_forbids_inline_script_and_framing(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "'unsafe-inline'" not in csp, "a nonce policy must not fall back to this"
    assert "'unsafe-eval'" not in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "nonce-" in csp


def test_every_inline_script_carries_the_nonce(client):
    """A script without the nonce would be blocked by our own CSP -- that would
    be a broken page, and the fix must never be to weaken the policy."""
    import re

    body = client.get("/signup").text
    csp = client.get("/signup").headers["Content-Security-Policy"]
    assert re.search(r"nonce-([\w-]+)", csp)
    assert "<script>" not in body, "un-nonced <script> would be blocked"
    assert "<style>" not in body
    assert body.count("<script nonce=") >= 1


def test_pages_carry_no_inline_style_attributes(client):
    """style="..." is governed by style-src and a nonce cannot whitelist it."""
    for path in ("/", "/login", "/signup", "/browse"):
        assert 'style="' not in client.get(path).text, path


def test_session_cookie_is_httponly_and_lax(client):
    r = client.post("/auth/signup", json=CREDS)
    raw = r.headers["set-cookie"]
    assert "HttpOnly" in raw
    assert "SameSite=lax" in raw.replace("samesite", "SameSite")


def test_secure_flag_follows_the_public_url(monkeypatch):
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://tenders.example.com")
    assert security.https_only() is True
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    assert security.https_only() is False


# ---- OAuth state ----

def test_forged_oauth_state_is_refused(client, monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "id")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "secret")
    client.get("/auth/google")                       # sets a genuine cookie
    from app import oauth

    # Correctly signed, but not the one paired with this browser's cookie.
    r = client.get(f"/auth/google/callback?code=x&state={oauth.make_state()}",
                   follow_redirects=False)
    assert r.headers["location"] == "/login?error=state"


# ---- deployment robustness: a bad DATABASE_URL must not kill the whole app ----

def test_provider_postgres_urls_are_normalised():
    """Neon/Supabase/Railway hand out postgres:// or postgresql://; SQLAlchemy
    needs the driver named. Pasting the provider's string must just work."""
    from app.db import normalize_database_url as n

    assert n("postgres://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert n("postgresql://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert n("postgresql+psycopg://u:p@h/db") == "postgresql+psycopg://u:p@h/db"
    assert n("  postgres://u:p@h/db  ") == "postgresql+psycopg://u:p@h/db"
    assert n("sqlite://") == "sqlite://"          # left alone
    assert n(None) == "" and n("") == ""


def test_empty_database_url_fails_loudly_not_silently(monkeypatch):
    """An empty value means someone tried to configure it and failed. Falling
    back to localhost would hide that behind a connection timeout."""
    import importlib

    import app.db as db

    monkeypatch.setenv("DATABASE_URL", "")
    reloaded = importlib.reload(db)
    try:
        assert reloaded.DATABASE_URL == ""
        with pytest.raises(RuntimeError, match="set but empty"):
            reloaded.get_engine()
    finally:
        monkeypatch.setenv("DATABASE_URL", "sqlite://")
        importlib.reload(db)


def test_health_does_not_need_a_database(client):
    """The whole app used to die at import if DATABASE_URL was unparseable, so
    even /health 500'd. It touches no database and must never depend on one."""
    import inspect

    from app import api

    assert "db" not in inspect.signature(api.health).parameters
    assert client.get("/health").json() == {"status": "ok"}


def test_serverless_does_not_pool_connections(monkeypatch):
    """A frozen function instance holding an idle connection exhausts a small
    Postgres; concurrent requests then queue and the site appears to hang."""
    from sqlalchemy.pool import NullPool

    import app.db as db

    monkeypatch.setenv("VERCEL", "1")
    assert db._pool_options() == {"poolclass": NullPool}
    monkeypatch.delenv("VERCEL")
    assert db._pool_options() == {"pool_pre_ping": True}
