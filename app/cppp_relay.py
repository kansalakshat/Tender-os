"""Open one CPPP tender for a visitor who types CPPP's CAPTCHA on our site.

This is NOT a connector and does NOT solve CAPTCHAs. Nothing here runs unless a
person asks for a tender and then types the CAPTCHA image themselves. The flow:

1. start()  -- GET the tender's own /cppp/tendersfullview/ page (the link the
   CPPP connector stored), which is a CAPTCHA form in front of the full notice.
   Keep the session's cookies and form fields, fetch the CAPTCHA image in that
   session, hand it back as a data: URI.
2. submit() -- POST that form with the visitor's CAPTCHA. CPPP answers with the
   full tender page, or the same form again with an error.

The page needs a Referer from eprocure.gov.in or it answers "Invalid Url", and
the CAPTCHA submit needs the session cookie CPPP's listing page sets or it answers
"Invalid parameter". So start() opens the listing first, as a visitor would.

The CPPP session lives on our server, not in the visitor's browser (a site
cannot set cookies for eprocure.gov.in), so the visitor gets a copy of the
tender page. Links on it that need that session will not work from their browser.
# ponytail: one tender page, not a full proxy. Proxy follow-up links if needed.

State between the two steps is signed JSON in a hidden form field, so nothing is
stored and the server only ever posts fields CPPP itself issued.
"""
from __future__ import annotations

import base64
import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from .auth import sign, unsign
from .compliance import user_agent

BASE = "https://eprocure.gov.in"
DETAIL_PREFIX = BASE + "/cppp/tendersfullview/"
LISTING_URL = BASE + "/cppp/latestactivetendersnew/cpppdata"
STATE_TTL = 600

TRANSPORT: httpx.BaseTransport | None = None  # tests swap in a MockTransport
# Set CPPP_DEBUG_DUMP=path to save CPPP's last reply to a CAPTCHA submission.
DEBUG_DUMP = Path(os.environ["CPPP_DEBUG_DUMP"]) if os.getenv("CPPP_DEBUG_DUMP") else None

# Drupal's wording is "The answer you entered for the CAPTCHA was not correct."
_BAD_CAPTCHA = re.compile(r"captcha[^.]*not correct|(invalid|incorrect|wrong)\s+captcha", re.I)
# CPPP's own pages for a link it will not open.
_REFUSED = re.compile(r"invalid\s+(parameter|url)", re.I)


def _page_text(html: str) -> str:
    tree = HTMLParser(html)
    for node in tree.css("script, style"):
        node.decompose()
    return tree.body.text(separator=" ") if tree.body else ""


class RelayError(RuntimeError):
    """CPPP answered with something this module does not recognise."""


class LinkRejected(RelayError):
    """CPPP served no CAPTCHA form for this tender link (expired, closed, invalid)."""


@dataclass
class Captcha:
    state: str       # signed, goes back in a hidden field
    image: str       # data:image/... URI
    message: str = ""


@dataclass
class TenderPage:
    html: str
    url: str


@dataclass
class CaptchaForm:
    action: str
    fields: dict[str, str]
    image_src: str


def is_detail_url(url: str | None) -> bool:
    return bool(url) and url.startswith(DETAIL_PREFIX)


def _client(cookies: dict | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=BASE, follow_redirects=True, timeout=30, transport=TRANSPORT,
        headers={"User-Agent": user_agent()}, cookies=cookies,
    )


def parse_form(html: str) -> CaptchaForm | None:
    """The CAPTCHA form in front of the notice: action, fields, image URL."""
    tree = HTMLParser(html)
    for form in tree.css("form"):
        img = form.css_first("img[data-drupal-selector=edit-captcha-image]")
        if img is None or form.css_first("input[name=captcha_response]") is None:
            continue
        fields = {}
        for node in form.css("input"):
            name = node.attributes.get("name")
            kind = (node.attributes.get("type") or "text").lower()
            if name and kind in ("hidden", "text"):
                fields[name] = node.attributes.get("value") or ""
        return CaptchaForm(form.attributes.get("action") or "", fields,
                           img.attributes.get("src") or "")
    return None


def _pack(cookies: httpx.Cookies, form: CaptchaForm, base: str) -> str:
    # Not dict(cookies): that raises CookieConflict once a cookie is re-set.
    raw = json.dumps({"c": {k.name: k.value for k in cookies.jar}, "f": form.fields,
                      "a": urljoin(base, form.action), "r": base},
                     separators=(",", ":"))
    return sign("cppp", base64.urlsafe_b64encode(raw.encode()).decode(), STATE_TTL)


def _unpack(state: str) -> dict | None:
    body = unsign("cppp", state)
    return json.loads(base64.urlsafe_b64decode(body.encode())) if body else None


def _captcha_from(resp: httpx.Response, client: httpx.Client, message: str) -> Captcha:
    form = parse_form(resp.text)
    if form is None or not form.image_src:
        raise LinkRejected(f"no CAPTCHA form at {resp.url} (HTTP {resp.status_code})")
    # The image is generated per captcha_sid and checked against this session,
    # so it has to be fetched with the same cookies, before they are packed.
    img = client.get(urljoin(str(resp.url), form.image_src),
                     headers={"Referer": str(resp.url)})
    kind = img.headers.get("content-type", "image/png").split(";")[0]
    if img.status_code != 200 or not kind.startswith("image/"):
        raise RelayError(f"CPPP CAPTCHA image returned HTTP {img.status_code} {kind}")
    image = f"data:{kind};base64,{base64.b64encode(img.content).decode()}"
    return Captcha(_pack(client.cookies, form, str(resp.url)), image, message)


# The listing takes ~3s for CPPP to build and is only fetched for its session
# cookie, so one session is reused for SESSION_TTL instead of per CAPTCHA.
# ponytail: one shared CPPP session for all visitors; per-visitor sessions if CPPP
# starts refusing concurrent lookups in one session.
SESSION_TTL = 900
_session: tuple[float, dict[str, str]] | None = None


def _session_cookies(client: httpx.Client) -> dict[str, str]:
    global _session
    if _session is None or time.monotonic() - _session[0] > SESSION_TTL:
        client.get(LISTING_URL)  # sets the SSESS session cookie
        _session = (time.monotonic(), {k.name: k.value for k in client.cookies.jar})
    return _session[1]


def forget_session() -> None:
    """Called when CPPP refuses a link, in case the shared session went stale."""
    global _session
    _session = None


def start(detail_url: str) -> Captcha:
    if not is_detail_url(detail_url):
        raise LinkRejected(f"not a CPPP tender link: {detail_url!r}")
    with _client() as c:
        c.cookies.update(_session_cookies(c))
        return _captcha_from(c.get(detail_url, headers={"Referer": LISTING_URL}), c, "")


def portal_error(html: str) -> str:
    """CPPP's own error line, e.g. Drupal's "The answer you entered ...". """
    node = HTMLParser(html).css_first("[role=alert], .messages--error, .alert-danger")
    return re.sub(r"\s+", " ", node.text()).strip() if node else ""


def submit(state: str, detail_url: str, captcha_text: str) -> TenderPage | Captcha:
    """The tender page, or a fresh CAPTCHA with a message saying what went wrong."""
    data = _unpack(state)
    if data is None:
        captcha = start(detail_url)
        captcha.message = "That CAPTCHA expired. Here is a new one."
        return captcha
    with _client(data["c"]) as c:
        resp = c.post(data["a"], data={**data["f"], "captcha_response": captcha_text,
                                       "op": "Submit"},
                      headers={"Referer": data["r"]})
        if DEBUG_DUMP:
            trail = " -> ".join(f"{r.status_code} {r.headers.get('location')}"
                                for r in resp.history)
            DEBUG_DUMP.write_text(
                f"<!-- sent cookies {sorted(data['c'])}; redirects {trail}; "
                f"final {resp.url} -->\n{resp.text}", encoding="utf8")
        if _REFUSED.search(_page_text(resp.text)):
            forget_session()
            raise LinkRejected(f"CPPP refused the tender link after the CAPTCHA: {resp.url}")
        if parse_form(resp.text) is None:
            return TenderPage(resp.text, str(resp.url))
        error = portal_error(resp.text)
        if not error or _BAD_CAPTCHA.search(error):
            msg = "CPPP says the CAPTCHA was wrong. Try this one."
        else:
            msg = f"CPPP says: {error}"
        return _captcha_from(resp, c, msg)


def with_base(html: str, url: str) -> str:
    """Point CPPP's relative links, styles and images back at CPPP."""
    tag = f'<base href="{url}">'
    head = re.search(r"<head[^>]*>", html, re.I)
    return html[:head.end()] + tag + html[head.end():] if head else tag + html
