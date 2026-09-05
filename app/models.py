from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint, func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def utcnow() -> datetime:
    """Naive UTC, matching the naive DateTime columns below."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# JSONB on Postgres, plain JSON elsewhere (so tests can run on SQLite).
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    license: Mapped[str | None] = mapped_column(Text)  # e.g. "NDSAP", "public-published"
    robots_txt_checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    robots_txt_allowed: Mapped[bool | None] = mapped_column(Boolean)
    rate_limit_seconds: Mapped[Decimal] = mapped_column(Numeric, default=2)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Tender(Base):
    __tablename__ = "tenders"
    __table_args__ = (
        UniqueConstraint("source_id", "external_ref", name="uq_tenders_source_ref"),
        Index("idx_tenders_deadline", "deadline"),
        Index("idx_tenders_category", "category"),
        Index("idx_tenders_status", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("sources.id"))
    external_ref: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    organization: Mapped[str | None] = mapped_column(Text)
    department: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    estimated_value: Mapped[Decimal | None] = mapped_column(Numeric)
    currency: Mapped[str] = mapped_column(String(8), default="INR")
    published_date: Mapped[date | None] = mapped_column(Date)
    deadline: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str | None] = mapped_column(Text)  # open|closed|awarded|cancelled
    document_url: Mapped[str | None] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    raw_payload: Mapped[dict | None] = mapped_column(JSONType)
    # Cross-source duplicates stay as distinct rows; a periodic fuzzy job links them.
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("tenders.id"))


class Company(Base):
    """A bidder's profile: the answers that drive tender matching.

    `contact_email` is first-party account data the company volunteers about
    itself, which is a different thing from compliance rule #7 -- that rule bans
    storing third-party personal data scraped *out of a source*, and is enforced
    on TenderRecord.raw_payload. Nothing here comes from a scrape.
    """

    __tablename__ = "companies"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Nullable on purpose: an anonymous visitor can still score a profile without
    # signing up. Claiming it later just sets this column.
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    contact_email: Mapped[str | None] = mapped_column(Text)
    # Answers. Lists live as JSON: they are read whole, never queried by element.
    sectors: Mapped[list] = mapped_column(JSONType, default=list)
    keywords: Mapped[list] = mapped_column(JSONType, default=list)
    districts: Mapped[list] = mapped_column(JSONType, default=list)
    states: Mapped[list] = mapped_column(JSONType, default=list)
    buyers: Mapped[list] = mapped_column(JSONType, default=list)
    # Exclusions, not preferences: these disqualify rather than deduct.
    exclude_keywords: Mapped[list] = mapped_column(JSONType, default=list)
    exclude_buyers: Mapped[list] = mapped_column(JSONType, default=list)
    min_lead_days: Mapped[int] = mapped_column(default=7)
    max_project_value: Mapped[Decimal | None] = mapped_column(Numeric)
    # default=utcnow, not server_default=func.now(): the DB clock is server-local
    # (IST here) while every timestamp we write in Python is naive UTC. Mixing the
    # two put created_at 5h30m *ahead* of updated_at on the same row.
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class User(Base):
    """A login. Deliberately thin -- the profile lives on Company.

    `password_hash` is a self-describing scrypt string (see app/auth.py); no plain
    password is ever stored, logged, or returned by any endpoint.
    """

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Stored lower-cased so "Ops@Acme" and "ops@acme" are the same account.
    email: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    # Nullable: an account created through Google has no password and never will.
    # Every password check must treat None as "cannot sign in this way".
    password_hash: Mapped[str | None] = mapped_column(Text)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Google's stable subject id, not the email -- people change their address and
    # Google reuses none of these. Unique so two accounts cannot claim one identity.
    google_sub: Mapped[str | None] = mapped_column(Text, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class ConnectorRun(Base):
    __tablename__ = "connector_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    source_id: Mapped[int | None] = mapped_column(ForeignKey("sources.id"))
    source_name: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(Text, nullable=False)  # ok|refused|error
    fetched: Mapped[int] = mapped_column(default=0)
    new: Mapped[int] = mapped_column(default=0)
    updated: Mapped[int] = mapped_column(default=0)
    errors: Mapped[int] = mapped_column(default=0)
    message: Mapped[str | None] = mapped_column(Text)
