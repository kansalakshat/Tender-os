import os

# Must be set before anything calls compliance.user_agent().
os.environ.setdefault("CONTACT_EMAIL", "tests@nirmaan.invalid")
os.environ.setdefault("DATABASE_URL", "sqlite://")

# Force-empty, not setdefault: a real SMTP_HOST in .env would otherwise make the
# signup tests open a TLS connection to a live mail server and send verification
# mail to addresses like ops@acme.invalid on every run. load_dotenv() does not
# override a key already present in os.environ, so setting it here wins.
os.environ["SMTP_HOST"] = ""
# Same reasoning: a configured OAuth client would make the "button is hidden when
# unconfigured" test fail depending on whose .env it ran against.
os.environ.pop("GOOGLE_CLIENT_ID", None)
os.environ.pop("GOOGLE_CLIENT_SECRET", None)

import re
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models import Base


@pytest.fixture
def session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


@pytest.fixture
def db(session_factory):
    session = session_factory()
    yield session
    session.close()


@pytest.fixture(autouse=True)
def never_send_real_email(monkeypatch, tmp_path):
    """Belt and braces around the env guard above: even if something re-reads .env
    mid-run, no test may reach a real mail server, and no test may append to the
    project's own outbox.log."""
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setattr("app.mailer.OUTBOX", tmp_path / "outbox.log")
    # Rate-limit buckets are module-level and would otherwise carry across tests:
    # the sixth signup in the whole session would 429 regardless of which test
    # made it. Each test starts with a clean budget.
    from app import security

    security.reset()


STATIC = Path(__file__).resolve().parent.parent / "static"


def page_scripts(html: str) -> list[str]:
    """The source of every script a page runs, in page order.

    Scripts are files under /static, so a check on "the page's JavaScript" has to
    read those files. A src that is not under /static, or not on disk, fails here:
    the CSP would block the first and the server would 404 the second.
    """
    out = []
    for attrs, body in re.findall(r"<script\b([^>]*)>(.*?)</script>", html, re.S):
        src = re.search(r'src="([^"]+)"', attrs)
        if not src:
            out.append(body)
            continue
        path = src.group(1).split("?", 1)[0]
        assert path.startswith("/static/"), f"script from outside /static: {path}"
        f = STATIC / path.removeprefix("/static/")
        assert f.is_file(), f"page links {path}, which does not exist"
        out.append(f.read_text(encoding="utf-8"))
    return out
