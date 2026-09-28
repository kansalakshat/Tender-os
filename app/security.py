"""Rate limiting and response hardening.

Both on the standard library plus the database the app already has. A rate
limiter is a counter per key and a comparison; a dependency for that would be
more code to audit, not less.

The counters live in Postgres, not process memory. In memory they were per
instance, and Vercel runs many short-lived instances that share nothing else,
so the login throttle there protected almost nothing.
"""
from __future__ import annotations

import os
import random
import secrets
import time

from fastapi import HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

# --- rate limiting ----------------------------------------------------------

# Failed sign-ins per (ip, email). Low, because a real person mistyping a
# password three times is normal and thirty times is not.
LOGIN_LIMIT, LOGIN_WINDOW = 8, 900          # 8 per 15 minutes
LOGIN_ACCOUNT_LIMIT = 30                    # per email, from every address
# Anything that creates an account or sends mail, per ip.
SIGNUP_LIMIT, SIGNUP_WINDOW = 5, 3600       # 5 per hour
MAIL_LIMIT, MAIL_WINDOW = 4, 3600           # 4 verification mails per hour

# Rows idle this long are deleted. Longer than any window above.
_STALE_SECONDS = 86400

# One atomic statement, so two instances counting the same key at once cannot
# both read "7" and both let the eighth guess through. Same syntax on Postgres
# and SQLite (3.35+), which is what the tests run on.
_HIT = text("""
    INSERT INTO rate_limits (key, window_start, count) VALUES (:key, :now, 1)
    ON CONFLICT (key) DO UPDATE SET
        count = CASE WHEN rate_limits.window_start <= :cutoff THEN 1
                     ELSE rate_limits.count + 1 END,
        window_start = CASE WHEN rate_limits.window_start <= :cutoff THEN :now
                            ELSE rate_limits.window_start END
    RETURNING count
""")


_LOOPBACK = {"127.0.0.1", "::1"}


def client_ip(request: Request) -> str:
    """The visitor's address. X-Forwarded-For is deliberately NOT trusted:
    nothing here strips it, so an attacker could forge it and get a fresh bucket
    per request.

    The one exception is a loopback peer. The public site reaches this process
    through cloudflared on the same machine, so every visitor arrived as
    127.0.0.1 and shared one bucket -- five signups an hour for the whole
    internet, and one sprayer could lock everybody out. Cloudflare's edge
    overwrites CF-Connecting-IP with the real client, and nothing but the local
    machine can open a loopback connection, so on that path it is trustworthy.
    """
    peer = request.client.host if request.client else "unknown"
    if peer in _LOOPBACK:
        return request.headers.get("cf-connecting-ip", "").strip()[:64] or peer
    return peer


def hit(db: Session, key: str, limit: int, window: int) -> bool:
    """Record an attempt. False when the caller is over the limit.

    Fixed windows: the count restarts `window` seconds after the first attempt.
    Committed at once, so a request that then fails still counts.
    """
    now = time.time()
    count = db.execute(_HIT, {"key": key[:300], "now": now,
                              "cutoff": now - window}).scalar_one()
    # Keys rotate (every ip, every email), so old rows would pile up forever.
    # One request in a hundred sweeps; nothing else needs to remember to.
    if random.random() < 0.01:
        db.execute(text("DELETE FROM rate_limits WHERE window_start < :t"),
                   {"t": now - _STALE_SECONDS})
    db.commit()
    return count <= limit


def enforce(db: Session, key: str, limit: int, window: int, message: str) -> None:
    if not hit(db, key, limit, window):
        raise HTTPException(
            status_code=429,
            detail=message,
            headers={"Retry-After": str(window)},
        )


def reset_key(db: Session, key: str) -> None:
    """Clear one counter. Called after a successful sign-in so a person who
    finally remembers their password is not still locked out."""
    db.execute(text("DELETE FROM rate_limits WHERE key = :key"), {"key": key[:300]})
    db.commit()


# --- response hardening -----------------------------------------------------

def https_only() -> bool:
    """Cookies get the Secure flag when the site is actually served over TLS.
    Setting it on a plain-http demo would silently break every login."""
    return (os.getenv("PUBLIC_BASE_URL") or "").strip().lower().startswith("https://")


def cookie_secure(request: Request) -> bool:
    """Whether a cookie set on this response gets the Secure flag.

    Decided per request, not from PUBLIC_BASE_URL alone: that names the public
    site (https), but the operator also signs in on http://127.0.0.1:8000, and
    a Secure cookie there is dropped by some browsers -- the sign-in "works"
    and the next page is signed out. Loopback over http never leaves the
    machine, so it has nothing for the flag to protect.
    """
    if request.url.scheme == "https":
        return True
    return https_only() and request.url.hostname not in ("127.0.0.1", "localhost", "::1")


def make_nonce() -> str:
    return secrets.token_urlsafe(16)


def csp(nonce: str) -> str:
    """Content-Security-Policy.

    Every script and stylesheet this app serves is inline in its own HTML, so
    they are allow-listed by nonce rather than by 'unsafe-inline'. That is the
    difference that matters: with a nonce, markup injected into a page cannot
    execute, because the attacker cannot guess the per-response value.
    """
    return "; ".join([
        "default-src 'self'",
        # wasm-unsafe-eval lets the hero model's meshopt decoder compile its
        # WebAssembly. It does not allow eval() or new Function() for JS.
        f"script-src 'self' 'nonce-{nonce}' 'wasm-unsafe-eval'",
        f"style-src 'self' 'nonce-{nonce}'",
        # blob: is the hero model: GLTFLoader unpacks its embedded textures
        # into blob: URLs and fetches (Chrome) or <img>-loads (others) them.
        "img-src 'self' data: blob:",
        "form-action 'self'",
        "connect-src 'self' blob:",
        "frame-ancestors 'none'",       # clickjacking
        "base-uri 'none'",              # stops <base> hijacking relative URLs
        "object-src 'none'",
    ])


# Swagger UI and ReDoc are served from a CDN and bootstrap themselves with an
# inline <script> that FastAPI generates -- we never see that markup, so we
# cannot stamp our nonce onto it. Under the site policy above /docs returned 200
# and rendered a blank page, because every asset and the bootstrap were blocked.
#
# The relaxation is scoped to the two documentation paths, which render our own
# OpenAPI schema and no user-supplied data.
# ponytail: vendoring swagger-ui-dist and serving it from /static would put the
# docs back under the strict policy. Do that if /docs ever renders user input.
def csp_docs() -> str:
    cdn = "https://cdn.jsdelivr.net"
    return "; ".join([
        "default-src 'self'",
        f"script-src 'self' 'unsafe-inline' {cdn} blob:",
        f"style-src 'self' 'unsafe-inline' {cdn} https://fonts.googleapis.com",
        "font-src 'self' data: https://fonts.gstatic.com",
        "img-src 'self' data: https://fastapi.tiangolo.com",
        "worker-src 'self' blob:",
        "connect-src 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "base-uri 'none'",
        "object-src 'none'",
    ])


SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    # Keeps verification tokens in URLs out of other sites' referer logs.
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "geolocation=(), microphone=(), camera=(), payment=()",
    "Cross-Origin-Opener-Policy": "same-origin",
}
