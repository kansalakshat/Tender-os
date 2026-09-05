"""data.gov.in normalization, using the real column names Assam publishes.

Field names below are taken verbatim from the live catalogue response for
"Assam Public Procurement Data" (OCDS paths flattened by the API).
"""
from decimal import Decimal

import pytest

from app.connectors.data_gov_in import DataGovInConnector, pick_field

# Exactly as the API reports them.
ASSAM_ROW = {
    "document_id": 1.0,
    "ocid": "ocds-abcdef-0001",
    "initiationtype": "tender",
    "tag": "tender",
    "id": 12345.0,
    "date": "2017-06-14",
    "tender_id": "2017_ASM_1234_1",
    "tender_externalreference": "NIT/PWD/2017/44",
    "tender_title": "Construction of RCC drain at Ward 4",
    "tender_mainprocurementcategory": "works",
    "tender_procurementmethod": "open",
    "tender_value_amount": "2500000",
    "tender_datepublished": "2017-06-14",
    "tender_milestones_duedate": "2017-07-01",
    "tender_status": "complete",
    "tender_numberoftenderers": 4.0,
    "buyer_name": "PWD (Roads) Assam",
    "fiscal_year": "2017-18",
    "_resource_id": "d9a9fd53-4edd-4f65-8dcd-d5844f7fa76e",
}


@pytest.fixture(scope="module")
def dgi():
    return object.__new__(DataGovInConnector)


def test_maps_every_ocds_field_we_care_about():
    """OCDS camelCase flattens to runs like 'datepublished' with no separator."""
    assert pick_field(ASSAM_ROW, "external_ref")[0] == "tender_id"
    assert pick_field(ASSAM_ROW, "title")[0] == "tender_title"
    assert pick_field(ASSAM_ROW, "organization")[0] == "buyer_name"
    assert pick_field(ASSAM_ROW, "category")[0] == "tender_mainprocurementcategory"
    assert pick_field(ASSAM_ROW, "estimated_value")[0] == "tender_value_amount"
    assert pick_field(ASSAM_ROW, "published_date")[0] == "tender_datepublished"
    assert pick_field(ASSAM_ROW, "deadline")[0] == "tender_milestones_duedate"
    assert pick_field(ASSAM_ROW, "status")[0] == "tender_status"


def test_normalizes_an_assam_row(dgi):
    from datetime import date

    rec = dgi.normalize(ASSAM_ROW)
    assert rec.external_ref == "d9a9fd53-4edd-4f65-8dcd-d5844f7fa76e:2017_ASM_1234_1"
    assert rec.title == "Construction of RCC drain at Ward 4"
    assert rec.organization == "PWD (Roads) Assam"
    assert rec.category == "works"
    assert rec.estimated_value == Decimal("2500000")
    assert rec.published_date == date(2017, 6, 14)
    assert rec.deadline == date(2017, 7, 1)
    # OCDS says "complete", which is not one of our four statuses, so it is dropped
    # rather than passed through -- and the 2017 deadline then derives "closed".
    assert rec.status == "closed"
    assert rec.source_url.endswith("d9a9fd53-4edd-4f65-8dcd-d5844f7fa76e")


def test_refs_are_namespaced_by_resource(dgi):
    """Two datasets can reuse a tender id; the resource id keeps them distinct."""
    other = dict(ASSAM_ROW, _resource_id="another-resource")
    assert dgi.normalize(other).external_ref != dgi.normalize(ASSAM_ROW).external_ref


# ---- discovery must never decide what gets ingested ----

def test_only_pinned_resources_are_ingested(monkeypatch):
    """Catalogue search matches tender COCONUT and paddy procurement."""
    conn = object.__new__(DataGovInConnector)
    conn._resource_ids = None
    monkeypatch.setattr(
        DataGovInConnector, "configured_resources", lambda self: ["pinned-1"]
    )
    monkeypatch.setattr(
        DataGovInConnector,
        "discover_resources",
        lambda self: (_ for _ in ()).throw(
            AssertionError("discovery must not be called during ingest")
        ),
    )
    assert conn.resource_ids() == ["pinned-1"]


def test_nothing_is_ingested_when_nothing_is_pinned(monkeypatch, caplog):
    conn = object.__new__(DataGovInConnector)
    conn._resource_ids = None
    monkeypatch.setattr(DataGovInConnector, "configured_resources", lambda self: [])
    assert conn.resource_ids() == []
    assert "nothing will be ingested" in caplog.text


def test_the_assam_datasets_are_pinned():
    conn = object.__new__(DataGovInConnector)
    pinned = conn.configured_resources()
    assert "d9a9fd53-4edd-4f65-8dcd-d5844f7fa76e" in pinned  # 2017-18
    assert len(pinned) == 6, "six Assam years were verified in the live catalogue"


def test_the_connector_refuses_without_an_api_key(monkeypatch):
    monkeypatch.delenv("DATA_GOV_IN_API_KEY", raising=False)
    conn = object.__new__(DataGovInConnector)
    conn.api_key = ""
    with pytest.raises(RuntimeError, match="DATA_GOV_IN_API_KEY"):
        conn._params()
