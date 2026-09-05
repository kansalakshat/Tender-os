"""Cross-source duplicate linking.

The same tender legitimately appears on more than one source (CPPP mirrors a
GeM-integrated listing, a state dataset repeats a central one). We keep both rows
-- each is a faithful record of what its source published -- and link them with
`duplicate_of` after the fact, rather than dropping data at ingest time where a
wrong guess is unrecoverable.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from decimal import Decimal
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import SessionLocal
from .models import Tender

log = logging.getLogger(__name__)

TITLE_THRESHOLD = 0.88
ORG_THRESHOLD = 0.80
VALUE_TOLERANCE = Decimal("0.02")  # 2%
DEADLINE_SLACK = timedelta(days=3)

_NOISE = re.compile(
    r"\b(tender|notice|nit|e-?tender|for|the|of|and|work|works|supply|"
    r"procurement|invitation|bid|bids|corrigendum|ltd|limited)\b",
    re.I,
)


def _canon(text: str | None) -> str:
    text = _NOISE.sub(" ", (text or "").lower())
    return " ".join(re.sub(r"[^a-z0-9 ]+", " ", text).split())


def similarity(a: str | None, b: str | None) -> float:
    ca, cb = _canon(a), _canon(b)
    if not ca or not cb:
        return 0.0
    return SequenceMatcher(None, ca, cb).ratio()


def values_match(a: Decimal | None, b: Decimal | None) -> bool:
    """Unknown values must not block a match -- most listings omit the value."""
    if a is None or b is None:
        return True
    if a == 0 or b == 0:
        return a == b
    return abs(a - b) / max(a, b) <= VALUE_TOLERANCE


def deadlines_match(a: date | None, b: date | None) -> bool:
    if a is None or b is None:
        return True
    return abs(a - b) <= DEADLINE_SLACK


def is_duplicate(a: Tender, b: Tender) -> bool:
    return (
        similarity(a.title, b.title) >= TITLE_THRESHOLD
        and similarity(a.organization, b.organization) >= ORG_THRESHOLD
        and values_match(a.estimated_value, b.estimated_value)
        and deadlines_match(a.deadline, b.deadline)
    )


def link_duplicates(db: Session | None = None, window_days: int = 120) -> int:
    """Point each duplicate at the oldest matching row. Returns links created.

    ponytail: O(n^2) within a deadline bucket. Buckets keep that tolerable at the
    tens-of-thousands scale we are at; if it stops being tolerable, block on a
    trigram index or a title MinHash instead of widening the loop.
    """
    own_session = db is None
    db = db or SessionLocal()
    linked = 0
    try:
        cutoff = date.today() - timedelta(days=window_days)
        rows = list(
            db.execute(
                select(Tender)
                .where(Tender.duplicate_of.is_(None))
                .where((Tender.deadline.is_(None)) | (Tender.deadline >= cutoff))
                .order_by(Tender.id)
            ).scalars()
        )

        buckets: dict[object, list[Tender]] = {}
        for row in rows:
            buckets.setdefault(row.deadline, []).append(row)

        # A row with no deadline can pair with any bucket, so it is always a candidate.
        undated = buckets.get(None, [])
        for row in rows:
            candidates = buckets.get(row.deadline, [])
            if row.deadline is not None:
                candidates = candidates + undated
            for other in candidates:
                # Link the newer row to the older one; same-source repeats are
                # already handled by the (source_id, external_ref) unique key.
                if other.id >= row.id or other.source_id == row.source_id:
                    continue
                if is_duplicate(row, other):
                    row.duplicate_of = other.duplicate_of or other.id
                    linked += 1
                    break
        db.commit()
        log.info("dedup: linked %d duplicate tenders", linked)
    finally:
        if own_session:
            db.close()
    return linked
