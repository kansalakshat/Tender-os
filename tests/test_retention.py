from datetime import date, timedelta

import pytest

from app.models import Source, Tender
from app import retention
from app.retention import cutoff_date, purge_expired

TODAY = date(2026, 9, 6)


@pytest.fixture
def seeded(session_factory):
    """Rows either side of every boundary the purge has to respect."""
    with session_factory() as db:
        src = Source(name="MP", base_url="https://mptenders.gov.in")
        db.add(src)
        db.flush()
        rows = {
            "long_gone": TODAY - timedelta(days=40),
            "week_old": TODAY - timedelta(days=7),
            "yesterday": TODAY - timedelta(days=1),
            "today": TODAY,
            "future": TODAY + timedelta(days=30),
        }
        for ref, deadline in rows.items():
            db.add(Tender(
                source_id=src.id, external_ref=ref, title=ref, deadline=deadline,
                status="open", source_url="https://mptenders.gov.in/x",
            ))
        db.add(Tender(
            source_id=src.id, external_ref="undated", title="undated", deadline=None,
            status="open", source_url="https://mptenders.gov.in/x",
        ))
        db.commit()
    return session_factory


def refs(session_factory) -> set[str]:
    with session_factory() as db:
        return {t.external_ref for t in db.query(Tender).all()}


def test_cutoff_is_the_deadline_plus_the_grace_period():
    assert cutoff_date(0, TODAY) == TODAY
    assert cutoff_date(7, TODAY) == date(2026, 8, 30)


def test_zero_grace_deletes_everything_already_closed(seeded):
    assert purge_expired(days=0, session_factory=seeded, today=TODAY) == 3
    # A tender closing *today* is still biddable, so it survives.
    assert refs(seeded) == {"today", "future", "undated"}


def test_grace_period_keeps_recently_closed_rows(seeded):
    assert purge_expired(days=14, session_factory=seeded, today=TODAY) == 1
    assert "long_gone" not in refs(seeded)
    assert "week_old" in refs(seeded)


def test_undated_tenders_are_never_purged(seeded):
    """Unknown is not expired -- deleting these would lose rows we cannot judge."""
    purge_expired(days=0, session_factory=seeded, today=TODAY)
    assert "undated" in refs(seeded)


def test_unset_retention_deletes_nothing(seeded, monkeypatch):
    """The default. Closed tenders are hidden by the API, not destroyed."""
    monkeypatch.setattr(retention, "DEFAULT_RETENTION_DAYS", None)
    assert retention.purge_expired(session_factory=seeded, today=TODAY) == 0
    assert refs(seeded) == {
        "long_gone", "week_old", "yesterday", "today", "future", "undated",
    }


def test_dry_run_counts_without_deleting(seeded):
    before = refs(seeded)
    assert purge_expired(days=0, dry_run=True, session_factory=seeded, today=TODAY) == 3
    assert refs(seeded) == before


def test_purging_nothing_is_not_an_error(seeded):
    assert purge_expired(days=999, session_factory=seeded, today=TODAY) == 0
    assert len(refs(seeded)) == 6


def test_duplicate_links_to_purged_rows_are_cleared_not_orphaned(session_factory):
    """A surviving row pointing at a deleted one would trip the foreign key."""
    with session_factory() as db:
        src = Source(name="MP", base_url="https://mptenders.gov.in")
        db.add(src)
        db.flush()
        old = Tender(
            source_id=src.id, external_ref="old", title="old",
            deadline=TODAY - timedelta(days=10), status="open",
            source_url="https://mptenders.gov.in/a",
        )
        db.add(old)
        db.flush()
        db.add(Tender(
            source_id=src.id, external_ref="live", title="live",
            deadline=TODAY + timedelta(days=10), status="open",
            source_url="https://mptenders.gov.in/b", duplicate_of=old.id,
        ))
        db.commit()

    assert purge_expired(days=0, session_factory=session_factory, today=TODAY) == 1
    with session_factory() as db:
        survivor = db.query(Tender).filter(Tender.external_ref == "live").one()
        assert survivor.duplicate_of is None


def test_purge_cli_refuses_to_guess_a_blast_radius(capsys, monkeypatch):
    """`tenders purge` with no --days and no RETENTION_DAYS used to crash on
    timedelta(days=None). It must decline, not delete and not blow up.

    The default is patched rather than read from the environment: otherwise this
    test passes or fails depending on whether the developer's .env happens to set
    RETENTION_DAYS, which is not what it is trying to prove.
    """
    from app import cli
    monkeypatch.setattr(cli, "DEFAULT_RETENTION_DAYS", None)
    assert cli.main(["purge", "--dry-run"]) == 2
    assert "RETENTION_DAYS is unset" in capsys.readouterr().err
