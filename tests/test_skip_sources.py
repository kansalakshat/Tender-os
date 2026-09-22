"""Skipping sources the host running the pass cannot reach.

GeM refuses connections from datacenter addresses, so the hosted runner skips
both its listing crawl and its bid documents. A skip that silently did nothing
would look identical to success while two thirds of the corpus went stale, so
it is worth a test that the filter drops the right row and only that row.
"""
import pytest

from app import enrich
from app.enrich import needs_enrichment
from app.models import Source, Tender


@pytest.fixture
def two_sources(session_factory, monkeypatch):
    """One GeM row and one CPPP row, both with a document waiting to be read."""
    monkeypatch.setattr(enrich, "SessionLocal", session_factory)
    db = session_factory()
    gem = Source(name="GeM", base_url="https://bidplus.gem.gov.in")
    cppp = Source(name="CPPP", base_url="https://eprocure.gov.in")
    db.add_all([gem, cppp])
    db.flush()
    db.add_all([
        Tender(external_ref="g1", title="gem one", source_url="u",
               source_id=gem.id, document_url="https://bidplus.gem.gov.in/d/1"),
        Tender(external_ref="c1", title="cppp one", source_url="u",
               source_id=cppp.id, document_url="https://eprocure.gov.in/d/1"),
    ])
    db.commit()
    db.close()
    return session_factory


def test_skip_sources_drops_that_source_and_keeps_the_rest(two_sources):
    assert len(needs_enrichment(limit=10)) == 2, "both are enrichable to begin with"

    kept = needs_enrichment(limit=10, skip_sources={"GeM"})
    assert len(kept) == 1, "GeM's row should have been dropped"

    # The surviving row is CPPP's, not merely a shorter list.
    db = two_sources()
    assert db.get(Tender, kept[0]).external_ref == "c1"
    db.close()


def test_empty_skip_means_skip_nothing_not_match_nothing(two_sources):
    assert len(needs_enrichment(limit=10, skip_sources=set())) == 2
    assert len(needs_enrichment(limit=10, skip_sources=None)) == 2


def test_an_unmatched_name_drops_nothing(two_sources):
    assert len(needs_enrichment(limit=10, skip_sources={"Nonesuch"})) == 2


def test_env_var_parsing_ignores_blanks_and_spacing():
    """SKIP_CONNECTORS="GeM, ,CPPP" is three fields, two of them real. Mirrors
    the parsing in run_prod_worker.run_once."""
    assert {n.strip() for n in "GeM, ,CPPP".split(",") if n.strip()} == {"GeM", "CPPP"}
    assert {n.strip() for n in "".split(",") if n.strip()} == set()
