"""Regression tests for findings from the 6 Sep 2026 hardening pass.

Each test names the hole it closes. If one of these ever fails, the vulnerability
is back, not merely the test.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import security
from app.api import app
from app import auth
from app.auth import SESSION_COOKIE, email_problem
from app.db import get_db
from app.models import RateLimit, Source, Tender, User

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


def test_old_rate_limit_rows_are_swept(session_factory, monkeypatch):
    """Rotating keys must not be a way to fill the table forever."""
    monkeypatch.setattr(security.random, "random", lambda: 0.0)   # always sweep
    with session_factory() as db:
        db.add(RateLimit(key="old", window_start=0.0, count=3))
        db.commit()
        security.hit(db, "new", 5, 60)
        assert {r.key for r in db.query(RateLimit)} == {"new"}


def test_rate_limit_is_shared_between_instances(session_factory):
    """The hole this closes: counters in process memory were per serverless
    instance. Two sessions stand in for two instances on one database."""
    a, b = session_factory(), session_factory()
    assert all(security.hit(a, "login:x", 2, 60) for _ in range(2))
    assert security.hit(b, "login:x", 2, 60) is False
    a.close(); b.close()


def test_rate_limit_window_restarts(session_factory, monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(security.time, "time", lambda: clock[0])
    with session_factory() as db:
        assert security.hit(db, "k", 1, 60)
        assert not security.hit(db, "k", 1, 60)
        clock[0] += 61
        assert security.hit(db, "k", 1, 60)


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


def test_docs_pages_can_actually_load_their_assets(client):
    """/docs returned 200 and rendered blank: Swagger UI ships from a CDN and
    bootstraps itself with an inline <script> FastAPI generates, both of which the
    site CSP blocked. Assert every external asset the docs HTML references is
    permitted by the policy served with it."""
    import re

    for path, directive in (("/docs", "script-src"), ("/redoc", "script-src")):
        response = client.get(path)
        assert response.status_code == 200, path
        csp = response.headers["Content-Security-Policy"]
        assert "'unsafe-inline'" in csp.split(directive, 1)[1].split(";", 1)[0]
        for host in set(re.findall(r"https://[a-z0-9.-]+", response.text)):
            assert host in csp, f"{path} loads {host}, which its CSP blocks"


def test_docs_relaxation_does_not_leak_into_the_site(client):
    """The CDN exemption is scoped to the documentation paths. Every page that
    renders data stays on the strict nonce policy."""
    for path in ("/", "/login", "/signup", "/browse", "/openapi.json"):
        csp = client.get(path).headers["Content-Security-Policy"]
        assert "'unsafe-inline'" not in csp, path
        assert "cdn.jsdelivr.net" not in csp, path


def test_every_script_is_a_same_origin_file(client):
    """An inline script without the nonce would be blocked by our own CSP -- that
    would be a broken page, and the fix must never be to weaken the policy. So
    there are none: every script is a file under /static, allowed by 'self'."""
    import re
    from tests.conftest import page_scripts

    body = client.get("/signup").text
    csp = client.get("/signup").headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "'unsafe-inline'" not in csp
    assert "<style" not in body
    tags = re.findall(r"<script\b[^>]*>", body)
    assert tags and all('src="/static/' in t for t in tags)
    assert len(page_scripts(body)) == len(tags)


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
    assert "poolclass" not in db._pool_options()


def test_pool_pings_only_idle_connections(monkeypatch):
    """A ping is a round trip to us-east-1; a connection used a moment ago skips
    it, one idle past IDLE_PING_SECONDS gets it, and a failed ping is replaced."""
    from sqlalchemy import create_engine, text

    import app.db as db

    engine = create_engine("sqlite://")
    db._ping_when_idle(engine)
    pings = []
    monkeypatch.setattr(engine.dialect, "do_ping", lambda conn: pings.append(1))

    with engine.connect() as c:          # first checkout: never used, so pinged
        c.execute(text("select 1"))
    with engine.connect() as c:          # back within the window: no ping
        c.execute(text("select 1"))
    assert len(pings) == 1

    clock = db.time.monotonic() + db.IDLE_PING_SECONDS + 1
    monkeypatch.setattr(db.time, "monotonic", lambda: clock)
    with engine.connect() as c:          # idle past the window: pinged again
        c.execute(text("select 1"))
    assert len(pings) == 2


def test_no_page_relies_on_inline_event_handlers(client):
    """A nonce whitelists a <script> block; it does NOT whitelist onclick=/onsubmit=
    attributes. Shipping those with our CSP silently broke every form on the site:
    signup, login, profile save and the browse search all did nothing when clicked.
    """
    import re

    handler = re.compile(r'\son[a-z]+\s*=\s*"', re.I)
    for path in ("/", "/login", "/signup", "/profile", "/browse"):
        html = client.get(path).text
        # Strip <script> bodies: `el.onclick = fn` inside JS is a property
        # assignment, which CSP permits. Only HTML attributes are the problem.
        markup = re.sub(r"<script\b[^>]*>.*?</script>", "", html, flags=re.S)
        found = handler.findall(markup)
        assert not found, f"{path} has CSP-blocked inline handlers: {found}"


def test_every_form_binds_its_submit_handler_in_script(client):
    from tests.conftest import page_scripts

    for path in ("/login", "/signup", "/profile", "/browse"):
        js = "".join(page_scripts(client.get(path).text))
        assert "addEventListener('submit'" in js, path


def test_no_duplicate_top_level_js_declarations(client):
    """Two script blocks on one page share a global scope.

    `function esc(){}` in the shared chrome plus `const esc = ...` in the browse
    script is a SyntaxError -- "Identifier 'esc' has already been declared" --
    which kills the whole second block. The page then renders fine, serves valid
    HTML, and simply never runs its JavaScript, so it sits on "loading..." while
    every server-side check passes.
    """
    import re

    decl = re.compile(
        r"^\s*(?:function\s+(\w+)|const\s+(\w+)\s*=|let\s+(\w+)\s*=)", re.M
    )
    from tests.conftest import page_scripts

    for path in ("/", "/login", "/signup", "/profile", "/browse", "/t/1"):
        html = client.get(path).text
        seen: dict[str, int] = {}
        blocks = page_scripts(html)
        assert blocks, path
        for i, block in enumerate(blocks):
            # Top level only: anything indented is inside a function or block.
            for m in decl.finditer(block):
                name = next(g for g in m.groups() if g)
                if m.group(0).startswith((" ", "\t")):
                    continue
                assert name not in seen or seen[name] == i, (
                    f"{path}: '{name}' declared at top level in script blocks "
                    f"{seen[name]} and {i} -- that is a SyntaxError at runtime"
                )
                seen[name] = i


def test_pages_are_not_cached_but_static_assets_still_are(client):
    """The same URL is the signed-out pitch, your matches, or a redirect to go
    finish answering -- whichever the session cookie says. A browser that
    heuristically caches it re-shows the page from before you signed in."""
    for path in ["/", "/login", "/signup", "/browse"]:
        cc = client.get(path).headers.get("Cache-Control", "")
        assert "no-store" in cc, f"{path} is cacheable: {cc!r}"
    # Fonts and scripts are content-addressed by mtime and must keep caching.
    css = client.get("/static/docs.css")
    if css.status_code == 200:
        assert "no-store" not in css.headers.get("Cache-Control", "")


# ---- 26 Sep 2026: every visitor shared one rate-limit bucket via the tunnel ----

def test_tunnel_visitors_get_their_own_bucket():
    """cloudflared connects from loopback, so without CF-Connecting-IP the whole
    internet was one address: five signups an hour, total."""
    from starlette.requests import Request

    def req(peer, **headers):
        return Request({"type": "http", "client": (peer, 1), "headers": [
            (k.replace("_", "-").encode(), v.encode()) for k, v in headers.items()]})

    assert security.client_ip(req("127.0.0.1", cf_connecting_ip="203.0.113.9")) == "203.0.113.9"
    assert security.client_ip(req("127.0.0.1")) == "127.0.0.1"
    # From anywhere but loopback the header is attacker-controlled and ignored.
    assert security.client_ip(req("198.51.100.4", cf_connecting_ip="1.2.3.4")) == "198.51.100.4"


def test_who_am_i_is_never_cached(client):
    """/me names the signed-in account; a shared cache must never keep it."""
    client.post("/auth/signup", json=CREDS)
    assert "no-store" in client.get("/me").headers["cache-control"]


def test_session_cookie_is_secure_except_on_local_http(monkeypatch):
    """PUBLIC_BASE_URL is https in production, but the operator also signs in
    on http://127.0.0.1:8000, where a Secure cookie can be silently dropped."""
    from starlette.requests import Request

    def req(url):
        scheme, rest = url.split("://")
        host = rest.split("/")[0]
        return Request({"type": "http", "scheme": scheme, "path": "/", "query_string": b"",
                        "server": (host.split(":")[0], 80), "headers": [(b"host", host.encode())]})

    monkeypatch.setenv("PUBLIC_BASE_URL", "https://tender-0s.vercel.app")
    assert security.cookie_secure(req("https://tender-0s.vercel.app/"))
    assert security.cookie_secure(req("http://tunnel.example/"))   # TLS ends upstream
    assert not security.cookie_secure(req("http://127.0.0.1:8000/"))
    assert not security.cookie_secure(req("http://localhost:8000/"))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://127.0.0.1:8000")
    assert not security.cookie_secure(req("http://tunnel.example/"))


# ---- 28 Sep 2026 pass ----

def test_cross_site_writes_are_refused(client):
    # A form on another site POSTing to logout would clear the session through
    # the response's Set-Cookie; to login, sign the victim into another account.
    for path in ("/auth/logout", "/auth/login"):
        r = client.post(path, json=CREDS, headers={"Sec-Fetch-Site": "cross-site"})
        assert r.status_code == 403, path
        assert "Content-Security-Policy" in r.headers


def test_same_origin_and_non_browser_writes_still_work(client):
    assert client.post("/auth/logout", headers={"Sec-Fetch-Site": "same-origin"}).status_code == 200
    assert client.post("/auth/logout").status_code == 200          # curl, API clients
    assert client.get("/health", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 200


@pytest.mark.parametrize("field,value", [
    ("name", "x" * 201),
    ("contact_email", "a" * 250 + "@b.in"),
    ("keywords", ["k" * 301]),
    ("buyers", ["b" * 301]),
])
def test_profile_fields_are_length_capped(client, field, value):
    r = client.post("/match", json={**PROFILE, field: value})
    assert r.status_code == 422, r.text


def test_sign_out_revokes_a_copied_session_cookie(client):
    assert client.post("/auth/signup", json=CREDS).status_code == 201
    stolen = client.cookies.get(SESSION_COOKIE)
    assert client.get("/me").json()["user"] is not None
    client.post("/auth/logout")
    client.cookies.set(SESSION_COOKIE, stolen)
    assert client.get("/me").json()["user"] is None


def test_sessions_from_before_the_epoch_column_stay_valid(session_factory):
    """Epoch 0 is left out of the stamp, so deploying this signs nobody out."""
    with session_factory() as db:
        user = User(email="a@acme.invalid", password_hash="scrypt$x")
        db.add(user); db.commit()
        base = f"{user.id}|{user.email}|{user.password_hash}|"
        legacy = auth._b64(auth.hmac.new(auth.SECRET, base.encode(),
                                         auth.hashlib.sha256).digest()[:16])
        assert auth.account_stamp(user) == legacy


def test_sign_out_does_not_break_an_unclicked_verification_link(session_factory):
    with session_factory() as db:
        user = User(email="b@acme.invalid", password_hash="scrypt$x")
        db.add(user); db.commit()
        link = auth.make_verification_token(user)
        user.session_epoch = 5; db.commit()
        assert auth.user_from_verification_token(db, link) is not None


# ---- 1 Oct 2026: Neon's free-plan transfer ran out and took the site down ----

def test_public_json_is_edge_cached_and_personal_pages_are_not(client):
    """/tenders reads no session, so Vercel's CDN may answer repeats without
    touching Neon. Anything personal must stay out of every shared cache."""
    assert "s-maxage" in client.get("/tenders").headers["cache-control"]
    assert "s-maxage" in client.get("/robots.txt").headers["cache-control"]
    for path in ["/", "/me", "/browse", "/tenders/999999"]:     # 404s are not kept
        cc = client.get(path).headers["cache-control"]
        assert "no-store" in cc and "s-maxage" not in cc, f"{path}: {cc!r}"


def test_robots_keeps_crawlers_off_the_expensive_urls(client):
    body = client.get("/robots.txt").text
    for path in ["/tenders", "/t/*/extras", "/buyer?*page=", "/admin"]:
        assert f"Disallow: {path}\n" in body
    assert "Disallow: /t/\n" not in body, "tender pages stay indexable"
    assert "User-agent: GPTBot\n" in body


def test_one_api_page_cannot_dump_the_corpus(client):
    assert client.get("/tenders?limit=100").status_code == 200
    assert client.get("/tenders?limit=500").status_code == 422


def test_a_scraper_is_throttled_and_a_person_is_not(client, monkeypatch):
    """Neon's free plan has 5 GB of transfer a month; a scraper looping over
    the API is what can spend it. A person browsing never gets near the limit."""
    monkeypatch.setattr(security, "LIST_LIMIT", 5)
    for _ in range(5):
        assert client.get("/tenders").status_code == 200
    blocked = client.get("/tenders")
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "60"
    assert "no-store" in blocked.headers["cache-control"], "a 429 must not be edge-cached"
    # Other pages still answer, and things with no database are never counted.
    assert client.get("/login").status_code == 200
    monkeypatch.setattr(security, "READ_LIMIT", 0)
    assert client.get("/healthz").status_code == 200
    assert client.get("/robots.txt").status_code == 200
    assert client.get("/login").status_code == 429


def test_on_vercel_each_visitor_gets_their_own_bucket(monkeypatch):
    """On Vercel the peer is Vercel's proxy. x-vercel-forwarded-for is set by
    Vercel itself and cannot be forged by the client."""
    from starlette.requests import Request
    monkeypatch.setenv("VERCEL", "1")
    req = Request({"type": "http", "client": ("10.0.0.1", 1), "headers": [
        (b"x-vercel-forwarded-for", b"203.0.113.9")]})
    assert security.client_ip(req) == "203.0.113.9"
    no_header = Request({"type": "http", "client": ("10.0.0.1", 1), "headers": []})
    assert security.client_ip(no_header) == "10.0.0.1"
