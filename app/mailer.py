"""Outbound email, on `smtplib` and `email.message` -- both standard library.

Sending is optional. With no SMTP host configured the message is appended to
`outbox.log` instead, which keeps the whole signup flow working on a machine that
has no mail credentials: the verification link is still produced, still valid, and
still one click -- you just read it out of the file rather than an inbox.

The link is deliberately NEVER returned in an HTTP response. A dev convenience
that leaks a token over the API is the kind of thing that survives into
production, so the only place it lands is the log or the real mailbox.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger(__name__)

OUTBOX = Path(__file__).resolve().parents[1] / "outbox.log"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def public_base_url() -> str:
    """Origin used to build links in emails and the OAuth redirect URI."""
    return _env("PUBLIC_BASE_URL", "http://127.0.0.1:8000").rstrip("/")


def smtp_configured() -> bool:
    return bool(_env("SMTP_HOST"))


def send(to: str, subject: str, body: str) -> bool:
    """True if it went over SMTP, False if it was written to the outbox instead."""
    sender = _env("SMTP_FROM") or _env("CONTACT_EMAIL") or "no-reply@localhost"
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to
    message["Subject"] = subject
    message.set_content(body)

    if not smtp_configured():
        # Not an error: this is the documented no-credentials path.
        with OUTBOX.open("a", encoding="utf-8") as fh:
            fh.write(f"--- to: {to}\n--- subject: {subject}\n{body}\n\n")
        log.warning(
            "SMTP_HOST unset; wrote the message for %s to %s instead of sending it",
            to, OUTBOX.name,
        )
        return False

    host, port = _env("SMTP_HOST"), int(_env("SMTP_PORT", "587"))
    user, password = _env("SMTP_USER"), _env("SMTP_PASSWORD")
    with smtplib.SMTP(host, port, timeout=20) as server:
        server.ehlo()
        if port != 25:
            server.starttls()      # plain 25 is usually a local relay with no TLS
            server.ehlo()
        if user:
            server.login(user, password)
        server.send_message(message)
    log.info("sent %r to %s", subject, to)
    return True


def send_verification(to: str, token: str) -> bool:
    link = f"{public_base_url()}/auth/verify?token={token}"
    return send(
        to,
        "Confirm your email address",
        "Confirm your email address to finish setting up your tender alerts:\n\n"
        f"{link}\n\n"
        "The link is good for 24 hours. If you did not create this account you can "
        "ignore this message -- nothing was set up.\n",
    )
