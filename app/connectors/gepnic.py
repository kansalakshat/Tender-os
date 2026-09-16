"""Generic connector for state e-procurement portals running NIC's GePNIC engine.

There are ~48 GePNIC instances nationally. They share a page structure, so one
connector serves all of them -- but each is a **separate domain with its own
robots.txt and its own terms**, and one state's permissiveness says nothing about
another's. Subclassing this class is therefore not enough to make a state run: the
domain must also be verified by hand and added to config/approved_sources.yaml,
or BaseConnector.run refuses.

Scope note (rule #3): like CPPP, GePNIC portals put an image CAPTCHA on their
richer listings -- FrontEndLatestActiveTenders and FrontEndTendersByOrganisation
both say "Provide Captcha and click on Search button to list ...". Those are
permanently off-limits. FrontEndListTendersbyDate renders server-side with no
CAPTCHA at all, and that is the only page this connector reads.

Known ceiling, found 2026-09-14: that page's default view is "Tenders/Auctions
Closing Today", so this connector only ever sees tenders closing on the day it
runs -- and late in the evening, none ("No Tenders found."). The same page has
"Closing within 7 days" / "Closing within 14 days" tabs, but they are Tapestry
form submits (tapestry.form.submit), not links. One plain POST of the form's
own hidden fields with submitname=LinkSubmit_1 was answered with the portal's
home page, not the listing. Making that work means replaying the site's
session and token flow; do it only after a human confirms in a browser that
the 14-day tab carries no CAPTCHA and records that in approved_sources.yaml.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterator
from urllib.parse import urljoin

from selectolax.parser import HTMLParser

from ..schemas import TenderRecord
from .base import BaseConnector
from .cppp import parse_dt

log = logging.getLogger(__name__)

# The un-gated listing. Do not point this at FrontEndLatestActiveTenders.
LISTING_PAGE = "FrontEndListTendersbyDate"
LISTING_PATH = f"/nicgep/app?page={LISTING_PAGE}&service=page"
# Tapestry's pager. The links are session-bound tokens, so pages must be followed
# rather than constructed -- there is no ?page=N to build.
_PAGER = "TablePages.linkPage"
# Title cell reads: <a>[Title]</a> [Reference No][Tender Id]
_BRACKETED = re.compile(r"\[([^\]]*)\]")


def split_org_chain(chain: str) -> tuple[str | None, str | None]:
    """GePNIC publishes 'Department||Division||Office' in one cell."""
    parts = [p.strip() for p in (chain or "").split("||") if p.strip()]
    if not parts:
        return None, None
    return parts[0], (parts[-1] if len(parts) > 1 else None)


class GePNICConnector(BaseConnector):
    """Base for a GePNIC instance. Subclasses set source_name and base_url."""

    license = "public-published"
    rate_limit_seconds = 3.0
    paths = (LISTING_PATH.split("?")[0],)
    max_pages = 25

    def parse_rows(self, html: str) -> list[dict]:
        rows: list[dict] = []
        for table in HTMLParser(html).css("table.list_table"):
            cell_rows = [tr for tr in table.css("tr") if len(tr.css("td")) >= 6]
            if not cell_rows:
                continue
            header = " ".join(c.text(strip=True) for c in cell_rows[0].css("td"))
            # GePNIC reuses class="list_table" for its search forms; the header row
            # is what identifies the actual tender listing.
            if "Organisation Chain" not in header:
                continue
            for tr in cell_rows[1:]:
                cells = tr.css("td")
                title_cell = cells[4]
                link = title_cell.css_first("a")
                # Bracketed groups: the anchor holds [Title], the trailing text
                # holds [Reference No][Tender Id].
                groups = _BRACKETED.findall(title_cell.text())
                organisation, department = split_org_chain(cells[5].text(strip=True))
                rows.append(
                    {
                        "serial": cells[0].text(strip=True),
                        "published": cells[1].text(strip=True),
                        "closing": cells[2].text(strip=True),
                        "opening": cells[3].text(strip=True),
                        "title": (link.text(strip=True).strip("[] ") if link else
                                  (groups[0] if groups else "")),
                        "reference_no": groups[-2].strip() if len(groups) >= 2 else "",
                        "tender_id": groups[-1].strip() if groups else "",
                        "organisation_chain": cells[5].text(strip=True),
                        "organisation": organisation,
                        "department": department,
                        # Detail links carry a Tapestry session token and expire, so
                        # they are kept for auditing but never published as a URL.
                        "detail_link": link.attributes.get("href", "") if link else "",
                    }
                )
        return rows

    def next_page_url(self, html: str, current: int, base: str) -> str | None:
        for anchor in HTMLParser(html).css("a"):
            href = anchor.attributes.get("href", "")
            if _PAGER in href and anchor.text(strip=True) == str(current + 1):
                return urljoin(base, href)
        return None

    def fetch_batch(self, since: datetime | None) -> Iterator[dict]:
        url = urljoin(self.base_url, LISTING_PATH)
        for page in range(1, self.max_pages + 1):
            resp = self.get(url)
            if resp.status_code != 200:
                log.warning("%s: page %d -> HTTP %s, stopping",
                            self.source_name, page, resp.status_code)
                return
            rows = self.parse_rows(resp.text)
            if not rows:
                log.info("%s: page %d had no rows, stopping", self.source_name, page)
                return
            for row in rows:
                # Unlike CPPP this listing is ordered by closing date, not publication
                # date, so an old record does not mean the rest are old -- filter the
                # row and keep going instead of stopping the crawl.
                if since:
                    published = parse_dt(row["published"])
                    if published and published < since:
                        continue
                yield row

            url = self.next_page_url(resp.text, page, str(resp.url))
            if not url:
                log.info("%s: no page %d, stopping", self.source_name, page + 1)
                return

    def normalize(self, raw: dict) -> TenderRecord:
        published = parse_dt(raw.get("published", ""))
        closing = parse_dt(raw.get("closing", ""))
        ref = (raw.get("tender_id") or "").strip() or (raw.get("reference_no") or "").strip()
        return TenderRecord(
            external_ref=ref,
            title=raw.get("title") or raw.get("reference_no") or ref,
            organization=raw.get("organisation"),
            department=raw.get("department") or raw.get("organisation"),
            category=None,        # not published on this listing
            estimated_value=None,  # value lives behind the CAPTCHA'd pages
            published_date=published.date() if published else None,
            deadline=closing.date() if closing else None,
            status=None,          # derived from the deadline in TenderRecord
            document_url=None,    # detail links expire with the session
            source_url=urljoin(self.base_url, LISTING_PATH),
            raw_payload=raw,
        )


# Every verified GePNIC instance: class name -> (source_name, host).
#
# A domain listed here still does NOTHING on its own: BaseConnector.run looks it
# up in config/approved_sources.yaml and refuses if it is absent. That file, not
# this table, is the compliance gate -- this one only spares us 20 near-identical
# class bodies. Each host below was verified by probe_sources.py, which fetches
# robots.txt through the project's own checker first and only then confirms the
# listing table parses server-side with no CAPTCHA over the data.
STATE_INSTANCES: dict[str, tuple[str, str]] = {
    # class name                   source_name                    host
    "MPTendersConnector":          ("MP eProcurement",            "mptenders.gov.in"),
    "HPTendersConnector":          ("HP eProcurement",            "hptenders.gov.in"),
    "RajasthanTendersConnector":   ("Rajasthan eProcurement",     "eproc.rajasthan.gov.in"),
    "WBTendersConnector":          ("WB eProcurement",            "wbtenders.gov.in"),
    "TNTendersConnector":          ("TN eProcurement",            "tntenders.gov.in"),
    "KeralaTendersConnector":      ("Kerala eProcurement",        "etenders.kerala.gov.in"),
    "AssamTendersConnector":       ("Assam eProcurement",         "assamtenders.gov.in"),
    "HaryanaTendersConnector":     ("Haryana eProcurement",       "etenders.hry.nic.in"),
    "PunjabTendersConnector":      ("Punjab eProcurement",        "eproc.punjab.gov.in"),
    "UPTendersConnector":          ("UP eProcurement",            "etender.up.nic.in"),
    "UttarakhandTendersConnector": ("Uttarakhand eProcurement",   "uktenders.gov.in"),
    "ManipurTendersConnector":     ("Manipur eProcurement",       "manipurtenders.gov.in"),
    "TripuraTendersConnector":     ("Tripura eProcurement",       "tripuratenders.gov.in"),
    "ArunachalTendersConnector":   ("Arunachal eProcurement",     "arunachaltenders.gov.in"),
    "OdishaTendersConnector":      ("Odisha eProcurement",        "tendersodisha.gov.in"),
    "JharkhandTendersConnector":   ("Jharkhand eProcurement",     "jharkhandtenders.gov.in"),
    "DNHTendersConnector":         ("DNH eProcurement",           "dnhtenders.gov.in"),
    "ChandigarhTendersConnector":  ("Chandigarh eProcurement",    "etenders.chd.nic.in"),
    "DefenceProcConnector":        ("Defence Procurement",        "defproc.gov.in"),
    "DelhiTendersConnector":       ("Delhi eProcurement",         "govtprocurement.delhi.gov.in"),
}

# Portals deliberately NOT here, so nobody re-probes them hoping for a different
# answer: mahatenders.gov.in and eproc.karnataka.gov.in (robots.txt disallows
# /nicgep/app); nagaland, meghalaya, mizoram, sikkim (listing renders empty --
# the data sits behind the CAPTCHA'd search, rule #3).

for _name, (_source, _host) in STATE_INSTANCES.items():
    globals()[_name] = type(
        _name,
        (GePNICConnector,),
        {
            "source_name": _source,
            "base_url": f"https://{_host}",
            "__module__": __name__,
            "__doc__": (
                f"{_source} -- https://{_host}. Verified by probe_sources.py; see "
                "config/approved_sources.yaml for the robots.txt/disclaimer record "
                "that actually gates it."
            ),
        },
    )

__all__ = [
    "GePNICConnector", "LISTING_PAGE", "LISTING_PATH", "STATE_INSTANCES",
    "split_org_chain", *STATE_INSTANCES,
]
