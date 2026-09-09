"""A long backfill must not hold everything in one transaction.

A full CPPP crawl is ~3,200 pages over a couple of hours. If that ran as a single
transaction, a failure on the last page would discard every row before it.
"""
from datetime import date, timedelta

import httpx
import pytest

from app.connectors import base
from app.connectors.base import BaseConnector
from app.models import ConnectorRun, Tender
from app.schemas import TenderRecord
from tests.test_robots import robots_responder, transport


class Flaky(BaseConnector):
    """Yields `total` rows, then fails at `fail_at` if asked to."""

    source_name = "flaky"
    base_url = "https://example.gov.in"
    rate_limit_seconds = 0.0
    paths = ("/",)
    total = 250
    fail_at: int | None = None

    def fetch_batch(self, since):
        for i in range(self.total):
            if self.fail_at is not None and i == self.fail_at:
                raise httpx.ConnectError("source went away", request=None)
            yield {"id": f"T{i:04d}"}

    def normalize(self, raw):
        return TenderRecord(
            external_ref=raw["id"], title=f"Tender {raw['id']}",
            source_url=f"https://example.gov.in/{raw['id']}",
        )


@pytest.fixture(autouse=True)
def _approved(monkeypatch):
    monkeypatch.setattr(
        base, "load_approved_sources", lambda: {"example.gov.in": {"domain": "example.gov.in"}}
    )


def make(session_factory, **attrs):
    conn = Flaky(
        session_factory=session_factory, client=transport(robots_responder("", status=404))
    )
    for key, value in attrs.items():
        setattr(conn, key, value)
    return conn


def test_a_completed_run_stores_everything(session_factory):
    summary = make(session_factory, total=250).run()
    assert (summary.status, summary.new) == ("ok", 250)
    with session_factory() as db:
        assert db.query(Tender).count() == 250


def test_rows_survive_a_failure_partway_through(session_factory):
    """The point of committing in batches: keep what we already fetched."""
    summary = make(session_factory, total=250, fail_at=210).run()
    assert summary.status == "error"

    with session_factory() as db:
        stored = db.query(Tender).count()
    # Everything up to the last completed batch of COMMIT_EVERY must have landed.
    assert stored >= 200, f"only {stored} rows survived; batched commits are not working"
    assert stored <= 210


def test_a_failed_run_is_recorded_for_operators(session_factory):
    make(session_factory, total=250, fail_at=10).run()
    with session_factory() as db:
        run = db.query(ConnectorRun).one()
        assert run.status == "error"
        assert run.message


def test_resuming_after_a_failure_fills_the_gap(session_factory):
    make(session_factory, total=250, fail_at=210).run()
    with session_factory() as db:
        before = db.query(Tender).count()

    second = make(session_factory, total=250).run()
    assert second.status == "ok"
    with session_factory() as db:
        assert db.query(Tender).count() == 250
    # The rows already stored are updated, not duplicated.
    assert second.new == 250 - before


# ---- expired rows are never written ----------------------------------------

class Mixed(BaseConnector):
    """Half open, half already closed -- what CPPP's deeper pages actually serve."""

    source_name = "mixed"
    base_url = "https://example.gov.in"
    rate_limit_seconds = 0.0
    paths = ("/",)

    def fetch_batch(self, since):
        for i in range(10):
            yield {"id": f"T{i:02d}", "days": i - 5}   # -5..-1 closed, 0..4 open

    def normalize(self, raw):
        return TenderRecord(
            external_ref=raw["id"], title=f"Tender {raw['id']}",
            deadline=date.today() + timedelta(days=raw["days"]),
            source_url=f"https://example.gov.in/{raw['id']}",
        )


def mixed(session_factory):
    return Mixed(
        session_factory=session_factory, client=transport(robots_responder("", status=404))
    )


def test_expired_rows_are_skipped_not_written(session_factory, monkeypatch):
    """The whole point: the purge should have nothing left to do."""
    monkeypatch.setattr(base, "DEFAULT_RETENTION_DAYS", 0)
    summary = mixed(session_factory).run()

    assert (summary.fetched, summary.new, summary.skipped) == (10, 5, 5)
    with session_factory() as db:
        deadlines = [t.deadline for t in db.query(Tender).all()]
    assert len(deadlines) == 5
    assert all(d >= date.today() for d in deadlines), "an expired row was written"


def test_the_grace_period_is_respected_at_ingest(session_factory, monkeypatch):
    """Skipping must mirror purge_expired, or the two disagree about "expired"."""
    monkeypatch.setattr(base, "DEFAULT_RETENTION_DAYS", 30)
    summary = mixed(session_factory).run()
    # Nothing here closed more than 30 days ago, so nothing is skipped.
    assert (summary.new, summary.skipped) == (10, 0)


def test_archive_mode_still_keeps_expired_rows(session_factory, monkeypatch):
    """RETENTION_DAYS unset means keep everything -- including closed tenders."""
    monkeypatch.setattr(base, "DEFAULT_RETENTION_DAYS", None)
    summary = mixed(session_factory).run()
    assert (summary.new, summary.skipped) == (10, 0)
