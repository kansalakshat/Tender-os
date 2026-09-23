"""Fill in tenders from their bid document.

A listing row is thin by design: GeM's card truncates the title at ~33 characters
and publishes no value at all. The bid PDF has the full item list, the buying
organisation, the estimated value and the EMD. This fetches it once per tender
and writes the better data back.

Runs as its own scheduled job rather than inside the connector: a connector pass
walks hundreds of listing rows in seconds, while a PDF is a ~150 KB download
each. Tying the two together would make every ingest as slow as the slowest
document, and a failed download would cost the listing row too.
"""
from __future__ import annotations

import logging
import os
import threading
from concurrent.futures import ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from decimal import Decimal

import httpx
from sqlalchemy.exc import OperationalError
from sqlalchemy import Text, func, or_, select
from sqlalchemy.orm import Session

from .bidpdf import parse_bid_pdf
from .compliance import assert_not_blocked, scrub_personal, user_agent
from .db import SessionLocal
from .models import Source, Tender, utcnow

log = logging.getLogger(__name__)

# Set on raw_payload once a tender has been through here, so a second pass skips
# it. A tender whose PDF yielded nothing is still marked: re-downloading 150 KB
# every six hours to re-learn that it is unparseable helps nobody.
DONE_KEY = "_enriched"

# Parsing, not downloading, is the cost of a document: pypdf's text extraction
# is pure Python, ~2s of CPU for a 10-page bid against ~0.5s to fetch it. The
# workers are threads, so under the GIL they all queued for one core however
# many were started. Handing the parse to a process pool puts every core on it;
# the threads just wait on the result with the GIL released.
_pool: ProcessPoolExecutor | None = None
_pool_lock = threading.Lock()


def _parse(data: bytes) -> dict:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ProcessPoolExecutor(max(1, (os.cpu_count() or 2) - 1))
        pool = _pool
    try:
        return pool.submit(parse_bid_pdf, data).result()
    except BrokenProcessPool:
        # A child was killed (memory, task manager). Drop the pool so the next
        # call builds a fresh one, and parse this document here.
        with _pool_lock:
            if _pool is pool:
                _pool = None
        return parse_bid_pdf(data)


def needs_enrichment(limit: int = 200, shard: tuple[int, int] | None = None,
                     skip_sources: set[str] | None = None,
                     only: list[int] | None = None) -> list[int]:
    """Ids of tenders with a document we have not read yet, soonest-closing first.

    `shard` is (index, count): take only ids where id % count == index. A bulk
    pass over tens of thousands of documents is one ~150 KB download each, so it
    is run as several processes at once; sharding on id keeps their work disjoint
    without any coordination between them.

    `skip_sources` drops rows by source name. It exists because where this runs
    decides what it can read: GeM's documents are on bidplus.gem.gov.in, which
    refuses connections from datacenter addresses, so a hosted runner would spend
    the whole pass failing on rows a laptop reads without trouble.
    """
    db = SessionLocal()
    try:
        # Ids only, and the already-read test done in SQL. Selecting
        # raw_payload to check one key meant ~30,000 JSON blobs crossing the
        # network before the first PDF was fetched -- per shard -- and the pass
        # looked wedged because nothing happened for minutes.
        #
        # Matched as text rather than with a JSON operator so the same query
        # runs on Postgres and on SQLite under test. The marker is a fixed key
        # this code writes; scraped content cannot collide with it.
        query = (
            select(Tender.id)
            .where(Tender.document_url.is_not(None))
            .where(Tender.duplicate_of.is_(None))
            .where(
                or_(
                    Tender.raw_payload.is_(None),
                    func.cast(Tender.raw_payload, Text).notlike(f'%"{DONE_KEY}"%'),
                )
            )
        )
        if skip_sources:
            query = query.where(
                Tender.source_id.not_in(
                    select(Source.id).where(Source.name.in_(skip_sources))
                )
            )
        if only is not None:
            # A crawl handing over the rows it just created. Without this they
            # join the back of a queue ordered by soonest deadline -- a bid
            # closing in a fortnight sits behind every one closing tomorrow, so
            # today's new bids would wait hours to be read.
            if not only:
                return []
            query = query.where(Tender.id.in_(only))
        if shard is not None:
            index, count = shard
            query = query.where(Tender.id % count == index)
        return list(db.execute(
            query.order_by(Tender.deadline.asc().nulls_last()).limit(limit)
        ).scalars())
    finally:
        db.close()


def enrich_one(db: Session, tender: Tender, client: httpx.Client) -> bool:
    """Read one tender's document and write back what it holds. True if changed."""
    assert_not_blocked(tender.document_url)
    try:
        resp = client.get(tender.document_url)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        log.warning("enrich %s: %s", tender.external_ref, exc)
        return False

    fields = _parse(resp.content)
    payload = dict(tender.raw_payload) if isinstance(tender.raw_payload, dict) else {}
    # Mark even an empty result, so an unparseable document is not retried forever.
    payload[DONE_KEY] = True
    changed = False

    # The full item list replaces the card's truncated stub. This is what lets
    # dedup match a GeM row against the same notice on another portal.
    items = fields.get("item_category")
    if items and len(items) > len(tender.title or ""):
        tender.title = items
        changed = True

    org = fields.get("organisation")
    if org and org != tender.organization:
        # "Damodar Valley Corporation" beats "Ministry Of Power" -- the listing
        # only names the ministry, the document names who is actually buying.
        tender.organization = org
        changed = True
    dept = fields.get("department")
    if dept and not tender.department:
        tender.department = dept
        changed = True

    value = fields.get("estimated_value")
    if value is not None and tender.estimated_value is None:
        tender.estimated_value = Decimal(str(value))
        changed = True

    # None of these has a column, and none needs one -- they are read off the
    # document and only ever displayed. "links" is the attachments the bid points
    # at (specification, terms, annexures); see bidpdf.extract_links.
    for key in ("emd_amount", "contract_period", "quantity", "office", "ministry",
                "bid_type", "offer_validity", "bid_opening", "mse_relaxation",
                "startup_relaxation", "links"):
        value_ = fields.get(key)
        # An empty link list is not worth storing, and writing a key that is
        # already identical would mark the row updated for no reason.
        if value_ in (None, [], "") or payload.get(key) == value_:
            continue
        payload[key] = value_
        changed = True

    # raw_payload is scrubbed on the way in everywhere else; do the same here.
    tender.raw_payload = scrub_personal(payload)
    if changed:
        tender.last_updated_at = utcnow()
    return changed


def enrich_pending(limit: int = 200, session_factory=SessionLocal,
                   shard: tuple[int, int] | None = None,
                   skip_sources: set[str] | None = None,
                   only: list[int] | None = None) -> int:
    """Enrich up to `limit` tenders. Returns how many changed."""
    ids = needs_enrichment(limit, shard, skip_sources, only)
    if not ids:
        return 0
    changed = 0
    db = session_factory()
    client = httpx.Client(
        headers={"User-Agent": user_agent()},
        timeout=httpx.Timeout(60.0),
        follow_redirects=True,
    )
    try:
        for n, tid in enumerate(ids, 1):
            try:
                tender = db.get(Tender, tid)
                if tender is None:
                    continue
                if enrich_one(db, tender, client):
                    changed += 1
                if n % 25 == 0:
                    db.commit()
                    log.info("enrich: %d/%d processed, %d changed", n, len(ids), changed)
            except OperationalError as exc:
                # A deadlock is transient by definition: the loser is told to
                # retry, not to stop. Two shards died mid-pass this way, minutes
                # after the nightly purge started deleting expired rows -- a
                # bulk DELETE against per-row UPDATEs -- and took 20,000
                # remaining documents with them, silently.
                #
                # Roll back and move on rather than retry this one tender: it
                # stays unmarked, so the next pass picks it up anyway.
                db.rollback()
                log.warning("enrich: %s on tender %s, skipping it: %s",
                            type(exc).__name__, tid, str(exc).splitlines()[0][:120])
        db.commit()
    finally:
        client.close()
        db.close()
    log.info("enrich: %d of %d tenders updated from their bid document", changed, len(ids))
    return changed
