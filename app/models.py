from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    JSON, Boolean, Date, DateTime, ForeignKey, Index, Numeric, String, Text, UniqueConstraint,
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
    # Health, written by the connector on every run: False when robots refuses.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    # Intent, written only by an operator. Separate from `active` precisely
    # because a run overwrites that one, so a source turned off would turn
    # itself back on at the next attempt.
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # The portal's own count of what it publishes, and when we last read it.
    # Lets the dashboard say what is left to collect rather than only what we
    # hold. Null where a portal publishes no total, or none has been seen yet.
    listing_total: Mapped[int | None] = mapped_column()
    listing_total_at: Mapped[datetime | None] = mapped_column(DateTime)


class Tender(Base):
    __tablename__ = "tenders"
    __table_args__ = (
        UniqueConstraint("source_id", "external_ref", name="uq_tenders_source_ref"),
        Index("idx_tenders_deadline", "deadline"),
        Index("idx_tenders_category", "category"),
        Index("idx_tenders_status", "status"),
        # Postgres does not index the referencing side of a foreign key. Without
        # this, deleting one tender scans the table to check nothing points at it,
        # and purging 5,335 rows ran for minutes. See migration 0008.
        Index("idx_tenders_duplicate_of", "duplicate_of"),
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
    # default=utcnow, not server_default=func.now(): func.now() is the DATABASE
    # clock, which is server-local, while every other timestamp in this schema is
    # Python UTC. Two naive columns on different clocks cannot be compared, and
    # last_updated_at was the worse case -- server-local on insert (here) and UTC
    # on update (BaseConnector._upsert), inside one column. Same reasoning as
    # Company.created_at below.
    first_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
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
    # Which of the answers above are boundaries rather than leanings. A name in
    # here ("location", "buyers", "sectors", "keywords") turns that answer into
    # a filter: a tender that does not match is not shown at all, instead of
    # being shown with a lower score. Empty for every profile that predates the
    # question, which is the right default -- it is what they were told.
    strict: Mapped[list] = mapped_column(JSONType, default=list, server_default="[]")
    # Exclusions, not preferences: these disqualify rather than deduct.
    exclude_keywords: Mapped[list] = mapped_column(JSONType, default=list)
    exclude_buyers: Mapped[list] = mapped_column(JSONType, default=list)
    min_lead_days: Mapped[int] = mapped_column(default=7)
    max_project_value: Mapped[Decimal | None] = mapped_column(Numeric)
    # Eligibility. Optional, and checked against typical criteria rather than used
    # to filter -- see app/eligibility.py.
    years_in_business: Mapped[int | None] = mapped_column()
    annual_turnover: Mapped[Decimal | None] = mapped_column(Numeric)
    largest_similar_work: Mapped[Decimal | None] = mapped_column(Numeric)
    # How much work can run at once, and how much cash can sit in bid securities.
    # Checked against the estimated value and EMD that app/enrich.py reads off the
    # bid document; before enrichment there was no per-tender figure to test.
    bid_capacity: Mapped[Decimal | None] = mapped_column(Numeric)
    emd_budget: Mapped[Decimal | None] = mapped_column(Numeric)
    registrations: Mapped[list] = mapped_column(JSONType, default=list)
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
