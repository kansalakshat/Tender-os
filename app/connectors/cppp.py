"""Central Public Procurement Portal (eprocure.gov.in), run by NIC.

Mandatory publication point for central government tenders above the GFR 2017
threshold, aggregating 100+ organisations -- including the GeM-integrated feed,
which is the compliant route to much of what GeM itself shows.

Scope note (rule #3): by November 2026 the portal is a JavaScript site. Its Active
Tenders and Tender Search pages sit behind an image CAPTCHA, so both are permanently
off-limits. The Closing Today page's "Closing within 14 days" tab shows central
tenders with no CAPTCHA, and that tab is the only listing this connector reads.

It reads the table a visitor sees, in a real browser. The page's data service uses
encrypted requests and a token; calling it directly would mean copying that
protection, so this does not. The tab receives its whole list in one response and
turns pages locally, so walking it asks the portal for nothing further.

Ceiling: a tender closing more than 14 days away appears once it enters the window.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Iterator

from ..compliance import user_agent
from ..schemas import TenderRecord
from .base import BaseConnector

log = logging.getLogger(__name__)

LISTING_PATH = "/cppp/"
LISTING_URL = "https://eprocure.gov.in/cppp/#/tendersClosingToday"
_TAB = "Closing within 14 days"
_LIST_RESPONSE = "**/centralTenderCalenderPublic/tenderClosingListIn14days"
_HEADING = "Central - Tenders Closing in 14 days"

# Shared with the GePNIC connectors, whose portals write "08-Nov-2025".
_DATE_FORMATS = ("%d-%b-%Y %I:%M %p", "%d-%b-%Y %H:%M", "%d-%b-%Y")
# This listing writes "03-11-2026 05:30 PM". Kept local for the reason gem.py gives:
# in the shared parser, %d-%m-%Y would make "03-04-2026" ambiguous for other sources.
_LISTING_DATE_FORMAT = "%d-%m-%Y %I:%M %p"
_IST = timezone(timedelta(hours=5, minutes=30))
# Canonical CPPP tender id, e.g. 2026_BPCL_26361 or 2026_JKRRD_149323_1.
_CANONICAL_TENDER_ID = re.compile(r"^\d{4}_[A-Za-z0-9]+_\d+(_\d+)?$")

# Every tab keeps its table and pagination in the page, hidden, so each of these must
# find the one on show or it reads Closing Today's rows instead.
_SHOWN_TABLE = "[...document.querySelectorAll('table')].find(t => t.offsetParent !== null)"
_VISIBLE_COUNT = f"""() => {{
  const shown = [...document.querySelectorAll('.total-count')]
    .find(e => e.offsetParent !== null);
  const n = shown ? parseInt(shown.textContent.replace(/\D/g, ''), 10) : 0;
  return n > 0 && {_SHOWN_TABLE}?.querySelector('tbody tr td') ? n : 0;
}}"""
_CELLS = f"""() => [...{_SHOWN_TABLE}.querySelectorAll('tbody tr')]
  .map(tr => [...tr.children].map(td => td.innerText.trim()))"""
_FIRST_CELL_CHANGED = f"""first => {_SHOWN_TABLE}
  ?.querySelector('tbody tr td')?.innerText.trim() !== first"""
_NEXT = "nav.v-pagination:visible >> nth=0 >> li.v-pagination__next button"


def external_ref(raw: dict) -> str:
    """Stable unique key for a CPPP row.

    The last segment is usually the canonical tender id, but some organisations
    put their own internal number there instead (e.g. '166992'), which is only unique
    within that organisation. When it is not canonical we fall back to a portal-wide
    unique id.
    """
    tender_id = (raw.get("tender_id") or "").strip()
    internal_id = (raw.get("internal_id") or "").strip()
    if _CANONICAL_TENDER_ID.match(tender_id):
        return tender_id
    if internal_id:
        return f"cppp-{internal_id}"
    return tender_id


def parse_dt(text: str) -> datetime | None:
    text = (text or "").strip()
    if not text or text in {"--", "-", "NA"}:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def listing_dt(text: str) -> datetime | None:
    try:
        return datetime.strptime((text or "").strip(), _LISTING_DATE_FORMAT)
    except ValueError:
        return None


def parse_row(cells: list[str]) -> dict | None:
    """One table row: S.No, published, closing, opening, title/ref/id, organisation.

    The title and reference number arrive joined, and both routinely contain '/',
    so only the tender id can be split off safely. The rest stays together as the
    title: nothing is lost, and the reference number remains searchable.
    """
    if len(cells) < 6 or "/" not in cells[4]:
        return None
    title, _, tender_id = cells[4].strip().rpartition("/")
    organisation = cells[5].strip()
    return {
        "serial": cells[0],
        "published": cells[1],
        "closing": cells[2],
        "opening": cells[3],
        "title": title.strip(),
        "organisation": organisation,
        "tender_id": tender_id.strip(),
        "internal_id": hashlib.sha1(f"{organisation}|{cells[4].strip()}".encode()).hexdigest()[:16],
        # Blank on purpose: merged over a stored row, it retires the expiring
        # /cppp/tendersfullview/ link the old listing gave.
        "url": "",
    }


def is_open(raw: dict, now: datetime | None = None) -> bool:
    """False once bidding has closed; the Closing Today list keeps those all day."""
    closing = listing_dt(raw.get("closing", ""))
    now = now or datetime.now(_IST).replace(tzinfo=None)
    return closing is None or closing >= now


class CPPPConnector(BaseConnector):
    source_name = "CPPP"
    base_url = "https://eprocure.gov.in"
    license = "public-published"
    paths = (LISTING_PATH,)
    requires_browser = True

    # ~400 pages of 25 in November 2026. A bound against a runaway walk, not a slice.
    max_pages = int(os.getenv("CPPP_MAX_PAGES", "1000"))

    def _open_list(self, page) -> int:
        """Open the 14-day tab and return the portal's own count for it."""
        page.goto(LISTING_URL, wait_until="domcontentloaded", timeout=60_000)
        page.wait_for_function(_VISIBLE_COUNT, timeout=60_000)
        # Waited for, or the count read next could still be Closing Today's.
        with page.expect_response(_LIST_RESPONSE, timeout=120_000):
            page.get_by_text(_TAB, exact=True).click()
        page.get_by_role("heading", name=_HEADING).wait_for(timeout=30_000)
        return page.wait_for_function(_VISIBLE_COUNT, timeout=60_000).json_value()

    def fetch_batch(self, since: datetime | None) -> Iterator[dict]:
        """Every open tender on the tab, whatever `since` says.

        A tender published weeks ago first appears here when it comes within 14
        days of closing, so a publication-date cutoff would drop tenders we have
        never seen. Rows already held cost only a fingerprint in BaseConnector.
        """
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_context(user_agent=user_agent()).new_page()
                self.listing_total = self._open_list(page)
                seen = 0
                for number in range(1, self.max_pages + 1):
                    cells = page.evaluate(_CELLS)
                    rows = [r for r in map(parse_row, cells) if r]
                    if not rows:
                        break
                    seen += len(rows)
                    yield from filter(is_open, rows)
                    next_button = page.locator(_NEXT)
                    if next_button.is_disabled():
                        break
                    next_button.click()
                    page.wait_for_function(_FIRST_CELL_CHANGED, arg=cells[0][0], timeout=30_000)
            finally:
                browser.close()
        # A redesign must read as a failure, never as a quiet "ok, 0 tenders".
        if not seen:
            raise RuntimeError("CPPP: the 14-day list showed no rows; has the page changed?")
        log.info("%s: read %d of the %d tenders the portal lists",
                 self.source_name, seen, self.listing_total)

    def normalize(self, raw: dict) -> TenderRecord:
        published = listing_dt(raw.get("published", ""))
        closing = listing_dt(raw.get("closing", ""))
        ref = external_ref(raw)
        return TenderRecord(
            external_ref=ref,
            title=raw.get("title") or ref,
            organization=raw.get("organisation") or None,
            department=raw.get("organisation") or None,
            category=None,       # not published on the listing
            estimated_value=None,  # value lives on the detail page, not the listing
            published_date=published.date() if published else None,
            deadline=closing.date() if closing else None,
            status=None,         # derived from the deadline in TenderRecord
            document_url=None,   # the listing links no document
            source_url=LISTING_URL,
            raw_payload=raw,
        )
