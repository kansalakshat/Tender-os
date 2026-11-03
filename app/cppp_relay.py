"""Open one CPPP tender for a visitor who types CPPP's CAPTCHA on our site.

This is NOT a connector and does NOT solve CAPTCHAs. Nothing here runs unless a
person asks for a tender and then types the CAPTCHA image themselves. The flow:

1. start()  -- GET CPPP's advanced search form, whose CAPTCHA arrives inside the
   page as a data: URI. Keep the session's cookie and the form's fields.
2. submit() -- POST that form with the tender's id and the visitor's CAPTCHA, then
   follow the result's link to the tender's page.

CPPP's newer site puts its CAPTCHA behind an encrypted data service, so this uses
the older search form, which is plain HTML.

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
import logging
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urljoin

import httpx
from selectolax.parser import HTMLParser

from .auth import sign, unsign
from .compliance import user_agent

log = logging.getLogger(__name__)

BASE = "https://eprocure.gov.in"
FORM_URL = BASE + "/eprocure/app?page=FrontEndAdvancedSearch&service=page"
STATE_TTL = 600
OPEN_TENDER, LIMITED_TENDER = "1", "2"     # the form's required Tender Type menu

TRANSPORT: httpx.BaseTransport | None = None  # tests swap in a MockTransport
# Set CPPP_DEBUG_DUMP=path to save CPPP's last reply to a CAPTCHA submission.
DEBUG_DUMP = Path(os.environ["CPPP_DEBUG_DUMP"]) if os.getenv("CPPP_DEBUG_DUMP") else None


class RelayError(RuntimeError):
    """CPPP answered with something this module does not recognise."""


class LinkRejected(RelayError):
    """CPPP accepted the CAPTCHA but its search does not list this tender."""


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
    image: str


def is_searchable(tender_id: str | None) -> bool:
    """Some buyers publish an id like "1", which no search could tell apart."""
    return len((tender_id or "").strip()) >= 5


def _normal(name: str | None) -> str:
    return " ".join((name or "").split()).lower()


@lru_cache(maxsize=1)
def _search_buyers() -> frozenset[str]:
    # ponytail: a snapshot of the form's menu, so viewing a tender costs CPPP no
    # request. A buyer added later shows the portal link until this is refreshed.
    path = Path(__file__).resolve().parents[1] / "config" / "cppp_search_buyers.txt"
    lines = path.read_text(encoding="utf-8").splitlines()
    return frozenset(_normal(x) for x in lines if x and not x.startswith("#"))


def holds_buyer(organization: str | None) -> bool:
    """The form lists 246 buyers. Railways, MES and NHAI, most of CPPP, are not
    among them, so a CAPTCHA for their tenders could only ever end in "not found"."""
    return _normal(organization) in _search_buyers()


def _client(cookies: dict | None = None) -> httpx.Client:
    return httpx.Client(
        base_url=BASE, follow_redirects=True, timeout=30, transport=TRANSPORT,
        headers={"User-Agent": user_agent()}, cookies=cookies,
    )


def parse_form(html: str) -> CaptchaForm | None:
    """The search form: action, every field at its default, CAPTCHA image."""
    form = HTMLParser(html).css_first("form#TenderAdvancedSearch")
    img = form.css_first("img#captchaImage") if form else None
    if img is None or form.css_first("input[name=captchaText]") is None:
        return None
    fields = {}
    for node in form.css("input"):
        name = node.attributes.get("name")
        kind = (node.attributes.get("type") or "text").lower()
        if name and kind == "hidden":
            fields[name] = node.attributes.get("value") or ""
        elif name and kind == "text":
            fields[name] = ""
    for node in form.css("select"):
        first = node.css_first("option[selected]") or node.css_first("option")
        if node.attributes.get("name") and first is not None:
            fields[node.attributes["name"]] = first.attributes.get("value") or ""
    return CaptchaForm(form.attributes.get("action") or "", fields,
                       img.attributes.get("src") or "")


def _pack(cookies: httpx.Cookies, form: CaptchaForm, base: str, tender_type: str) -> str:
    # Not dict(cookies): that raises CookieConflict once a cookie is re-set.
    raw = json.dumps({"c": {k.name: k.value for k in cookies.jar}, "f": form.fields,
                      "a": urljoin(base, form.action), "r": base, "t": tender_type},
                     separators=(",", ":"))
    return sign("cppp", base64.urlsafe_b64encode(raw.encode()).decode(), STATE_TTL)


def _unpack(state: str) -> dict | None:
    body = unsign("cppp", state)
    return json.loads(base64.urlsafe_b64decode(body.encode())) if body else None


def _captcha_from(resp: httpx.Response, client: httpx.Client, message: str,
                  tender_type: str) -> Captcha:
    form = parse_form(resp.text)
    if form is None or not form.image.startswith("data:image/"):
        raise RelayError(f"no CAPTCHA form at {resp.url} (HTTP {resp.status_code})")
    return Captcha(_pack(client.cookies, form, str(resp.url), tender_type), form.image, message)


def start(message: str = "", tender_type: str = OPEN_TENDER) -> Captcha:
    with _client() as c:
        return _captcha_from(c.get(FORM_URL), c, message, tender_type)


def tender_link(html: str, tender_id: str) -> str | None:
    """The link in the result row for exactly this tender id."""
    exact = re.compile(rf"(?<![\w-]){re.escape(tender_id)}(?![\w-])")
    for row in HTMLParser(html).css("table.list_table tr"):
        link = row.css_first("a[href]")
        if link is not None and exact.search(row.text()):
            return link.attributes["href"]
    return None


def portal_error(html: str) -> str:
    """CPPP's own error line. Only these nodes: its script holds the same words."""
    nodes = HTMLParser(html).css(".error")
    return " ".join(re.sub(r"\s+", " ", n.text()).strip() for n in nodes).strip()


def submit(state: str, tender_id: str, captcha_text: str) -> TenderPage | Captcha:
    """The tender page, or a fresh CAPTCHA with a message saying what went wrong."""
    data = _unpack(state)
    # The action came from CPPP's own form. Signed, so a visitor cannot change
    # it, but a page CPPP serves could still point it anywhere -- and this
    # server would then POST wherever it said. Only ever to CPPP.
    if data is not None and not str(data.get("a", "")).startswith(BASE + "/"):
        raise RelayError(f"CPPP form posts off-site: {data.get('a')!r}")
    if data is None:
        return start("That CAPTCHA expired. Here is a new one.")
    with _client(data["c"]) as c:
        resp = c.post(data["a"], data={**data["f"], "tenderId": tender_id,
                                       "TenderType": data["t"],
                                       "captchaText": captcha_text, "submit": "Search"},
                      headers={"Referer": data["r"]})
        if DEBUG_DUMP:
            DEBUG_DUMP.write_text(f"<!-- final {resp.url} -->\n{resp.text}", encoding="utf8")
        href = tender_link(resp.text, tender_id)
        if href:
            url = urljoin(str(resp.url), href)
            if not url.startswith(BASE + "/"):
                raise RelayError(f"CPPP result links off-site: {url!r}")
            page = c.get(url, headers={"Referer": str(resp.url)})
            return TenderPage(page.text, str(page.url))
        error = portal_error(resp.text)
        if "invalid captcha" in error.lower():
            return _captcha_from(resp, c, "CPPP says the CAPTCHA was wrong. Try this one.",
                                 data["t"])
    # The CAPTCHA passed and the search found nothing. CPPP shows its form again
    # for this too, so the form alone does not mean a wrong CAPTCHA. The menu is
    # required and our listing does not say which kind this is, so the other kind
    # gets one try.
    log.warning("CPPP search found no %s (type %s) at %s: %r",
                tender_id, data["t"], resp.url, error)
    if data["t"] == OPEN_TENDER:
        return start("One more step: CPPP checks limited tenders separately. "
                     "Please type this one too.", LIMITED_TENDER)
    raise LinkRejected(f"CPPP's search does not list {tender_id}")


def with_base(html: str, url: str) -> str:
    """Point CPPP's relative links, styles and images back at CPPP."""
    tag = f'<base href="{url}">'
    head = re.search(r"<head[^>]*>", html, re.I)
    return html[:head.end()] + tag + html[head.end():] if head else tag + html
