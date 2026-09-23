"""Saving tenders to come back to.

The interesting case is not adding or removing -- it is what happens when a
saved tender closes. Expired tenders are purged daily, so a restricting foreign
key here would have the whole nightly ingest fail the first time anyone saved
something that later closed.
"""
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.api import app
from app.auth import SESSION_COOKIE, make_session
from app.db import get_db
from app.models import Base, Source, Tender, User, WishlistItem
from app.retention import purge_expired

SOON = date.today() + timedelta(days=20)
GONE = date.today() - timedelta(days=5)


@pytest.fixture
def own_db():
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )

    # SQLite ignores foreign keys unless each connection asks for them, and this
    # must be attached BEFORE anything opens one: StaticPool keeps a single
    # connection for the engine's life, so a listener added after create_all()
    # never runs and the cascade below silently does nothing -- the test would
    # pass while Postgres behaved differently.
    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(dbapi_connection, _record):
        cur = dbapi_connection.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def client(own_db):
    db = own_db()
    src = Source(name="CPPP", base_url="https://eprocure.gov.in")
    db.add_all([User(email="a@example.com", email_verified=True),
                User(email="b@example.com", email_verified=True), src])
    db.flush()
    db.add_all([
        Tender(id=1, source_id=src.id, external_ref="open1", source_url="u",
               title="Supply of switchgear", deadline=SOON, status="open"),
        Tender(id=2, source_id=src.id, external_ref="closing", source_url="u",
               title="Cable laying work", deadline=GONE, status="open"),
    ])
    db.commit()
    ids = {u.email: u.id for u in db.query(User).all()}
    db.close()
    app.dependency_overrides[get_db] = lambda: own_db()
    yield TestClient(app), ids, own_db
    app.dependency_overrides.clear()


def _as(c, uid):
    c.cookies.set(SESSION_COOKIE, make_session(uid))
    return c


def test_save_then_see_it_listed(client):
    c, ids, _ = client
    _as(c, ids["a@example.com"])
    assert c.post("/wishlist/1").json() == {"saved": True}
    body = c.get("/wishlist").text
    assert "Supply of switchgear" in body


def test_saving_twice_is_not_an_error_and_stores_one_row(client):
    """A second tab, or an impatient second click."""
    c, ids, sf = client
    _as(c, ids["a@example.com"])
    assert c.post("/wishlist/1").status_code == 200
    assert c.post("/wishlist/1").status_code == 200
    db = sf()
    assert db.query(WishlistItem).count() == 1
    db.close()


def test_unsaving_removes_it(client):
    c, ids, _ = client
    _as(c, ids["a@example.com"])
    c.post("/wishlist/1")
    assert c.request("DELETE", "/wishlist/1").json() == {"saved": False}
    assert "Supply of switchgear" not in c.get("/wishlist").text


def test_one_persons_list_is_not_anothers(client):
    c, ids, _ = client
    _as(c, ids["a@example.com"]).post("/wishlist/1")
    body = _as(c, ids["b@example.com"]).get("/wishlist").text
    assert "Supply of switchgear" not in body


def test_signed_out_cannot_save_and_is_sent_to_sign_in(client):
    c, _ids, _ = client
    c.cookies.clear()
    assert c.post("/wishlist/1").status_code == 401
    r = c.get("/wishlist", follow_redirects=False)
    assert r.status_code == 303 and "/login" in r.headers["location"]


def test_saving_a_tender_that_does_not_exist_is_a_404(client):
    c, ids, _ = client
    assert _as(c, ids["a@example.com"]).post("/wishlist/9999").status_code == 404


def test_the_purge_still_works_when_a_saved_tender_closes(client):
    """The reason both keys cascade. A restricting key would make the nightly
    purge fail, and the whole ingest with it, over one bookmark."""
    c, ids, sf = client
    _as(c, ids["a@example.com"])
    c.post("/wishlist/2")                       # a tender whose deadline passed

    deleted = purge_expired(days=0, session_factory=sf)
    assert deleted == 1, "the expired tender must still be purgeable"

    db = sf()
    assert db.query(WishlistItem).count() == 0, "its saved row goes with it"
    db.close()
    # And the page still renders rather than 500ing on a dangling row.
    assert c.get("/wishlist").status_code == 200
