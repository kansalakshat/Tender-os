"""One bad row must not end a 20,000-document pass.

Two shards died mid-run on a deadlock, minutes after the nightly purge began
deleting expired rows -- a bulk DELETE meeting per-row UPDATEs. Nothing noticed
for three hours, and the documents they still had to read went with them.
"""
import logging

import pytest
from sqlalchemy.exc import OperationalError

from app import enrich
from app.models import Source, Tender


@pytest.fixture
def three_tenders(session_factory, monkeypatch):
    monkeypatch.setattr(enrich, "SessionLocal", session_factory)
    db = session_factory()
    src = Source(name="GeM", base_url="https://bidplus.gem.gov.in")
    db.add(src)
    db.flush()
    for n in range(3):
        db.add(Tender(source_id=src.id, external_ref=f"t{n}", title="t",
                      source_url="u", document_url=f"https://bidplus.gem.gov.in/d/{n}"))
    db.commit()
    db.close()
    return session_factory


def test_a_deadlock_on_one_tender_does_not_end_the_pass(three_tenders, monkeypatch, caplog):
    seen = []

    def flaky(db, tender, client):
        seen.append(tender.external_ref)
        if tender.external_ref == "t1":
            raise OperationalError("UPDATE tenders", {}, Exception("deadlock detected"))
        return True

    monkeypatch.setattr(enrich, "enrich_one", flaky)
    with caplog.at_level(logging.WARNING):
        changed = enrich.enrich_pending(limit=10, session_factory=three_tenders)

    assert len(seen) == 3, "every tender was still attempted"
    assert changed == 2, "the two good ones were enriched"
    assert any("skipping it" in r.message or "skipping it" in r.getMessage()
               for r in caplog.records), "and the failure was reported, not swallowed"


def test_the_skipped_tender_is_left_for_the_next_pass(three_tenders, monkeypatch):
    """It must not be marked read, or the document is lost for good."""
    def always_deadlock(db, tender, client):
        raise OperationalError("UPDATE tenders", {}, Exception("deadlock detected"))

    monkeypatch.setattr(enrich, "enrich_one", always_deadlock)
    enrich.enrich_pending(limit=10, session_factory=three_tenders)
    assert len(enrich.needs_enrichment(limit=10)) == 3, "all three are still pending"
