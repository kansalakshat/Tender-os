"""Vercel entrypoint.

Vercel's Python runtime looks for an ASGI app named `app` in a file under /api,
and `vercel.json` rewrites every path here, so FastAPI's own router still does
the routing.

WHAT DOES NOT WORK ON VERCEL, and why it is not a bug in this file:

1. `app/scheduler.py` cannot run. Serverless functions exist only for the length
   of one request; there is no always-on process to hold an APScheduler loop.
   Ingest has to run somewhere with a real process -- your machine, a small VM,
   Railway, Fly.io -- pointed at the same DATABASE_URL. Vercel Cron can trigger
   short jobs, but a full CPPP backfill walks ~3,200 pages at 3s each and will
   outlast any serverless timeout.

2. The rate limiter in `app/security.py` keeps its buckets in process memory.
   Serverless invocations do not share memory, so the login throttle becomes
   close to useless here. This is a real security regression, not a nuisance:
   before exposing this publicly, move those buckets to Postgres or Redis.

3. `DATABASE_URL` must point at a hosted Postgres (Neon, Supabase, Vercel
   Postgres). The default in app/db.py is localhost and will fail here.
"""
from app.api import app

# Vercel looks for this name.
__all__ = ["app"]
