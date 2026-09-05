"""Google sign-in (OAuth 2.0 authorization code flow), on httpx -- already a
dependency, so nothing new is added for this.

Why the userinfo endpoint rather than decoding the `id_token`: verifying that JWT
means fetching Google's rotating public keys and doing RSA verification, which
needs a crypto dependency. Calling `/userinfo` with the access token we just
received *directly from Google's token endpoint over TLS* gets the same claims
with no signature work. The token never passes through the browser, so there is
nothing for the user to tamper with.

Nothing here runs unless GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET are set; the
sign-in button stays hidden rather than becoming a link that 500s.
"""
from __future__ import annotations

import logging
import os
import secrets
from urllib.parse import urlencode

import httpx

from .auth import sign, unsign
from .mailer import public_base_url

log = logging.getLogger(__name__)

AUTH_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"
USERINFO_ENDPOINT = "https://openidconnect.googleapis.com/v1/userinfo"

STATE_TTL_SECONDS = 600      # ten minutes is plenty to finish a consent screen


class OAuthError(Exception):
    """Message is safe to show the user verbatim."""


def _env(name: str) -> str:
    return (os.getenv(name) or "").strip()


def configured() -> bool:
    return bool(_env("GOOGLE_CLIENT_ID") and _env("GOOGLE_CLIENT_SECRET"))


def redirect_uri() -> str:
    """Must match a URI registered on the Google OAuth client, exactly."""
    return f"{public_base_url()}/auth/google/callback"


def make_state() -> str:
    """Signed, expiring CSRF token. Carries a nonce so two tabs get two states."""
    return sign("oauth", secrets.token_urlsafe(12), STATE_TTL_SECONDS)


def check_state(state: str | None) -> bool:
    return unsign("oauth", state) is not None


def authorize_url(state: str) -> str:
    return AUTH_ENDPOINT + "?" + urlencode(
        {
            "client_id": _env("GOOGLE_CLIENT_ID"),
            "redirect_uri": redirect_uri(),
            "response_type": "code",
            "scope": "openid email profile",
            "state": state,
            # No refresh token is requested: we read the profile once at sign-in
            # and never act on the user's behalf afterwards.
            "access_type": "online",
            "prompt": "select_account",
        }
    )


def exchange_code(code: str) -> dict:
    """Swap the one-time code for an access token, then read the profile.

    Returns the userinfo claims: `sub`, `email`, `email_verified`, `name`.
    """
    if not configured():
        raise OAuthError("Google sign-in is not configured on this server")
    try:
        with httpx.Client(timeout=20.0) as client:
            token_response = client.post(
                TOKEN_ENDPOINT,
                data={
                    "code": code,
                    "client_id": _env("GOOGLE_CLIENT_ID"),
                    "client_secret": _env("GOOGLE_CLIENT_SECRET"),
                    "redirect_uri": redirect_uri(),
                    "grant_type": "authorization_code",
                },
            )
            if token_response.status_code != 200:
                # Google's body names the real cause (redirect_uri_mismatch is the
                # usual one) and it is a configuration fault, not a user fault.
                log.error("google token exchange failed: %s", token_response.text[:400])
                raise OAuthError("Google rejected the sign-in attempt")
            access_token = token_response.json().get("access_token")
            if not access_token:
                raise OAuthError("Google returned no access token")

            info = client.get(
                USERINFO_ENDPOINT, headers={"Authorization": f"Bearer {access_token}"}
            )
            if info.status_code != 200:
                log.error("google userinfo failed: %s", info.text[:400])
                raise OAuthError("could not read your Google profile")
    except httpx.HTTPError as exc:
        log.error("google oauth transport error: %s", exc)
        raise OAuthError("could not reach Google -- try again")

    claims = info.json()
    if not claims.get("sub") or not claims.get("email"):
        raise OAuthError("Google did not return an email address")
    return claims
