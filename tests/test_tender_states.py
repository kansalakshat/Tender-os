"""tenders.states: worked out once when a tender is saved, read by every state
filter and the home page's counts, with the same rule the regex always used."""
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select

from app import geo, web
from app.connectors.base import BaseConnector
from app.models import Source, Tender
from app.schemas import TenderRecord

TODAY = date(2026, 11, 18)


class _Dummy(BaseConnector):
    source_name = "Rajasthan eProcurement"
    base_url = "https://eproc.rajasthan.gov.in"
    paths = ("/",)

    def fetch_batch(self, since=None):
        return iter(())

    def normalize(self, raw):
        raise NotImplementedError


def _save(db, src, ref, **kw):
    rec = TenderRecord(external_ref=ref, source_url="u" + ref, **kw)
    c = _Dummy.__new__(_Dummy)
    c.created_ids = []
    c._upsert(db, src, rec)
    db.commit()
    return db.execute(select(Tender).where(Tender.external_ref == ref)).scalar_one()


def test_rule_matches_the_old_text_search():
    assert geo.tender_states("Rajasthan eProcurement", "Supply of pipes", None, None, None, None) == "|Rajasthan|"
    assert geo.tender_states("CPPP", "Four-laning from Jaipur to Gwalior", None, None, None, None) == \
        "|Madhya Pradesh|Rajasthan|"
    assert geo.tender_states("CPPP", "Lakhimpur Kheri road", None, None, None, None) == "|Assam|Uttar Pradesh|"
    assert geo.tender_states("CPPP", "Pump repair", "Ministry of Railways", None, None, None) == "|"
    assert geo.tender_states("GeM", "Chairs", "Dept", None, "Collectorate, Indore", None) == "|Madhya Pradesh|"


def test_the_fetcher_stores_states_on_create_and_update(session_factory):
    db = session_factory()
    raj = Source(name="Rajasthan eProcurement", base_url="https://eproc.rajasthan.gov.in"); db.add(raj); db.flush()
    t = _save(db, raj, "a", title="Construction of road", organization="PWD")
    assert t.states == "|Rajasthan|"                       # from its state portal
    t = _save(db, raj, "a", title="Construction of road near Gwalior", organization="PWD")
    assert t.states == "|Madhya Pradesh|Rajasthan|"        # recomputed when the text changes


def test_enrichment_recomputes_states(session_factory, monkeypatch):
    from app import enrich
    db = session_factory()
    gem = Source(name="GeM", base_url="https://bidplus.gem.gov.in"); db.add(gem); db.flush()
    t = Tender(source_id=gem.id, external_ref="g", source_url="u", title="Chairs", organization="Ministry",
               document_url="https://bidplus.gem.gov.in/showbidDocument/1", states="|")
    db.add(t); db.commit()

    class R:
        content = b""
        def raise_for_status(self): pass
    class C:
        def get(self, url): return R()
    monkeypatch.setattr(enrich, "_parse", lambda b: {"office": "Collectorate Bhopal"})
    monkeypatch.setattr(enrich, "assert_not_blocked", lambda u: None)
    assert enrich.enrich_one(db, t, C())
    assert t.states == "|Madhya Pradesh|"


def _rows(session_factory, monkeypatch):
    monkeypatch.setattr(web, "today_ist", lambda: TODAY)
    db = session_factory()
    cppp = Source(name="CPPP", base_url="https://eprocure.gov.in"); db.add(cppp); db.flush()
    soon, old = TODAY + timedelta(days=5), TODAY - timedelta(days=5)
    db.add_all([
        Tender(source_id=cppp.id, external_ref="s", source_url="1", title="Road in Jaipur", deadline=soon,
               states="|Rajasthan|"),
        # stored says Goa although the text does not: proves the column is what is read
        Tender(source_id=cppp.id, external_ref="g", source_url="2", title="Pump repair", deadline=soon,
               states="|Goa|"),
        # not worked out yet: found by the old text search, and counted at home
        Tender(source_id=cppp.id, external_ref="n", source_url="3", title="Hall at Udaipur", deadline=soon),
        Tender(source_id=cppp.id, external_ref="c", source_url="4", title="Closed Jaipur", deadline=old),
    ])
    db.commit()
    return db


def test_state_filter_reads_the_column_and_falls_back_for_null(session_factory, monkeypatch):
    db = _rows(session_factory, monkeypatch)
    def refs(st):
        return {r for (r,) in db.execute(select(Tender.external_ref).where(geo.state_clause(st, [], "sqlite")))}
    assert refs("Rajasthan") == {"s", "n", "c"}             # stored, plus NULL rows by text
    assert refs("Goa") == {"g"}


def test_home_counts_come_from_the_column(session_factory, monkeypatch):
    db = _rows(session_factory, monkeypatch)
    from app import db as dbm
    dbm._SHARED.clear()
    assert web._state_counts(db, TODAY) == {"Goa": 1, "Rajasthan": 2}   # closed one not counted


def test_fill_states_fills_only_missing_open_rows(session_factory, monkeypatch):
    db = _rows(session_factory, monkeypatch)
    assert geo.fill_states(db, only_open_on=TODAY) == 1
    by = dict(db.execute(select(Tender.external_ref, Tender.states)).all())
    assert by == {"s": "|Rajasthan|", "g": "|Goa|", "n": "|Rajasthan|", "c": None}
    assert geo.fill_states(db, only_open_on=TODAY) == 0      # nothing left to do


def test_refresh_fixes_rows_an_older_fetcher_changed(session_factory, monkeypatch):
    """An older fetcher rewrites a row's text but not its states; a NULL-only
    fill never looks at it again. refresh=True recomputes every row."""
    db = _rows(session_factory, monkeypatch)
    geo.fill_states(db, only_open_on=TODAY)
    t = db.execute(select(Tender).where(Tender.external_ref == "s")).scalar_one()
    t.title = "Road in Gwalior"                 # what an old fetcher would write
    db.commit()
    assert geo.fill_states(db, only_open_on=TODAY) == 0                    # NULL-only: misses it
    assert geo.fill_states(db, only_open_on=TODAY, refresh=True) == 2      # it, and "g" (texts say none)
    by = dict(db.execute(select(Tender.external_ref, Tender.states)).all())   # from the database
    assert by["s"] == "|Madhya Pradesh|" and by["g"] == "|" and by["n"] == "|Rajasthan|"
    assert geo.fill_states(db, only_open_on=TODAY, refresh=True) == 0      # nothing left to fix


def test_a_save_between_read_and_write_is_not_overwritten(session_factory, monkeypatch):
    """Backfill reads a row, then enrichment saves new states and moves
    last_updated_at before backfill writes: backfill must leave it alone."""
    from datetime import datetime
    db = _rows(session_factory, monkeypatch)
    t = db.execute(select(Tender).where(Tender.external_ref == "n")).scalar_one()
    seen = t.last_updated_at
    t.states, t.last_updated_at = "|Madhya Pradesh|", datetime(2030, 1, 1)   # enrichment wins the race
    db.commit()
    assert geo._store_states(db, [(t.id, "|Rajasthan|", seen)], only_null=True) == 0
    assert geo._store_states(db, [(t.id, "|Rajasthan|", seen)], only_null=False) == 0
    db.refresh(t)
    assert t.states == "|Madhya Pradesh|"
    # read as it still is: written
    assert geo._store_states(db, [(t.id, "|Rajasthan|", t.last_updated_at)], only_null=False) == 1


def test_enrichment_reads_the_cached_source_name_not_a_select_per_document():
    import inspect
    from app import enrich
    src = inspect.getsource(enrich.enrich_one)
    assert "db.get(Source" not in src and "source_name(tender) or None" in src


def test_migration_0015_adds_and_fills_the_column():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0015_tender_states.py").read_text()
    assert 'revision = "0015"' in src and 'down_revision = "0014"' in src
    assert 'op.add_column("tenders", sa.Column("states"' in src and "fill_states(db, only_open_on=" in src
