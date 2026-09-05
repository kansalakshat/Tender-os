"""The robots.txt result is cached for a week -- but only when it was a yes."""
from datetime import timedelta

import httpx
import pytest

from app.compliance import user_agent
from app.models import Source, utcnow
from tests.test_robots import DummyConnector, robots_responder


def client_counting(handler, counter):
    def counted(request):
        counter.append(request.url.path)
        return handler(request)

    return httpx.Client(
        transport=httpx.MockTransport(counted),
        headers={"User-Agent": user_agent()},
        follow_redirects=True,
    )


@pytest.fixture(autouse=True)
def _approved(monkeypatch):
    monkeypatch.setattr(
        "app.connectors.base.load_approved_sources",
        lambda: {"example.gov.in": {"domain": "example.gov.in"}},
    )


def test_a_positive_result_is_reused_within_the_week(session_factory):
    calls = []
    conn = DummyConnector(
        session_factory=session_factory,
        client=client_counting(robots_responder("", status=404), calls),
    )
    with session_factory() as db:
        src = conn.ensure_source_row(db)
        assert conn.robots_allowed_cached(db, src) is True
        db.commit()
        assert calls.count("/robots.txt") == 1

        assert conn.robots_allowed_cached(db, src) is True
        assert calls.count("/robots.txt") == 1, "cached, so no second fetch"


def test_the_check_repeats_after_a_week(session_factory):
    calls = []
    conn = DummyConnector(
        session_factory=session_factory,
        client=client_counting(robots_responder("", status=404), calls),
    )
    with session_factory() as db:
        src = conn.ensure_source_row(db)
        conn.robots_allowed_cached(db, src)
        src.robots_txt_checked_at = utcnow() - timedelta(days=8)
        db.flush()

        conn.robots_allowed_cached(db, src)
        assert calls.count("/robots.txt") == 2


def test_a_refusal_is_never_cached(session_factory):
    """A dropped connection is indistinguishable from a Disallow, so we retry."""
    calls = []

    def unreachable(request):
        raise httpx.ConnectError("network down", request=request)

    conn = DummyConnector(
        session_factory=session_factory, client=client_counting(unreachable, calls)
    )
    conn.max_retries = 1  # keep the test quick
    with session_factory() as db:
        src = conn.ensure_source_row(db)
        assert conn.robots_allowed_cached(db, src) is False
        first = len(calls)
        assert conn.robots_allowed_cached(db, src) is False
        assert len(calls) > first, "a refusal must be re-checked, not cached for a week"


def test_a_source_recovers_after_a_transient_failure(session_factory):
    """The bug this guards: one bad network moment muting a source for a week."""
    state = {"up": False}
    ok = robots_responder("", status=404)

    def flaky(request):
        if not state["up"]:
            raise httpx.ConnectError("network down", request=request)
        return ok(request)

    conn = DummyConnector(
        session_factory=session_factory,
        client=httpx.Client(transport=httpx.MockTransport(flaky)),
    )
    conn.max_retries = 1
    summary = conn.run()
    assert summary.status == "refused"

    state["up"] = True
    recovered = DummyConnector(
        session_factory=session_factory,
        client=httpx.Client(transport=httpx.MockTransport(flaky)),
    )
    summary = recovered.run()
    assert summary.status == "ok", "source must recover on the very next run"
    assert summary.new == 1

    with session_factory() as db:
        src = db.query(Source).one()
        assert src.robots_txt_allowed is True
        assert src.active is True


# ---- a refusal must say WHY: unreachable and disallowed are different problems ----

def test_an_unreachable_source_does_not_report_itself_as_disallowed(session_factory):
    def unreachable(request):
        raise httpx.ConnectError("network down", request=request)

    conn = DummyConnector(
        session_factory=session_factory, client=httpx.Client(transport=httpx.MockTransport(unreachable))
    )
    conn.max_retries = 1
    summary = conn.run()
    assert summary.status == "refused"
    assert "could not reach" in summary.message
    assert "disallows" not in summary.message


def test_a_real_disallow_says_so(session_factory):
    body = "User-agent: *\nDisallow: /\n"
    conn = DummyConnector(
        session_factory=session_factory, client=client_counting(robots_responder(body), [])
    )
    summary = conn.run()
    assert summary.status == "refused"
    assert "disallows" in summary.message
