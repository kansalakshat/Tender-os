"""Warn before Neon's free plan runs out, and say so the day it does.

On 2026-09-28 the month's 5 GB of network transfer ran out and Neon refused every
connection: every page that reads the database returned 500 until the month
turned over. Nothing had warned. This runs daily from .github/workflows/
neon-usage.yml and exits 1 -- which GitHub emails to the repository owner --
when the database refuses a connection, or when a limit is mostly spent.

Neon's API reports storage on the free plan, but leaves transfer and compute at
0 (its consumption endpoints are for paid plans). Those two are printed as "not
reported" rather than as a reassuring 0, and checked if Neon ever fills them in.
The console's Usage page is the only place transfer shows on this plan.

    NEON_API_KEY=... NEON_PROJECT_ID=... DATABASE_URL=... python neon_usage.py
"""
from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import datetime, timezone

API = "https://console.neon.tech/api/v2"
GB = 1024 ** 3

# Free plan, per project. (field, label, limit, unit divisor, unit name)
LIMITS = (
    ("data_transfer_bytes", "network transfer", 5 * GB, GB, "GB"),
    ("compute_time_seconds", "compute", 100 * 3600, 3600, "CU-hours"),
)
STORAGE_LIMIT = 0.5 * GB

WARN_USED = 0.60        # this share of a monthly limit already gone
WARN_PACE = 0.90        # or on course for this share by the month's end
WARN_STORAGE = 0.70


def get(path: str, key: str) -> dict:
    req = urllib.request.Request(API + path, headers={
        "Authorization": f"Bearer {key}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def when(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def check(p: dict, now: datetime) -> list[str]:
    """Problems with one project's usage; empty when all is well."""
    start, end = when(p["consumption_period_start"]), when(p["consumption_period_end"])
    # Early in the month a day's spike would project wildly; judge the pace
    # against at least a fifth of the period.
    elapsed = max((now - start) / (end - start), 0.2)
    problems = []
    for field, label, limit, div, unit in LIMITS:
        used = p.get(field) or 0
        if not used:
            print(f"  {label}: not reported by Neon on this plan -- see the console's Usage page")
            continue
        pace = used / elapsed
        line = (f"{label}: {used / div:.2f} of {limit / div:g} {unit} "
                f"({used / limit:.0%}), on pace for {pace / limit:.0%} by {end:%d %b}")
        print("  " + line)
        if used >= limit * WARN_USED or pace >= limit * WARN_PACE:
            problems.append(line)
    size = p.get("synthetic_storage_size") or 0
    line = f"storage: {size / GB:.3f} of {STORAGE_LIMIT / GB:g} GB ({size / STORAGE_LIMIT:.0%})"
    print("  " + line)
    if size >= STORAGE_LIMIT * WARN_STORAGE:
        problems.append(line)
    return problems


def database_answers(url: str) -> str | None:
    """None if a query goes through, else why not ("exceeded the quota" when a
    free-plan limit has been hit)."""
    import psycopg
    try:
        with psycopg.connect(url, connect_timeout=30) as conn:
            conn.execute("select 1")
        return None
    except psycopg.Error as exc:
        return str(exc).splitlines()[0][:300]


def main() -> int:
    key = os.getenv("NEON_API_KEY", "").strip()
    if not key:
        print("NEON_API_KEY is not set. Create one at console.neon.tech -> "
              "Account settings -> API keys, and add it as a repository secret.")
        return 1
    now = datetime.now(timezone.utc)
    problems = []

    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        why = database_answers(url)
        print(f"database: {'answering' if why is None else 'REFUSING -- ' + why}")
        if why is not None:
            problems.append(f"the database is refusing connections: {why}")

    # A project-scoped key may not list projects, so name the project outright.
    ids = [os.environ["NEON_PROJECT_ID"]] if os.getenv("NEON_PROJECT_ID") else [
        item["id"] for item in get("/projects", key)["projects"]]
    for pid in ids:
        p = get(f"/projects/{pid}", key)["project"]
        print(f"{p['name']} ({p['id']}):")
        problems += [f"{p['name']}: {x}" for x in check(p, now)]
    if problems:
        print("\nNeeds attention -- the site's database pages fail while Neon refuses:")
        print("\n".join("  " + x for x in problems))
        return 1
    print("\nAll within the free plan.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
