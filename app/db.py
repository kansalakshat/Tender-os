from __future__ import annotations

import logging
import os
import threading
import time

from dotenv import load_dotenv
from sqlalchemy import create_engine, event
from sqlalchemy.exc import DisconnectionError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

load_dotenv()

LOCAL_DEFAULT = "postgresql+psycopg://postgres:postgres@localhost:5432/tenders"


def normalize_database_url(raw: str | None) -> str:
    """Accept the URL shape hosted providers actually hand out.

    Neon, Supabase, Railway and Vercel Postgres all give you `postgres://...` or
    `postgresql://...`. SQLAlchemy needs the driver named, and psycopg 3 is what
    this project installs, so both are rewritten to `postgresql+psycopg://`.
    Pasting the provider's string verbatim is the normal thing to do; it should
    not be a deployment failure.
    """
    url = (raw or "").strip()
    for prefix in ("postgresql://", "postgres://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


# An explicitly empty DATABASE_URL is NOT the same as an unset one: a deployment
# that sets the variable to "" means to configure it and failed, and silently
# falling back to localhost would hide that behind a connection timeout.
DATABASE_URL = (
    normalize_database_url(os.environ["DATABASE_URL"])
    if "DATABASE_URL" in os.environ
    else LOCAL_DEFAULT
)

_engine = None
_sessionmaker = sessionmaker(expire_on_commit=False, future=True)


def get_engine():
    """Built on first use, not at import.

    Doing this at import time meant one bad DATABASE_URL took the whole process
    down before FastAPI existed -- every route 500'd, including /health, which
    touches no database. A misconfigured database should break the pages that
    need a database, and say so.
    """
    global _engine
    if _engine is None:
        if not DATABASE_URL:
            raise RuntimeError(
                "DATABASE_URL is set but empty. Point it at a Postgres instance, "
                "e.g. postgresql+psycopg://user:pass@host/dbname -- a provider's "
                "postgres:// URL is accepted too."
            )
        _engine = create_engine(DATABASE_URL, future=True, **_pool_options())
        if not isinstance(_engine.pool, NullPool):
            _ping_when_idle(_engine)
    return _engine


# The database is in us-east-1 and the server is not: every round trip is ~270 ms.
# pool_pre_ping paid one of those on every request just to ask "still there?".
# A connection that was in use a moment ago is; only one that sat idle long
# enough for Neon to suspend, or a NAT to forget it, is worth checking.
IDLE_PING_SECONDS = 60


def _ping_when_idle(engine) -> None:
    @event.listens_for(engine, "checkin")
    def _checkin(dbapi_conn, record):
        record.info["idle_since"] = time.monotonic()

    @event.listens_for(engine, "checkout")
    def _checkout(dbapi_conn, record, proxy):
        if time.monotonic() - record.info.get("idle_since", 0) < IDLE_PING_SECONDS:
            return
        try:
            engine.dialect.do_ping(dbapi_conn)
        except Exception as exc:
            # The pool discards this connection and checks out a fresh one.
            raise DisconnectionError() from exc


def _pool_options() -> dict:
    """Serverless must not pool.

    A function instance is frozen between requests, so any connection it holds is
    idle-but-occupied. With several instances warm, a small Postgres (a Neon free
    tier especially) runs out of connections and new requests queue instead of
    being served -- which looks exactly like the site hanging.

    NullPool opens one connection per request and closes it. That costs a connect
    per request, which is the right trade when the alternative is exhausting the
    database. A long-lived process keeps the normal pool.
    """
    if os.getenv("VERCEL") or os.getenv("AWS_LAMBDA_FUNCTION_NAME"):
        return {"poolclass": NullPool}
    # No pool_pre_ping: see _ping_when_idle, which checks only stale connections.
    return {}


def SessionLocal():
    """A Session bound to the lazily created engine. Callable, so every existing
    `SessionLocal()` call site is unchanged."""
    return _sessionmaker(bind=get_engine())


def __getattr__(name):
    # `from app.db import engine` still works, and still builds it on demand.
    if name == "engine":
        return get_engine()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def memo(db: Session, key: str, compute):
    """compute() once per session, i.e. once per request. Cleared on flush, so a
    session never reads back a memo from before its own writes."""
    store = db.info.setdefault("memo", {})
    if key not in store:
        store[key] = compute()
    return store[key]


@event.listens_for(Session, "after_flush")
def _forget_memo(session, _flush_context):
    session.info.pop("memo", None)


# Pages, figures and lookups that are the same for every visitor. The database is
# an ocean away (~270 ms a query), so the landing page's six counts cost seconds;
# a minute of staleness on "how many tenders are open" costs nothing.
#
# Expired entries are still served while one background thread recomputes them,
# so only the first visitor after a restart ever waits on the database.
_SHARED: dict[tuple, tuple[float, object]] = {}
_REFRESHING: set[tuple] = set()


def shared(db: Session, key: tuple, seconds: float, compute):
    """compute(db) -> value, cached for `seconds`, same for every visitor."""
    hit = _SHARED.get(key)
    if hit is None:
        _SHARED[key] = (time.monotonic() + seconds, compute(db))
        return _SHARED[key][1]
    if hit[0] <= time.monotonic() and key not in _REFRESHING:
        _REFRESHING.add(key)
        threading.Thread(target=_refresh, args=(key, seconds, compute), daemon=True).start()
    return hit[1]


def _refresh(key: tuple, seconds: float, compute) -> None:
    try:
        with SessionLocal() as db:       # the request's session is closed by now
            _SHARED[key] = (time.monotonic() + seconds, compute(db))
    except Exception:
        logging.getLogger(__name__).exception(
            "refreshing %s failed; still serving the old value", key)
    finally:
        _REFRESHING.discard(key)


def get_db():
    """FastAPI dependency. Lives here, not in api.py, so app/web.py shares the
    exact same one -- two copies means a test override reaches only one of them.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
