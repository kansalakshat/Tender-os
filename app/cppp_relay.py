"""Open one CPPP tender for a visitor who types CPPP's CAPTCHA on our site.

This is NOT a connector and does NOT solve CAPTCHAs. Nothing here runs unless a
person asks for a tender and then types the CAPTCHA image themselves. The flow:

1. start()  -- in a Kernel cloud browser, open CPPP's "Closing within 14 days" list,
   find the tender, click it as a visitor would, and hand back the CAPTCHA image.
   Kernel holds the browser on standby, unbilled, while the visitor types.
2. submit() -- type what the visitor entered and return the details CPPP shows.

Vercel cannot run a browser and CPPP keeps its CAPTCHA in the live page, so the
browser has to survive between the two requests. Tested 2026-11-03: the same CAPTCHA
was still there after about two minutes on standby.

State between the two steps is signed JSON in a hidden form field, so nothing is
stored here. The Playwright code runs on Kernel's machine, so every value put into
it goes through json.dumps.
"""
from __future__ import annotations

import base64
import json
import os
import re
from dataclasses import dataclass
from datetime import date, timedelta

import httpx

from .auth import sign, unsign
from .compliance import user_agent
from .connectors.cppp import _HEADING, _TAB, _VISIBLE_COUNT, LISTING_URL

API = "https://api.onkernel.com"
LIST_DAYS = 14
STATE_TTL = 600
BROWSER_SECONDS = 600       # Kernel deletes a browser left idle this long
FIND_SECONDS = 240          # inside Vercel's 300 s ceiling

TRANSPORT: httpx.BaseTransport | None = None  # tests swap in a MockTransport
_CAPTCHA_TEXT = re.compile(r"^[A-Za-z0-9]{1,20}$")
# Below about 1280 wide CPPP does not draw the CAPTCHA image at all.
_SETUP = """
await page.setViewportSize({width: 1280, height: 900});
const cdp = await context.newCDPSession(page);
await cdp.send('Network.setUserAgentOverride', {userAgent: %s});
"""
_IMAGE = """
const image = page.locator('img[alt*="aptcha"]').first();
await image.waitFor({timeout: 30000});
await page.waitForTimeout(1000);
return (await image.screenshot()).toString('base64');
"""
# After a rejected answer something on CPPP's page can cover this icon, which timed
# the click out and showed the visitor "CPPP is not answering". A new image is only
# a nicety, so failing to get one must not end the lookup.
_REFRESH = """
await page.keyboard.press('Escape').catch(() => {});
await page.locator('.mdi-refresh, .mdi-reload').first().click({timeout: 5000}).catch(() => {});
await page.waitForTimeout(1500);
"""
# CPPP lays each block out as a ribbon title over label and value pairs. Read in page
# order, because only some blocks sit inside .card-Header: the two that hold the tender
# document and the buyer's address do not.
_READ_DETAILS = """
return await page.evaluate(() => {
  const sections = [];
  for (const el of document.querySelectorAll('.ribbon-text, .v-list-item__content')) {
    if (el.matches('.ribbon-text')) { sections.push({title: el.innerText.trim(), fields: []}); continue; }
    const label = (el.querySelector('.listTitle')?.innerText || '').trim();
    if (label && sections.length) sections[sections.length - 1].fields.push(
      {label, value: (el.querySelector('.Details')?.innerText || '').trim()});
  }
  return sections.filter(s => s.fields.length);
});
"""


class RelayError(RuntimeError):
    """Kernel or CPPP answered with something this module does not recognise."""


class NotListed(RelayError):
    """CPPP's 14-day list does not show this tender."""


class Expired(RelayError):
    """The signed state or the cloud browser behind it has gone."""


@dataclass
class Captcha:
    state: str       # signed, goes back in a hidden field
    image: str       # data:image/... URI
    message: str = ""


@dataclass
class TenderDetails:
    sections: list[dict]


def enabled() -> bool:
    return bool(os.getenv("KERNEL_API_KEY"))


def can_open(tender_id: str | None, deadline: date | None, today: date | None = None) -> bool:
    """Only tenders on the 14-day list, with an id that tells one row from another:
    some buyers publish an id like "1"."""
    today = today or date.today()
    return (enabled() and len((tender_id or "").strip()) >= 5 and deadline is not None
            and today <= deadline <= today + timedelta(days=LIST_DAYS))


def _client() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=FIND_SECONDS + 30, transport=TRANSPORT,
                        headers={"Authorization": f"Bearer {os.environ['KERNEL_API_KEY']}"})


def _run(client: httpx.Client, session: str, code: str, seconds: int = 60):
    resp = client.post(f"/browsers/{session}/playwright/execute",
                       json={"code": code, "timeout_sec": seconds})
    if resp.status_code == 404:
        raise Expired(f"browser {session} has gone")
    if resp.status_code >= 400:
        raise RelayError(f"Kernel answered HTTP {resp.status_code}")
    body = resp.json()
    if not body.get("success"):
        raise RelayError(f"browser step failed: {str(body.get('error'))[:300]}")
    return body.get("result")


def _delete(client: httpx.Client, session: str) -> None:
    # Best effort: if this fails, Kernel deletes the browser at its own timeout.
    try:
        client.delete(f"/browsers/{session}")
    except httpx.HTTPError:
        pass


def _pack(session: str, tender_id: str) -> str:
    raw = json.dumps({"s": session, "t": tender_id}, separators=(",", ":"))
    return sign("cppp", base64.urlsafe_b64encode(raw.encode()).decode(), STATE_TTL)


def _session_for(state: str, tender_id: str) -> str:
    body = unsign("cppp", state)
    data = json.loads(base64.urlsafe_b64decode(body.encode())) if body else None
    if not data or data.get("t") != tender_id:
        raise Expired("state expired, forged, or for another tender")
    return data["s"]


def _png(data: str) -> str:
    return f"data:image/png;base64,{data}"


def _setup() -> str:
    return _SETUP % json.dumps(user_agent())


def _find_code(tender_id: str, from_end: bool) -> str:
    """Open the list, click this tender, return the CAPTCHA. Same list as the connector.

    The tab receives its whole list at once and turns pages locally, so walking it
    asks CPPP for nothing further. Two things keep the visitor's wait short: searching
    inside the page (65 ms a page against 160 ms) and starting from the nearer end,
    since the list is sorted by closing date.

    Closing Today is waited for before clicking the tab. Clicking sooner read that
    table instead (1,556 tenders, not about 9,800) and missed tenders.
    """
    return _setup() + f"""
await page.goto({json.dumps(LISTING_URL)}, {{waitUntil: 'domcontentloaded', timeout: 60000}});
const count = {json.dumps(_VISIBLE_COUNT)};
await page.waitForFunction(`(${{count}})()`, null, {{timeout: 60000}});
const listed = page.waitForResponse(/tenderClosingListIn14days/, {{timeout: 120000}});
await page.getByText({json.dumps(_TAB)}, {{exact: true}}).click();
await listed;
await page.getByRole('heading', {{name: {json.dumps(_HEADING)}}}).waitFor({{timeout: 30000}});
await page.waitForFunction(`(${{count}})()`, null, {{timeout: 60000}});
const at = await page.evaluate(async ({{ending, fromEnd}}) => {{
  const shown = list => [...document.querySelectorAll(list)].find(e => e.offsetParent !== null);
  const rows = () => [...shown('table').querySelectorAll('tbody tr')];
  const first = () => rows()[0]?.children[0]?.innerText.trim();
  const turn = async button => {{
    const before = first();
    button.click();
    for (let waited = 0; first() === before; waited += 5) {{
      if (waited > 15000) throw new Error('the list stopped turning');
      await new Promise(done => setTimeout(done, 5));
    }}
  }};
  const nav = shown('nav.v-pagination');
  if (fromEnd) await turn([...nav.querySelectorAll('li.v-pagination__item button')].pop());
  const step = nav.querySelector(fromEnd ? 'li.v-pagination__prev button' : 'li.v-pagination__next button');
  for (let pages = 0; pages < 2000; pages++) {{
    const found = rows().findIndex(r => (r.children[4]?.innerText || '').trim().endsWith(ending));
    if (found >= 0) return found;
    if (step.disabled) return -1;
    await turn(step);
  }}
  return -1;
}}, {{ending: '/' + {json.dumps(tender_id)}, fromEnd: {json.dumps(from_end)}}});
if (at < 0) return null;
await page.locator('table tbody tr >> visible=true').nth(at).locator('a.table-Link-Url').click();
await page.waitForURL(/#\\/captcha/, {{timeout: 30000}});
{_IMAGE}
"""


def start(tender_id: str, deadline: date | None = None) -> Captcha:
    with _client() as c:
        resp = c.post("/browsers", json={"headless": True, "timeout_seconds": BROWSER_SECONDS})
        if resp.status_code >= 400:
            raise RelayError(f"Kernel would not start a browser: HTTP {resp.status_code}")
        session = resp.json()["session_id"]
        try:
            from_end = deadline is not None and deadline > date.today() + timedelta(days=LIST_DAYS // 2)
            image = _run(c, session, _find_code(tender_id, from_end), FIND_SECONDS)
        except RelayError:
            _delete(c, session)
            raise
        if not image:
            _delete(c, session)
            raise NotListed(f"{tender_id} is not on CPPP's 14-day list")
        return Captcha(_pack(session, tender_id), _png(image))


def new_image(state: str, tender_id: str) -> Captcha:
    """Another CAPTCHA from the same browser, for one the visitor cannot read."""
    session = _session_for(state, tender_id)
    with _client() as c:
        image = _run(c, session, _setup() + _REFRESH + _IMAGE)
    return Captcha(_pack(session, tender_id), _png(image))


def submit(state: str, tender_id: str, captcha_text: str) -> TenderDetails | Captcha:
    """The tender's details, or a fresh CAPTCHA with a message saying what went wrong."""
    session = _session_for(state, tender_id)
    text = captcha_text.strip()
    with _client() as c:
        if not _CAPTCHA_TEXT.match(text):
            image = _run(c, session, _setup() + _IMAGE)
            return Captcha(_pack(session, tender_id), _png(image),
                           "Please type only the letters and numbers in the image.")
        result = _run(c, session, _setup() + f"""
await page.getByRole('textbox').first().fill({json.dumps(text)});
await page.getByRole('button', {{name: 'Submit'}}).click();
await page.waitForURL(/tendersfullview/, {{timeout: 20000}}).catch(() => {{}});
if (!page.url().includes('tendersfullview')) {{
  {_REFRESH}
  const again = await (async () => {{ {_IMAGE} }})();
  return {{image: again}};
}}
// The page appears before its data: the blocks are drawn first and filled in after.
await page.waitForFunction(() => [...document.querySelectorAll('.Details')]
  .some(e => e.innerText.trim()), null, {{timeout: 30000}});
await page.waitForTimeout(500);
const details = await (async () => {{ {_READ_DETAILS} }})();
return {{details}};
""")
        if result and result.get("image"):
            return Captcha(_pack(session, tender_id), _png(result["image"]),
                           "CPPP did not accept that. Please try this new image.")
        _delete(c, session)
    if not result or not result.get("details"):
        raise RelayError("CPPP's tender page came back empty")
    return TenderDetails(result["details"])
