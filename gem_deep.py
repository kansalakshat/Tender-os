"""GeM beyond the Daily run's first 500 pages, then the documents of what it found.

The Daily run reads GeM's newest 500 pages (~5,000 bids), which keeps up with
what is published each day. Long-running bids sit deeper in the listing and
were never reached: on 10 Oct 2026 pages 2,700+ were almost entirely new.

Splits pages 501..end across SHARDS crawlers that run at once (each one keeps
GeM's 3s-per-page pace), then reads the bid documents of every unread tender
with DOC_WORKERS threads. Started by mac/start.sh and windows/start.ps1.

    DATABASE_URL=... python gem_deep.py
"""
from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

FIRST_PAGE = 501            # the Daily run covers 1..500
LAST_PAGE = 5000            # past GeM's end (~4,720 pages); a crawler stops at an empty page
SHARDS = 3
DOC_WORKERS = 6
DOC_LIMIT = 30_000

HERE = os.path.dirname(os.path.abspath(__file__))
log = logging.getLogger("gem-deep")


def main() -> int:
    if not os.environ.get("DATABASE_URL"):
        # app/db.py would quietly fall back to localhost.
        print("DATABASE_URL is unset -- refusing to run.", file=sys.stderr)
        return 1
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    span = -(-(LAST_PAGE - FIRST_PAGE + 1) // SHARDS)
    children = []
    for i in range(SHARDS):
        start = FIRST_PAGE + i * span
        out = open(os.path.join(HERE, f"gem_deep_{i + 1}.log"), "a", encoding="utf-8")
        children.append(subprocess.Popen(
            [sys.executable, "-u", "-m", "app.cli", "run", "GeM",
             "--start-page", str(start), "--max-pages", str(span)],
            cwd=HERE, stdout=out, stderr=subprocess.STDOUT))
        log.info("crawler %d: pages %d-%d, log gem_deep_%d.log",
                 i + 1, start, start + span - 1, i + 1)

    def stop(*_):
        for c in children:
            c.terminate()
        sys.exit(1)
    signal.signal(signal.SIGTERM, stop)

    for i, c in enumerate(children, 1):
        log.info("crawler %d finished with exit code %d", i, c.wait())

    from app.enrich import enrich_pending

    log.info("reading documents, %d workers", DOC_WORKERS)
    with ThreadPoolExecutor(max_workers=DOC_WORKERS) as pool:
        read = sum(pool.map(
            lambda i: enrich_pending(limit=DOC_LIMIT // DOC_WORKERS, shard=(i, DOC_WORKERS)),
            range(DOC_WORKERS)))
    log.info("done: read %d bid document(s)", read)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
