"""Re-crawling a tender must not undo what its bid document taught us.

app/enrich.py writes into the same raw_payload column the connector owns, so a
plain field-by-field overwrite erased every enriched key each time the listing
was crawled again -- observed live as the enriched-row count going *down* while
a backfill ran. The daily cycle re-reads the same first pages every day, so this
would have wiped and redone the same work forever.
"""
from decimal import Decimal

import pytest

from app.connectors.base import BaseConnector
from app.enrich import DONE_KEY
from app.models import Source, Tender
from app.schemas import TenderRecord


class _Dummy(BaseConnector):
    source_name = "GeM"
    base_url = "https://bidplus.gem.gov.in"
    paths = ("/all-bids",)

    def fetch_batch(self, since=None):
        return iter(())

    def normalize(self, raw):
        raise NotImplementedError


@pytest.fixture
def enriched_row(session_factory):
    """A tender already improved by a pass over its bid document."""
    db = session_factory()
    src = Source(name="GeM", base_url="https://bidplus.gem.gov.in")
    db.add(src)
    db.flush()
    db.add(Tender(
        source_id=src.id, external_ref="GEM/2026/B/1", source_url="u",
        title="Supply, Installation and commissioning of a Demineralised Water Unit",
        organization="Directorate Of Purchase And Stores",
        estimated_value=Decimal("624000"),
        raw_payload={
            DONE_KEY: True, "emd_amount": 409000.0, "bid_type": "Two Packet Bid",
            "links": [{"label": "Technical specification", "url": "https://mkp.gem.gov.in/s.pdf"}],
            "serial": "1",
        },
    ))
    db.commit()
    return db, src


def _recrawl(db, src, **over):
    """The thin row the listing hands back on the next crawl."""
    rec = TenderRecord(**{
        "external_ref": "GEM/2026/B/1",
        "title": "Supply, Installation and com...",     # the card truncates
        "organization": "Pmo",                          # the card names the ministry
        "estimated_value": None,                        # the card publishes no value
        "source_url": "u",
        "raw_payload": {"serial": "2", "end": "12-10-2026"},
        **over,
    })
    created = _Dummy.__new__(_Dummy)._upsert(db, src, rec)
    db.commit()
    return created


def test_recrawl_keeps_everything_the_document_taught_us(enriched_row):
    db, src = enriched_row
    assert _recrawl(db, src) == 0, "an existing row is updated, not created"

    t = db.execute(db.query(Tender).statement).scalars().one()
    # The enriched payload keys survive...
    assert t.raw_payload[DONE_KEY] is True
    assert t.raw_payload["emd_amount"] == 409000.0
    assert t.raw_payload["bid_type"] == "Two Packet Bid"
    assert t.raw_payload["links"][0]["label"] == "Technical specification"
    # ...the listing's own keys are still refreshed...
    assert t.raw_payload["serial"] == "2"
    assert t.raw_payload["end"] == "12-10-2026"
    # ...and the better title, buyer and value are not thrown away.
    assert t.title.startswith("Supply, Installation and commissioning")
    assert t.organization == "Directorate Of Purchase And Stores"
    assert t.estimated_value == Decimal("624000")


def test_a_listing_value_still_wins_when_it_has_one(enriched_row):
    """Protection is against forgetting, not against learning."""
    db, src = enriched_row
    _recrawl(db, src, estimated_value=Decimal("777000"))
    t = db.execute(db.query(Tender).statement).scalars().one()
    assert t.estimated_value == Decimal("777000")


def test_an_unenriched_row_is_still_updated_normally(session_factory):
    """The guards key off the enriched marker; without it, nothing changes."""
    db = session_factory()
    src = Source(name="GeM", base_url="https://bidplus.gem.gov.in")
    db.add(src)
    db.flush()
    db.add(Tender(source_id=src.id, external_ref="GEM/2026/B/1", source_url="u",
                  title="A much longer original title here", organization="Old",
                  raw_payload={"serial": "1"}))
    db.commit()
    _recrawl(db, src)
    t = db.execute(db.query(Tender).statement).scalars().one()
    assert t.title == "Supply, Installation and com..."
    assert t.organization == "Pmo"


def test_an_unchanged_recrawl_does_not_look_like_an_update(enriched_row):
    """Every site instance re-downloads rows whose last_updated_at moved. A
    re-crawl that changed nothing used to move it anyway, for every row."""
    db, src = enriched_row
    _recrawl(db, src)                                   # first crawl changes things
    t = db.execute(db.query(Tender).statement).scalars().one()
    stamp = t.last_updated_at
    _recrawl(db, src)                                   # identical second crawl
    db.expire_all()
    assert db.get(Tender, t.id).last_updated_at == stamp

    _recrawl(db, src, raw_payload={"serial": "3", "end": "12-10-2026"})
    db.expire_all()
    assert db.get(Tender, t.id).last_updated_at > stamp, "a real change still counts"


def test_an_unchanged_listing_is_not_downloaded_again(enriched_row):
    """Loading every re-crawled row whole to learn it had not changed was most
    of Neon's monthly transfer. An identical listing is settled by a fingerprint;
    only a changed one loads the row."""
    from sqlalchemy import event
    db, src = enriched_row
    _recrawl(db, src)                            # first sight: loads and fingerprints

    seen = []
    engine = db.get_bind()
    listen = lambda *a: seen.append(a[2])
    event.listen(engine, "before_cursor_execute", listen)
    try:
        assert _recrawl(db, src) == 0            # identical listing
        full_loads = [s for s in seen if "tenders.title" in s and s.lstrip().upper().startswith("SELECT")]
        assert full_loads == [], full_loads
        assert not any(s.lstrip().upper().startswith("UPDATE") for s in seen)

        seen.clear()
        _recrawl(db, src, raw_payload={"serial": "9", "end": "15-10-2026"})
        assert any(s.lstrip().upper().startswith("UPDATE") for s in seen), "a change is still written"
    finally:
        event.remove(engine, "before_cursor_execute", listen)
    db.expire_all()
    t = db.execute(db.query(Tender).statement).scalars().one()
    assert t.raw_payload["end"] == "15-10-2026"
    assert t.raw_payload[DONE_KEY] is True, "enrichment still survives a changed listing"
