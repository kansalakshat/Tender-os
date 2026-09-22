"""Enrich one shard of the backlog. Local, because GeM's documents are on a host
that refuses datacenter addresses (see gem_enrich.cmd).

    python enrich_shard.py <index> <count> [limit]
"""
from __future__ import annotations

import logging
import os
import sys


def main() -> int:
    index, count = int(sys.argv[1]), int(sys.argv[2])
    limit = int(sys.argv[3]) if len(sys.argv) > 3 else 20_000
    if not 0 <= index < count:
        print(f"shard index {index} is not in range(0, {count})", file=sys.stderr)
        return 2
    if not os.environ.get("DATABASE_URL"):
        # app/db.py would quietly fall back to localhost, and a backlog pass that
        # enriches the wrong database looks exactly like one that worked.
        print("DATABASE_URL is unset -- refusing to run.", file=sys.stderr)
        return 1

    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    from app.enrich import enrich_pending

    changed = enrich_pending(limit=limit, shard=(index, count))
    logging.getLogger("enrich").info(
        "shard %d/%d finished: %d tender(s) changed", index, count, changed
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
