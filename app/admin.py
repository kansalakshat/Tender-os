"""Who is an operator, and the numbers only an operator sees.

Membership is by email, from ADMIN_EMAILS, and it is the only rule -- there is
no admin column on users, so nobody becomes an operator by editing a row.

Everything here is about the shape of the system rather than any one tender, so
it is deliberately unreachable for ordinary visitors: /admin answers 404, not
403, for anyone who is not on the list. A 403 confirms the page exists, which
tells a stranger there is something worth finding.
"""
from __future__ import annotations

import os
import time
from datetime import date, timedelta

from sqlalchemy import Text, case, func, select
from sqlalchemy.orm import Session

from .models import Company, ConnectorRun, Source, Tender, User, utcnow

# The owner's address is the default so a fresh deployment is not locked out of
# its own dashboard. Override with a comma-separated ADMIN_EMAILS.
DEFAULT_ADMINS = "nirmaanos35@gmail.com"


def admin_emails() -> set[str]:
    raw = os.getenv("ADMIN_EMAILS", DEFAULT_ADMINS)
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def is_admin(user: User | None) -> bool:
    return bool(user and (user.email or "").strip().lower() in admin_emails())


def daily_intake(db: Session, days: int = 7) -> list[dict]:
    """New tenders per day, oldest first, with empty days present as zero.

    Filled in Python rather than with a date series: a day that fetched nothing
    is the interesting one, and a query that simply omits it looks identical to
    a day that has not happened yet.
    """
    # UTC, because first_seen_at is stored as naive UTC. Using the local date
    # made today's bar read zero until 05:30 IST and clipped the oldest day.
    start = utcnow().date() - timedelta(days=days - 1)
    rows = db.execute(
        select(func.date(Tender.first_seen_at), func.count())
        .where(func.date(Tender.first_seen_at) >= start)
        .group_by(func.date(Tender.first_seen_at))
    ).all()
    # SQLite hands back a string here, Postgres a date; normalise before lookup.
    counts = {str(d): n for d, n in rows}
    return [
        {"day": (d := start + timedelta(days=i)).isoformat(),
         "label": d.strftime("%a %d %b"),
         "count": counts.get(d.isoformat(), 0)}
        for i in range(days)
    ]


def by_source(db: Session) -> list[dict]:
    """Per portal: what we hold, what is still open, and when it last answered."""
    today = date.today()
    open_case = case(
        # The join is an outer one, so a source with no tenders at all arrives
        # as a row of NULLs -- and "deadline IS NULL" would count that phantom
        # as an open tender. Rule it out before asking anything about dates.
        (Tender.id.is_(None), 0),
        (Tender.deadline.is_(None), 1),
        (Tender.deadline >= today, 1),
        else_=0,
    )
    rows = db.execute(
        select(
            Source.id, Source.name,
            func.count(Tender.id),
            func.coalesce(func.sum(open_case), 0),
            func.max(Tender.first_seen_at),
        )
        .join(Tender, Tender.source_id == Source.id, isouter=True)
        .group_by(Source.id, Source.name)
        .order_by(func.count(Tender.id).desc())
    ).all()
    return [
        {"id": sid, "name": name, "total": total, "open": int(open_), "last_seen": last}
        for sid, name, total, open_, last in rows
    ]


def recent_runs(db: Session, limit: int = 12) -> list[ConnectorRun]:
    return list(db.execute(
        select(ConnectorRun).order_by(ConnectorRun.started_at.desc()).limit(limit)
    ).scalars())


def failing_sources(db: Session, days: int = 7) -> list[dict]:
    """Portals whose most recent run did not succeed.

    The *latest* run per source, not a count of failures: a source that failed
    ten times and then recovered is healthy, and one that succeeded ten times
    and just broke is not. Only the last answer tells you which.
    """
    since = utcnow() - timedelta(days=days)
    latest = (
        select(ConnectorRun.source_name, func.max(ConnectorRun.started_at).label("at"))
        .where(ConnectorRun.started_at >= since)
        .group_by(ConnectorRun.source_name)
        .subquery()
    )
    rows = db.execute(
        select(ConnectorRun)
        .join(latest, (ConnectorRun.source_name == latest.c.source_name)
              & (ConnectorRun.started_at == latest.c.at))
        .where(ConnectorRun.status != "ok")
        .order_by(ConnectorRun.started_at.desc())
    ).scalars()
    return [
        {"source": r.source_name, "status": r.status, "at": r.started_at,
         "message": (r.message or "")[:200], "errors": r.errors}
        for r in rows
    ]


def totals(db: Session) -> dict:
    today = date.today()
    one = lambda stmt: db.execute(stmt).scalar_one()          # noqa: E731
    open_filter = (Tender.deadline.is_(None)) | (Tender.deadline >= today)
    return {
        "tenders": one(select(func.count()).select_from(Tender)),
        "open": one(select(func.count()).select_from(Tender).where(
            Tender.duplicate_of.is_(None), open_filter)),
        "duplicates": one(select(func.count()).select_from(Tender).where(
            Tender.duplicate_of.is_not(None))),
        "users": one(select(func.count()).select_from(User)),
        "companies": one(select(func.count()).select_from(Company)),
        # Enrichment is the pass that fills EMD, value and the document links, so
        # how far it has got is the one number that explains a thin-looking site.
        "with_documents": one(select(func.count()).select_from(Tender).where(
            Tender.document_url.is_not(None))),
        # Matched as text rather than with a JSON operator: the same query has
        # to run against Postgres in production and SQLite under test, and the
        # marker is a fixed key that cannot collide with scraped content.
        "enriched": one(select(func.count()).select_from(Tender).where(
            Tender.raw_payload.is_not(None),
            func.cast(Tender.raw_payload, Text).like('%"_enriched"%'),
        )),
        "valued": one(select(func.count()).select_from(Tender).where(
            Tender.estimated_value.is_not(None))),
    }


# Measured at ~0.9s warm against the live database. Polled every 10s by one
# dashboard that is fine; by three open tabs it is not, and Neon bills compute
# and scales to zero when idle. Cached so the cost is per-interval, not
# per-viewer. Process-local, which is all it needs to be: it is a counter, and
# a reader that gets a nine-second-old number loses nothing.
_CACHE_SECONDS = 9.0
_cache: tuple[float, dict] | None = None


def live_counts(db: Session, now=None) -> dict:
    """The few numbers worth polling every few seconds.

    Separate from totals(): that one runs eight counts over the whole table and
    is fine for a page load, but polling it would have the dashboard scanning
    tens of thousands of rows every tick. This is two counts and a group-by.

    It counts the table, not this process's job, on purpose -- a backfill run
    from a terminal moves these numbers too, and an operator watching the
    dashboard wants to see the corpus growing, not only what they clicked.
    """
    global _cache
    clock = now or time.monotonic
    if _cache is not None and clock() - _cache[0] < _CACHE_SECONDS:
        return _cache[1]

    today = date.today()
    open_filter = (Tender.deadline.is_(None)) | (Tender.deadline >= today)
    total = db.execute(select(func.count()).select_from(Tender)).scalar_one()
    open_ = db.execute(
        select(func.count()).select_from(Tender)
        .where(Tender.duplicate_of.is_(None), open_filter)
    ).scalar_one()
    per_source = db.execute(
        select(Source.name, func.count(Tender.id))
        .join(Tender, Tender.source_id == Source.id)
        .group_by(Source.name)
        .order_by(func.count(Tender.id).desc())
    ).all()
    result = {
        "total": total,
        "open": open_,
        "sources": [{"name": n, "total": c} for n, c in per_source],
        "at": utcnow().isoformat(),
    }
    _cache = (clock(), result)
    return result
