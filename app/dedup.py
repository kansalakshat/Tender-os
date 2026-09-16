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


def survivor(a: Tender, b: Tender) -> tuple[Tender, Tender]:
    """(keep, hide) for two rows that are the same notice on different portals.

    A row whose document can actually be downloaded wins, whatever its age. The
    same tender appears on CPPP and on GeM; CPPP gates its detail page behind a
    CAPTCHA and so carries no document, while GeM serves the bid PDF to an
    ordinary GET. Keeping the older row (which is usually CPPP, ingested first)
    would hide the only copy a user can actually open.

    Age breaks the tie, so the result is stable when neither has a document.
    """
    if bool(a.document_url) != bool(b.document_url):
        return (a, b) if a.document_url else (b, a)
    return (a, b) if a.id < b.id else (b, a)


def _root(row: Tender, by_id: dict[int, Tender]) -> int:
    """Follow a duplicate_of chain to the surviving row, so nothing points at a
    row that is itself hidden. Guards against a cycle rather than trusting one
    cannot form."""
    seen: set[int] = set()
    while row.duplicate_of is not None and row.duplicate_of not in seen:
        seen.add(row.duplicate_of)
        nxt = by_id.get(row.duplicate_of)
        if nxt is None:
            break
        row = nxt
    return row.id


# Titles at or above TITLE_THRESHOLD necessarily share words, so a row only has
# to be compared with rows that share one. Without this the scan is every pair in
# a deadline bucket: at 26,330 rows in the window that is ~65 million
# SequenceMatcher calls, which timed out at fifteen minutes and left the
# scheduled dedup job unable to finish.
#
# This narrows the candidate set only. Any pair the old scan would have linked is
# still compared, because a pair sharing no word cannot reach 0.88.
_INDEX_CACHE: dict[int, dict[str, list[Tender]]] = {}


def _tokens(row: Tender) -> set[str]:
    return set(_canon(row.title).split())


def _index(bucket: list[Tender]) -> dict[str, list[Tender]]:
    """word -> rows in this bucket containing it. Built once per bucket."""
    key = id(bucket)
    hit = _INDEX_CACHE.get(key)
    if hit is not None:
        return hit
    idx: dict[str, list[Tender]] = {}
    for row in bucket:
        for tok in _tokens(row):
            idx.setdefault(tok, []).append(row)
    _INDEX_CACHE[key] = idx
    return idx


def _candidates(row: Tender, buckets: dict[object, list[Tender]],
                undated: list[Tender]) -> list[Tender]:
    """Rows worth comparing with `row`: same deadline bucket, sharing a word."""
    same = buckets.get(row.deadline, [])
    pool: list[Tender] = []
    seen: set[int] = set()
    words = _tokens(row)
    for bucket in (same, undated) if row.deadline is not None else (same,):
        if not bucket:
            continue
        idx = _index(bucket)
        for tok in words:
            for other in idx.get(tok, ()):
                if other.id not in seen:
                    seen.add(other.id)
                    pool.append(other)
    return pool


def link_duplicates(db: Session | None = None, window_days: int = 120) -> int:
    """Point each duplicate at its surviving twin. Returns links created.

    Which row survives is survivor()'s call, not id order: the downloadable copy
    wins. Hiding is done with duplicate_of, never a DELETE -- every listing,
    search and match query already filters duplicate_of IS NULL, so a hidden row
    vanishes from the product while staying auditable and reachable by URL.

    ponytail: O(n^2) within a deadline bucket. Buckets keep that tolerable at the
    tens-of-thousands scale we are at; if it stops being tolerable, block on a
    trigram index or a title MinHash instead of widening the loop.
    """
    own_session = db is None
    db = db or SessionLocal()
    linked = 0
    try:
        _INDEX_CACHE.clear()
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
            candidates = _candidates(row, buckets, undated)
            if row.duplicate_of is not None:
                continue  # already hidden by an earlier pairing
            for other in candidates:
                # same-source repeats are already handled by the
                # (source_id, external_ref) unique key.
                if other.id == row.id or other.source_id == row.source_id:
                    continue
                if other.duplicate_of is not None:
                    continue
                if is_duplicate(row, other):
                    keep, hide = survivor(row, other)
                    hide.duplicate_of = keep.id
                    linked += 1
                    if hide is row:
                        break
        # Collapse any chain so a visible row never points at a hidden one.
        by_id = {r.id: r for r in rows}
        for row in rows:
            if row.duplicate_of is not None:
                root = _root(row, by_id)
                if root != row.id:
                    row.duplicate_of = root
        db.commit()
        log.info("dedup: linked %d duplicate tenders", linked)
    finally:
        if own_session:
            db.close()
    return linked
