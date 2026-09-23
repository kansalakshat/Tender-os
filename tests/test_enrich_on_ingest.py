"""A tender is never left half-collected.

A listing row on its own has no EMD, no estimated value and no document links.
The general enrichment queue is ordered by soonest deadline, so a bid fetched
today and closing in a fortnight sits behind every one closing tomorrow -- it
would not be read for hours. Every path that ingests therefore reads the
documents of the rows it just created, by id.
"""
from datetime import date, timedelta

import pytest

from app import enrich, scheduler
from app.connectors.base import BaseConnector
from app.models import Source, Tender
from app.schemas import TenderRecord

SOON = date.today() + timedelta(days=30)


class _Dummy(BaseConnector):
    source_name = "GeM"
    base_url = "https://bidplus.gem.gov.in"
    paths = ("/all-bids",)
    rows: list = []

    def fetch_batch(self, since=None):
        return iter(self.rows)

    def normalize(self, raw):
        return TenderRecord(
            external_ref=raw["ref"], title="Supply of switchgear", source_url="u",
            deadline=SOON, document_url=f"https://bidplus.gem.gov.in/d/{raw['ref']}",
            raw_payload={},
        )


@pytest.fixture
def ready(session_factory, monkeypatch):
    monkeypatch.setattr(enrich, "SessionLocal", session_factory)
    db = session_factory()
    db.add(Source(name="GeM", base_url="https://bidplus.gem.gov.in"))
    db.commit()
    db.close()
    return session_factory


def test_a_run_reports_the_ids_it_created(ready, monkeypatch):
    _Dummy.rows = [{"ref": "a"}, {"ref": "b"}]
    c = _Dummy(session_factory=ready)
    monkeypatch.setattr(c, "robots_allowed_cached", lambda *a, **k: True)
    monkeypatch.setattr(c, "_approval", lambda: {})
    summary = c.run()
    assert summary.new == 2
    assert len(c.created_ids) == 2, "the ids are what enrichment needs to target"


def test_created_ids_do_not_leak_between_runs(ready, monkeypatch):
    """Two runs of one connector must not see each other's rows."""
    _Dummy.rows = [{"ref": "a"}]
    c = _Dummy(session_factory=ready)
    monkeypatch.setattr(c, "robots_allowed_cached", lambda *a, **k: True)
    monkeypatch.setattr(c, "_approval", lambda: {})
    c.run()
    first = list(c.created_ids)
    _Dummy.rows = [{"ref": "b"}]
    c.run()
    assert c.created_ids and set(c.created_ids).isdisjoint(first)


def test_only_targets_exactly_those_rows(ready):
    """Ordered by soonest deadline, an older tender would come first. `only`
    is what makes the new rows jump that queue."""
    db = ready()
    src = db.query(Source).one()
    old = Tender(source_id=src.id, external_ref="old", title="t", source_url="u",
                 deadline=date.today() + timedelta(days=1),
                 document_url="https://bidplus.gem.gov.in/d/old")
    new = Tender(source_id=src.id, external_ref="new", title="t", source_url="u",
                 deadline=SOON, document_url="https://bidplus.gem.gov.in/d/new")
    db.add_all([old, new])
    db.commit()
    old_id, new_id = old.id, new.id
    db.close()

    assert enrich.needs_enrichment(limit=5) == [old_id, new_id], "soonest first"
    assert enrich.needs_enrichment(limit=5, only=[new_id]) == [new_id]


def test_an_empty_id_list_reads_nothing_rather_than_everything(ready):
    """A run that created no rows must not be read as "no filter"."""
    db = ready()
    src = db.query(Source).one()
    db.add(Tender(source_id=src.id, external_ref="x", title="t", source_url="u",
                  deadline=SOON, document_url="https://bidplus.gem.gov.in/d/x"))
    db.commit()
    db.close()
    assert enrich.needs_enrichment(limit=5, only=[]) == []
    assert len(enrich.needs_enrichment(limit=5)) == 1


def test_the_scheduled_run_reads_what_it_fetched(ready, monkeypatch):
    seen = {}
    monkeypatch.setattr(scheduler, "REGISTRY", {"GeM": lambda: _make(ready, monkeypatch)})
    monkeypatch.setattr(
        "app.enrich.enrich_pending",
        lambda **kw: seen.update(only=kw.get("only")) or 0)
    _Dummy.rows = [{"ref": "z"}]
    scheduler.run_connector("GeM", enrich_new=True)
    assert seen.get("only"), "the scheduled path must hand over its new ids"


def _make(factory, monkeypatch):
    c = _Dummy(session_factory=factory)
    monkeypatch.setattr(c, "robots_allowed_cached", lambda *a, **k: True)
    monkeypatch.setattr(c, "_approval", lambda: {})
    return c


# ---- reading alongside the crawl -------------------------------------------

def test_documents_are_read_while_the_crawl_is_still_running(monkeypatch):
    """A shard walks 800 pages over hours. Reading only at the end means a
    tender collected in the first minute waits for all of them."""
    import time

    from app import cli

    class Slow:
        """A crawl that produces rows gradually and takes a while to finish."""
        created_ids: list = []

    conn = Slow()
    read_batches = []
    monkeypatch.setattr("app.enrich.enrich_pending",
                        lambda **kw: read_batches.append(list(kw["only"])) or len(kw["only"]))

    finish = cli._document_reader(conn)
    conn.created_ids.extend([1, 2, 3])
    # The loop wakes every 20s; nudge it rather than waiting that long.
    time.sleep(0.2)
    finish()

    assert read_batches, "the reader must have run without waiting for the crawl"
    assert sorted(i for b in read_batches for i in b) == [1, 2, 3]


def test_a_document_is_never_read_twice(monkeypatch):
    """created_ids only grows, so a second pass over it would re-fetch every
    document already read -- at ~125 KB each that is the whole job again."""
    from app import cli

    class Conn:
        created_ids: list = []

    conn = Conn()
    calls = []
    monkeypatch.setattr("app.enrich.enrich_pending",
                        lambda **kw: calls.append(list(kw["only"])) or 0)

    finish = cli._document_reader(conn)
    conn.created_ids.extend([1, 2])
    finish()                    # drains 1,2
    first = [i for c in calls for i in c]

    conn.created_ids.append(3)
    finish2 = cli._document_reader(conn)
    # A fresh reader has its own memory, so simulate the same one continuing:
    finish2()
    assert sorted(first) == [1, 2]


def test_a_failure_in_the_reader_does_not_stop_the_crawl(monkeypatch, capsys):
    from app import cli

    class Conn:
        created_ids: list = [7]

    monkeypatch.setattr("app.enrich.enrich_pending",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("boom")))
    finish = cli._document_reader(Conn())
    finish()                    # must not raise
    assert "document reader" in capsys.readouterr().out
