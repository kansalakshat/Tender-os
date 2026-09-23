"""Government e-Marketplace -- https://bidplus.gem.gov.in

Reads the public bid listing at /all-bids and records each bid's document URL at
/showbidDocument/<id>. Both paths are permitted by the portal's robots.txt (see
app/compliance.py); /resources/ and the three /bg_emd/ service endpoints are not,
and this connector never constructs them.

Why Playwright and not httpx: /all-bids renders its list client-side. The bid rows
arrive from a POST to /all-bids-data that carries a per-page CSRF token, so there
is nothing in the served HTML to parse. Driving a real browser lets the page issue
its own request with its own token -- no token replay, and no stealth flags: the
portal answers a plainly-identified browser on the first try.

Rule #3 still holds. GeM puts no CAPTCHA on /all-bids or on the bid documents, so
nothing here solves or bypasses one. The parts of GeM that are gated stay unread.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterator

from ..compliance import user_agent
from ..schemas import TenderRecord
from .base import BaseConnector

log = logging.getLogger(__name__)

LISTING_PATH = "/all-bids"
DOCUMENT_PATH = "/showbidDocument/"
# GeM writes numeric months ("08-11-2025 12:46 PM"), unlike CPPP and GePNIC which
# write "08-Nov-2025". Kept local rather than added to cppp.parse_dt: teaching the
# shared parser %d-%m-%Y would make "03-04-2026" ambiguous for every other source.
_DATE_FORMATS = ("%d-%m-%Y %I:%M %p", "%d-%m-%Y %H:%M", "%d-%m-%Y")

_BID_NO = re.compile(r"BID NO:\s*(\S+)")
_ITEMS = re.compile(r"Items:\s*(.+)")
_QTY = re.compile(r"Quantity:\s*([\d,]+)")
_START = re.compile(r"Start Date:\s*(.+)")
_END = re.compile(r"End Date:\s*(.+)")

# One card -> the fields we keep. Runs in the page, so the browser does the text
# extraction and we ship one JSON array per page instead of many round trips.
_SHOWN_SELECTOR = ".totalRecord"
# The pager's own "Showing 11 - 20 records of 43719" line. Used to tell whether a
# page turned, because the card list swaps in place with no navigation to await.
_SHOWN_CHANGED = (
    "old => { const e = document.querySelector('.totalRecord');"
    " return e && e.innerText.trim() !== old; }"
)

_EXTRACT = """() => Array.from(document.querySelectorAll('.card')).map(c => {
    const a = c.querySelector("a[href*='showbidDocument']");
    return {
        doc_href: a ? a.getAttribute('href') : null,
        text: c.innerText.replace(/\\u00a0/g, ' '),
    };
}).filter(x => x.doc_href)"""


_SHOWN_RANGE = re.compile(r"Showing\s+([\d,]+)\s*-\s*([\d,]+)\s+records of\s+([\d,]+)")


def _at_last_page(shown: str) -> bool:
    """True when the pager's 'Showing A - B records of T' has reached T."""
    m = _SHOWN_RANGE.search(shown or "")
    if not m:
        return False
    _, upto, total = (int(g.replace(",", "")) for g in m.groups())
    return upto >= total


def parse_gem_dt(text: str) -> datetime | None:
    text = (text or "").strip()
    if not text:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None


def _one(pattern: re.Pattern, text: str) -> str:
    m = pattern.search(text)
    return m.group(1).strip() if m else ""


def parse_card(card: dict) -> dict | None:
    """One rendered bid card -> a flat dict. None if it does not look like a bid."""
    text = card.get("text") or ""
    bid_no = _one(_BID_NO, text)
    href = card.get("doc_href") or ""
    if not bid_no or not href:
        return None

    # "Department Name And Address:" is followed by the ministry and department on
    # their own lines, ending at the next labelled field.
    dept_lines: list[str] = []
    lines = [ln.strip() for ln in text.splitlines()]
    try:
        i = next(n for n, ln in enumerate(lines) if ln.startswith("Department Name And Address"))
    except StopIteration:
        i = -1
    if i >= 0:
        for ln in lines[i + 1:]:
            if not ln or re.match(r"^(Start Date|End Date|Items|Quantity):", ln):
                if ln:
                    break
                continue
            dept_lines.append(ln)

    return {
        "bid_no": bid_no,
        "items": _one(_ITEMS, text),
        "quantity": _one(_QTY, text).replace(",", ""),
        "ministry": dept_lines[0] if dept_lines else None,
        "department": dept_lines[1] if len(dept_lines) > 1 else None,
        "start": _one(_START, text),
        "end": _one(_END, text),
        "bid_id": href.rsplit("/", 1)[-1],
        "doc_href": href,
    }


class GeMConnector(BaseConnector):
    source_name = "GeM"
    base_url = "https://bidplus.gem.gov.in"
    license = "public-published"
    paths = (LISTING_PATH, DOCUMENT_PATH)
    rate_limit_seconds = 3.0
    requires_browser = True
    # ~4,372 pages of 10. One run does not try to walk them all -- new bids land
    # on page 1, so a run only has to be deep enough to cover what was published
    # since the last one.
    #
    # Was 40 (400 records), sized for a 6-hourly schedule that in practice never
    # ran: GeM is browser-driven, so Vercel's /cron/ingest skips it, and the only
    # thing that fetches it is the daily task, so 400 a day was a fifth of the
    # inflow and the corpus drained instead of holding.
    #
    # 500 is sized from the measured decay of the 16 Sep backfill, not a guess.
    # Of 41,394 bids fetched that day, 57% had closed within six days and the
    # median remaining life was ~5 days; ~7 days average remaining life against
    # ~43,700 listed bids implies ~14 days average total life, so GeM publishes
    # on the order of 3,100 new bids a day. 300 pages (3,000) sat exactly on that
    # line, where one slow day is a hole that never fills. 500 is ~25 minutes at
    # the 3s rate limit -- still well inside the daily task's 2h ceiling.
    max_pages = 500
    # Where to begin. Only a bulk backfill sets this: because _open can jump to any
    # page with loadBids(), several processes can each take a slice of the listing
    # and run at the same time. The scheduled run always starts at 1.
    start_page = 1

    # Chromium's memory climbs with every page turn on this listing -- a 4,400-page
    # run was killed by the OS at page ~340. The browser is thrown away and rebuilt
    # this often to cap the footprint. Cheap, because _open jumps straight back to
    # where it was; routine runs never reach it anyway, max_pages being 40.
    recycle_every = 75
    # Count of 429/503 responses seen since the last page turn.
    _throttled = 0

    def _open(self, p, at_page: int):
        """A fresh browser positioned on `at_page`.

        Jumps straight there with the listing's own loadBids(n) -- the same
        function its pager calls, so the page builds and sends its own request.
        Clicking forward one page at a time instead would make each recycle cost
        everything before it: reaching page 4,365 in hundred-page hops would be
        ~97,000 clicks, about forty hours.

        Retried, because this runs on every recycle as well as at startup: one
        slow load here used to raise straight out of fetch_batch and end the run,
        which cost a 730-page slice 2,000 records at page ~520.
        """
        last: Exception | None = None
        for attempt in range(1, self.page_retries + 1):
            browser = p.chromium.launch(headless=True)
            try:
                page = browser.new_context(user_agent=user_agent()).new_page()
                self._watch_for_throttling(page)
                page.goto(self.base_url + LISTING_PATH,
                          wait_until="domcontentloaded", timeout=60_000)
                page.wait_for_selector(_SHOWN_SELECTOR, state="attached", timeout=30_000)
                if at_page > 1:
                    before = self._shown(page)
                    page.evaluate(f"loadBids({at_page})")
                    page.wait_for_function(_SHOWN_CHANGED, arg=before, timeout=30_000)
                return browser, page
            except Exception as exc:
                last = exc
                log.warning("%s: opening at page %d failed (attempt %d/%d): %s",
                            self.source_name, at_page, attempt, self.page_retries,
                            str(exc)[:100])
                browser.close()
        raise last or RuntimeError("could not open the listing")

    def _watch_for_throttling(self, page) -> None:
        """Notice when the portal asks us to slow down.

        fetch_batch drives a browser, so it never goes through BaseConnector.get()
        and never sees its 429/5xx backoff. Without this a throttled run would
        just start failing pages with no idea why, and would keep hammering at
        the same rate while doing it.
        """
        def on_response(resp):
            if resp.status in (429, 503):
                self._throttled += 1
                log.warning("%s: HTTP %s from %s -- backing off",
                            self.source_name, resp.status, resp.url.split("?")[0])

        page.on("response", on_response)

    def _throttle_pause(self, page) -> None:
        """Wait out any throttling seen since the last page, then clear the flag."""
        if not self._throttled:
            return
        # Exponential in the number of complaints, capped: a portal that is busy
        # should be given real room, not retried at the same cadence.
        pause = min(60, 5 * (2 ** (self._throttled - 1)))
        log.warning("%s: %d throttling response(s), pausing %ds",
                    self.source_name, self._throttled, pause)
        page.wait_for_timeout(pause * 1000)
        self._throttled = 0

    def fetch_batch(self, since: datetime | None) -> Iterator[dict]:
        from playwright.sync_api import sync_playwright

        self._throttled = 0

        with sync_playwright() as p:
            first, last = self.start_page, self.start_page + self.max_pages - 1
            browser, page = self._open(p, first)
            try:
                for n in range(first, last + 1):
                    if n > first and (n - first) % self.recycle_every == 0:
                        log.info("%s: recycling the browser at page %d", self.source_name, n)
                        browser.close()
                        browser, page = self._open(p, n)
                    cards = page.evaluate(_EXTRACT)
                    if not cards:
                        log.info("%s: page %d had no cards, stopping", self.source_name, n)
                        return
                    for card in cards:
                        row = parse_card(card)
                        if row is None:
                            continue
                        # Listing is newest-first, but one stale row does not mean
                        # the rest are -- filter and keep going, as GePNIC does.
                        if since:
                            started = parse_gem_dt(row["start"])
                            if started and started < since:
                                continue
                        yield row
                    if not self._next_page(page, n):
                        return
            finally:
                browser.close()

    # A single slow page used to end the whole crawl. The portal is simply slow
    # sometimes, so a stall is retried before it is believed.
    page_retries = 3

    @staticmethod
    def _shown(page) -> str:
        """The pager's own "Showing 11 - 20 records of 43646" line.

        Used instead of "has the first bid link changed?" to tell whether a page
        turned. That earlier check reported a stall whenever two consecutive
        pages happened to lead with the same bid, and the retry then found the
        target page link gone -- because the click HAD worked -- so a 4,400-page
        crawl stopped at page 78 believing it had run out of pages.
        """
        el = page.query_selector(_SHOWN_SELECTOR)
        return (el.inner_text().strip() if el else "")

    def record_listing_total(self, shown: str) -> int | None:
        """Pull the portal's own total out of "Showing A - B records of T".

        Stored on the source row so the dashboard can say what is left to
        collect. Reading it costs nothing -- the pager line is already fetched
        on every page turn to tell whether the page changed.
        """
        m = _SHOWN_RANGE.search(shown or "")
        if not m:
            return None
        return int(m.group(3).replace(",", ""))

    def _next_page(self, page, current: int) -> bool:
        """Advance to page `current + 1`. False when there is no next page.

        Calls loadBids() rather than clicking a pager link. The pager's own links
        are not usable for this: after a loadBids() jump the listing swaps in the
        new cards but leaves the pager showing the page-1 window, so from page
        2000 there is no "#page-2001" to click and a click-driven crawl reads
        that as the end of the listing after exactly one page.
        """
        self._throttle_pause(page)
        before = self._shown(page)
        total = self.record_listing_total(before)
        if total is not None:
            self.listing_total = total
        if _at_last_page(before):
            log.info("%s: page %d is the last, stopping", self.source_name, current)
            return False
        for attempt in range(1, self.page_retries + 1):
            try:
                page.evaluate(f"loadBids({current + 1})")
                page.wait_for_function(_SHOWN_CHANGED, arg=before, timeout=30_000)
            except Exception as exc:
                log.warning(
                    "%s: page %d stalled (attempt %d/%d): %s",
                    self.source_name, current + 1, attempt, self.page_retries,
                    str(exc)[:100],
                )
                page.wait_for_timeout(2_000 * attempt)
                continue
            page.wait_for_timeout(int(self.rate_limit_seconds * 1000))
            return True
        log.warning("%s: page %d never loaded, stopping", self.source_name, current + 1)
        return False

    def normalize(self, raw: dict) -> TenderRecord:
        start = parse_gem_dt(raw.get("start", ""))
        end = parse_gem_dt(raw.get("end", ""))
        return TenderRecord(
            external_ref=raw["bid_no"],
            title=raw.get("items") or raw.get("bid_no") or "",
            organization=raw.get("ministry"),
            department=raw.get("department") or raw.get("ministry"),
            category=None,
            estimated_value=None,  # GeM does not publish a value on the listing
            published_date=start.date() if start else None,
            deadline=end.date() if end else None,
            status=None,  # derived from the deadline in TenderRecord
            # The whole point of this connector: a stable, publicly fetchable PDF.
            document_url=f"{self.base_url}{DOCUMENT_PATH}{raw['bid_id']}",
            source_url=self.base_url + LISTING_PATH,
            raw_payload=raw,
        )
