"""Central Public Procurement Portal (eprocure.gov.in), run by NIC.

Mandatory publication point for central government tenders above the GFR 2017
threshold, aggregating 100+ organisations -- including the GeM-integrated feed,
which is the compliant route to much of what GeM itself shows.

Scope note (rule #3): CPPP exposes the same data through two pages. The search
form on /cppp/latestactivetendersnew/cpppdata and the whole
/eprocure/app?page=FrontEndLatestActiveTenders page sit behind an image CAPTCHA,
so both are permanently off-limits. The DEFAULT listing on the cpppdata page is
server-rendered without any CAPTCHA, and its pagination links are plain URLs.
That default listing is the only thing this connector touches.
"""
from __future__ import annotations

import base64
import logging
import os
import re
from datetime import datetime
from typing import Iterator
from urllib.parse import quote

from selectolax.parser import HTMLParser

from ..schemas import TenderRecord
from .base import BaseConnector

log = logging.getLogger(__name__)

LISTING_PATH = "/cppp/latestactivetendersnew/cpppdata"
# CPPP joins the fields of its detail-link with this literal separator.
_SEGMENT_SEP = "A13h1"
_DATE_FORMATS = ("%d-%b-%Y %I:%M %p", "%d-%b-%Y %H:%M", "%d-%b-%Y")
# Canonical CPPP tender id, e.g. 2026_BPCL_26361 or 2026_JKRRD_149323_1.
_CANONICAL_TENDER_ID = re.compile(r"^\d{4}_[A-Za-z0-9]+_\d+(_\d+)?$")


def external_ref(raw: dict) -> str:
    """Stable unique key for a CPPP row.

    The last URL segment is usually the canonical tender id, but some organisations
    put their own internal number there instead (e.g. '166992'), which is only unique
    within that organisation. When it is not canonical we fall back to CPPP's own
    record id, which is portal-wide unique.
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


def _decode_link(href: str) -> list[str]:
    """CPPP detail links carry the record's fields as base64 segments.

    e.g. .../tendersfullview/<internal_id>A13h1<hash>A13h1<hash>A13h1<ts>A13h1<ref>A13h1<tender_id>
    Decoding them is more reliable than splitting the cell text, because both the
    title and the reference number routinely contain '/'.
    """
    tail = href.rsplit("/", 1)[-1]
    out = []
    for seg in tail.split(_SEGMENT_SEP):
        try:
            out.append(base64.b64decode(seg + "==").decode("utf-8", "replace"))
        except Exception:
            out.append("")
    return out


class CPPPConnector(BaseConnector):
    source_name = "CPPP"
    base_url = "https://eprocure.gov.in"
    license = "public-published"
    rate_limit_seconds = 3.0
    paths = (LISTING_PATH,)

    # A full crawl is ~3,200 pages. The scheduler runs incrementally with `since`,
    # which stops early because the listing is sorted by publication date desc.
    max_pages = int(os.getenv("CPPP_MAX_PAGES", "50"))
    # Backfills resume from here. A single 3,200-page run is long enough that one
    # stalled connection loses hours of work, so seed the database in chunks:
    #   tenders run CPPP --max-pages 300 --start-page 1
    #   tenders run CPPP --max-pages 300 --start-page 301   ...
    # Re-running a chunk is safe -- rows upsert on (source_id, external_ref).
    start_page = 1

    def page_url(self, page: int) -> str:
        """Page 1 is the bare listing; later pages use CPPP's base64 `url` param.

        A plain ?page=N is silently ignored by the site (it returns page 1), so the
        encoding below is not decoration -- it is the only pagination that works.
        """
        if page <= 1:
            return self.base_url + LISTING_PATH
        target = f"{self.base_url}{LISTING_PATH}?page={page}"
        return (
            f"{self.base_url}{LISTING_PATH}"
            f"?url={quote(base64.b64encode(target.encode()).decode(), safe='')}"
        )

    def parse_rows(self, html: str) -> list[dict]:
        rows = []
        tree = HTMLParser(html)
        for table in tree.css("table.list_table"):
            headers = [th.text(strip=True) for th in table.css("th")]
            if "Organisation Name" not in headers:
                continue  # the search form uses the same class
            for tr in table.css("tr"):
                cells = tr.css("td")
                if len(cells) < 7:
                    continue
                link = cells[4].css_first("a")
                if link is None:
                    continue
                href = link.attributes.get("href", "")
                segments = _decode_link(href)
                rows.append(
                    {
                        "serial": cells[0].text(strip=True),
                        "published": cells[1].text(strip=True),
                        "closing": cells[2].text(strip=True),
                        "opening": cells[3].text(strip=True),
                        "title": link.text(strip=True),
                        "organisation": cells[5].text(strip=True),
                        "corrigendum": cells[6].text(strip=True),
                        "url": href,
                        "internal_id": segments[0] if segments else "",
                        "reference_no": segments[-2] if len(segments) >= 2 else "",
                        "tender_id": segments[-1] if segments else "",
                    }
                )
        return rows

    def fetch_batch(self, since: datetime | None) -> Iterator[dict]:
        for page in range(self.start_page, self.start_page + self.max_pages):
            resp = self.get(self.page_url(page))
            if resp.status_code != 200:
                log.warning("CPPP: page %d returned HTTP %s, stopping", page, resp.status_code)
                return
            rows = self.parse_rows(resp.text)
            if not rows:
                log.info("CPPP: page %d had no rows, stopping", page)
                return
            for row in rows:
                # Listing is sorted by publication date descending, so once we are
                # past `since` there is nothing newer on later pages.
                published = parse_dt(row["published"])
                if since and published and published < since:
                    log.info("CPPP: reached records older than %s, stopping", since)
                    return
                yield row

    def normalize(self, raw: dict) -> TenderRecord:
        published = parse_dt(raw.get("published", ""))
        closing = parse_dt(raw.get("closing", ""))
        ref = external_ref(raw)
        return TenderRecord(
            external_ref=ref,
            title=raw.get("title") or raw.get("reference_no") or ref,
            organization=raw.get("organisation") or None,
            department=raw.get("organisation") or None,
            category=None,       # not published on the listing
            estimated_value=None,  # value lives on the detail page, not the listing
            published_date=published.date() if published else None,
            deadline=closing.date() if closing else None,
            status=None,         # derived from the deadline in TenderRecord
            document_url=raw.get("url") or None,
            source_url=raw.get("url") or (self.base_url + LISTING_PATH),
            raw_payload=raw,
        )
