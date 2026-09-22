"""Run the full scheduler against the production database.

Why this exists: /cron/ingest on Vercel covers CPPP and the GePNIC states, but it
cannot run GeM (which drives a real browser) or enrichment (which downloads a
~150 KB PDF per tender) inside a serverless function with a 300s ceiling. So the
jobs that need a browser and time run here, on a machine that has both, and write
to the same database the site reads.

Nothing is copied or synced: this points the ordinary scheduler at the production
DATABASE_URL, so every connector, the enrichment pass, dedup and the purge all
land directly in the live data.

    python run_prod_worker.py            # runs until interrupted
    python run_prod_worker.py --once     # one full cycle, then exit

`--once` is the form for Task Scheduler / cron: a laptop is not an always-on
host, so a daily task that runs a cycle and exits survives reboots and sleep,
which a long-lived process does not. `daily_ingest.cmd` is that task's command.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import timedelta

from dotenv import dotenv_values, load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))


def run_once(since_hours: float) -> None:
    """Every connector, then enrich, dedup, purge -- one pass, then return.

    The window is wider than a day on purpose: the same overlap the scheduler
    uses, so a run that fails or is missed leaves no permanent hole.
    """
    from app.connectors import REGISTRY
    from app.dedup import link_duplicates
    from app.enrich import enrich_pending
    from app.models import utcnow
    from app.retention import purge_expired
    from app.scheduler import run_connector

    log = logging.getLogger("prod-worker")
    since = utcnow() - timedelta(hours=since_hours)
    for name in REGISTRY:
        try:
            run_connector(name, since=since)
        except Exception:                     # one source must not sink the run
            log.exception("%s failed", name)
    # Enrich before dedup: dedup matches on title, and a GeM listing title is a
    # stub until enrichment replaces it. Same order as app/scheduler.py.
    log.info("enriched %d tender(s)", enrich_pending())
    log.info("linked %d duplicate(s)", link_duplicates())
    log.info("purged %d tender(s)", purge_expired())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true",
                        help="run one full cycle and exit, instead of staying up")
    parser.add_argument("--since-hours", type=float, default=48.0,
                        help="--once only: how far back to re-read (default 48)")
    args = parser.parse_args()

    # Read before load_dotenv: .env is loaded with override=True and points at a
    # local database, so reading this afterwards would pick up localhost instead.
    from_env = os.environ.get("DATABASE_URL")

    load_dotenv(os.path.join(HERE, ".env"), override=True)

    # .env.production is gitignored and does not exist on a build runner, so CI
    # passes the URL in the environment instead. The file wins when present, so
    # running this by hand on a workstation behaves exactly as it always has.
    prod = (
        dotenv_values(os.path.join(HERE, ".env.production")).get("DATABASE_URL")
        or from_env
    )
    if not prod:
        print(
            "No DATABASE_URL in .env.production or the environment -- refusing to run.",
            file=sys.stderr,
        )
        return 1

    # Set before app.db is imported: the engine is built at import time from this.
    os.environ["DATABASE_URL"] = prod

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger("prod-worker")

    from urllib.parse import urlsplit

    from app.scheduler import build_scheduler

    log.warning("writing to PRODUCTION at %s", urlsplit(prod).hostname)
    if args.once:
        run_once(args.since_hours)
        return 0

    scheduler = build_scheduler()
    log.info("jobs: %s", ", ".join(j.id for j in scheduler.get_jobs()))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
