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
from .connectors.cppp import _CELLS, _FIRST_CELL_CHANGED, _NEXT, _TAB, _VISIBLE_COUNT, LISTING_URL

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
_REFRESH = """
await page.locator('.mdi-refresh, .mdi-reload').first().click();
await page.waitForTimeout(1500);
"""
# CPPP lays each block out as a ribbon title over label and value pairs.
_READ_DETAILS = """
return await page.evaluate(() => [...document.querySelectorAll('.card-Header')].map(card => ({
  title: (card.querySelector('.ribbon-text')?.innerText || '').trim(),
  fields: [...card.querySelectorAll('.v-list-item__content')].map(item => ({
    label: (item.querySelector('.listTitle')?.innerText || '').trim(),
    value: (item.querySelector('.Details')?.innerText || '').trim(),
  })).filter(f => f.label),
})).filter(s => s.fields.length));
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


def _find_code(tender_id: str) -> str:
    """Open the list, click this tender, return the CAPTCHA. Same steps and selectors
    as the connector. The tab receives its whole list at once, so turning pages asks
    CPPP for nothing further."""
    return _setup() + f"""
await page.goto({json.dumps(LISTING_URL)}, {{waitUntil: 'domcontentloaded', timeout: 60000}});
const count = {json.dumps(_VISIBLE_COUNT)};
await page.waitForFunction(`(${{count}})()`, null, {{timeout: 60000}});
const listed = page.waitForResponse(/tenderClosingListIn14days/, {{timeout: 120000}});
await page.getByText({json.dumps(_TAB)}, {{exact: true}}).click();
await listed;
await page.waitForFunction(`(${{count}})()`, null, {{timeout: 60000}});
const ending = '/' + {json.dumps(tender_id)};
const cells = {json.dumps(_CELLS)};
const changed = {json.dumps(_FIRST_CELL_CHANGED)};
for (let turn = 0; turn < 1000; turn++) {{
  const rows = await page.evaluate(`(${{cells}})()`);
  const at = rows.findIndex(r => (r[4] || '').trim().endsWith(ending));
  if (at >= 0) {{
    await page.locator('table tbody tr >> visible=true').nth(at).locator('a.table-Link-Url').click();
    await page.waitForURL(/#\\/captcha/, {{timeout: 30000}});
    {_IMAGE}
  }}
  const next = page.locator({json.dumps(_NEXT)});
  if (!rows.length || await next.isDisabled()) return null;
  await next.click();
  await page.waitForFunction(`(${{changed}})(${{JSON.stringify(rows[0][0])}})`, null, {{timeout: 30000}});
}}
return null;
"""


def start(tender_id: str) -> Captcha:
    with _client() as c:
        resp = c.post("/browsers", json={"headless": True, "timeout_seconds": BROWSER_SECONDS})
        if resp.status_code >= 400:
            raise RelayError(f"Kernel would not start a browser: HTTP {resp.status_code}")
        session = resp.json()["session_id"]
        try:
            image = _run(c, session, _find_code(tender_id), FIND_SECONDS)
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
await page.waitForURL(/tendersfullview/, {{timeout: 15000}}).catch(() => {{}});
if (!page.url().includes('tendersfullview')) {{
  {_REFRESH}
  const again = await (async () => {{ {_IMAGE} }})();
  return {{image: again}};
}}
await page.locator('.card-Header').first().waitFor({{timeout: 15000}});
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
