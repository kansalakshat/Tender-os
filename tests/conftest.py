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
