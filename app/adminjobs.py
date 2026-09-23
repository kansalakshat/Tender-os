"""Run a connector from the dashboard, in this process.

This is the "my machine is the server" button. There is no trick to it and no
trick is possible: a browser cannot crawl GeM on the page's behalf, because
bidplus.gem.gov.in sends no CORS headers, so script on our origin may not read
its responses. What the button actually does is start the crawl *here*, in the
process serving the request -- so whose IP is used is decided by where this app
is running, not by who clicked.

That is exactly what is wanted when the operator runs the app on their own
machine, which is the only place GeM answers: Vercel has no browser at all, and
GitHub's runner ranges are refused at the socket.

One job at a time, in a thread, with its log kept in memory. In memory because
this is a live view of something happening now, and a job that outlives the
process has nothing to show anyway -- the thread died with it.
"""
from __future__ import annotations

import os
import threading
from collections import deque
from datetime import datetime

from .models import utcnow

# The name that means "read bid documents" rather than "fetch a listing".
ENRICH_JOB = "Bid documents"
# Workers are threads waiting on downloads, so more helps until the portal is
# the limit rather than us. Measured: 8 reads ~200 documents a minute; the cap
# is politeness to a government host, not a technical ceiling.
MAX_WORKERS = 8

# Bounded: a full GeM walk emits thousands of lines and this is a status panel,
# not an archive. The interesting end is the recent one.
_MAX_LINES = 300


class Job:
    def __init__(self, name: str):
        self.name = name
        self.started_at: datetime = utcnow()
        self.finished_at: datetime | None = None
        self.status = "running"
        self.lines: deque[str] = deque(maxlen=_MAX_LINES)
        self.summary: str | None = None

    def log(self, line: str) -> None:
        self.lines.append(f"{utcnow().strftime('%H:%M:%S')}  {line}")

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "status": self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
            "summary": self.summary,
            "lines": list(self.lines),
        }


_lock = threading.Lock()
_current: Job | None = None


def current() -> Job | None:
    return _current


def _enrich(job: Job, workers: int, limit: int) -> int:
    """Read bid documents with several workers in this process.

    Threads, not processes: every worker spends its time waiting on a ~150 KB
    download, so the GIL is free almost all of the time and there is nothing to
    gain from separate interpreters. Sharded on id, which needs_enrichment
    supports, so the workers never hand each other the same tender.
    """
    from .enrich import enrich_pending

    done = [0] * workers
    threads = []

    def work(index: int) -> None:
        try:
            done[index] = enrich_pending(
                limit=max(1, limit // workers), shard=(index, workers)
            )
        except Exception as exc:                # one worker must not sink the job
            job.log(f"enrich worker {index}: {type(exc).__name__}: {exc}")

    for i in range(workers):
        t = threading.Thread(target=work, args=(i,), name=f"enrich-{i}", daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join()
    return sum(done)


def _run(job: Job, name: str, max_pages: int | None, since_hours: float | None,
         then_enrich: int = 0, workers: int = 4) -> None:
    global _current
    from datetime import timedelta

    from .connectors import REGISTRY

    try:
        if name == ENRICH_JOB:
            job.log(f"reading bid documents, {workers} workers, up to {then_enrich}")
            changed = _enrich(job, workers, then_enrich)
            job.summary = f"read {changed} bid document(s)"
            job.status = "ok"
            job.log(job.summary)
            return
        connector = REGISTRY[name]()
        if max_pages is not None and hasattr(connector, "max_pages"):
            connector.max_pages = max_pages
        since = utcnow() - timedelta(hours=since_hours) if since_hours else None
        job.log(f"starting {name}"
                + (f", {max_pages} pages" if max_pages else "")
                + (f", last {since_hours:g}h" if since_hours else ", full walk"))
        try:
            summary = connector.run(since=since)
        finally:
            connector.close()
        job.summary = str(summary)
        job.status = "ok" if summary.status == "ok" else summary.status
        job.log(job.summary)

        # The same job reads the documents behind what it just fetched, rather
        # than leaving a second pass to catch up later. A listing row without
        # its document has no EMD, no value and no links -- half a tender.
        if then_enrich:
            job.log(f"reading bid documents, {workers} workers, up to {then_enrich}")
            changed = _enrich(job, workers, then_enrich)
            job.summary += f" | read {changed} bid document(s)"
            job.log(f"read {changed} bid document(s)")
    except Exception as exc:                    # the panel must show the failure
        job.status = "error"
        job.summary = f"{type(exc).__name__}: {exc}"
        job.log(job.summary)
    finally:
        job.finished_at = utcnow()
        with _lock:
            if _current is job:
                pass            # keep it: the panel shows the last run's outcome


def start(name: str, max_pages: int | None = None,
          since_hours: float | None = None, then_enrich: int = 0,
          workers: int = 4) -> tuple[bool, str]:
    """Begin a run. False when one is already going."""
    global _current
    from .connectors import REGISTRY

    if name != ENRICH_JOB and name not in REGISTRY:
        return False, f"unknown connector {name!r}"
    workers = max(1, min(workers, MAX_WORKERS))
    with _lock:
        if _current is not None and _current.status == "running":
            return False, f"{_current.name} is still running"
        job = Job(name)
        _current = job
    # daemon: this must never hold up an interpreter that is trying to exit.
    threading.Thread(
        target=_run, args=(job, name, max_pages, since_hours, then_enrich, workers),
        name=f"adminjob-{name}", daemon=True,
    ).start()
    return True, "started"


def can_run_browser_jobs() -> bool:
    """False where a browser-driven connector cannot work at all.

    Serverless has no Chromium, and saying so in the panel is kinder than a
    button that always fails. VERCEL is set by Vercel's own runtime.
    """
    return not os.getenv("VERCEL")
