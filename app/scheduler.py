from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from .connectors import REGISTRY
from .models import utcnow
from .dedup import link_duplicates
from .enrich import enrich_pending
from .retention import DEFAULT_RETENTION_DAYS, purge_expired
from .schemas import RunSummary

log = logging.getLogger(__name__)

INTERVAL_HOURS = float(os.getenv("SCHEDULE_INTERVAL_HOURS", "6"))
# Re-fetch a little further back than the interval, so a slow publish or a failed
# run does not leave a permanent hole in the data.
OVERLAP = timedelta(hours=2)


def run_connector(name: str, since: datetime | None = None, enrich_new: bool = False,
                  resume_file: str | None = None, slices: int = 1):
    """Run one connector, optionally reading the documents it just collected.

    `enrich_new` covers this run's own rows only. The general enrichment queue
    is ordered by soonest deadline, so a bid fetched today and closing in a
    fortnight sits behind every one closing tomorrow -- without this it would
    not be read for hours, and a listing row with no document has no EMD, no
    value and no links.

    `slices` (page-based connectors only) splits the pages into that many crawls
    run at once, each with its own browser: GeM's 500 pages took ~37 minutes in
    one. `resume_file` then gets a _1, _2 ... suffix per slice.
    """
    from concurrent.futures import ThreadPoolExecutor

    connectors = [REGISTRY[name]() for _ in range(slices)]
    if slices > 1:
        first, total = connectors[0].start_page, connectors[0].max_pages
        per = -(-total // slices)
        for i, c in enumerate(connectors):
            c.start_page, c.max_pages = first + i * per, min(per, total - i * per)
    for i, c in enumerate(connectors):
        if resume_file and hasattr(c, "resume_file"):
            stem, ext = os.path.splitext(resume_file)
            c.resume_file = resume_file if slices == 1 else f"{stem}_{i + 1}{ext}"
    try:
        if slices == 1:
            summary = connectors[0].run(since=since)
        else:
            with ThreadPoolExecutor(max_workers=slices) as pool:
                summary = _combine(list(pool.map(lambda c: c.run(since=since), connectors)))
        log.info("%s", summary)
        fresh = [i for c in connectors for i in c.created_ids]
        if enrich_new and fresh:
            from .enrich import enrich_pending

            # Several workers, sharded on id so none reads another's rows. One
            # worker manages ~25 documents a minute, and a day's GeM bids run to
            # thousands: read serially, they took longer than the crawl itself.
            workers = max(1, int(os.getenv("ENRICH_WORKERS", "6")))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                read = sum(pool.map(
                    lambda i: enrich_pending(limit=len(fresh), only=fresh, shard=(i, workers)),
                    range(workers),
                ))
            log.info("%s: read %d of %d new bid document(s)", name, read, len(fresh))
        return summary
    finally:
        for c in connectors:
            c.close()


def _combine(parts: list[RunSummary]) -> RunSummary:
    """One summary for a sliced run: counts added, the worst status wins."""
    statuses = {p.status for p in parts}
    out = RunSummary(
        source=parts[0].source,
        status="error" if "error" in statuses else next(iter(statuses - {"ok"}), "ok"),
        message="; ".join(p.message for p in parts if p.message) or None,
        started_at=min(p.started_at for p in parts),
        finished_at=max((p.finished_at for p in parts if p.finished_at), default=None),
    )
    for p in parts:
        out.fetched += p.fetched
        out.new += p.new
        out.updated += p.updated
        out.skipped += p.skipped
        out.errors += p.errors
    return out


def run_incremental(name: str):
    return run_connector(
        name, since=utcnow() - timedelta(hours=INTERVAL_HOURS) - OVERLAP,
        # Same rule as every other caller: a tender is never left half-collected
        # for a later pass to find.
        enrich_new=True,
    )


def build_scheduler() -> BlockingScheduler:
    scheduler = BlockingScheduler()
    for offset, name in enumerate(REGISTRY):
        scheduler.add_job(
            run_incremental,
            "interval",
            hours=INTERVAL_HOURS,
            args=[name],
            id=f"connector:{name}",
            # Stagger starts so two connectors do not wake up together, and run each
            # once shortly after boot rather than waiting a full interval.
            next_run_time=datetime.now() + timedelta(seconds=10 + offset * 30),
            max_instances=1,
            coalesce=True,
            misfire_grace_time=3600,
        )
    # Enrich BEFORE dedup, every cycle. Order matters: dedup matches on title, and
    # a GeM listing title is a truncated stub that matches nothing. Enrichment
    # replaces it with the full item list from the bid PDF, so the dedup that
    # follows can actually pair a GeM row with the same notice on CPPP.
    scheduler.add_job(
        enrich_pending, "interval", hours=INTERVAL_HOURS, id="enrich",
        coalesce=True, max_instances=1, misfire_grace_time=3600,
    )
    scheduler.add_job(
        link_duplicates, "interval", hours=max(INTERVAL_HOURS, 6), id="dedup", coalesce=True
    )
    # Only when RETENTION_DAYS is set. Daily rather than every cycle: deleting is
    # irreversible, so doing it less often leaves a longer window to notice a
    # retention setting that is too aggressive.
    if DEFAULT_RETENTION_DAYS is not None:
        scheduler.add_job(purge_expired, "interval", hours=24, id="purge", coalesce=True)
    return scheduler


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    scheduler = build_scheduler()
    log.info("scheduler starting: %s every %sh", ", ".join(REGISTRY), INTERVAL_HOURS)
    if DEFAULT_RETENTION_DAYS is None:
        log.info("retention: nothing is deleted; closed tenders are hidden, not purged")
    else:
        log.warning(
            "expired tenders are PERMANENTLY DELETED daily, %d day(s) after their "
            "deadline (RETENTION_DAYS). Unset it to keep them forever.",
            DEFAULT_RETENTION_DAYS,
        )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")


if __name__ == "__main__":
    main()
