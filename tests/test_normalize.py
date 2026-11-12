from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.connectors.cppp import external_ref
from app.connectors.data_gov_in import DataGovInConnector, parse_date, parse_value, pick_field
from app.schemas import TenderRecord



# ---- CPPP's listing is covered in test_cppp.py ----

def test_external_ref_prefers_the_canonical_tender_id():
    assert external_ref({"tender_id": "2026_BPCL_26361", "internal_id": "14084436"}) == "2026_BPCL_26361"
    assert external_ref({"tender_id": "2026_JKRRD_149323_1", "internal_id": "1"}) == "2026_JKRRD_149323_1"


def test_external_ref_falls_back_when_the_id_is_org_local():
    # '166992' is a department-internal number and could collide across organisations.
    assert external_ref({"tender_id": "166992", "internal_id": "14084425"}) == "cppp-14084425"


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
    assert parse_value("tender_value", "Rs. 5,00,000") == Decimal("500000")
    assert parse_value("tender_value", "Rs.5,00,000/-") == Decimal("500000")
    # The unit can sit beside the number instead of in the header.
    assert parse_value("Tender Value", "25 Lakhs") == Decimal("2500000")
    assert parse_value("Tender Value", "1.5 Cr") == Decimal("15000000")
    assert parse_value("Estimated Value (Rs. in Cr)", "12.5") == Decimal("125000000")
    assert parse_value("Replacement Cost", "500") == Decimal("500")   # "lac" inside a word
    assert parse_value("Tender Value", "0.00") is None                 # not published


def test_emd_is_not_read_as_the_tender_value():
    assert pick_field({"EMD Amount": "5000"}, "estimated_value") == (None, None)
    assert pick_field({"Bid Security Amount": "5000"}, "estimated_value") == (None, None)
    assert pick_field({"Tender Fee Amount": "500"}, "estimated_value") == (None, None)
    assert pick_field({"Cost of Tender Document": "1000"}, "estimated_value") == (None, None)


def test_single_digit_day_with_a_time_keeps_its_date():
    assert parse_date("1/2/2026 10:00").date() == date(2026, 2, 1)


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
