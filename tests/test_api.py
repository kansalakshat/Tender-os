from datetime import date
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api import app, get_db
from app.models import Source, Tender


@pytest.fixture
def client(session_factory):
    with session_factory() as db:
        src = Source(
            name="CPPP", base_url="https://eprocure.gov.in", license="public-published",
            robots_txt_allowed=True,
        )
        db.add(src)
        db.flush()
        db.add_all([
            Tender(source_id=src.id, external_ref="a", title="Supply of desktop computers",
                   organization="NTPC Limited", department="IT", category="Goods",
                   estimated_value=Decimal("500000"), deadline=date(2026, 9, 7),
                   status="open", source_url="https://eprocure.gov.in/a"),
            Tender(source_id=src.id, external_ref="b", title="Construction of RCC drain",
                   organization="CPWD", department="Civil", category="Works",
                   estimated_value=Decimal("9000000"), deadline=date(2026, 10, 20),
                   status="open", source_url="https://eprocure.gov.in/b"),
            Tender(source_id=src.id, external_ref="c", title="Old expired tender",
                   organization="CPWD", estimated_value=None, deadline=date(2020, 1, 1),
                   status="closed", source_url="https://eprocure.gov.in/c",
                   duplicate_of=1),
        ])
        db.commit()

    app.dependency_overrides[get_db] = lambda: session_factory()
    yield TestClient(app)
    app.dependency_overrides.clear()


def total(client, **params):
    return client.get("/tenders", params=params).json()["total"]


def test_lists_tenders_and_hides_linked_duplicates(client):
    body = client.get("/tenders").json()
    assert body["total"] == 2
    assert all(item["duplicate_of"] is None for item in body["items"])


def test_duplicates_can_be_asked_for(client):
    assert total(client, include_duplicates=True) == 3


def test_filters(client):
    assert total(client, category="Works") == 1
    assert total(client, status="open") == 2
    assert total(client, department="civil") == 1          # case-insensitive
    assert total(client, organization="cpwd") == 1
    assert total(client, q="drain") == 1
    assert total(client, min_value=1_000_000) == 1
    assert total(client, max_value=1_000_000) == 1
    assert total(client, deadline_from="2026-10-01") == 1
    assert total(client, deadline_to="2026-09-30") == 1


def test_pagination_reports_the_full_total(client):
    body = client.get("/tenders", params={"limit": 1, "offset": 1}).json()
    assert body["total"] == 2
    assert len(body["items"]) == 1


def test_detail_includes_raw_payload_and_404s_cleanly(client):
    listed = client.get("/tenders").json()["items"][0]
    detail = client.get(f"/tenders/{listed['id']}")
    assert detail.status_code == 200
    assert "raw_payload" in detail.json()
    assert client.get("/tenders/999999").status_code == 404


def test_sources_expose_compliance_metadata(client):
    src = client.get("/sources").json()[0]
    assert src["license"] == "public-published"
    assert src["robots_txt_allowed"] is True


def test_sort_is_restricted_to_known_columns(client):
    assert client.get("/tenders", params={"sort": "title; drop table tenders"}).status_code == 422
