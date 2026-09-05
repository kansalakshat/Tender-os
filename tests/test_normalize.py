from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from app.connectors.cppp import CPPPConnector, external_ref
from app.connectors.data_gov_in import DataGovInConnector, parse_date, parse_value, pick_field
from app.schemas import TenderRecord

FIXTURE = Path(__file__).parent / "fixtures" / "cppp_listing.html"


@pytest.fixture(scope="module")
def cppp():
    return object.__new__(CPPPConnector)  # parsing needs no HTTP client


@pytest.fixture(scope="module")
def rows(cppp):
    return cppp.parse_rows(FIXTURE.read_text(encoding="utf-8", errors="replace"))


# ---- CPPP: parsed from a real captured listing page ----

def test_parses_every_row_on_the_page(rows):
    assert len(rows) == 10


def test_row_fields_are_extracted(rows):
    first = rows[0]
    assert first["title"].startswith("HIRING RESIDENTIAL ENGINEER")
    assert first["organisation"] == "Bharat Petroleum Corporation Limited"
    assert first["published"] == "24-Aug-2026 10:24 PM"
    assert first["tender_id"] == "2026_BPCL_26361"


def test_normalizes_dates_and_status(cppp, rows):
    rec = cppp.normalize(rows[0])
    assert rec.published_date == date(2026, 8, 24)
    assert rec.deadline == date(2026, 9, 7)
    assert rec.status in {"open", "closed"}
    assert rec.currency == "INR"


def test_ignores_tables_that_are_not_the_tender_listing(cppp):
    """CPPP reuses class="list_table" for its forms, so the header is the real guard."""
    not_the_listing = """
      <table class="list_table">
        <thead><tr><th>Tender ID</th><th>Tender Title</th></tr></thead>
        <tbody><tr>
          <td>1</td><td>2</td><td>3</td><td>4</td>
          <td><a href="https://eprocure.gov.in/cppp/tendersfullview/Zm9v">x</a></td>
          <td>6</td><td>7</td>
        </tr></tbody>
      </table>
    """
    assert cppp.parse_rows(not_the_listing) == []


def test_titles_containing_slashes_survive(cppp, rows):
    """Titles and reference numbers routinely contain '/', so text splitting fails."""
    ref_row = next(r for r in rows if "/" in r["title"])
    rec = cppp.normalize(ref_row)
    assert "/" in rec.title
    assert rec.external_ref


def test_external_ref_prefers_the_canonical_tender_id():
    assert external_ref({"tender_id": "2026_BPCL_26361", "internal_id": "14084436"}) == "2026_BPCL_26361"
    assert external_ref({"tender_id": "2026_JKRRD_149323_1", "internal_id": "1"}) == "2026_JKRRD_149323_1"


def test_external_ref_falls_back_when_the_id_is_org_local():
    # '166992' is a department-internal number and could collide across organisations.
    assert external_ref({"tender_id": "166992", "internal_id": "14084425"}) == "cppp-14084425"


def test_every_row_yields_a_unique_ref(cppp, rows):
    refs = [cppp.normalize(r).external_ref for r in rows]
    assert len(set(refs)) == len(refs)


def test_source_url_points_back_at_the_source(cppp, rows):
    rec = cppp.normalize(rows[0])
    assert rec.source_url.startswith("https://eprocure.gov.in/")


# ---- data.gov.in: column names differ per dataset ----

@pytest.fixture(scope="module")
def dgi():
    return object.__new__(DataGovInConnector)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("2026-08-24", date(2026, 8, 24)),
        ("24-08-2026", date(2026, 8, 24)),
        ("24/08/2026", date(2026, 8, 24)),
        ("24-Aug-2026", date(2026, 8, 24)),
        ("2026-08-24 10:30:00", date(2026, 8, 24)),
        ("", None),
        ("NA", None),
        ("not a date", None),
    ],
)
def test_parses_the_date_formats_publishers_actually_use(raw, expected):
    got = parse_date(raw)
    assert (got.date() if got else None) == expected


def test_money_columns_honour_their_stated_unit():
    """A lakh column read as rupees turns a 45-lakh tender into 45 rupees."""
    assert parse_value("tender_value", "1,23,456.50") == Decimal("123456.50")
    assert parse_value("Estimated Cost (Rs. in Lakhs)", "45.5") == Decimal("4550000.0")
    assert parse_value("Value in Crore", "2") == Decimal("20000000")
    assert parse_value("tender_value", "") is None
    assert parse_value("tender_value", "N/A") is None


def test_maps_columns_by_alias(dgi):
    row = {
        "Tender ID": "ASM/2026/01",
        "Name of Work": "Road repair Guwahati",
        "Department": "PWD",
        "Estimated Cost (Rs. in Lakhs)": "45.5",
        "Date of Publication": "01-04-2026",
        "Last Date": "20-04-2026",
        "_resource_id": "abc-123",
    }
    rec = dgi.normalize(row)
    assert rec.external_ref == "abc-123:ASM/2026/01"
    assert rec.title == "Road repair Guwahati"
    assert rec.department == "PWD"
    assert rec.estimated_value == Decimal("4550000.0")
    assert rec.published_date == date(2026, 4, 1)
    assert rec.deadline == date(2026, 4, 20)


def test_rows_without_an_id_get_a_stable_derived_ref(dgi):
    row = {"Work Name": "Culvert repair", "_resource_id": "r1"}
    first = dgi.normalize(row).external_ref
    second = dgi.normalize(dict(row)).external_ref
    assert first == second, "same row must not create a second tender on re-run"
    assert dgi.normalize({"Work Name": "Other", "_resource_id": "r1"}).external_ref != first


def test_exact_column_match_wins_over_substring(dgi):
    raw = {"total_value_of_contracts": "999", "estimated_value": "500", "_resource_id": "r"}
    assert pick_field(raw, "estimated_value")[0] == "estimated_value"


# ---- shared record rules ----

def test_status_is_derived_from_the_deadline():
    future = TenderRecord(
        external_ref="a", title="t", source_url="https://x.gov.in",
        deadline=date.today() + timedelta(days=5),
    )
    past = TenderRecord(
        external_ref="b", title="t", source_url="https://x.gov.in",
        deadline=date.today() - timedelta(days=5),
    )
    assert (future.status, past.status) == ("open", "closed")


def test_unknown_status_values_are_dropped_not_invented():
    rec = TenderRecord(
        external_ref="a", title="t", source_url="https://x.gov.in", status="under evaluation"
    )
    assert rec.status is None


def test_nonsense_values_are_discarded():
    rec = TenderRecord(
        external_ref="a", title="t", source_url="https://x.gov.in",
        estimated_value=Decimal("-100"),
    )
    assert rec.estimated_value is None


def test_personal_data_never_reaches_the_raw_payload():
    """Rule #7: tender metadata only, even in the audit copy."""
    rec = TenderRecord(
        external_ref="a", title="t", source_url="https://x.gov.in",
        raw_payload={
            "title": "Road work",
            "bidder_email": "someone@example.com",
            "Contact Number": "9999999999",
            "officer_name": "A Person",
            "nested": [{"mobile": "8888888888"}],
            "estimated_value": 5000,
        },
    )
    flat = str(rec.raw_payload)
    assert "someone@example.com" not in flat
    assert "9999999999" not in flat
    assert "8888888888" not in flat
    assert "A Person" not in flat
    assert rec.raw_payload["title"] == "Road work"
    assert rec.raw_payload["estimated_value"] == 5000


def test_empty_required_fields_are_rejected():
    with pytest.raises(ValueError):
        TenderRecord(external_ref="  ", title="t", source_url="https://x.gov.in")
    with pytest.raises(ValueError):
        TenderRecord(external_ref="a", title="", source_url="https://x.gov.in")
