from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable, Iterator
from urllib.parse import urljoin, urlsplit
from urllib.robotparser import RobotFileParser

import httpx
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..enrich import DONE_KEY
from ..compliance import (
    BlockedSourceError,
    RobotsDisallowedError,
    assert_not_blocked,
    user_agent,
)
from ..db import SessionLocal
from ..models import ConnectorRun, Source, Tender, utcnow
from ..retention import DEFAULT_RETENTION_DAYS, cutoff_date
from ..schemas import RunSummary, TenderRecord

log = logging.getLogger(__name__)

APPROVED_SOURCES_FILE = (
    Path(__file__).resolve().parents[2] / "config" / "approved_sources.yaml"
)
ROBOTS_RECHECK = timedelta(days=7)
# Flush a long backfill to disk periodically rather than in one giant transaction.
COMMIT_EVERY = 200


def load_approved_sources() -> dict[str, dict]:
    """Domains a human has manually verified. No entry -> the connector refuses to run.

    One state's permissive robots.txt says nothing about another's, so every domain
    is approved individually (required for the Tier-2 GePNIC instances).
    """
    if not APPROVED_SOURCES_FILE.exists():
        return {}
    data = yaml.safe_load(APPROVED_SOURCES_FILE.read_text(encoding="utf-8")) or {}
    return {entry["domain"].lower(): entry for entry in data.get("sources", [])}


def _block_gem(request: httpx.Request) -> None:
    """httpx hook: fires per request, so redirects are checked too."""
    assert_not_blocked(str(request.url))


class BaseConnector(ABC):
    source_name: str
    base_url: str
    license: str | None = None
    rate_limit_seconds: float = 2.0
    paths: tuple[str, ...] = ("/",)  # paths robots.txt must permit for us to run
    max_retries: int = 4
    # True when fetch_batch drives a real browser. Such a connector cannot run in
    # a serverless function -- there is no Chromium there and no room in a 300s
    # ceiling for one -- so /cron/ingest skips it rather than logging the same
    # ImportError every night. It still runs from app/scheduler.py on a host that
    # has a browser.
    requires_browser: bool = False
    # Set by a connector that can see the portal's own count of what it
    # publishes; persisted to the source row at the end of a run. None where the
    # portal publishes no such number.
    listing_total: int | None = None

    def __init_subclass__(cls, **kw):
        """Rule #1 at class-definition time: a GeM connector cannot even be declared."""
        super().__init_subclass__(**kw)
        if getattr(cls, "base_url", None):
            assert_not_blocked(cls.base_url)

    def __init__(self, session_factory=SessionLocal, client: httpx.Client | None = None):
        assert_not_blocked(self.base_url)
        self.session_factory = session_factory
        self._last_request = 0.0
        # Why the last robots check said no. "Unreachable" and "disallowed" are very
        # different operational problems and must not be reported as the same thing.
        self.robots_reason: str | None = None
        # Ids this run created, for whoever reads their documents next. On the
        # instance, never the class: a shared list would hand one run's rows to
        # another.
        self.created_ids: list[int] = []
        self._client = client or httpx.Client(
            headers={"User-Agent": user_agent()},
            timeout=httpx.Timeout(45.0),
            follow_redirects=True,
            event_hooks={"request": [_block_gem]},
        )

    # ---- HTTP ----

    @property
    def host(self) -> str:
        return (urlsplit(self.base_url).hostname or "").lower()

    def get(self, url: str, **kwargs) -> httpx.Response:
        """Rate-limited GET with exponential backoff (rules #3, #4)."""
        assert_not_blocked(url)
        delay = self.rate_limit_seconds
        last_exc: Exception | None = None
        for attempt in range(self.max_retries):
            wait = self._last_request + self.rate_limit_seconds - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last_request = time.monotonic()
            try:
                resp = self._client.get(url, **kwargs)
            except BlockedSourceError:
                raise
            except httpx.HTTPError as exc:
                last_exc = exc
                log.warning("%s: %s (attempt %d)", self.source_name, exc, attempt + 1)
            else:
                # 429/5xx are the site telling us to slow down. We slow down; we do
                # not rotate proxies or otherwise evade the limit (rule #3).
                if resp.status_code == 429 or resp.status_code >= 500:
                    last_exc = httpx.HTTPStatusError(
                        f"HTTP {resp.status_code}", request=resp.request, response=resp
                    )
                    log.warning(
                        "%s: HTTP %s, backing off", self.source_name, resp.status_code
                    )
                else:
                    return resp
            if attempt < self.max_retries - 1:
                time.sleep(delay)
                delay *= 2
        raise last_exc or httpx.HTTPError("request failed")

    # ---- Compliance ----

    def check_robots_allowed(self, paths: Iterable[str]) -> bool:
        """Fetch and evaluate robots.txt for the paths we need. Fails closed.

        Returns a plain bool (any falsehood means do not fetch); the reason is left
        on self.robots_reason for the operator-facing log.
        """
        self.robots_reason = None
        robots_url = urljoin(self.base_url, "/robots.txt")
        assert_not_blocked(robots_url)
        try:
            # Via self.get so a flaky network gets the same retry/backoff as any other
            # request -- a dropped connection should not read as "we are unwelcome".
            resp = self.get(robots_url)
        except httpx.HTTPError as exc:
            self.robots_reason = (
                f"could not reach {robots_url} ({exc}); refusing until it answers"
            )
            log.error("%s: robots.txt unreachable (%s) -> refusing", self.source_name, exc)
            return False

        if resp.status_code in (401, 403):
            # Access to the rules themselves is restricted; assume we are not welcome.
            self.robots_reason = f"robots.txt returned HTTP {resp.status_code} (access restricted)"
            log.error(
                "%s: robots.txt returned %s -> refusing", self.source_name, resp.status_code
            )
            return False
        if resp.status_code >= 500:
            self.robots_reason = f"robots.txt returned HTTP {resp.status_code} (server error)"
            log.error(
                "%s: robots.txt HTTP %s -> refusing", self.source_name, resp.status_code
            )
            return False
        if resp.status_code >= 400:
            # RFC 9309: robots.txt unavailable (404/410) means crawling is unrestricted.
            log.info(
                "%s: no robots.txt (HTTP %s) -> allowed", self.source_name, resp.status_code
            )
            return True

        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        ua = user_agent()
        for path in paths:
            url = urljoin(self.base_url, path)
            if not parser.can_fetch(ua, url) or not parser.can_fetch("*", url):
                self.robots_reason = f"robots.txt disallows {path}"
                log.error("%s: robots.txt disallows %s -> refusing", self.source_name, path)
                return False
        return True

    def _approval(self) -> dict:
        entry = load_approved_sources().get(self.host)
        if not entry:
            raise RobotsDisallowedError(
                f"{self.host!r} is not in config/approved_sources.yaml. A human must "
                "verify its robots.txt and terms and add it there before it can run."
            )
        return entry

    def ensure_source_row(self, db: Session) -> Source:
        src = db.execute(
            select(Source).where(Source.name == self.source_name)
        ).scalar_one_or_none()
        if src is None:
            src = Source(name=self.source_name, base_url=self.base_url, license=self.license)
            db.add(src)
        src.base_url = self.base_url
        src.license = self.license
        src.rate_limit_seconds = self.rate_limit_seconds
        db.flush()
        return src

    def robots_allowed_cached(self, db: Session, src: Source) -> bool:
        """Weekly re-check; the cached answer is reused in between (rule #2).

        Only a positive answer is cached. A refusal is re-checked on the next run,
        because we cannot tell a real Disallow from a dropped connection, and one
        bad network moment must not mute a source for a week. The cost of being
        wrong in this direction is a single extra robots.txt request per run.
        """
        fresh = (
            src.robots_txt_checked_at is not None
            and utcnow() - src.robots_txt_checked_at < ROBOTS_RECHECK
        )
        if fresh and src.robots_txt_allowed:
            return True
        allowed = self.check_robots_allowed(self.paths)
        src.robots_txt_checked_at = utcnow()
        src.robots_txt_allowed = allowed
        db.flush()
        return allowed

    # ---- Per-source implementation ----

    @abstractmethod
    def fetch_batch(self, since: datetime | None) -> Iterator[dict]:
        """Yield raw source records."""

    @abstractmethod
    def normalize(self, raw: dict) -> TenderRecord:
        """Map one raw record onto TenderRecord."""

    # ---- Orchestration ----

    def run(
        self, since: datetime | None = None, deadline: float | None = None
    ) -> RunSummary:
        """`deadline` is a time.monotonic() value to stop by, not a duration.

        Needed because /cron/ingest runs against a hard 300s function ceiling and
        a single connector can outlast it on its own -- CPPP alone is 3s a page.
        Checking a budget only between connectors, which is what this did first,
        never gets the chance to fire, and the function is killed mid-request
        with no response at all. Stopping here costs nothing: rows commit in
        batches as they arrive, and the next run's overlapping `since` window
        re-reads whatever this one did not reach.
        """
        summary = RunSummary(source=self.source_name)
        self.created_ids = []           # a second run() must start clean
        db = self.session_factory()
        src = None
        try:
            self._approval()
            src = self.ensure_source_row(db)
            # One guard, here, because every caller routes through run(): the
            # scheduler, the CLI, /cron/ingest and the dashboard button. Checked
            # before robots, since asking a portal we have switched off whether
            # we may read it is a request that should not happen at all.
            if not src.enabled:
                summary.status = "disabled"
                summary.message = "switched off by an operator; nothing was fetched"
                db.commit()
                return summary
            if not self.robots_allowed_cached(db, src):
                src.active = False
                summary.status = "refused"
                summary.message = self.robots_reason or (
                    "robots.txt disallows the paths this connector needs"
                )
                db.commit()
                return summary

            src.active = True
            # A row the purge would delete the moment it lands is not worth
            # writing. CPPP's "latest active tenders" listing is not actually
            # filtered to active ones past roughly page 1000 -- one ten-minute
            # stretch of backfilling produced 1,487 already-closed rows -- so
            # without this the crawl writes rows for the next purge to delete.
            # The condition mirrors purge_expired's exactly, grace period and
            # all, so the two can never disagree about what "expired" means.
            # Retention unset (archive mode) keeps everything, as before.
            expiry_cutoff = (
                None if DEFAULT_RETENTION_DAYS is None
                else cutoff_date(DEFAULT_RETENTION_DAYS)
            )
            for raw in self.fetch_batch(since):
                if deadline is not None and time.monotonic() >= deadline:
                    summary.message = (
                        f"stopped at the time budget after {summary.fetched} records"
                    )
                    log.warning("%s: %s", self.source_name, summary.message)
                    break
                summary.fetched += 1
                try:
                    record = self.normalize(raw)
                except Exception as exc:  # one bad row must not kill the run
                    summary.errors += 1
                    log.warning("%s: normalize failed: %s", self.source_name, exc)
                    continue
                if (
                    expiry_cutoff is not None
                    and record.deadline is not None
                    and record.deadline < expiry_cutoff
                ):
                    summary.skipped += 1
                    continue
                summary.new += self._upsert(db, src, record)
                # Commit as we go. A full CPPP backfill is ~3,200 pages over a
                # couple of hours; holding that in one transaction means a failure
                # on the last page throws away every row before it.
                if summary.fetched % COMMIT_EVERY == 0:
                    # Record the portal's own total as soon as it is known, not
                    # at the end: a shard walking 1,600 pages runs for hours, and
                    # "how much is left" is a question worth answering while it
                    # is still running rather than once it no longer matters.
                    if self.listing_total is not None and src.listing_total != self.listing_total:
                        src.listing_total = self.listing_total
                        src.listing_total_at = utcnow()
                    db.commit()
                    log.info("%s: committed %d records so far", self.source_name,
                             summary.fetched)
            summary.updated = (
                summary.fetched - summary.new - summary.skipped - summary.errors
            )
            # What the portal says it publishes, if this connector saw it. Kept
            # on the source row so the dashboard can report what is left to
            # collect rather than only what we already hold.
            if self.listing_total is not None:
                src.listing_total = self.listing_total
                src.listing_total_at = utcnow()
            db.commit()
        except (BlockedSourceError, RobotsDisallowedError) as exc:
            db.rollback()
            summary.status = "refused"
            summary.message = str(exc)
        except Exception as exc:
            db.rollback()
            summary.status = "error"
            summary.errors += 1
            summary.message = str(exc)
            log.exception("%s: run failed", self.source_name)
        finally:
            summary.finished_at = utcnow()
            db.add(
                ConnectorRun(
                    source_id=src.id if src is not None else None,
                    source_name=self.source_name,
                    started_at=summary.started_at,
                    finished_at=summary.finished_at,
                    status=summary.status,
                    fetched=summary.fetched,
                    new=summary.new,
                    updated=summary.updated,
                    errors=summary.errors,
                    message=summary.message,
                )
            )
            db.commit()
            db.close()
        return summary

    def _upsert(self, db: Session, src: Source, rec: TenderRecord) -> int:
        """Returns 1 if the row was created, 0 if it already existed."""
        existing = db.execute(
            select(Tender).where(
                Tender.source_id == src.id, Tender.external_ref == rec.external_ref
            )
        ).scalar_one_or_none()
        values = rec.model_dump()
        values.pop("external_ref")
        if existing is None:
            row = Tender(source_id=src.id, external_ref=rec.external_ref, **values)
            db.add(row)
            # Flushed so the id exists now. Whoever runs next wants to read the
            # documents behind exactly these rows, and the general queue is
            # ordered by soonest deadline -- a bid closing in a fortnight sits
            # behind every one closing tomorrow, so "enrich after crawling"
            # would not reach today's new bids for hours.
            db.flush()
            self.created_ids.append(row.id)
            return 1

        old_payload = existing.raw_payload if isinstance(existing.raw_payload, dict) else {}
        # MERGE raw_payload, never replace it. app/enrich.py writes what it reads
        # out of the bid document into this same column -- emd_amount, links,
        # bid_type, the enriched-already marker -- and a plain setattr wiped all
        # of it every time the listing was crawled again. The listing's own keys
        # still win, because that is the fresher copy of what the listing says.
        values["raw_payload"] = {**old_payload, **(values.get("raw_payload") or {})}

        # A listing that does not publish a value must not erase one we already
        # read off the document. Unconditional: "the source stopped saying" is
        # never a reason to forget, whatever put the number there.
        if values.get("estimated_value") is None and existing.estimated_value is not None:
            values.pop("estimated_value", None)

        if old_payload.get(DONE_KEY):
            # These two come out better from the document than from the card,
            # which is the entire reason enrichment reads it: the card truncates
            # the title at ~33 characters and names only the ministry. Re-crawling
            # must not undo that, and the enriched marker means no second pass
            # would come along to fix it.
            if len(values.get("title") or "") < len(existing.title or ""):
                values.pop("title", None)
            if existing.organization:
                values.pop("organization", None)

        for key, val in values.items():
            setattr(existing, key, val)
        existing.last_updated_at = utcnow()
        return 0

    def close(self) -> None:
        self._client.close()
