"""GePNIC-pattern connector, parsed from a real captured mptenders.gov.in page."""
from datetime import date
from pathlib import Path

import pytest

from app.connectors.gepnic import (
    LISTING_PATH,
    GePNICConnector,
    MPTendersConnector,
    split_org_chain,
)

FIXTURE = Path(__file__).parent / "fixtures" / "gepnic_mp_listing.html"


@pytest.fixture(scope="module")
def mp():
    return object.__new__(MPTendersConnector)


@pytest.fixture(scope="module")
def html():
    return FIXTURE.read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def rows(mp, html):
    return mp.parse_rows(html)


def test_parses_the_listing(rows):
    assert len(rows) == 10


def test_header_row_is_not_treated_as_a_tender(rows):
    assert all(r["serial"] != "S.No" for r in rows)
    assert all(r["tender_id"] for r in rows)


def test_extracts_the_bracketed_title_reference_and_id(rows):
    first = rows[0]
    assert first["title"] == "stationary items"
    assert first["reference_no"] == "2641/2026/Date/30-07-2026"
    assert first["tender_id"] == "2026_HED_525995_1"


def test_splits_the_organisation_chain():
    assert split_org_chain("Dept of Higher Education||Rewa College") == (
        "Dept of Higher Education", "Rewa College",
    )
    assert split_org_chain("Single Org") == ("Single Org", None)
    assert split_org_chain("") == (None, None)


def test_normalizes_a_row(mp, rows):
    rec = mp.normalize(rows[0])
    assert rec.external_ref == "2026_HED_525995_1"
    assert rec.organization == "Department of Higher Education"
    assert rec.published_date == date(2026, 8, 3)
    assert rec.deadline == date(2026, 8, 25)
    assert rec.currency == "INR"


def test_every_row_has_a_unique_ref(mp, rows):
    refs = [mp.normalize(r).external_ref for r in rows]
    assert len(set(refs)) == len(refs)


def test_expiring_detail_links_are_not_published_as_urls(mp, rows):
    """The detail href carries a Tapestry session token and dies with the session."""
    rec = mp.normalize(rows[0])
    assert rec.document_url is None
    assert "sp=" not in rec.source_url
    assert rec.source_url.endswith(LISTING_PATH.split("/nicgep")[-1]) or "nicgep" in rec.source_url
    # ...but it is kept for auditing.
    assert "sp=" in rec.raw_payload["detail_link"]


def test_finds_the_next_page_link(mp, html):
    nxt = mp.next_page_url(html, 1, "https://mptenders.gov.in/nicgep/app")
    assert nxt is not None
    assert "TablePages.linkPage" in nxt
    assert nxt.startswith("https://mptenders.gov.in/")


def test_stops_when_there_is_no_next_page(mp, html):
    assert mp.next_page_url(html, 99, "https://mptenders.gov.in/nicgep/app") is None


def test_ignores_tables_that_are_not_the_listing(mp):
    """GePNIC reuses class="list_table" for its search forms."""
    form = """
      <table class="list_table"><tr>
        <td>a</td><td>b</td><td>c</td><td>d</td><td>e</td><td>f</td>
      </tr></table>
    """
    assert mp.parse_rows(form) == []


# ---- the reason this class exists separately from its subclasses ----

def test_the_generic_class_is_not_runnable_on_its_own(session_factory):
    """A GePNIC subclass with no verified domain must not run."""
    class UnverifiedState(GePNICConnector):
        source_name = "Some State"
        base_url = "https://someotherstate.gov.in"

    import httpx
    # run() refuses at the allowlist check before any request, but a real client
    # would still cost an SSL context to build.
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200)))
    summary = UnverifiedState(session_factory=session_factory, client=client).run()
    assert summary.status == "refused"
    assert "approved_sources.yaml" in summary.message


def test_mp_is_scoped_to_the_ungated_listing(mp):
    """FrontEndLatestActiveTenders / ByOrganisation are CAPTCHA-gated (rule #3)."""
    assert "FrontEndListTendersbyDate" in LISTING_PATH
    assert "FrontEndLatestActiveTenders" not in LISTING_PATH
    assert "FrontEndTendersByOrganisation" not in LISTING_PATH
    assert mp.normalize(mp.parse_rows(FIXTURE.read_text(encoding="utf-8", errors="replace"))[0])


def test_mp_is_approved_and_registered():
    from app.connectors import REGISTRY
    from app.connectors.base import load_approved_sources

    assert REGISTRY["MP eProcurement"] is MPTendersConnector
    entry = load_approved_sources()["mptenders.gov.in"]
    assert entry["license"] == "public-published"
    assert entry["robots_verified_on"]
