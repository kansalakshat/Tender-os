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
import json
import logging
import os
import sys
import time
from datetime import date, timedelta

from dotenv import dotenv_values, load_dotenv

HERE = os.path.dirname(os.path.abspath(__file__))
# Crawls run at once per source. GeM's 500 pages took ~37 minutes in one
# browser; three each take a third, at the same per-page pace.
SLICES = {"GeM": 3}
# Seconds before a failed source is tried again.
RETRY_PAUSE = 60


def run_once(since_hours: float, progress_dir: str | None = None) -> None:
    """Purge, then every connector, enrich and dedup -- one pass, then return.

    The window is wider than a day on purpose: the same overlap the scheduler
    uses, so a run that fails or is missed leaves no permanent hole.

    `progress_dir` is for the fetching laptop, which restarts the run whenever
    the start script runs again. There, a source that already finished today is
    skipped and GeM carries on from the page it had reached: a restart at 02:28
    re-read all of CPPP and 800 GeM bids for 43 new ones. CI leaves it unset, so
    a run dispatched by hand still fetches everything.
    """
    from app.connectors import REGISTRY
    from app.dedup import link_duplicates
    from app.enrich import enrich_pending
    from app.models import utcnow
    from app.retention import purge_expired
    from app.scheduler import run_connector

    log = logging.getLogger("prod-worker")
    # Dedup keeps the columns it compares here between runs and downloads only
    # what changed (app/dedup.py). CI restores the same path from actions/cache.
    os.environ.setdefault("DEDUP_CACHE", os.path.join(HERE, ".cache", "dedup.json"))
    # Where this runs decides what it can reach: GeM refuses datacenter
    # addresses outright, so a hosted runner names it here instead of spending
    # the run timing out against a host that will not answer.
    skip = {n.strip() for n in os.getenv("SKIP_CONNECTORS", "").split(",") if n.strip()}
    unknown = skip - set(REGISTRY)
    if unknown:
        # Loud, because a typo here silently fetches a source you meant to skip.
        log.warning("SKIP_CONNECTORS names unknown connectors: %s", ", ".join(sorted(unknown)))

    # Purge first. At the end it sat behind the crawl, enrichment and dedup, and
    # for a week every run timed out or crashed before reaching it. It is cheap,
    # and it also spares enrichment and dedup rows that are about to go.
    try:
        log.info("purged %d tender(s)", purge_expired())
    except Exception:                         # the ingest still runs; purge.yml retries
        log.exception("purge failed")

    since = utcnow() - timedelta(hours=since_hours)
    if progress_dir:
        os.makedirs(progress_dir, exist_ok=True)
    done_file = os.path.join(progress_dir, "daily_done.json") if progress_dir else None
    done = finished_today(done_file)

    def fetch(name: str) -> bool:
        """Run one source. False if it failed and is worth another try."""
        try:
            summary = run_connector(
                name, since=since, enrich_new=True, slices=SLICES.get(name, 1),
                resume_file=os.path.join(progress_dir, f"daily_{name}.json") if progress_dir else None)
        except Exception:                     # one source must not sink the run
            log.exception("%s failed", name)
            return False
        if done_file and summary.status == "ok":
            done.add(name)
            with open(done_file, "w", encoding="utf-8") as f:
                json.dump({"day": date.today().isoformat(), "done": sorted(done)}, f)
        return summary.status != "error"

    failed = []
    for name in REGISTRY:
        if name in skip:
            log.info("%s: skipped (SKIP_CONNECTORS)", name)
        elif name in done:
            log.info("%s: skipped, already finished today", name)
        elif not fetch(name):
            failed.append(name)
    # Once more after a pause. A dropped connection (the laptop's internet went
    # at 03:20 on 14 Oct 2026) used to cost a source for the rest of the day;
    # GeM's retry carries on from the page it had reached.
    if failed:
        log.warning("%s failed; trying again in %ds", ", ".join(failed), RETRY_PAUSE)
        time.sleep(RETRY_PAUSE)
        for name in failed:
            fetch(name)
    # Enrich before dedup: dedup matches on title, and a GeM listing title is a
    # stub until enrichment replaces it. Same order as app/scheduler.py.
    #
    # enrich_pending's own default is 200, which is sized for a six-hourly loop.
    # Once a day against GeM's ~3,000 new bids that never catches up, and the
    # backlog only grows -- so the daily pass takes a bigger bite. ~1.5s per
    # document puts 2,000 at roughly 50 minutes.
    # Same reasoning as SKIP_CONNECTORS: GeM's bid PDFs sit on the host that
    # refuses this runner, so there is nothing to gain by trying them.
    no_docs = {n.strip() for n in os.getenv("ENRICH_SKIP_SOURCES", "").split(",") if n.strip()}

    # Read with several workers, the same way the dashboard does. They wait on
    # downloads rather than compute, so threads are the right shape and a single
    # worker leaves the run bounded by one ~150 KB fetch at a time: measured at
    # ~25 documents a minute against ~145 with eight. At one worker a daily
    # cycle could not keep up with a day's new bids, which is how the backlog
    # reached 28,000 in the first place.
    workers = max(1, int(os.getenv("ENRICH_WORKERS", "6")))
    limit = int(os.getenv("ENRICH_LIMIT", "4000"))
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        counts = list(pool.map(
            lambda i: enrich_pending(limit=max(1, limit // workers),
                                     shard=(i, workers), skip_sources=no_docs or None),
            range(workers),
        ))
    log.info("enriched %d tender(s) with %d worker(s)", sum(counts), workers)
    log.info("linked %d duplicate(s)", link_duplicates())


def finished_today(done_file: str | None) -> set[str]:
    """Sources the Daily run on this machine finished today (its local date)."""
    try:
        with open(done_file, encoding="utf-8") as f:
            saved = json.load(f)
        return set(saved["done"]) if saved["day"] == date.today().isoformat() else set()
    except (TypeError, OSError, ValueError, KeyError):   # unset, missing or garbled
        return set()


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
