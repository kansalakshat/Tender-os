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
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .bidpdf import parse_bid_pdf
from .compliance import assert_not_blocked, scrub_personal, user_agent
from .db import SessionLocal
from .models import Tender, utcnow

log = logging.getLogger(__name__)

# Set on raw_payload once a tender has been through here, so a second pass skips
# it. A tender whose PDF yielded nothing is still marked: re-downloading 150 KB
# every six hours to re-learn that it is unparseable helps nobody.
DONE_KEY = "_enriched"


def needs_enrichment(limit: int = 200, shard: tuple[int, int] | None = None) -> list[int]:
    """Ids of tenders with a document we have not read yet, soonest-closing first.

    `shard` is (index, count): take only ids where id % count == index. A bulk
    pass over tens of thousands of documents is one ~150 KB download each, so it
    is run as several processes at once; sharding on id keeps their work disjoint
    without any coordination between them.
    """
    db = SessionLocal()
    try:
        query = (
            select(Tender.id, Tender.raw_payload)
            .where(Tender.document_url.is_not(None))
            .where(Tender.duplicate_of.is_(None))
        )
        if shard is not None:
            index, count = shard
            query = query.where(Tender.id % count == index)
        rows = db.execute(query.order_by(Tender.deadline.asc().nulls_last())).all()
        out = []
        for tid, payload in rows:
            if isinstance(payload, dict) and payload.get(DONE_KEY):
                continue
            out.append(tid)
            if len(out) >= limit:
                break
        return out
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

    fields = parse_bid_pdf(resp.content)
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

    for key in ("emd_amount", "contract_period", "quantity", "office", "ministry"):
        if key in fields:
            payload[key] = fields[key]
            changed = True

    # raw_payload is scrubbed on the way in everywhere else; do the same here.
    tender.raw_payload = scrub_personal(payload)
    if changed:
        tender.last_updated_at = utcnow()
    return changed


def enrich_pending(limit: int = 200, session_factory=SessionLocal,
                   shard: tuple[int, int] | None = None) -> int:
    """Enrich up to `limit` tenders. Returns how many changed."""
    ids = needs_enrichment(limit, shard)
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
            tender = db.get(Tender, tid)
            if tender is None:
                continue
            if enrich_one(db, tender, client):
                changed += 1
            if n % 25 == 0:
                db.commit()
                log.info("enrich: %d/%d processed, %d changed", n, len(ids), changed)
        db.commit()
    finally:
        client.close()
        db.close()
    log.info("enrich: %d of %d tenders updated from their bid document", changed, len(ids))
    return changed
