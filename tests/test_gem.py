"""GeM connector: card parsing, dates, and the document URL the UI depends on."""
from app.connectors import REGISTRY
from app.connectors.gem import GeMConnector, parse_card, parse_gem_dt

# A card exactly as the page renders it (innerText, newline separated).
CARD = {
    "doc_href": "/showbidDocument/8553536",
    "text": (
        "BID NO: GEM/2025/B/6860447\n"
        "View Corrigendum/Representation\n"
        "Items: ABG Machine,Wash or rinse Cart\n"
        "Quantity: 5,058\n"
        "Department Name And Address:\n"
        "Ministry of Health and Family Welfare\n"
        "Department of Health and Family Welfare\n"
        "Start Date: 08-11-2025 12:46 PM\n"
        "End Date: 16-09-2026 9:00 AM"
    ),
}


def test_parse_card_pulls_every_field():
    row = parse_card(CARD)
    assert row["bid_no"] == "GEM/2025/B/6860447"
    assert row["items"] == "ABG Machine,Wash or rinse Cart"
    assert row["quantity"] == "5058"  # comma stripped
    assert row["ministry"] == "Ministry of Health and Family Welfare"
    assert row["department"] == "Department of Health and Family Welfare"
    assert row["bid_id"] == "8553536"


def test_parse_card_rejects_a_non_bid_card():
    assert parse_card({"doc_href": "", "text": "Some banner"}) is None
    assert parse_card({"doc_href": "/showbidDocument/1", "text": "no bid number"}) is None


def test_gem_dates_are_numeric_month_not_named():
    """GeM writes 08-11-2025; CPPP writes 08-Nov-2025. Mixing them misreads months."""
    assert parse_gem_dt("08-11-2025 12:46 PM").isoformat() == "2025-11-08T12:46:00"
    assert parse_gem_dt("16-09-2026 9:00 AM").isoformat() == "2026-09-16T09:00:00"
    assert parse_gem_dt("") is None
    assert parse_gem_dt("not a date") is None


def test_normalize_sets_a_stable_document_url():
    """The download button on /t/{id} renders only when this is set."""
    rec = object.__new__(GeMConnector).normalize(parse_card(CARD))
    assert rec.document_url == "https://bidplus.gem.gov.in/showbidDocument/8553536"
    assert rec.external_ref == "GEM/2025/B/6860447"
    assert rec.deadline.isoformat() == "2026-09-16"
    assert rec.published_date.isoformat() == "2025-11-08"
    assert rec.organization == "Ministry of Health and Family Welfare"


def test_gem_is_registered():
    assert REGISTRY["GeM"] is GeMConnector


def test_gem_only_reads_the_two_permitted_paths():
    """robots.txt disallows /resources/ and the /bg_emd/ endpoints; we never go there."""
    assert GeMConnector.paths == ("/all-bids", "/showbidDocument/")
    for path in GeMConnector.paths:
        assert not path.startswith("/resources")
        assert "bg_emd" not in path
