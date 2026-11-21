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
    """What run() does for one listing: upsert, commit, then refill states."""
    rec = TenderRecord(external_ref=ref, source_url="u" + ref, **kw)
    c = _Dummy.__new__(_Dummy)
    c.created_ids, c.states_todo = [], []
    c._upsert(db, src, rec)
    db.commit()
    geo.refill_states(db, c.states_todo)
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
    db.refresh(t)
    assert t.states == "|Madhya Pradesh|Rajasthan|"        # recomputed when the text changes


def test_an_enrichment_save_during_a_fetch_is_not_overwritten(session_factory):
    """The mirror case: the fetcher loads a tender, keeps reading pages, and
    saves later. If enrichment saved newer text in between (here a department
    in Bhopal, which the listing does not carry, so the fetcher leaves it), the
    states must come from the row as saved, not from the fetcher's old copy."""
    from datetime import datetime
    db = session_factory()
    cppp = Source(name="CPPP", base_url="https://eprocure.gov.in"); db.add(cppp); db.flush()
    tid = _save(db, cppp, "m", title="Supply of chairs").id
    c = _Dummy.__new__(_Dummy)
    c.created_ids, c.states_todo = [], []
    c._upsert(db, cppp, TenderRecord(external_ref="m", source_url="um",
                                     title="Supply of chairs at Jaipur"))      # loaded, not saved yet
    with session_factory() as other:                                           # enrichment saves meanwhile
        row = other.get(Tender, tid)
        row.department, row.states = "Collectorate Bhopal", "|Madhya Pradesh|"
        row.last_updated_at = datetime(2030, 1, 1)
        other.commit()
    db.commit()                                                                # the fetcher's batch commits
    geo.refill_states(db, c.states_todo)
    # Saved now: the fetcher's Jaipur title and enrichment's Bhopal department.
    # Worked out from the fetcher's old copy it would be "|Rajasthan|" alone.
    assert _states(session_factory, tid) == "|Madhya Pradesh|Rajasthan|"


def test_a_tender_updated_twice_in_one_run_never_keeps_stale_states(session_factory, monkeypatch):
    """The fetcher's session keeps its objects after a commit, so on a second
    update its copy already says NULL while the database holds the refilled
    value. The second save must still write NULL: if its refill then fails,
    the row must say "not worked out" (NULL), never the first update's states."""
    db = session_factory()
    raj = Source(name="Rajasthan eProcurement", base_url="https://eproc.rajasthan.gov.in"); db.add(raj); db.flush()
    c = _Dummy.__new__(_Dummy)
    c.created_ids, c.states_todo = [], []
    c._upsert(db, raj, TenderRecord(external_ref="r", source_url="ur", title="Road"))
    db.commit()
    tid = db.execute(select(Tender.id).where(Tender.external_ref == "r")).scalar_one()
    c._upsert(db, raj, TenderRecord(external_ref="r", source_url="ur", title="Road near Gwalior"))
    db.commit()
    keep = db.get(Tender, tid)            # the session's copy, kept: it says NULL from here on
    geo.refill_states(db, c.states_todo)
    assert keep.states is None            # the copy is not refreshed by the refill
    assert _states(session_factory, tid) == "|Madhya Pradesh|Rajasthan|"
    c._upsert(db, raj, TenderRecord(external_ref="r", source_url="ur", title="Road near Surat"))
    db.commit()
    monkeypatch.setattr(geo, "fill_states", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone")))
    geo.refill_states(db, c.states_todo)
    assert _states(session_factory, tid) is None          # not the stale "|Madhya Pradesh|Rajasthan|"


def test_a_failed_refill_leaves_null_and_does_not_raise(session_factory, monkeypatch):
    db = session_factory()
    raj = Source(name="Rajasthan eProcurement", base_url="https://eproc.rajasthan.gov.in"); db.add(raj); db.flush()
    _save(db, raj, "f", title="Road")
    c = _Dummy.__new__(_Dummy)
    c.created_ids, c.states_todo = [], []
    c._upsert(db, raj, TenderRecord(external_ref="f", source_url="uf", title="Road near Gwalior"))
    db.commit()
    def boom(*a, **k): raise RuntimeError("database gone")
    monkeypatch.setattr(geo, "fill_states", boom)
    geo.refill_states(db, c.states_todo)                  # logs, does not raise
    assert c.states_todo == []
    tid = db.execute(select(Tender.id).where(Tender.external_ref == "f")).scalar_one()
    assert _states(session_factory, tid) is None          # NULL: the filter reads the text itself


def _gem_tender(session_factory, monkeypatch, office):
    """One GeM tender whose document names `office`; enrich_pending wired to it."""
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
        def __init__(self, *a, **k): pass
        def get(self, url): return R()
        def close(self): pass
    monkeypatch.setattr(enrich.httpx, "Client", C)
    monkeypatch.setattr(enrich, "_parse", lambda b: {"office": office})
    monkeypatch.setattr(enrich, "assert_not_blocked", lambda u: None)
    monkeypatch.setattr(enrich, "needs_enrichment", lambda *a, **k: [t.id])
    return db, t.id


def _states(session_factory, tid):
    with session_factory() as fresh:              # read from the database, not a cached object
        return fresh.execute(select(Tender.states).where(Tender.id == tid)).scalar_one()


def test_enrichment_recomputes_states(session_factory, monkeypatch):
    from app import enrich
    db, tid = _gem_tender(session_factory, monkeypatch, "Collectorate Bhopal")
    assert enrich.enrich_pending(limit=1, session_factory=session_factory) == 1
    assert _states(session_factory, tid) == "|Madhya Pradesh|"


def test_a_fetcher_save_during_enrichment_is_not_overwritten(session_factory, monkeypatch):
    """Enrichment loads a tender, downloads its document (slow), then saves.
    If the fetcher saves newer text in between, the states must come from that
    newer text, not from the copy enrichment loaded before the download."""
    from app import enrich
    db, tid = _gem_tender(session_factory, monkeypatch, "Collectorate Bhopal")
    real = enrich.enrich_one

    def fetcher_saves_mid_download(edb, tender, client):
        with session_factory() as other:                                 # the fetcher, another session
            row = other.get(Tender, tid)
            row.title = "Chairs for Collectorate Jaipur"
            row.states = "|Rajasthan|"
            other.commit()
        return real(edb, tender, client)                                 # enrichment carries on with its old copy

    monkeypatch.setattr(enrich, "enrich_one", fetcher_saves_mid_download)
    enrich.enrich_pending(limit=1, session_factory=session_factory)
    # Saved now: the fetcher's Jaipur title and the document's Bhopal office.
    # The old code wrote "|Madhya Pradesh|", worked out from its stale "Chairs".
    assert _states(session_factory, tid) == "|Madhya Pradesh|Rajasthan|"


def test_enrichment_writes_null_even_when_its_copy_already_says_null(session_factory, monkeypatch):
    """Loaded with states NULL; a fill stores a value during the download.
    Setting NULL again is no change to SQLAlchemy unless flagged, so the
    value would survive the commit -- and NULL-only fills never look again."""
    from app import enrich
    db, tid = _gem_tender(session_factory, monkeypatch, "Collectorate Bhopal")
    with session_factory() as other:
        other.get(Tender, tid).states = None
        other.commit()
    tender = db.get(Tender, tid)
    db.refresh(tender)
    assert tender.states is None                         # enrichment's copy says NULL
    with session_factory() as other:                     # a fill lands during the download
        other.get(Tender, tid).states = "|"
        other.commit()

    class R:
        content = b""
        def raise_for_status(self): pass
    class C:
        def get(self, url): return R()
    assert enrich.enrich_one(db, tender, C())
    db.commit()                                          # before any refill
    assert _states(session_factory, tid) is None


def test_enrich_one_does_not_work_out_states_from_its_loaded_copy():
    import inspect
    from app import enrich
    src = inspect.getsource(enrich.enrich_one)
    assert "states_of" not in src and "clear_states(tender)" in src


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


def test_migration_0015_only_adds_the_column_and_imports_no_app_code():
    """A migration must still run on a fresh database after app/ is renamed."""
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "migrations" / "versions" / "0015_tender_states.py").read_text()
    assert 'revision = "0015"' in src and 'down_revision = "0014"' in src
    assert 'op.add_column("tenders", sa.Column("states"' in src
    import re
    assert not re.search(r"^\s*(from|import) app\b", src, re.M)
