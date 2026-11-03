from datetime import date, datetime

import pytest

from app.connectors.cppp import CPPPConnector, external_ref, is_open, parse_row
from app.cppp_relay import is_detail_url

# Copied from the live 14-day tab on 2026-11-03.
ROW = [
    "1", "10-09-2026 06:00 PM", "03-11-2026 05:30 PM", "04-11-2026 11:00 AM",
    "purchase of ophthalmic surgical items on rate contract for two years"
    "/18/VR/RPC/SSK/2026-27/2026_AIMSD_925659_1",
    "All India Institute of Medical Science-New Delhi", "",
]
LOCAL_ID_ROW = [
    "2", "13-10-2026 12:10 PM", "03-11-2026 04:11 AM", "04-11-2026 04:11 AM",
    "Naphthenic Base Oil/GEM/2026/B/8140948/23665", "Balmer Lawrie and Co Limited", "",
]


@pytest.fixture(scope="module")
def cppp():
    return object.__new__(CPPPConnector)  # parsing needs no browser


def test_splits_only_the_tender_id_off_the_joined_cell():
    raw = parse_row(ROW)
    assert raw["tender_id"] == "2026_AIMSD_925659_1"
    assert raw["title"].startswith("purchase of ophthalmic")
    assert raw["title"].endswith("/18/VR/RPC/SSK/2026-27")
    assert raw["organisation"] == "All India Institute of Medical Science-New Delhi"


def test_normalizes_the_numeric_day_first_dates(cppp):
    rec = cppp.normalize(parse_row(ROW))
    assert rec.published_date == date(2026, 9, 10)
    assert rec.deadline == date(2026, 11, 3)
    assert rec.external_ref == "2026_AIMSD_925659_1"
    assert rec.document_url is None


def test_ref_matches_rows_stored_from_the_old_listing(cppp):
    assert cppp.normalize(parse_row(ROW)).external_ref == external_ref(
        {"tender_id": "2026_AIMSD_925659_1", "internal_id": "14084436"})


def test_an_org_local_id_gets_a_stable_portal_wide_ref(cppp):
    """'23665' is one department's number and could collide across organisations."""
    first = cppp.normalize(parse_row(LOCAL_ID_ROW)).external_ref
    assert first.startswith("cppp-")
    assert first == cppp.normalize(parse_row(LOCAL_ID_ROW)).external_ref
    other = LOCAL_ID_ROW[:5] + ["Another Organisation", ""]
    assert first != cppp.normalize(parse_row(other)).external_ref


def test_a_row_without_a_tender_cell_is_ignored():
    assert parse_row(["No data available"]) is None


def test_only_tenders_still_open_for_bids_are_kept():
    raw = parse_row(ROW)   # closes 03-11-2026 05:30 PM
    assert is_open(raw, now=datetime(2026, 11, 3, 17, 29))
    assert not is_open(raw, now=datetime(2026, 11, 3, 17, 31))


def test_retires_the_expiring_detail_link():
    """Merged over a stored row, the blank removes the dead /cppp/tendersfullview/ link."""
    stored = {"url": "https://eprocure.gov.in/cppp/tendersfullview/MTQwOTE3MjE="}
    assert is_detail_url(stored["url"])
    assert not is_detail_url({**stored, **parse_row(ROW)}["url"])


def test_points_back_at_the_listing_the_row_came_from(cppp):
    assert cppp.normalize(parse_row(ROW)).source_url.endswith("/cppp/#/tendersClosingToday")
