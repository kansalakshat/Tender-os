"""Accounts and sessions, on the standard library only.

No passlib, no bcrypt, no python-jose, no itsdangerous. `hashlib.scrypt` is a
proper memory-hard password KDF and ships with Python, so a dependency here would
buy nothing (ladder rung 3). The same goes for the session: an HMAC-signed cookie
needs `hmac` and `secrets`, both stdlib.

What is deliberately NOT simplified, because it is a security boundary:
per-password random salts, constant-time comparison, an expiring signed cookie,
and login errors that do not reveal whether an email is registered.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .db import get_db
from .models import User, utcnow

log = logging.getLogger(__name__)

# scrypt cost. n=2**14 with r=8 needs ~16MB per hash, which is the point: it makes
# offline cracking expensive. Raise n (never lower it) if login feels too cheap.
_N, _R, _P, _DKLEN = 2**14, 8, 1, 32
_MAXMEM = 64 * 1024 * 1024

MIN_PASSWORD_LEN = 10
# scrypt reads the whole password, so an unbounded one is a free CPU burner on
# an unauthenticated endpoint. Far above anything a password manager produces.
MAX_PASSWORD_LEN = 256
SESSION_COOKIE = "tender_session"
SESSION_DAYS = 30
VERIFICATION_HOURS = 24

_ENV_SECRET = os.getenv("SESSION_SECRET", "").strip()
if _ENV_SECRET:
    SECRET = _ENV_SECRET.encode()
    if len(SECRET) < 32:
        # Anyone who guesses it can mint a session for any account.
        log.warning("SESSION_SECRET is under 32 characters; generate a longer one.")
else:
    # Ephemeral: fine for a demo, but every restart invalidates every session.
    # Set SESSION_SECRET in .env before anyone relies on staying logged in.
    SECRET = secrets.token_bytes(32)
    log.warning(
        "SESSION_SECRET is unset; using a random per-process secret. "
        "Everyone will be logged out on restart. Set it in .env to persist logins."
    )


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---- passwords -------------------------------------------------------------

def hash_password(password: str) -> str:
    """Self-describing hash: the cost parameters travel with it, so raising them
    later does not invalidate hashes already stored."""
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=_DKLEN, maxmem=_MAXMEM
    )
    return f"scrypt${_N}${_R}${_P}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored: str | None) -> bool:
    # A Google-only account has no hash. It must fail the password path, not crash.
    if not stored:
        return False
    try:
        scheme, n, r, p, salt, digest = stored.split("$")
        if scheme != "scrypt":
            return False
        candidate = hashlib.scrypt(
            password.encode(), salt=_unb64(salt),
            n=int(n), r=int(r), p=int(p), dklen=len(_unb64(digest)), maxmem=_MAXMEM,
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, _unb64(digest))


def password_problem(password: str) -> str | None:
    """Returns why a password is unacceptable, or None if it is fine."""
    if len(password or "") < MIN_PASSWORD_LEN:
        return f"password must be at least {MIN_PASSWORD_LEN} characters"
    if len(password) > MAX_PASSWORD_LEN:
        return f"password must be at most {MAX_PASSWORD_LEN} characters"
    return None


def normalize_email(email: str) -> str:
    return (email or "").strip().lower()


# ---- sessions --------------------------------------------------------------

def sign(purpose: str, subject: str | int, ttl_seconds: int) -> str:
    """`<purpose>.<subject>.<expiry-epoch>.<hmac>`. Stateless, so no tokens table.

    The purpose is inside the signed payload, which is the point: without it a
    30-day session cookie would also be a valid email-verification token, and
    verifying your address would hand out a login. The expiry is signed too, or a
    stolen token would never stop working.
    """
    expires = int(datetime.now().timestamp()) + ttl_seconds
    payload = f"{purpose}.{subject}.{expires}"
    sig = hmac.new(SECRET, payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(sig)}"


def unsign(purpose: str, token: str | None) -> str | None:
    """The subject, or None if the token is forged, expired, or for another use."""
    if not token:
        return None
    try:
        got_purpose, subject, expires, sig = token.rsplit(".", 3)
        if not hmac.compare_digest(got_purpose, purpose):
            return None
        payload = f"{got_purpose}.{subject}.{expires}"
        expected = hmac.new(SECRET, payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(_unb64(sig), expected):
            return None
        if int(expires) < datetime.now().timestamp():
            return None
        return subject
    except (ValueError, TypeError):
        return None


def account_stamp(user: User) -> str:
    """Binds a token to one account, not just to a row number.

    A bare user id was the whole session once, and it was a real hole: the same
    SESSION_SECRET signs cookies for the local database and for Neon, so "user
    5" signed in one database was "user 5" -- a stranger -- in the other. The
    same happens after any restore or reseed that hands an id to someone new.

    The stamp covers the email, the password hash and the Google link, so it
    also revokes every outstanding session when any of those change: a new
    password, or a Google sign-in that strips a squatter's password (see
    user_from_google), logs out whoever held the old one.
    """
    material = f"{user.id}|{user.email}|{user.password_hash or ''}|{user.google_sub or ''}"
    return _b64(hmac.new(SECRET, material.encode(), hashlib.sha256).digest()[:16])


def _stamped(purpose: str, user: User, ttl_seconds: int) -> str:
    return sign(purpose, f"{user.id}:{account_stamp(user)}", ttl_seconds)


def _read_stamped(purpose: str, token: str | None) -> tuple[int, str] | None:
    subject = unsign(purpose, token)
    try:
        user_id, stamp = (subject or "").split(":")
        return int(user_id), stamp
    except ValueError:
        return None


def _user_for(db: Session, purpose: str, token: str | None) -> User | None:
    """The account a token names, or None if it names a different one now."""
    parsed = _read_stamped(purpose, token)
    if parsed is None:
        return None
    user = db.get(User, parsed[0])
    if user is None or not hmac.compare_digest(parsed[1], account_stamp(user)):
        return None
    return user


def make_session(user: User) -> str:
    return _stamped("session", user, SESSION_DAYS * 86400)


def read_session(token: str | None) -> tuple[int, str] | None:
    """(user id, stamp) from a well-signed cookie. Not proof of an account on
    its own -- current_user() checks the stamp against the row."""
    return _read_stamped("session", token)


def make_verification_token(user: User) -> str:
    return _stamped("verify", user, VERIFICATION_HOURS * 3600)


def user_from_verification_token(db: Session, token: str | None) -> User | None:
    return _user_for(db, "verify", token)


def _secure(request: Request) -> bool:
    # Imported lazily: app.security imports fastapi, and auth is imported early.
    from .security import cookie_secure

    return cookie_secure(request)


def set_session_cookie(response, user: User, request: Request) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        make_session(user),
        max_age=SESSION_DAYS * 86400,
        httponly=True,       # JS cannot read it, so an XSS cannot lift the session
        samesite="lax",       # not sent on cross-site POSTs, which is our CSRF defence
        # On for https and the public site, off for http on loopback -- see
        # security.cookie_secure. Hard-coding True breaks the local dashboard.
        secure=_secure(request),
    )


def clear_session_cookie(response, request: Request) -> None:
    response.delete_cookie(
        SESSION_COOKIE, httponly=True, samesite="lax", secure=_secure(request)
    )


# ---- FastAPI dependencies --------------------------------------------------

def current_user(request: Request, db: Session = Depends(get_db)) -> User | None:
    """The signed-in user, or None. Never raises -- pages work signed out."""
    return _user_for(db, "session", request.cookies.get(SESSION_COOKIE))


# ---- registration / login --------------------------------------------------

class AuthError(Exception):
    """Message is safe to show the user verbatim."""


# Deliberately conservative. The old check was `"@" in email`, which accepted
# `<img src=x onerror=alert(1)>@evil.com` -- markup that then reached the page.
# Output is escaped as well; this is the other half of that defence.
_EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9.-]{1,255}\.[A-Za-z]{2,24}$")


def email_problem(email: str) -> str | None:
    if not _EMAIL_RE.match(email or ""):
        return "that does not look like an email address"
    return None


def register(db: Session, email: str, password: str) -> User:
    email = normalize_email(email)
    problem = email_problem(email)
    if problem:
        raise AuthError(problem)
    problem = password_problem(password)
    if problem:
        raise AuthError(problem)
    exists = db.execute(
        select(User).where(func.lower(User.email) == email)
    ).scalar_one_or_none()
    if exists is not None:
        raise AuthError("that email is already registered -- try signing in")
    user = User(email=email, password_hash=hash_password(password), last_login_at=utcnow())
    db.add(user)
    db.commit()
    return user


def authenticate(db: Session, email: str, password: str) -> User:
    if len(password or "") > MAX_PASSWORD_LEN:
        raise AuthError("email or password is incorrect")
    user = db.execute(
        select(User).where(func.lower(User.email) == normalize_email(email))
    ).scalar_one_or_none()
    # Hash even when the user is missing, so response time does not reveal which
    # emails are registered, and give one identical message for both failures.
    if user is None:
        hash_password(password)
        raise AuthError("email or password is incorrect")
    if user.password_hash is None:
        # Registered through Google and never set a password. Say so plainly --
        # the account provably exists, so there is nothing left to withhold, and
        # "incorrect password" would send them round in circles forever.
        raise AuthError("this account signs in with Google -- use the Google button")
    if not verify_password(password, user.password_hash):
        raise AuthError("email or password is incorrect")
    user.last_login_at = utcnow()
    db.commit()
    return user


def user_from_google(db: Session, sub: str, email: str, email_verified: bool) -> User:
    """Find or create the account behind a Google identity.

    Matching on `sub` first, then on the email address, and only when Google says
    it verified that address. Linking on an unverified email would let anyone who
    can create a Google account with your address take over your login.
    """
    email = normalize_email(email)
    if email_problem(email):
        raise AuthError("Google returned an address we cannot accept")
    user = db.execute(
        select(User).where(User.google_sub == sub)
    ).scalar_one_or_none()

    if user is None and email_verified:
        user = db.execute(
            select(User).where(func.lower(User.email) == email)
        ).scalar_one_or_none()
        if user is not None:
            user.google_sub = sub          # link Google to the existing password account
            if not user.email_verified:
                # Nobody ever proved they own this address, so the password on
                # it may be a squatter's: sign up with a victim's email, wait for
                # them to use Google, share their account. Google has now proved
                # ownership; the unproven password goes, and with it (through
                # account_stamp) every session it issued.
                user.password_hash = None

    if user is None:
        if not email_verified:
            raise AuthError(
                "Google did not confirm that email address, so we cannot open an "
                "account with it"
            )
        user = User(email=email, google_sub=sub, password_hash=None)
        db.add(user)

    # Google verified the address; that is the whole point of trusting it here.
    user.email_verified = user.email_verified or email_verified
    user.last_login_at = utcnow()
    db.commit()
    return user
