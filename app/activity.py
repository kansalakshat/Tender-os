"""What this machine is doing right now: fetching listings or reading documents.

The admin page's job log lives in the server's memory, so a crawl started in
another process (gem_deep.py, the CLI) never showed there. Each process writes
one small file here as it works -- a heartbeat -- and the admin page lists the
fresh ones. Local files, not the database: a beat every few seconds would be
Neon transfer spent on a status line.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

DIR = Path(__file__).resolve().parents[1] / ".cache" / "activity"
# Older than this and the process is taken to have stopped. A GeM page that
# stalls retries for up to ~90s, and a document can take 60s to time out.
FRESH_SECONDS = 150
_lock = threading.Lock()


def beat(kind: str, detail: str) -> None:
    """kind is "fetching", "reading" or "dedup". Never raises: a status line
    must not stop a crawl."""
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        path = DIR / f"{os.getpid()}-{kind}.json"
        tmp = path.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps({"kind": kind, "detail": detail, "at": time.time()}))
        with _lock:
            os.replace(tmp, path)
    except OSError:
        pass


def current() -> list[dict]:
    """Fresh heartbeats, fetching first. Stale files are removed."""
    out = []
    now = time.time()
    for path in DIR.glob("*.json") if DIR.exists() else []:
        try:
            item = json.loads(path.read_text())
        except (OSError, ValueError):
            continue
        if now - item.get("at", 0) > FRESH_SECONDS:
            try:
                path.unlink()
            except OSError:
                pass
            continue
        out.append(item)
    return sorted(out, key=lambda i: (i["kind"] != "fetching", i["detail"]))


if __name__ == "__main__":
    import tempfile

    DIR = Path(tempfile.mkdtemp())
    beat("fetching", "GeM: 200 read, 200 new")
    beat("reading", "bid documents: 25 of 688")
    assert [i["kind"] for i in current()] == ["fetching", "reading"]
    stale = DIR / "1-reading.json"
    stale.write_text(json.dumps({"kind": "reading", "detail": "x", "at": 0}))
    assert len(current()) == 2 and not stale.exists(), "stale beats are dropped"
    print("activity ok")
