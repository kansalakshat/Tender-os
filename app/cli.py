"""Operator CLI: `tenders <command>` (or `python -m app.cli <command>`)."""
from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta

from sqlalchemy import func

from .connectors import REGISTRY
from .models import ConnectorRun, utcnow
from .dedup import link_duplicates
from .retention import DEFAULT_RETENTION_DAYS, cutoff_date, purge_expired


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

        # Read the documents behind the rows this run just created, here, now.
        # A listing row on its own has no EMD, no value and no links, and the
        # general queue is ordered by soonest deadline -- so a bid fetched today
        # and closing in a fortnight would not be read for hours.
        if args.enrich and connector.created_ids:
            from .enrich import enrich_pending

            read = enrich_pending(limit=len(connector.created_ids),
                                  only=connector.created_ids)
            print(f"read {read} of {len(connector.created_ids)} new bid document(s)")
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


def cmd_sources(args) -> int:
    """List, enable or disable sources.

    Disabling writes `enabled`, not `active`: the connector rewrites `active` on
    every run to record whether robots let it in, so a source switched off
    through that column would switch itself back on at the next attempt.
    """
    from sqlalchemy import select

    from .db import SessionLocal
    from .models import Source, Tender

    db = SessionLocal()
    try:
        if args.action == "list":
            rows = db.execute(
                select(Source.name, Source.enabled, func.count(Tender.id))
                .join(Tender, Tender.source_id == Source.id, isouter=True)
                .group_by(Source.id, Source.name, Source.enabled)
                .order_by(func.count(Tender.id).desc())
            ).all()
            for name, enabled, rows_held in rows:
                print(f"{'on ' if enabled else 'OFF'}  {rows_held:>7}  {name}")
            return 0

        if args.action == "prune-empty":
            # Only sources that have actually been tried. One that has never run
            # has produced nothing for the same reason an unopened letter has no
            # reply, and switching it off would make that permanent.
            tried = select(ConnectorRun.source_name).distinct()
            targets = db.execute(
                select(Source)
                .where(Source.enabled.is_(True), Source.name.in_(tried))
                .where(~Source.id.in_(select(Tender.source_id).where(
                    Tender.source_id.is_not(None)).distinct()))
                .order_by(Source.name)
            ).scalars().all()
            if not targets:
                print("nothing to prune: every source that has run holds at least one row")
                return 0
            for src in targets:
                print(("would disable " if args.dry_run else "disabled ") + src.name)
                if not args.dry_run:
                    src.enabled = False
            if not args.dry_run:
                db.commit()
            print(f"{len(targets)} source(s)")
            return 0

        names = args.names
        if not names:
            print("name at least one source", file=sys.stderr)
            return 2
        wanted = args.action == "enable"
        found = db.execute(select(Source).where(Source.name.in_(names))).scalars().all()
        missing = set(names) - {s.name for s in found}
        for name in sorted(missing):
            print(f"unknown source {name!r}", file=sys.stderr)
        for src in found:
            src.enabled = wanted
            print(f"{'enabled' if wanted else 'disabled'} {src.name}")
        db.commit()
        return 1 if missing else 0
    finally:
        db.close()


def cmd_dedup(args) -> int:
    print(f"linked {link_duplicates(window_days=args.window_days)} duplicates")
    return 0


def cmd_purge(args) -> int:
    """Permanently delete expired tenders. Irreversible -- a closed tender cannot
    be re-fetched from any source we are allowed to read."""
    if args.days is None:
        print(
            "RETENTION_DAYS is unset, so expired tenders are kept and simply hidden "
            "by GET /tenders. Pass --days N to delete the ones that closed more than "
            "N days ago, or set RETENTION_DAYS to purge on a schedule.",
            file=sys.stderr,
        )
        return 2
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
    p_run.add_argument("--enrich", action="store_true",
                       help="read the bid document of every row this run creates, "
                            "in the same pass, so a new tender is never half-collected")
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

    p_src = sub.add_parser("sources", help="list, enable or disable sources")
    p_src.add_argument("action", choices=["list", "enable", "disable", "prune-empty"])
    p_src.add_argument("names", nargs="*", help="source names, for enable/disable")
    p_src.add_argument("--dry-run", action="store_true",
                       help="prune-empty: show what would be switched off")
    p_src.set_defaults(func=cmd_sources)

    p_dedup = sub.add_parser("dedup", help="link cross-source duplicates")
    p_dedup.add_argument("--window-days", type=int, default=120)
    p_dedup.set_defaults(func=cmd_dedup)

    p_purge = sub.add_parser(
        "purge", help="permanently delete tenders whose deadline has passed"
    )
    # No default when RETENTION_DAYS is unset: an irreversible delete should not
    # pick a blast radius on the operator's behalf.
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
