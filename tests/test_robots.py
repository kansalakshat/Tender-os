"""The compliance rules are the product. These tests are what keep them true."""
import httpx
import pytest

from app.compliance import BlockedSourceError, assert_not_blocked, host_is_blocked, user_agent
from app.connectors import REGISTRY
from app.connectors.base import BaseConnector
from app.schemas import TenderRecord


def transport(handler):
    return httpx.Client(
        transport=httpx.MockTransport(handler),
        headers={"User-Agent": user_agent()},
        follow_redirects=True,
    )


class DummyConnector(BaseConnector):
    source_name = "dummy"
    base_url = "https://example.gov.in"
    license = "test"
    rate_limit_seconds = 0.0
    paths = ("/tenders",)

    def fetch_batch(self, since):
        yield {"id": "T1"}

    def normalize(self, raw):
        return TenderRecord(
            external_ref=raw["id"], title="Test tender",
            source_url="https://example.gov.in/tenders/T1",
        )


def robots_responder(body: str, status: int = 200):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(status, text=body)
        return httpx.Response(200, text="ok")
    return handler


# ---- rule #1: GeM is permanently off-limits ----

@pytest.mark.parametrize(
    "url",
    [
        "https://gem.gov.in/",
        "https://bidplus.gem.gov.in/bidlists",
        "http://GEM.GOV.IN/x",
        "https://mkp.gem.gov.in/api",
    ],
)
def test_gem_hosts_are_blocked(url):
    assert host_is_blocked(url)
    with pytest.raises(BlockedSourceError):
        assert_not_blocked(url)


def test_lookalike_hosts_are_not_over_blocked():
    # We block gem.gov.in and its subdomains, not any string containing "gem".
    assert not host_is_blocked("https://eprocure.gov.in/x")
    assert not host_is_blocked("https://gem.gov.in.evil.example.com/x")


def test_declaring_a_gem_connector_raises_at_import_time():
    with pytest.raises(BlockedSourceError):
        class GemConnector(BaseConnector):  # noqa: F811
            source_name = "gem"
            base_url = "https://bidplus.gem.gov.in"

            def fetch_batch(self, since):
                yield {}

            def normalize(self, raw):
                raise NotImplementedError


def test_no_gem_connector_is_registered():
    assert not any(host_is_blocked(c.base_url) for c in REGISTRY.values())


def test_requests_to_blocked_hosts_are_refused():
    conn = DummyConnector(client=transport(robots_responder("")))
    with pytest.raises(BlockedSourceError):
        conn.get("https://bidplus.gem.gov.in/bidlists")


def test_redirect_to_blocked_host_is_refused():
    """A source cannot launder a GeM request through a redirect."""
    def handler(request):
        if request.url.host == "example.gov.in":
            return httpx.Response(302, headers={"Location": "https://gem.gov.in/data"})
        return httpx.Response(200, text="should never get here")

    from app.connectors.base import _block_gem
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        follow_redirects=True,
        event_hooks={"request": [_block_gem]},
    )
    conn = DummyConnector(client=client)
    with pytest.raises(BlockedSourceError):
        conn.get("https://example.gov.in/anything")


# ---- rule #2: robots.txt is checked before the first request ----

def test_allows_when_robots_permits():
    conn = DummyConnector(client=transport(robots_responder("User-agent: *\nAllow: /\n")))
    assert conn.check_robots_allowed(["/tenders"]) is True


def test_refuses_when_robots_disallows_our_path():
    body = "User-agent: *\nDisallow: /tenders\n"
    conn = DummyConnector(client=transport(robots_responder(body)))
    assert conn.check_robots_allowed(["/tenders"]) is False


def test_refuses_on_blanket_disallow():
    conn = DummyConnector(client=transport(robots_responder("User-agent: *\nDisallow: /\n")))
    assert conn.check_robots_allowed(["/tenders"]) is False


def test_missing_robots_means_allowed():
    # RFC 9309: robots.txt unavailable (404) => crawling unrestricted. This is the
    # real-world case for eprocure.gov.in.
    conn = DummyConnector(client=transport(robots_responder("nope", status=404)))
    assert conn.check_robots_allowed(["/tenders"]) is True


@pytest.mark.parametrize("status", [401, 403, 500, 503])
def test_fails_closed_when_robots_cannot_be_read(status):
    conn = DummyConnector(client=transport(robots_responder("denied", status=status)))
    assert conn.check_robots_allowed(["/tenders"]) is False


def test_fails_closed_when_robots_unreachable():
    def handler(request):
        raise httpx.ConnectError("network down", request=request)

    conn = DummyConnector(client=transport(handler))
    assert conn.check_robots_allowed(["/tenders"]) is False


# ---- run() refuses rather than fetching ----

def test_run_refuses_and_writes_nothing_when_disallowed(session_factory, monkeypatch):
    monkeypatch.setattr(
        "app.connectors.base.load_approved_sources",
        lambda: {"example.gov.in": {"domain": "example.gov.in", "license": "test"}},
    )
    body = "User-agent: *\nDisallow: /\n"
    conn = DummyConnector(session_factory=session_factory, client=transport(robots_responder(body)))
    summary = conn.run()

    assert summary.status == "refused"
    assert summary.fetched == 0

    from app.models import ConnectorRun, Source, Tender
    with session_factory() as db:
        assert db.query(Tender).count() == 0
        assert db.query(Source).one().robots_txt_allowed is False
        assert db.query(Source).one().active is False
        assert db.query(ConnectorRun).one().status == "refused"


def test_run_ingests_when_allowed(session_factory, monkeypatch):
    monkeypatch.setattr(
        "app.connectors.base.load_approved_sources",
        lambda: {"example.gov.in": {"domain": "example.gov.in", "license": "test"}},
    )
    conn = DummyConnector(
        session_factory=session_factory, client=transport(robots_responder("", status=404))
    )
    summary = conn.run()

    assert (summary.status, summary.fetched, summary.new) == ("ok", 1, 1)

    from app.models import Tender
    with session_factory() as db:
        assert db.query(Tender).one().external_ref == "T1"

    # A second run updates rather than duplicating.
    conn2 = DummyConnector(
        session_factory=session_factory, client=transport(robots_responder("", status=404))
    )
    second = conn2.run()
    assert (second.new, second.updated) == (0, 1)


def test_run_refuses_domains_missing_from_the_allowlist(session_factory, monkeypatch):
    monkeypatch.setattr("app.connectors.base.load_approved_sources", lambda: {})
    conn = DummyConnector(
        session_factory=session_factory, client=transport(robots_responder("", status=404))
    )
    summary = conn.run()
    assert summary.status == "refused"
    assert "approved_sources.yaml" in summary.message


# ---- rule #5: we identify honestly ----

def test_user_agent_names_the_tool_and_a_contact():
    ua = user_agent()
    assert "IndianTenderAggregator" in ua
    assert "@" in ua
    for browser_lie in ("Mozilla", "Chrome", "Safari", "Windows NT"):
        assert browser_lie not in ua


def test_placeholder_contact_email_is_rejected(monkeypatch):
    monkeypatch.setenv("CONTACT_EMAIL", "you@example.com")
    with pytest.raises(RuntimeError):
        user_agent()
    monkeypatch.setenv("CONTACT_EMAIL", "")
    with pytest.raises(RuntimeError):
        user_agent()


# ---- the real sources, as verified during the build ----

def test_tier1_connectors_are_in_the_allowlist():
    from urllib.parse import urlsplit

    from app.connectors.base import load_approved_sources

    approved = load_approved_sources()
    for connector in REGISTRY.values():
        # Read base_url off the class; instantiating would build a real HTTP client.
        host = urlsplit(connector.base_url).hostname.lower()
        assert host in approved, f"{connector.source_name} not approved"


def test_cppp_never_touches_the_captcha_gated_pages():
    """The search form and FrontEndLatestActiveTenders are CAPTCHA-gated (rule #3)."""
    from app.connectors.cppp import CPPPConnector
    conn = object.__new__(CPPPConnector)
    urls = [conn.page_url(1), conn.page_url(2), conn.page_url(7)]
    for url in urls:
        assert "FrontEndLatestActiveTenders" not in url
        assert "captcha" not in url.lower()
        assert url.startswith("https://eprocure.gov.in/cppp/latestactivetendersnew/cpppdata")
