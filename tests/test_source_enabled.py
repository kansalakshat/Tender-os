"""Switching a source off, and what that must survive.

`enabled` exists separately from `active` because the connector rewrites
`active` on every run to record whether robots let it in. A source switched off
through that column would switch itself back on at the next attempt, which is
the bug this column exists to avoid.
"""
import pytest

from app.connectors.base import BaseConnector
from app.models import ConnectorRun, Source, Tender


class _Dummy(BaseConnector):
    source_name = "GeM"
    base_url = "https://bidplus.gem.gov.in"
    paths = ("/all-bids",)
    fetched = False

    def fetch_batch(self, since=None):
        type(self).fetched = True
        return iter(())

    def normalize(self, raw):
        raise NotImplementedError


def test_a_disabled_source_is_not_fetched(session_factory, monkeypatch):
    db = session_factory()
    db.add(Source(name="GeM", base_url="https://bidplus.gem.gov.in", enabled=False))
    db.commit()
    db.close()

    _Dummy.fetched = False
    c = _Dummy(session_factory=session_factory)
    # Robots must not even be consulted: asking a portal we have switched off
    # for permission is a request that should not happen.
    monkeypatch.setattr(
        c, "check_robots_allowed",
        lambda *a, **k: pytest.fail("robots.txt was checked for a disabled source"),
    )
    summary = c.run()
    assert summary.status == "disabled"
    assert _Dummy.fetched is False


def test_enabled_defaults_to_on_for_a_new_source(session_factory):
    db = session_factory()
    src = Source(name="New", base_url="https://example.invalid")
    db.add(src)
    db.commit()
    assert src.enabled is True
    db.close()


def test_listing_total_is_parsed_from_the_pager_line():
    from app.connectors.gem import GeMConnector

    c = GeMConnector.__new__(GeMConnector)
    assert c.record_listing_total("Showing 11 - 20 records of 45,720") == 45720
    assert c.record_listing_total("Showing 1 - 10 records of 43719") == 43719
    # No total in the text is "unknown", never zero: zero would read as
    # "nothing left to collect".
    assert c.record_listing_total("loading...") is None
    assert c.record_listing_total("") is None
