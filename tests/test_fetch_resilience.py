"""Fetching survives the things that went wrong on real nights.

14 Oct 2026, 03:20: the laptop's internet dropped for a minute. The Daily run's
GeM crawl, all three deep crawlers and the deep document pass ended for the day.
10 Oct: the Daily run and a deep crawler inserted the same bid, and the loser's
unique-key error ended its crawl. Dedup spent 65 minutes re-comparing pairs it
had compared the day before.
"""
import json
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import OperationalError

from app import enrich, scheduler
from app.connectors.base import BaseConnector
from app.connectors.gem import GeMConnector
from app.models import Source, Tender
from app.schemas import RunSummary, TenderRecord


# ---- a page that never loads is a failure, not the end of the listing -------

class _DeadPage:
    def query_selector(self, _):
        return None

    def evaluate(self, _):
        raise TimeoutError("net::ERR_INTERNET_DISCONNECTED")

    def wait_for_timeout(self, _):
        pass


def test_a_page_that_never_loads_fails_the_crawl():
    gem = object.__new__(GeMConnector)
    gem._throttled = 0
    with pytest.raises(RuntimeError, match="page 301 never loaded"):
        gem._next_page(_DeadPage(), 300)


# ---- two crawlers inserting the same bid ------------------------------------

class _Dummy(BaseConnector):
    source_name = "GeM"
    base_url = "https://bidplus.gem.gov.in"
    paths = ("/all-bids",)

    def fetch_batch(self, since=None):
        return iter(())

    def normalize(self, raw):
        raise NotImplementedError


def test_a_bid_another_crawler_just_inserted_is_skipped_not_fatal(session_factory):
    db = session_factory()
    src = Source(name="GeM", base_url="https://bidplus.gem.gov.in")
    db.add(src)
    db.commit()
    crawler = _Dummy.__new__(_Dummy)
    crawler.created_ids = []
    real = crawler._fingerprint

    def other_crawler_wins(rec):
        # Between this crawler's "do we have it?" and its INSERT.
        if rec.external_ref == "GEM/1":
            with session_factory() as other:
                other.add(Tender(source_id=src.id, external_ref="GEM/1", title="t",
                                 source_url="u"))
                other.commit()
        return real(rec)

    crawler._fingerprint = other_crawler_wins
    recs = [TenderRecord(external_ref=ref, title="t", source_url="u")
            for ref in ("GEM/1", "GEM/2")]
    assert crawler._upsert_many(db, src, recs) == 1, "only GEM/2 is new to this crawler"
    db.commit()
    assert db.query(Tender).count() == 2
    assert len(crawler.created_ids) == 1


# ---- dedup compares only pairs with something new ---------------------------

def test_dedup_compares_only_new_or_changed_rows(session_factory, tmp_path, monkeypatch):
    import app.dedup as dedup
    from app.models import utcnow

    cache = str(tmp_path / "dedup.json")
    with session_factory() as db:
        a = Source(name="CPPP", base_url="https://eprocure.gov.in")
        b = Source(name="GeM", base_url="https://bidplus.gem.gov.in")
        db.add_all([a, b])
        db.flush()
        for n in range(5):
            db.add(Tender(source_id=a.id, external_ref=f"A{n}", source_url="u", organization="CPWD",
                          title=f"Construction of boundary wall at depot {n}"))
            db.add(Tender(source_id=b.id, external_ref=f"B{n}", source_url="u", organization="CPWD",
                          title=f"Painting of office building at block {n}"))
        db.flush()
        db.query(Tender).update({Tender.last_updated_at: utcnow() - timedelta(days=3)})
        # The newest row sets the stamp; rows within an hour of it are re-read
        # on the next run (clock-skew margin), the rest come from the cache.
        recent = db.query(Tender).filter_by(external_ref="B4").one()
        recent.last_updated_at = utcnow() - timedelta(days=1)
        db.commit()
        b_id, recent_id = b.id, recent.id
        dedup.link_duplicates(db, cache_path=cache)
    built = json.load(open(cache))["written"]

    with session_factory() as db:
        twin = Tender(source_id=b_id, external_ref="B99", source_url="u", organization="CPWD",
                      title="Construction of boundary wall at depot 3")
        db.add(twin)
        db.commit()
        twin_id = twin.id

    pairs = []
    real = dedup.is_duplicate
    monkeypatch.setattr(dedup, "is_duplicate", lambda x, y: pairs.append({x.id, y.id}) or real(x, y))
    with session_factory() as db:
        assert dedup.link_duplicates(db, cache_path=cache) == 1, "the new twin is still found"
    assert pairs and all(p & {twin_id, recent_id} for p in pairs), "an old pair was compared again"
    assert json.load(open(cache))["written"] == built, "a delta run must not reset the cache's age"


# ---- the Daily run splits GeM and retries what failed ------------------------

def test_a_sliced_run_splits_the_pages_and_combines_the_results(monkeypatch, tmp_path):
    made = []

    class Fake:
        start_page, max_pages, resume_file = 1, 500, None

        def __init__(self):
            self.created_ids = []
            made.append(self)

        def run(self, since=None):
            self.created_ids = [self.start_page]
            return RunSummary(source="GeM", fetched=10, new=1,
                              status="error" if self.start_page == 168 else "ok")

        def close(self):
            pass

    monkeypatch.setattr(scheduler, "REGISTRY", {"GeM": Fake})
    summary = scheduler.run_connector("GeM", slices=3, resume_file=str(tmp_path / "daily_GeM.json"))
    assert [(c.start_page, c.max_pages) for c in made] == [(1, 167), (168, 167), (335, 166)]
    assert [c.resume_file[-16:] for c in made] == [
        "daily_GeM_1.json", "daily_GeM_2.json", "daily_GeM_3.json"]
    assert (summary.fetched, summary.new, summary.status) == (30, 3, "error")


def test_the_daily_run_tries_a_failed_source_once_more(tmp_path, monkeypatch):
    import app.connectors, app.dedup, app.retention
    import run_prod_worker

    ran = []

    def fake_run_connector(name, since, enrich_new, resume_file=None, slices=1):
        ran.append((name, slices))
        return SimpleNamespace(status="ok" if name == "CPPP" else "error")

    monkeypatch.setattr(app.connectors, "REGISTRY", {"CPPP": None, "GeM": None})
    monkeypatch.setattr(scheduler, "run_connector", fake_run_connector)
    monkeypatch.setattr(app.retention, "purge_expired", lambda: 0)
    monkeypatch.setattr(enrich, "enrich_pending", lambda **kw: 0)
    monkeypatch.setattr(app.dedup, "link_duplicates", lambda: 0)
    monkeypatch.setattr(run_prod_worker, "RETRY_PAUSE", 0)

    run_prod_worker.run_once(48.0, progress_dir=str(tmp_path))
    assert ran == [("CPPP", 1), ("GeM", 3), ("GeM", 3)]


# ---- the document pass and the deep crawl outlive a dropped connection -------

def test_reading_documents_survives_an_unreachable_database(monkeypatch):
    def down(*a, **kw):
        raise OperationalError("SELECT", {}, Exception("failed to resolve host"))

    monkeypatch.setattr(enrich, "needs_enrichment", down)
    assert enrich.enrich_pending(limit=10) == 0


def test_a_deep_crawler_that_dies_is_relaunched(monkeypatch, tmp_path):
    import gem_deep

    codes = [1, 0]
    launched = []

    class FakeProc:
        def __init__(self, args, **kw):
            launched.append(args)

        def wait(self):
            return codes.pop(0)

        def terminate(self):
            pass

    monkeypatch.setattr(gem_deep, "SHARDS", 1)
    monkeypatch.setattr(gem_deep, "RELAUNCH_PAUSE", 0)
    monkeypatch.setattr(gem_deep, "HERE", str(tmp_path))
    monkeypatch.setattr(gem_deep.subprocess, "Popen", FakeProc)
    monkeypatch.setattr(gem_deep.signal, "signal", lambda *a: None)
    monkeypatch.setattr(enrich, "enrich_pending", lambda **kw: 0)
    assert gem_deep.main() == 0
    assert len(launched) == 2, "the crawler that exited with an error ran again"
    assert launched[0] == launched[1], "with the same slice and resume file"
