from datetime import date, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from app.api import app, get_db
from app.models import Source, Tender


SOON = date.today() + timedelta(days=3)
LATER = date.today() + timedelta(days=40)
EXPIRED = date.today() - timedelta(days=200)


@pytest.fixture
def client(session_factory):
    with session_factory() as db:
        src = Source(
            name="CPPP", base_url="https://eprocure.gov.in", license="public-published",
            robots_txt_allowed=True,
        )
        db.add(src)
        db.flush()
        # Deadlines are relative to today on purpose. GET /tenders now hides
        # past-deadline rows by default, so a fixture pinned to fixed dates
        # quietly changes meaning the morning those dates pass.
        db.add_all([
            Tender(source_id=src.id, external_ref="a", title="Supply of desktop computers",
                   organization="NTPC Limited", department="IT", category="Goods",
                   estimated_value=Decimal("500000"), deadline=SOON,
                   status="open", source_url="https://eprocure.gov.in/a"),
            Tender(source_id=src.id, external_ref="b", title="Construction of RCC drain",
                   organization="CPWD", department="Civil", category="Works",
                   estimated_value=Decimal("9000000"), deadline=LATER,
                   status="open", source_url="https://eprocure.gov.in/b"),
            Tender(source_id=src.id, external_ref="c", title="Linked duplicate of the drain",
                   organization="CPWD", estimated_value=None, deadline=LATER,
                   status="open", source_url="https://eprocure.gov.in/c",
                   duplicate_of=1),
            # status says "open" while the deadline says otherwise, which is what
            # a row ingested before its deadline passed actually looks like.
            Tender(source_id=src.id, external_ref="d", title="Old expired tender",
                   organization="Defunct Board", estimated_value=None, deadline=EXPIRED,
                   status="open", source_url="https://eprocure.gov.in/d"),
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
    midpoint = (date.today() + timedelta(days=10)).isoformat()
    assert total(client, deadline_from=midpoint) == 1
    assert total(client, deadline_to=midpoint) == 1


def test_closed_tenders_are_hidden_by_default(client):
    """The whole point of keeping expired rows instead of deleting them."""
    titles = [t["title"] for t in client.get("/tenders").json()["items"]]
    assert "Old expired tender" not in titles
    assert total(client, include_closed=True) == 3
    assert total(client, include_closed=True, include_duplicates=True) == 4


def test_open_is_decided_by_the_deadline_not_the_stored_status(client):
    """`status` is frozen at ingest, so the expired row still says "open"."""
    assert total(client, status="open") == 2
    assert total(client, status="open", include_closed=True) == 3


def test_a_tender_with_no_deadline_is_unknown_not_expired(client, session_factory):
    with session_factory() as db:
        from app.models import Tender as T
        db.add(T(source_id=1, external_ref="e", title="Undated notice",
                 source_url="https://eprocure.gov.in/e"))
        db.commit()
    assert total(client, q="Undated") == 1


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


# ---- the daily purge trigger ------------------------------------------------

def test_cron_purge_refuses_when_no_secret_is_configured(client, monkeypatch):
    """Fail closed. A delete endpoint must not be open just because a variable
    was never set."""
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert client.get("/cron/purge").status_code == 503


def test_cron_purge_rejects_a_wrong_or_missing_secret(client, monkeypatch):
    monkeypatch.setenv("CRON_SECRET", "right-secret")
    assert client.get("/cron/purge").status_code == 401
    assert client.get(
        "/cron/purge", headers={"Authorization": "Bearer wrong-secret"}
    ).status_code == 401
    assert client.get(
        "/cron/purge", headers={"Authorization": "right-secret"}
    ).status_code == 401


def test_cron_purge_deletes_only_expired_rows(client, monkeypatch, session_factory):
    from app import api
    monkeypatch.setenv("CRON_SECRET", "right-secret")
    monkeypatch.setattr(api, "DEFAULT_RETENTION_DAYS", 0)
    # The endpoint opens its own session, so overriding get_db is not enough.
    monkeypatch.setattr(api, "SessionLocal", session_factory)

    body = client.get(
        "/cron/purge", headers={"Authorization": "Bearer right-secret"}
    ).json()
    assert body["deleted"] == 1                       # only the expired fixture row
    assert total(client, include_closed=True) == 2    # the two open ones survive


def test_cron_purge_deletes_nothing_when_retention_is_unset(client, monkeypatch):
    from app import api
    monkeypatch.setenv("CRON_SECRET", "right-secret")
    monkeypatch.setattr(api, "DEFAULT_RETENTION_DAYS", None)
    body = client.get(
        "/cron/purge", headers={"Authorization": "Bearer right-secret"}
    ).json()
    assert body["deleted"] == 0
    assert total(client, include_closed=True) == 3


def test_cron_ingest_needs_the_same_secret(client, monkeypatch):
    monkeypatch.delenv("CRON_SECRET", raising=False)
    assert client.get("/cron/ingest").status_code == 503
    monkeypatch.setenv("CRON_SECRET", "right-secret")
    assert client.get("/cron/ingest").status_code == 401
    assert client.get(
        "/cron/ingest", headers={"Authorization": "Bearer nope"}
    ).status_code == 401


def test_cron_ingest_stops_when_the_time_budget_is_spent(client, monkeypatch):
    """A 300s function ceiling means returning a partial answer beats a 504."""
    from app import api
    monkeypatch.setenv("CRON_SECRET", "right-secret")
    monkeypatch.setenv("INGEST_BUDGET_SECONDS", "0")     # every source is unreachable
    body = client.get(
        "/cron/ingest", headers={"Authorization": "Bearer right-secret"}
    ).json()
    assert body["ran"] == []
    assert set(body["not_reached"]) == set(api.REGISTRY)


def test_cron_ingest_reports_a_failing_source_without_sinking_the_run(client, monkeypatch):
    from app import api
    monkeypatch.setenv("CRON_SECRET", "right-secret")
    monkeypatch.setenv("INGEST_BUDGET_SECONDS", "600")

    class Boom:
        max_pages = 1
        def run(self, since=None): raise RuntimeError("source is down")
        def close(self): pass

    monkeypatch.setattr(api, "REGISTRY", {"boom": Boom})
    body = client.get(
        "/cron/ingest", headers={"Authorization": "Bearer right-secret"}
    ).json()
    assert body["ran"] == [
        {"source": "boom", "status": "error", "message": "source is down"}
    ]
