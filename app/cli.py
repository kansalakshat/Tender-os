"""Operator CLI: `tenders <command>` (or `python -m app.cli <command>`)."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

from .connectors import REGISTRY
from .models import utcnow
from .dedup import link_duplicates
from .retention import DEFAULT_RETENTION_DAYS, cutoff_date, purge_expired
from .retention import purge_expired


def cmd_run(args) -> int:
    since = utcnow() - timedelta(hours=args.since_hours) if args.since_hours else None
    failed = False
    for name in args.names or list(REGISTRY):
        if name not in REGISTRY:
            print(f"unknown connector {name!r}; known: {', '.join(REGISTRY)}", file=sys.stderr)
            return 2
        connector = REGISTRY[name]()
        if args.rate_limit is not None:
            # An explicit operator choice for one run. The class default stays as
            # it is, so the scheduler keeps the polite cadence. Backoff on 429/5xx
            # is untouched -- this changes how fast we ask, never how we react to
            # being told to slow down.
            connector.rate_limit_seconds = max(args.rate_limit, 0.25)
        # Only meaningful for page-based connectors; ignored by the rest.
        for attr in ("max_pages", "start_page"):
            value = getattr(args, attr)
            if value is not None and hasattr(connector, attr):
                setattr(connector, attr, value)
        try:
            summary = connector.run(since=since)
        finally:
            connector.close()
        print(summary)
        failed |= summary.status != "ok"
    return 1 if failed else 0


def cmd_check_robots(args) -> int:
    """Check robots.txt for every connector without writing anything."""
    blocked = False
    for name in args.names or list(REGISTRY):
        connector = REGISTRY[name]()
        try:
            allowed = connector.check_robots_allowed(connector.paths)
        finally:
            connector.close()
        print(f"{name:<14} {connector.base_url:<32} {'ALLOWED' if allowed else 'DISALLOWED'}")
        blocked |= not allowed
    return 1 if blocked else 0


def cmd_discover(args) -> int:
    """List data.gov.in candidates so a human can pin the real ones."""
    from .connectors.data_gov_in import RESOURCES_FILE, DataGovInConnector

    connector = DataGovInConnector()
    try:
        candidates = connector.discover_resources()
    finally:
        connector.close()

    pinned = set(connector.configured_resources())
    for c in candidates:
        mark = "PINNED" if c["id"] in pinned else "      "
        print(f'{mark}  {c["id"]}  {c["title"][:70]}')
        if args.show_fields:
            print(f'          fields: {", ".join(c["fields"][:12])}')
    print()
    print(f"{len(candidates)} candidates, {len(pinned)} pinned.")
    print(
        "Catalogue search is imprecise -- 'tender' matches tender coconut prices "
        "and 'procurement' matches crop procurement bought under MSP. Read the "
        "titles and fields, then add only genuine procurement datasets to "
        f"{RESOURCES_FILE.name}."
    )
    return 0


def cmd_dedup(args) -> int:
    print(f"linked {link_duplicates(window_days=args.window_days)} duplicates")
    return 0


def cmd_purge(args) -> int:
    """Delete expired tenders. Destructive and irreversible -- see app/retention.py."""
    count, path = purge_expired(grace_days=args.grace_days, dry_run=args.dry_run)
    if args.dry_run:
        print(f"dry run: {count} tenders would be purged")
    else:
        print(f"purged {count} tenders" + (f"; backup written to {path}" if path else ""))
    return 0


def cmd_purge(args) -> int:
    """Permanently delete expired tenders. Irreversible -- a closed tender cannot
    be re-fetched from any source we are allowed to read."""
    cutoff = cutoff_date(args.days)
    doomed = purge_expired(days=args.days, dry_run=True)
    if args.dry_run:
        print(f"would delete {doomed} tender(s) with a deadline before {cutoff}")
        return 0
    if doomed and not args.yes:
        answer = input(
            f"Permanently delete {doomed} tender(s) with a deadline before {cutoff}? "
            "This cannot be undone. Type 'yes' to continue: "
        )
        if answer.strip().lower() != "yes":
            print("aborted; nothing was deleted")
            return 1
    print(f"deleted {purge_expired(days=args.days)} tender(s)")
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="tenders", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="run one or more connectors now")
    p_run.add_argument("names", nargs="*", help=f"default: all ({', '.join(REGISTRY)})")
    p_run.add_argument("--since-hours", type=float, default=None,
                       help="only fetch records published in the last N hours")
    p_run.add_argument("--max-pages", type=int, default=None,
                       help="page-based connectors: how many listing pages to read")
    p_run.add_argument("--start-page", type=int, default=None,
                       help="page-based connectors: resume a backfill from this page")
    p_run.add_argument("--rate-limit", type=float, default=None,
                       help="seconds between requests for this run only "
                            "(default: the connector's own, 3s for CPPP/GePNIC)")
    p_run.set_defaults(func=cmd_run)

    p_robots = sub.add_parser("check-robots", help="check robots.txt only, write nothing")
    p_robots.add_argument("names", nargs="*")
    p_robots.set_defaults(func=cmd_check_robots)

    p_disc = sub.add_parser("discover-data-gov-in",
                            help="list candidate data.gov.in datasets to pin")
    p_disc.add_argument("--show-fields", action="store_true")
    p_disc.set_defaults(func=cmd_discover)

    p_dedup = sub.add_parser("dedup", help="link cross-source duplicates")
    p_dedup.add_argument("--window-days", type=int, default=120)
    p_dedup.set_defaults(func=cmd_dedup)

    p_purge = sub.add_parser(
        "purge-expired",
        help="DELETE tenders whose deadline has passed (writes a backup first)",
    )
    p_purge.add_argument("--grace-days", type=int, default=0,
                         help="keep tenders that closed within the last N days")
    p_purge.add_argument("--dry-run", action="store_true",
                         help="report the count and delete nothing")
    p_purge.set_defaults(func=cmd_purge)

    p_purge = sub.add_parser(
        "purge", help="permanently delete tenders whose deadline has passed"
    )
    p_purge.add_argument("--days", type=int, default=DEFAULT_RETENTION_DAYS,
                         help=f"grace period after the deadline "
                              f"(default {DEFAULT_RETENTION_DAYS}, from RETENTION_DAYS)")
    p_purge.add_argument("--dry-run", action="store_true",
                         help="count what would go, delete nothing")
    p_purge.add_argument("--yes", action="store_true",
                         help="skip the confirmation prompt (for cron)")
    p_purge.set_defaults(func=cmd_purge)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
