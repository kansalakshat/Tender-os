"""data.gov.in -- the Open Government Data Platform.

Official API, free registered key, datasets published under the National Data
Sharing and Accessibility Policy (NDSAP), which permits reuse including
commercial reuse. We only ever call api.data.gov.in, which is also the host whose
robots.txt the base class checks.

Dataset shapes vary wildly between publishers -- a state procurement dump and a
ministry tender dump share no column names -- so normalisation maps columns by
alias rather than assuming one schema.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Iterator

import yaml

from ..schemas import TenderRecord
from .base import BaseConnector

log = logging.getLogger(__name__)

RESOURCES_FILE = Path(__file__).resolve().parents[2] / "config" / "data_gov_in_resources.yaml"
PAGE_SIZE = 100
DISCOVERY_KEYWORDS = ("tender", "procurement")

# Canonical field -> substrings we accept as a column name, best match first.
FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "external_ref": ("tender_id", "tender_no", "tender_reference", "bid_number",
                     "nit_no", "reference_no", "ref_no", "work_id", "package_id",
                     "externalreference", "ocid"),
    "title": ("tender_title", "name_of_work", "work_name", "tender_brief", "subject",
              "item_description", "description", "title", "particulars"),
    "organization": ("buyer_name", "organisation", "organization", "psu", "ministry",
                     "office_name", "procuring_entity", "procuringentity", "agency",
                     "buyer"),
    "department": ("department", "dept", "division", "circle"),
    "category": ("tender_category", "mainprocurementcategory", "category",
                 "tender_type", "product_category", "sector", "type_of_work"),
    "estimated_value": ("estimated_value", "tender_value", "estimated_cost",
                        "contract_value", "awarded_value", "tender_amount",
                        "value_amount", "value_of_work", "amount", "cost", "value"),
    "published_date": ("published_date", "publish_date", "date_of_publication",
                       "publication_date", "datepublished", "issue_date",
                       "tender_date"),
    "deadline": ("bid_submission_closing", "closing_date", "last_date", "due_date",
                 "submission_deadline", "end_date", "date_of_closing",
                 "tenderperiod_enddate", "enddate", "duedate", "closingdate"),
    "status": ("tender_status", "status", "stage"),
    "document_url": ("tender_document", "document_url", "document", "url", "link"),
}
# OCDS (Open Contracting Data Standard) publishers -- Assam's datasets, for one --
# use camelCase paths like tender/tenderPeriod/endDate, which normalise to
# "tender_tenderperiod_enddate" with no separator inside the camelCase word. That
# is why several aliases above are run together ("duedate", "datepublished").

_DATE_FORMATS = (
    "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y",
    "%d-%b-%Y %I:%M %p", "%Y-%m-%d %H:%M:%S", "%d-%m-%Y %H:%M", "%m/%d/%Y",
)


def _norm_key(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(key).lower()).strip("_")


def pick_field(raw: dict, canonical: str) -> tuple[str | None, object]:
    """Return (matched_column_name, value) for a canonical field, or (None, None)."""
    normalized = {_norm_key(k): (k, v) for k, v in raw.items()}
    for alias in FIELD_ALIASES.get(canonical, ()):
        if alias in normalized:  # exact match wins
            return normalized[alias]
    for alias in FIELD_ALIASES.get(canonical, ()):
        for nkey, (orig, val) in normalized.items():
            if alias in nkey:
                return orig, val
    return None, None


def parse_date(value) -> datetime | None:
    text = str(value or "").strip()
    if not text or text.lower() in {"na", "n/a", "-", "--", "null", "none"}:
        return None
    # Try the whole string first, then common truncations (trailing time, timezone).
    candidates = list(dict.fromkeys([text, text[:19], text[:11], text[:10]]))
    for fmt in _DATE_FORMATS:
        for candidate in candidates:
            try:
                return datetime.strptime(candidate, fmt)
            except ValueError:
                continue
    return None


def parse_value(column: str | None, value) -> Decimal | None:
    """Parse a money column, honouring lakh/crore units named in the column header.

    Getting the scale wrong turns a 5-lakh tender into a 5-rupee one, so the unit
    is read from the column name rather than assumed.
    """
    text = re.sub(r"[^0-9.\-]", "", str(value or ""))
    if not text or text in {"-", ".", "-."}:
        return None
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    header = _norm_key(column or "")
    if "crore" in header:
        amount *= Decimal(10_000_000)
    elif "lakh" in header or "lac" in header:
        amount *= Decimal(100_000)
    return amount


class DataGovInConnector(BaseConnector):
    source_name = "data.gov.in"
    base_url = "https://api.data.gov.in"
    license = "NDSAP"
    rate_limit_seconds = 2.0
    paths = ("/resource/", "/lists")

    def __init__(self, *args, resource_ids: list[str] | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.api_key = os.getenv("DATA_GOV_IN_API_KEY", "").strip()
        self._resource_ids = resource_ids

    def _params(self, **extra) -> dict:
        if not self.api_key:
            raise RuntimeError(
                "DATA_GOV_IN_API_KEY is not set. Register free at https://data.gov.in "
                "and put the key in .env before running this connector."
            )
        return {"api-key": self.api_key, "format": "json", **extra}

    # ---- resource selection ----

    def configured_resources(self) -> list[str]:
        if not RESOURCES_FILE.exists():
            return []
        data = yaml.safe_load(RESOURCES_FILE.read_text(encoding="utf-8")) or {}
        return [str(r["id"]) for r in (data.get("resources") or []) if r.get("id")]

    def discover_resources(self) -> list[dict]:
        """List catalogue datasets matching our keywords, for a human to review.

        Returns candidates -- it does NOT decide what gets ingested. See
        resource_ids() for why that separation matters.
        """
        found: dict[str, dict] = {}
        for keyword in DISCOVERY_KEYWORDS:
            resp = self.get(
                f"{self.base_url}/lists",
                params=self._params(limit=100, offset=0, **{"filters[title]": keyword}),
            )
            if resp.status_code != 200:
                log.warning(
                    "data.gov.in: catalogue search %r -> HTTP %s", keyword, resp.status_code
                )
                continue
            for rec in (resp.json().get("records") or []):
                rid = rec.get("index_name") or rec.get("resource_id") or rec.get("id")
                if not rid or rid in found:
                    continue
                found[str(rid)] = {
                    "id": str(rid),
                    "title": (rec.get("title") or "").strip(),
                    "org": rec.get("org"),
                    "fields": [f.get("name") or f.get("id")
                               for f in (rec.get("field") or [])],
                    "matched": keyword,
                }
        log.info("data.gov.in: %d candidate datasets found", len(found))
        return list(found.values())

    def resource_ids(self) -> list[str]:
        """Only pinned datasets are ingested. Discovery never feeds this directly.

        Catalogue search is far too imprecise to trust unattended: "tender" matches
        tender coconut market prices, and "procurement" matches paddy bought under
        MSP. A human reads the candidates and pins the real ones.
        """
        if self._resource_ids is not None:
            return self._resource_ids
        pinned = self.configured_resources()
        if not pinned:
            log.warning(
                "data.gov.in: no resources pinned in %s, so nothing will be ingested. "
                "Run `tenders discover-data-gov-in` to list candidates.",
                RESOURCES_FILE.name,
            )
        return pinned

    # ---- fetch ----

    def fetch_batch(self, since: datetime | None) -> Iterator[dict]:
        for resource_id in self.resource_ids():
            offset = 0
            while True:
                resp = self.get(
                    f"{self.base_url}/resource/{resource_id}",
                    params=self._params(offset=offset, limit=PAGE_SIZE),
                )
                if resp.status_code != 200:
                    log.warning(
                        "data.gov.in: resource %s -> HTTP %s, skipping",
                        resource_id, resp.status_code,
                    )
                    break
                payload = resp.json()
                records = payload.get("records") or []
                if not records:
                    break
                for rec in records:
                    rec = dict(rec)
                    rec["_resource_id"] = resource_id
                    rec["_resource_title"] = payload.get("title")
                    yield rec
                offset += len(records)
                total = payload.get("total")
                if len(records) < PAGE_SIZE or (total is not None and offset >= int(total)):
                    break

    # ---- normalize ----

    def normalize(self, raw: dict) -> TenderRecord:
        resource_id = raw.get("_resource_id", "")
        data = {k: v for k, v in raw.items() if not k.startswith("_")}

        _, ref = pick_field(data, "external_ref")
        _, title = pick_field(data, "title")
        _, org = pick_field(data, "organization")
        _, dept = pick_field(data, "department")
        _, category = pick_field(data, "category")
        value_col, value = pick_field(data, "estimated_value")
        _, published = pick_field(data, "published_date")
        _, deadline = pick_field(data, "deadline")
        _, status = pick_field(data, "status")
        _, doc = pick_field(data, "document_url")

        # No id column in this dataset -> derive a stable one from the row itself,
        # so re-runs update the same row instead of inserting duplicates.
        if ref is None or not str(ref).strip():
            digest = hashlib.sha1(
                json.dumps(data, sort_keys=True, default=str).encode()
            ).hexdigest()[:16]
            ref = f"{resource_id}:{digest}"
        else:
            ref = f"{resource_id}:{str(ref).strip()}"

        published_dt = parse_date(published)
        deadline_dt = parse_date(deadline)
        doc_url = str(doc).strip() if doc and str(doc).startswith("http") else None

        return TenderRecord(
            external_ref=ref,
            title=str(title).strip() if title and str(title).strip() else f"Untitled ({ref})",
            organization=str(org).strip() if org else raw.get("_resource_title"),
            department=str(dept).strip() if dept else None,
            category=str(category).strip() if category else None,
            estimated_value=parse_value(value_col, value),
            published_date=published_dt.date() if published_dt else None,
            deadline=deadline_dt.date() if deadline_dt else None,
            status=str(status).strip().lower() if status else None,
            document_url=doc_url,
            source_url=f"https://www.data.gov.in/resource/{resource_id}",
            raw_payload=raw,
        )
