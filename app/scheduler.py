from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler

from .connectors import REGISTRY
from .models import utcnow
from .dedup import link_duplicates
from .retention import DEFAULT_RETENTION_DAYS, purge_expired

log = logging.getLogger(__name__)

INTERVAL_HOURS = float(os.getenv("SCHEDULE_INTERVAL_HOURS", "6"))
# Re-fetch a little further back than the interval, so a slow publish or a failed
# run does not leave a permanent hole in the data.
OVERLAP = timedelta(hours=2)


def run_connector(name: str, since: datetime | None = None):
    connector = REGISTRY[name]()
    try:
        summary = connector.run(since=since)
        log.info("%s", summary)
        return summary
    finally:
        connector.close()


def run_incremental(name: str):
    return run_connector(name, since=utcnow() - timedelta(hours=INTERVAL_HOURS) - OVERLAP)


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
    scheduler.add_job(
        link_duplicates, "interval", hours=max(INTERVAL_HOURS, 6), id="dedup", coalesce=True
    )
    # Runs daily, not every cycle: deleting is irreversible, so doing it less often
    # leaves a longer window to notice a retention setting that is too aggressive.
    scheduler.add_job(purge_expired, "interval", hours=24, id="purge", coalesce=True)
    return scheduler


def main():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    scheduler = build_scheduler()
    log.info("scheduler starting: %s every %sh", ", ".join(REGISTRY), INTERVAL_HOURS)
    log.warning(
        "expired tenders are PERMANENTLY DELETED daily, %d day(s) after their "
        "deadline (RETENTION_DAYS). Set it higher to keep them longer.",
        DEFAULT_RETENTION_DAYS,
    )
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("scheduler stopped")


if __name__ == "__main__":
    main()
