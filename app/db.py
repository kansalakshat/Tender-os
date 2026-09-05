from __future__ import annotations

import os

from dotenv import load_dotenv
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
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
    return _engine


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
    # pool_pre_ping costs a round trip per checkout; worth it for a process that
    # can otherwise hand out a connection the database has since dropped.
    return {"pool_pre_ping": True}


def SessionLocal():
    """A Session bound to the lazily created engine. Callable, so every existing
    `SessionLocal()` call site is unchanged."""
    return _sessionmaker(bind=get_engine())


def __getattr__(name):
    # `from app.db import engine` still works, and still builds it on demand.
    if name == "engine":
        return get_engine()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def get_db():
    """FastAPI dependency. Lives here, not in api.py, so app/web.py shares the
    exact same one -- two copies means a test override reaches only one of them.
    """
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
