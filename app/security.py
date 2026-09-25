"""Rate limiting and response hardening.

Both on the standard library. A rate limiter is a dict of timestamps and a
comparison; a dependency for that would be more code to audit, not less.

ponytail: the limiter is per-process and in memory, so counters reset on restart
and are not shared between workers. That is honest for a single-process
deployment. Behind more than one worker, or behind a load balancer, move the
buckets to Redis or Postgres -- the `hit()` signature does not change.
"""
from __future__ import annotations

import logging
import os
import secrets
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

# --- rate limiting ----------------------------------------------------------

_BUCKETS: dict[str, deque[float]] = defaultdict(deque)

# Failed sign-ins per (ip, email). Low, because a real person mistyping a
# password three times is normal and thirty times is not.
LOGIN_LIMIT, LOGIN_WINDOW = 8, 900          # 8 per 15 minutes
LOGIN_ACCOUNT_LIMIT = 30                    # per email, from every address
# Anything that creates an account or sends mail, per ip.
SIGNUP_LIMIT, SIGNUP_WINDOW = 5, 3600       # 5 per hour
MAIL_LIMIT, MAIL_WINDOW = 4, 3600           # 4 verification mails per hour

_MAX_BUCKETS = 20_000

# Serverless invocations do not share memory, so these buckets protect almost
# nothing there. Say so loudly rather than letting the deployment quietly believe
# it is throttled.
if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
    logging.getLogger(__name__).warning(
        "Rate limiting is in-process and this looks like a serverless runtime: "
        "login throttling is NOT effective here. Move the buckets to Postgres or "
        "Redis before exposing this publicly."
    )


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


def hit(key: str, limit: int, window: int) -> bool:
    """Record an attempt. False when the caller is over the limit."""
    now = time.monotonic()
    bucket = _BUCKETS[key]
    while bucket and now - bucket[0] > window:
        bucket.popleft()
    if not bucket:
        _BUCKETS.pop(key, None)
        bucket = _BUCKETS[key]
    # Unbounded growth is itself a denial of service: an attacker rotating keys
    # would otherwise fill memory. Drop the whole table rather than serve wrong.
    if len(_BUCKETS) > _MAX_BUCKETS:
        _BUCKETS.clear()
        bucket = _BUCKETS[key]
    if len(bucket) >= limit:
        return False
    bucket.append(now)
    return True


def enforce(key: str, limit: int, window: int, message: str) -> None:
    if not hit(key, limit, window):
        raise HTTPException(
            status_code=429,
            detail=message,
            headers={"Retry-After": str(window)},
        )


def reset_key(key: str) -> None:
    """Clear one bucket. Called after a successful sign-in so a person who
    finally remembers their password is not still locked out."""
    _BUCKETS.pop(key, None)


def reset() -> None:
    """Test hook. Never called by the app."""
    _BUCKETS.clear()


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
        f"script-src 'self' 'nonce-{nonce}'",
        f"style-src 'self' 'nonce-{nonce}'",
        "img-src 'self' data:",
        "form-action 'self'",
        "connect-src 'self'",
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
