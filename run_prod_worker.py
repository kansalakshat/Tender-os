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

Leave it running, or start it from Task Scheduler / systemd. It logs to stdout.
"""
from __future__ import annotations

import logging
import os
import sys

from dotenv import dotenv_values, load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    load_dotenv(os.path.join(HERE, ".env"), override=True)

    prod = dotenv_values(os.path.join(HERE, ".env.production")).get("DATABASE_URL")
    if not prod:
        print("No DATABASE_URL in .env.production -- refusing to run.", file=sys.stderr)
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
    scheduler = build_scheduler()
    log.info("jobs: %s", ", ".join(j.id for j in scheduler.get_jobs()))
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
