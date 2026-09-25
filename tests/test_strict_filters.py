"""Answers as boundaries rather than leanings.

Every answer has always been a preference: a tender that misses one still
appears, ranked lower. That is right for "we mostly do electrical" and wrong
for "we cannot work outside Madhya Pradesh". Marking an answer strict turns it
into a filter.
"""
from datetime import date, timedelta

import pytest

from fastapi.testclient import TestClient

from app.api import app
from app.auth import SESSION_COOKIE, make_session
# Imported here, at collection time, on purpose: tests/test_security.py reloads
# app.db, which rebinds get_db to a new function object. The routes captured the
# original at import, so a get_db imported later keys the dependency override to
# something the app never looks up -- the request then reaches the real (empty)
# engine and fails with "no such table".
from app.db import get_db
from app.matching import match_score, strict_set
from app.models import Company, Source, Tender, User

TODAY = date.today()
SOON = TODAY + timedelta(days=30)


@pytest.fixture(autouse=True)
def _clear_digest_cache():
    """match_digest memoises on (answers, corpus, day). Two tests with the same
    number of tenders and the same timestamps produce the same corpus stamp, so
    without this one test is served the other's results."""
    from app import matching, security

    matching._DIGESTS.clear()
    # And the rate limiter, which another module's tests deliberately fill to
    # its cap. Left there, the page requests below are throttled rather than
    # served, and the failure looks like a matching bug.
    security._BUCKETS.clear()
    yield
    matching._DIGESTS.clear()
    security._BUCKETS.clear()


class Profile:
    """Duck-typed like the Company row, which is what match_score accepts."""

    def __init__(self, **kw):
        self.sectors = kw.get("sectors", ["electrical_power"])
        self.keywords = kw.get("keywords", [])
        self.districts = kw.get("districts", [])
        self.states = kw.get("states", [])
        self.buyers = kw.get("buyers", [])
        self.exclude_keywords = []
        self.exclude_buyers = []
        self.min_lead_days = 0
        self.max_project_value = None
        self.strict = kw.get("strict", [])


def tender(title="Supply of switchgear and cable", org="Public Works Department"):
    return Tender(external_ref="x", title=title, organization=org,
                  department=org, source_url="u", deadline=SOON, status="open")


def test_location_is_a_preference_by_default(): 
    p = Profile(states=["Madhya Pradesh"])
    got = match_score(p, tender(), today=TODAY)
    assert got is not None, "a non-matching location only costs points"


def test_location_marked_strict_excludes_elsewhere():
    p = Profile(states=["Madhya Pradesh"], strict=["location"])
    assert match_score(p, tender(), today=TODAY) is None

    # ...and still admits a tender that does match.
    hit = tender(title="Supply of switchgear in Madhya Pradesh")
    assert match_score(p, hit, today=TODAY) is not None


def test_a_strict_answer_with_nothing_in_it_binds_nothing():
    """Marking location strict and naming no place must not hide everything --
    that reads as a broken site, not as a filter."""
    p = Profile(states=[], districts=[], strict=["location"])
    assert match_score(p, tender(), today=TODAY) is not None


def test_buyers_strict_excludes_other_buyers():
    p = Profile(buyers=["Damodar Valley"], strict=["buyers"])
    assert match_score(p, tender(org="Public Works Department"), today=TODAY) is None
    assert match_score(p, tender(org="Damodar Valley Corporation"), today=TODAY) is not None


def test_sectors_strict_requires_a_sector_not_just_a_keyword():
    """Without strict, a keyword hit alone is enough. With it, the sector must
    match too -- that is the difference the toggle is for."""
    p = Profile(sectors=["medical_pharma"], keywords=["switchgear"])
    assert match_score(p, tender(), today=TODAY) is not None, "keyword alone carries it"

    p.strict = ["sectors"]
    assert match_score(p, tender(), today=TODAY) is None


def test_district_or_state_satisfies_one_location_boundary():
    """Districts and states are one answer to a bidder. Naming both must mean
    either will do, not that both must match."""
    p = Profile(states=["Madhya Pradesh"], districts=["Bhopal"], strict=["location"])
    assert match_score(p, tender(title="Supply of switchgear and cable, Madhya Pradesh"), today=TODAY) is not None


def test_override_beats_the_saved_answer_in_both_directions():
    p = Profile(states=["Madhya Pradesh"], strict=["location"])
    # The page relaxes it...
    assert match_score(p, tender(), today=TODAY, strict=set()) is not None
    # ...and can tighten a profile that saved nothing.
    loose = Profile(states=["Madhya Pradesh"])
    assert match_score(loose, tender(), today=TODAY, strict={"location"}) is None


def test_strict_set_ignores_names_it_does_not_know():
    """The value arrives from a query string, so it is attacker-controlled."""
    assert strict_set(Profile(), {"location", "sql", "../etc"}) == {"location"}
    assert strict_set(Profile(strict=["nonsense"])) == set()


# ---- the page toggle -------------------------------------------------------

@pytest.fixture
def own_db():
    """A database this module owns outright.

    Deliberately not conftest's session_factory: these tests drive the real app
    through TestClient, and sharing the app-level dependency override with
    modules that build their own clients made the request reach a different
    engine entirely ("no such table: users") depending on run order.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.models import Base

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def matches_page(own_db):
    """A signed-in owner whose profile hides everything outside one state."""

    session_factory = own_db
    db = session_factory()
    user = User(email="owner@example.com", email_verified=True)
    src = Source(name="CPPP", base_url="https://eprocure.gov.in")
    db.add_all([user, src])
    db.flush()
    db.add(Company(user_id=user.id, name="Acme", sectors=["electrical_power"],
                   states=["Madhya Pradesh"], strict=["location"], min_lead_days=0))
    db.add_all([
        Tender(source_id=src.id, external_ref="in", source_url="u", deadline=SOON,
               status="open", title="Supply of switchgear and cable, Madhya Pradesh",
               organization="MP Power"),
        Tender(source_id=src.id, external_ref="out", source_url="u", deadline=SOON,
               status="open", title="Supply of switchgear and cable, Tamil Nadu",
               organization="TN Power"),
    ])
    db.commit()
    cid = db.query(Company).one().id
    uid = user.id
    db.close()

    app.dependency_overrides[get_db] = lambda: session_factory()
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, make_session(user))
    yield c, cid
    app.dependency_overrides.clear()


def test_strict_profile_hides_the_other_tender(matches_page):
    c, cid = matches_page
    body = c.get(f"/c/{cid}").text
    assert "Madhya Pradesh" in body
    assert "Tamil Nadu" not in body, "a strict location must hide the rest"
    # And the page offers the way out, with the count of what is hidden.
    assert "Everything, ranked" in body


def test_the_toggle_relaxes_without_editing_the_profile(matches_page, own_db):
    c, cid = matches_page
    body = c.get(f"/c/{cid}?filters=off").text
    assert "Madhya Pradesh" in body and "Tamil Nadu" in body

    # The saved answer is untouched: the toggle is a view, not an edit.
    db = own_db()
    assert db.query(Company).one().strict == ["location"]
    db.close()


def test_the_toggle_keeps_the_chosen_sort(matches_page):
    """Switching mode must not silently reorder the page underneath you."""
    c, cid = matches_page
    body = c.get(f"/c/{cid}?filters=off&sort=deadline&order=asc").text
    assert 'value="deadline" selected' in body or "value=\"deadline\" selected" in body


def test_the_toggle_is_offered_even_when_nothing_was_ticked(own_db):
    """A profile that ticked no boundary still gets the switch. Strict then
    means "every answer I did give", so the control is useful rather than a
    switch that does nothing -- and a reader who cannot see the choice cannot
    know it exists."""
    session_factory = own_db
    db = session_factory()
    user = User(email="b@example.com", email_verified=True)
    src = Source(name="CPPP", base_url="https://eprocure.gov.in")
    db.add_all([user, src])
    db.flush()
    db.add(Company(user_id=user.id, name="B", sectors=["electrical_power"],
                   states=["Madhya Pradesh"], strict=[], min_lead_days=0))
    db.add_all([
        Tender(source_id=src.id, external_ref="in", source_url="u", deadline=SOON,
               status="open", title="Supply of switchgear and cable, Madhya Pradesh"),
        Tender(source_id=src.id, external_ref="out", source_url="u", deadline=SOON,
               status="open", title="Supply of switchgear and cable, Tamil Nadu"),
    ])
    db.commit()
    cid, uid = db.query(Company).one().id, user.id
    db.close()

    app.dependency_overrides[get_db] = lambda: session_factory()
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, make_session(user))

    # Default: nothing was ticked, so nothing is hidden...
    body = c.get(f"/c/{cid}").text
    assert "Everything, ranked" in body, "the switch must be visible by default"
    assert "Madhya Pradesh" in body and "Tamil Nadu" in body

    # ...but asking for strict narrows to the answers that were given.
    tightened = c.get(f"/c/{cid}?filters=on").text
    assert "Madhya Pradesh" in tightened
    assert "Tamil Nadu" not in tightened
    app.dependency_overrides.clear()


def test_a_blank_answer_never_becomes_a_boundary(own_db):
    """Strict must only bind the answers that were filled in. A profile with no
    place named must not be narrowed to nowhere."""
    from app.matching import answered_fields

    class P:
        sectors = ["electrical_power"]
        keywords = []
        districts = []
        states = []
        buyers = []

    assert answered_fields(P()) == {"sectors"}


def test_the_way_back_survives_zero_matches(own_db):
    """Strict can narrow a profile to nothing. That is exactly when the reader
    needs the switch, so it must not live inside the "if there are rows" block."""
    session_factory = own_db
    db = session_factory()
    user = User(email="z@example.com", email_verified=True)
    src = Source(name="CPPP", base_url="https://eprocure.gov.in")
    db.add_all([user, src])
    db.flush()
    # Answers that no tender here can satisfy together.
    db.add(Company(user_id=user.id, name="Z", sectors=["electrical_power"],
                   buyers=["Nobody In Particular"], strict=["buyers"], min_lead_days=0))
    db.add(Tender(source_id=src.id, external_ref="t", source_url="u", deadline=SOON,
                  status="open", title="Supply of switchgear and cable",
                  organization="Someone Else"))
    db.commit()
    cid, uid = db.query(Company).one().id, user.id
    db.close()

    app.dependency_overrides[get_db] = lambda: session_factory()
    c = TestClient(app)
    c.cookies.set(SESSION_COOKIE, make_session(user))

    body = c.get(f"/c/{cid}").text
    assert "0 open tenders" in body, "the strict answer should hide the one tender"
    assert "Everything, ranked" in body, "the switch must still be on the page"

    # And it works from there.
    assert "Supply of switchgear" in c.get(f"/c/{cid}?filters=off").text
    app.dependency_overrides.clear()
