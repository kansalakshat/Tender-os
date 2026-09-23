"""The operator dashboard, and who may see that it exists at all.

The access rule is the part worth testing: /admin must be indistinguishable
from a URL that was never routed, for everyone who is not an operator. A 403
would confirm the page exists, which is exactly what someone probing for an
admin panel wants to learn.
"""
import pytest
from fastapi.testclient import TestClient

from app import admin
from app.api import app
from app.auth import SESSION_COOKIE, make_session
from app.db import get_db
from app.models import Source, Tender, User


@pytest.fixture(autouse=True)
def _clear_live_cache():
    """live_counts() memoises in a module global, so without this one test's
    counts are served to the next -- and the leak is invisible until tests are
    run in a different order."""
    admin._cache = None
    yield
    admin._cache = None


@pytest.fixture
def client(session_factory, monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    db = session_factory()
    db.add_all([
        User(email="boss@example.com", email_verified=True),
        User(email="someone@example.com", email_verified=True),
    ])
    src = Source(name="GeM", base_url="https://bidplus.gem.gov.in")
    db.add(src)
    db.flush()
    db.add(Tender(source_id=src.id, external_ref="g1", title="a tender", source_url="u"))
    db.commit()
    ids = {u.email: u.id for u in db.query(User).all()}
    db.close()

    app.dependency_overrides[get_db] = lambda: session_factory()
    yield TestClient(app), ids
    app.dependency_overrides.clear()


def _as(c, uid):
    c.cookies.set(SESSION_COOKIE, make_session(uid))
    return c


def test_anonymous_gets_404_not_403(client):
    c, _ = client
    assert c.get("/admin").status_code == 404


def test_an_ordinary_signed_in_user_gets_404(client):
    c, ids = client
    r = _as(c, ids["someone@example.com"]).get("/admin")
    assert r.status_code == 404
    # And nothing in the body hints that a dashboard exists.
    assert "Operations" not in r.text


def test_the_operator_sees_the_dashboard(client):
    c, ids = client
    r = _as(c, ids["boss@example.com"]).get("/admin")
    assert r.status_code == 200
    assert "Operations" in r.text


def test_stats_and_fetch_are_closed_to_everyone_else(client):
    """The page being hidden is worth little if its endpoints are not."""
    c, ids = client
    _as(c, ids["someone@example.com"])
    assert c.get("/admin/stats").status_code == 404
    assert c.post("/admin/fetch", json={"connector": "GeM"}).status_code == 404


def test_is_admin_is_case_and_space_insensitive(monkeypatch):
    monkeypatch.setenv("ADMIN_EMAILS", "Boss@Example.com , other@x.io")
    assert admin.is_admin(User(email="boss@example.com"))
    assert admin.is_admin(User(email="  OTHER@X.IO  "))
    assert not admin.is_admin(User(email="nope@example.com"))
    assert not admin.is_admin(None)


def test_daily_intake_reports_empty_days_as_zero(session_factory):
    """A day that fetched nothing is the interesting one; a query that omits it
    looks exactly like a day that has not happened yet."""
    db = session_factory()
    rows = admin.daily_intake(db, days=7)
    db.close()
    assert len(rows) == 7
    assert all(r["count"] == 0 for r in rows)
    assert [r["day"] for r in rows] == sorted(r["day"] for r in rows), "oldest first"


def test_a_source_with_no_tenders_reports_zero_open(session_factory):
    """The join is an outer one: an empty source arrives as a row of NULLs, and
    a naive "deadline IS NULL means open" counts that phantom as a tender."""
    db = session_factory()
    db.add(Source(name="Nothing Yet", base_url="https://example.invalid"))
    db.commit()
    rows = {r["name"]: r for r in admin.by_source(db)}
    db.close()
    assert rows["Nothing Yet"]["total"] == 0
    assert rows["Nothing Yet"]["open"] == 0


def test_daily_intake_counts_today_in_utc(session_factory):
    """first_seen_at is stored as naive UTC, so the buckets must be UTC too --
    otherwise today's bar reads zero for the first hours of an IST day."""
    from app.models import utcnow

    db = session_factory()
    src = Source(name="S", base_url="https://example.invalid")
    db.add(src)
    db.flush()
    db.add(Tender(source_id=src.id, external_ref="x1", title="t",
                  source_url="u", first_seen_at=utcnow()))
    db.commit()
    rows = admin.daily_intake(db, days=7)
    db.close()
    assert rows[-1]["day"] == utcnow().date().isoformat()
    assert rows[-1]["count"] == 1, "a row stored just now belongs to today"


def test_live_counts_are_closed_to_everyone_else(client):
    c, ids = client
    assert c.get("/admin/live").status_code == 404                 # anonymous
    _as(c, ids["someone@example.com"])
    assert c.get("/admin/live").status_code == 404                 # signed in, not admin


def test_live_counts_shape(client):
    c, ids = client
    d = _as(c, ids["boss@example.com"]).get("/admin/live").json()
    assert set(d) >= {"total", "open", "sources", "at"}
    assert d["total"] == 1 and d["open"] == 1
    assert d["sources"] == [{"name": "GeM", "total": 1}]


def test_live_counts_omit_sources_holding_nothing(session_factory):
    """The ticker is a list of what is arriving; a portal with no rows is noise."""
    db = session_factory()
    db.add(Source(name="Empty", base_url="https://example.invalid"))
    db.commit()
    names = [s["name"] for s in admin.live_counts(db)["sources"]]
    db.close()
    assert "Empty" not in names


def test_live_counts_are_cached_between_polls(session_factory, monkeypatch):
    """Measured at ~0.9s against the real database; polled by several open tabs
    that is a database kept permanently awake for a row counter."""
    db = session_factory()
    src = Source(name="S", base_url="https://example.invalid")
    db.add(src)
    db.flush()
    db.add(Tender(source_id=src.id, external_ref="a", title="t", source_url="u"))
    db.commit()

    clock = [1000.0]
    first = admin.live_counts(db, now=lambda: clock[0])
    assert first["total"] == 1

    # A row lands, but a poll one second later must not re-run the counts.
    db.add(Tender(source_id=src.id, external_ref="b", title="t", source_url="u"))
    db.commit()
    clock[0] += 1
    assert admin.live_counts(db, now=lambda: clock[0])["total"] == 1, "served from cache"

    # Past the TTL it recounts.
    clock[0] += admin._CACHE_SECONDS
    assert admin.live_counts(db, now=lambda: clock[0])["total"] == 2
    db.close()


def test_fetch_takes_json_not_a_form(client, monkeypatch):
    """The whole app posts JSON. A form here needs python-multipart, which is
    not installed -- and the failure was a 500 whose HTML body broke the
    dashboard's JSON parsing, so the real error never reached the screen."""
    started = {}

    def fake_start(name, max_pages=None, since_hours=None, **kw):
        started.update(name=name, max_pages=max_pages, since_hours=since_hours)
        return True, "started"

    monkeypatch.setattr("app.adminjobs.start", fake_start)
    c, ids = client
    r = _as(c, ids["boss@example.com"]).post(
        "/admin/fetch", json={"connector": "GeM", "pages": 25, "since_hours": 6}
    )
    assert r.status_code == 200 and r.json()["ok"] is True
    assert started == {"name": "GeM", "max_pages": 25, "since_hours": 6.0}


def test_blank_pages_means_the_connector_default(client, monkeypatch):
    """An omitted field must not arrive as zero, which would read as
    "fetch no pages" and quietly do nothing."""
    started = {}
    monkeypatch.setattr(
        "app.adminjobs.start",
        lambda name, max_pages=None, since_hours=None, **kw: (
            started.update(max_pages=max_pages, since_hours=since_hours), (True, "ok"))[1],
    )
    c, ids = client
    _as(c, ids["boss@example.com"]).post("/admin/fetch", json={"connector": "GeM"})
    assert started == {"max_pages": None, "since_hours": None}


# ---- jobs started from the dashboard ---------------------------------------

def test_a_fetch_reads_the_documents_in_the_same_job(client, monkeypatch):
    """One job, not two. A listing row without its document has no EMD, no
    value and no links, so fetching without reading leaves half a tender."""
    got = {}

    def fake_start(name, max_pages=None, since_hours=None, then_enrich=0, workers=4):
        got.update(name=name, then_enrich=then_enrich, workers=workers)
        return True, "started"

    monkeypatch.setattr("app.adminjobs.start", fake_start)
    c, ids = client
    r = _as(c, ids["boss@example.com"]).post(
        "/admin/fetch", json={"connector": "GeM", "enrich": 500, "workers": 6})
    assert r.status_code == 200
    assert got == {"name": "GeM", "then_enrich": 500, "workers": 6}


def test_documents_only_is_a_job_of_its_own(client, monkeypatch):
    got = {}
    monkeypatch.setattr(
        "app.adminjobs.start",
        lambda name, **kw: (got.update(name=name, **kw), (True, "ok"))[1])
    c, ids = client
    from app.adminjobs import ENRICH_JOB

    _as(c, ids["boss@example.com"]).post(
        "/admin/fetch", json={"connector": ENRICH_JOB, "enrich": 100})
    assert got["name"] == ENRICH_JOB and got["then_enrich"] == 100


def test_workers_are_capped(monkeypatch):
    """The cap is politeness to a government host, not a technical ceiling, so
    it must not be settable from a form field."""
    from app import adminjobs

    seen = {}
    monkeypatch.setattr(adminjobs.threading, "Thread",
                        lambda target, args, name, daemon: type(
                            "T", (), {"start": lambda self: seen.update(workers=args[5])})())
    adminjobs._current = None
    ok, _ = adminjobs.start(adminjobs.ENRICH_JOB, then_enrich=10, workers=9999)
    assert ok and seen["workers"] == adminjobs.MAX_WORKERS
    adminjobs._current = None


def test_a_serverless_host_offers_no_job_controls(client, monkeypatch):
    """Not about the browser. A job is a background thread and the instance is
    frozen once it answers, so the work would stop partway through -- and the
    job log lives in that process's memory, so the next poll can reach a
    different instance and find nothing. A button that reports "started" and
    then goes quiet is worse than no button."""
    monkeypatch.setenv("VERCEL", "1")
    c, ids = client
    body = _as(c, ids["boss@example.com"]).get("/admin").text
    assert "id=fetchform" not in body, "no control where the job cannot finish"
    assert "Jobs cannot run on this host" in body
    # The numbers are still worth showing; only the controls go.
    assert "Bid documents read" in body and "Portals" in body


def test_a_host_that_can_finish_a_job_offers_the_controls(client, monkeypatch):
    monkeypatch.delenv("VERCEL", raising=False)
    c, ids = client
    body = _as(c, ids["boss@example.com"]).get("/admin").text
    assert "id=fetchform" in body
    assert '<option value="GeM"' in body and "Bid documents" in body
