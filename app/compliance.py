"""Hard compliance rules, enforced in code.

These are architectural constraints, not TODOs. Every one of them is checked at
runtime rather than documented and hoped for.
"""
from __future__ import annotations

import os
import re
from urllib.parse import urlsplit

__version__ = "0.1.0"

# Rule #1: GeM's robots.txt disallows automated access. We never build a connector
# for it and we never let a request reach it -- not directly, not via a redirect,
# not via a misconfigured allowlist entry. Getting GeM data legitimately requires a
# data-sharing agreement with GeM: a business-development task, not an engineering
# one. Do not "temporarily" remove a host from this tuple.
BLOCKED_HOSTS = ("gem.gov.in",)


class BlockedSourceError(RuntimeError):
    """Raised when anything in the system points at a permanently blocked host."""


class RobotsDisallowedError(RuntimeError):
    """Raised when a source's robots.txt does not permit the paths we need."""


def host_is_blocked(url: str) -> bool:
    host = (urlsplit(url).hostname or "").lower().rstrip(".")
    return any(host == b or host.endswith("." + b) for b in BLOCKED_HOSTS)


def assert_not_blocked(url: str) -> None:
    if host_is_blocked(url):
        raise BlockedSourceError(
            f"{urlsplit(url).hostname!r} is permanently blocked (compliance rule #1). "
            "GeM disallows automated access; obtaining this data requires a formal "
            "data-sharing agreement with GeM, not a scraper."
        )


# Rule #5: identify honestly. No spoofed browser UA. The contact address must be a
# real, monitored mailbox so a site operator can reach a human; the placeholder from
# .env.example is rejected so this cannot be silently skipped.
_PLACEHOLDER_EMAILS = ("you@example.com", "example.com", "changeme")


def user_agent() -> str:
    email = (os.getenv("CONTACT_EMAIL") or "").strip()
    if not email or any(p in email.lower() for p in _PLACEHOLDER_EMAILS):
        raise RuntimeError(
            "CONTACT_EMAIL is unset or still the placeholder. Compliance rule #5 "
            "requires we identify honestly with a reachable contact address. "
            "Set CONTACT_EMAIL in .env before running any connector."
        )
    return (
        f"IndianTenderAggregator/{__version__} (+public procurement data aggregator; "
        f"contact: {email}) python-httpx"
    )


# Rule #7: tender metadata only. Never persist bidder/seller personal details or
# contact information, even when a source volunteers them in a payload we store for
# auditing. raw_payload is scrubbed on the way in, so the database never holds it.
_PERSONAL_KEY_RE = re.compile(
    r"(e[-_ ]?mail|mobile|phone|contact|bidder|vendor[-_ ]?name|supplier[-_ ]?name|"
    r"address|aadhaar|aadhar|\bpan\b|gstin|account[-_ ]?no|ifsc|dob|"
    r"person|official[-_ ]?name|officer|designation)",
    re.I,
)
_REDACTED = "[redacted:personal-data-rule-7]"


def scrub_personal(value):
    """Recursively drop keys that look like personal/contact data.

    Deliberately over-broad: a false positive costs one audit field, a false
    negative means we stored someone's phone number.
    """
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _PERSONAL_KEY_RE.search(str(k)) else scrub_personal(v))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [scrub_personal(v) for v in value]
    return value
