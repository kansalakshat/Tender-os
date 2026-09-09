from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .compliance import scrub_personal
from .models import utcnow

VALID_STATUSES = {"open", "closed", "awarded", "cancelled"}

# Upper bound on any answer list, so one profile cannot make every search slow.
MAX_LIST_ITEMS = 50


class TenderRecord(BaseModel):
    """The single normalized shape every connector must produce."""

    model_config = ConfigDict(str_strip_whitespace=True)

    external_ref: str
    title: str
    organization: str | None = None
    department: str | None = None
    category: str | None = None
    estimated_value: Decimal | None = None
    currency: str = "INR"
    published_date: date | None = None
    deadline: date | None = None
    status: str | None = None
    document_url: str | None = None
    source_url: str
    raw_payload: dict = Field(default_factory=dict)

    @field_validator("external_ref", "title", "source_url")
    @classmethod
    def _required_nonempty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be empty")
        return v.strip()

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip().lower()
        return v if v in VALID_STATUSES else None

    @field_validator("estimated_value")
    @classmethod
    def _sane_value(cls, v: Decimal | None) -> Decimal | None:
        # Negative or absurd values mean we misparsed; store nothing rather than a lie.
        if v is None or v < 0 or v > Decimal("1e15"):
            return None
        return v

    @field_validator("raw_payload")
    @classmethod
    def _scrub(cls, v: dict) -> dict:
        return scrub_personal(v or {})  # rule #7, enforced at the boundary

    @model_validator(mode="after")
    def _derive_status(self):
        if self.status is None and self.deadline is not None:
            self.status = "open" if self.deadline >= date.today() else "closed"
        return self


@dataclass
class RunSummary:
    source: str
    status: str = "ok"  # ok | refused | error
    fetched: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0
    message: str | None = None
    started_at: datetime = field(default_factory=utcnow)
    finished_at: datetime | None = None

    def __str__(self) -> str:
        return (
            f"[{self.source}] {self.status}: fetched={self.fetched} new={self.new} "
            f"updated={self.updated} skipped={self.skipped} errors={self.errors}"
            + (f" -- {self.message}" if self.message else "")
        )


# ---- API response models ----

class SourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    base_url: str
    license: str | None
    robots_txt_checked_at: datetime | None
    robots_txt_allowed: bool | None
    rate_limit_seconds: Decimal
    active: bool


class TenderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_id: int
    external_ref: str
    title: str
    organization: str | None
    department: str | None
    category: str | None
    estimated_value: Decimal | None
    currency: str
    published_date: date | None
    deadline: date | None
    status: str | None
    document_url: str | None
    source_url: str
    first_seen_at: datetime | None
    last_updated_at: datetime | None
    duplicate_of: int | None


class TenderDetailOut(TenderOut):
    raw_payload: dict | None


class Page(BaseModel):
    total: int
    limit: int
    offset: int
    items: list


# ---- Company profile / matching ----

class CompanyIn(BaseModel):
    """The questionnaire. Every field here exists because something in the
    tender data can actually discriminate on it -- see app/matching.py."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str
    contact_email: str | None = None
    sectors: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    districts: list[str] = Field(default_factory=list)
    states: list[str] = Field(default_factory=list)
    buyers: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    exclude_buyers: list[str] = Field(default_factory=list)
    min_lead_days: int = Field(7, ge=0, le=365)
    # Kept, but inert today: estimated_value is null on 100% of live rows because
    # neither portal publishes it on a listing page. It starts working the moment
    # detail-page ingestion lands; until then the form says so.
    max_project_value: Decimal | None = Field(None, ge=0)

    @field_validator("name")
    @classmethod
    def _name_required(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("company name must not be empty")
        return v.strip()

    @field_validator("sectors")
    @classmethod
    def _known_sectors(cls, v: list[str]) -> list[str]:
        # Reject unknown keys loudly. Silently dropping them would hand back an
        # empty result set with no explanation of why.
        from .matching import SECTOR_LABELS

        bad = [s for s in v if s not in SECTOR_LABELS]
        if bad:
            raise ValueError(
                f"unknown sector(s): {', '.join(bad)}. "
                f"Valid: {', '.join(sorted(SECTOR_LABELS))}"
            )
        return list(dict.fromkeys(v))

    @field_validator("districts")
    @classmethod
    def _known_districts(cls, v: list[str]) -> list[str]:
        from .matching import MP_DISTRICTS

        by_lower = {d.lower(): d for d in MP_DISTRICTS}
        bad = [d for d in v if d.lower() not in by_lower]
        if bad:
            raise ValueError(f"unknown district(s): {', '.join(bad)}")
        return list(dict.fromkeys(by_lower[d.lower()] for d in v))

    @field_validator("states")
    @classmethod
    def _known_states(cls, v: list[str]) -> list[str]:
        from .matching import STATES

        by_lower = {s.lower(): s for s in STATES}
        bad = [s for s in v if s.lower() not in by_lower]
        if bad:
            raise ValueError(f"unknown state(s): {', '.join(bad)}")
        return list(dict.fromkeys(by_lower[s.lower()] for s in v))

    @field_validator("keywords", "exclude_keywords")
    @classmethod
    def _clean_keywords(cls, v: list[str]) -> list[str]:
        seen = (k.strip().lower() for k in v)
        out = list(dict.fromkeys(k for k in seen if k))
        # Every entry costs a regex per candidate row on every search. This is a
        # bound on our own work, not a product limit anyone will hit honestly.
        if len(out) > MAX_LIST_ITEMS:
            raise ValueError(f"at most {MAX_LIST_ITEMS} keywords")
        return out

    @field_validator("buyers", "exclude_buyers")
    @classmethod
    def _clean_buyers(cls, v: list[str]) -> list[str]:
        # Free text on purpose: buyer names come from `tenders.organization`, which
        # grows every time a new department publishes. An allowlist would go stale.
        out = list(dict.fromkeys(b.strip() for b in v if b and b.strip()))
        if len(out) > MAX_LIST_ITEMS:
            raise ValueError(f"at most {MAX_LIST_ITEMS} buyers")
        return out

    @model_validator(mode="after")
    def _needs_a_signal(self):
        # Matching requires a sector or a keyword; a profile with neither can
        # never match anything, so refuse it at the door rather than at query time.
        if not self.sectors and not self.keywords:
            raise ValueError("give at least one sector or one keyword")
        return self


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    contact_email: str | None
    sectors: list[str]
    keywords: list[str]
    districts: list[str]
    states: list[str]
    buyers: list[str]
    exclude_keywords: list[str]
    exclude_buyers: list[str]
    min_lead_days: int
    max_project_value: Decimal | None
    created_at: datetime | None
    updated_at: datetime | None


class MatchOut(BaseModel):
    """A tender plus why it matched. The reasons are the product: a score with
    no explanation is not something a bidder can act on."""

    score: int
    reasons: list[str]
    tender: TenderOut
