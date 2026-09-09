"""Permanent deletion of tenders whose deadline has passed. Off by default.

Nothing calls this unless RETENTION_DAYS is set. There is deliberately no
backup: it was asked for and then asked to be removed, and a half-kept one
(Vercel writes to /tmp, which dies with the invocation) is worse than none --
it reads like a safety net that is not there. Deletion here is final.

Most expired rows never reach this function: connectors skip them at ingest
(app/connectors/base.py). This stays as the catch for rows that expire while
sitting in the table.

This is destructive and irreversible. A closed tender cannot be re-fetched: CPPP
and the GePNIC portals drop a tender off their listing once it closes, so a row
deleted here is gone for good, along with any history of that procurement.

`RETENTION_DAYS` is the grace period, in days after the deadline, and it exists
for a specific reason: buyers extend deadlines by corrigendum, and a corrigendum
can land after the original date has passed. In one ordinary six-hour ingest, 89
of 386 fetched rows were updates to tenders we already had. A grace period of 0
means a tender is deleted the day after it closes, and if a corrigendum arrives
later it comes back as a brand-new row with a new `first_seen_at`.

Rows with no deadline are never touched -- "unknown" is not "expired".
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

from sqlalchemy import and_, delete, func, select, update

from .db import SessionLocal
from .models import Tender

log = logging.getLogger(__name__)

# Unset (the default) means never delete anything. A closed tender cannot be
# re-fetched, and the archive is worth more than the disk it costs; the API hides
# past-deadline rows at query time instead (app/api.py, `include_closed`).
# Set RETENTION_DAYS to a number of days after the deadline to re-enable purging.
_RETENTION_ENV = os.getenv("RETENTION_DAYS", "").strip()
DEFAULT_RETENTION_DAYS = int(_RETENTION_ENV) if _RETENTION_ENV else None

def cutoff_date(days: int, today: date | None = None) -> date:
    """Tenders with a deadline strictly before this date are purged."""
    return (today or date.today()) - timedelta(days=days)


def purge_expired(
    days: int | None = None,
    dry_run: bool = False,
    session_factory=SessionLocal,
    today: date | None = None,
) -> int:
    """Delete expired tenders. Returns how many rows went (or would go).

    `dry_run=True` counts without deleting, which is the only way to see the blast
    radius before an irreversible operation.
    """
    days = DEFAULT_RETENTION_DAYS if days is None else days
    if days is None:
        log.info("purge: RETENTION_DAYS is unset, keeping every tender")
        return 0
    cutoff = cutoff_date(days, today)
    condition = and_(Tender.deadline.is_not(None), Tender.deadline < cutoff)

    db = session_factory()
    try:
        doomed = db.execute(
            select(func.count()).select_from(Tender).where(condition)
        ).scalar_one()
        if dry_run:
            log.info("purge (dry run): %d tenders with a deadline before %s", doomed, cutoff)
            return doomed
        if not doomed:
            return 0

        # A surviving row may be linked to one about to be deleted. Clear the
        # pointer first, or the delete trips the duplicate_of foreign key.
        db.execute(
            update(Tender)
            .where(Tender.duplicate_of.in_(select(Tender.id).where(condition)))
            .values(duplicate_of=None)
            .execution_options(synchronize_session=False)
        )
        db.execute(delete(Tender).where(condition).execution_options(
            synchronize_session=False
        ))
        db.commit()
        # Logged at warning: this is data leaving the system permanently, and the
        # operator should be able to find out afterwards exactly what went.
        log.warning(
            "purged %d tenders with a deadline before %s (retention %d day(s))",
            doomed, cutoff, days,
        )
        return doomed
    finally:
        db.close()
