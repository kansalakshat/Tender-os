"""Every fact we hold about one tender, as (label, value, mono) rows.

The tender page shows all of them; list rows show preview_facts(), the same
rows minus what the row's own meta lines already print. One builder for both,
and for the JSON rows browse.js and profile.js draw, so the three cannot drift.
"""
from __future__ import annotations

from datetime import date

from sqlalchemy.orm import object_session

from .matching import derive_districts, derive_states
from .models import Source, Tender


def money(value) -> str:
    """Rupees, in the units an Indian bidder actually reads them in."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return ""
    if n >= 1e7:
        return f"Rs {n / 1e7:.2f} crore".replace(".00 ", " ")
    if n >= 1e5:
        return f"Rs {n / 1e5:.2f} lakh".replace(".00 ", " ")
    return f"Rs {n:,.0f}"


def clean(v) -> str:
    """The listing prints an em-dash placeholder in empty cells."""
    v = str(v or "").strip()
    return "" if v in {"", "--", "-", "NA", "N/A"} else v


def source_name(t: Tender) -> str:
    db = object_session(t)
    src = db.get(Source, t.source_id) if db is not None and t.source_id else None
    return src.name if src else ""


def tender_facts(t: Tender, today: date | None = None) -> list[tuple[str, str, bool]]:
    """(label, value, mono). A fact with no value is dropped by whoever prints it,
    so an un-enriched tender simply shows fewer rows."""
    today = today or date.today()
    # The connector keeps the whole scraped listing row, so the closing *time*,
    # the bid-opening date, the corrigendum flag and the buyer's own reference
    # number are already here -- they just never had a column of their own.
    raw = t.raw_payload if isinstance(t.raw_payload, dict) else {}
    places = sorted(derive_states(t.title) | derive_districts(t.title))
    window = (t.deadline - t.published_date).days if t.published_date and t.deadline else None
    # The stored status is set at ingest and goes stale once the deadline passes.
    status = "closed" if t.deadline and t.deadline < today else (t.status or "")
    qty = str(raw.get("quantity", "")).strip()
    # GePNIC prints the buyer as "Org||Department||Office"; one part is just the buyer.
    chain = [p.strip() for p in clean(raw.get("organisation_chain")).split("||") if p.strip()]
    return [
        ("Tender ID", t.external_ref or "", True),
        ("Buyer's reference", clean(raw.get("reference_no")), True),
        ("Buyer", t.organization or "unnamed buyer", False),
        ("Department", t.department or "", False),
        ("Organisation chain", " > ".join(chain) if len(chain) > 1 else "", False),
        # The listing carries a closing *time*, which the deadline column drops.
        # CPPP and GePNIC call it "closing", GeM "end".
        ("Bids close", (clean(raw.get("closing")) or clean(raw.get("end")) or str(t.deadline))
         if t.deadline else "not stated", True),
        ("Bids opened", clean(raw.get("opening")), True),
        ("Published", clean(raw.get("published")) or clean(raw.get("start"))
         or str(t.published_date or "not stated"), True),
        ("Bidding window",
         f"{window} day{'s' if window != 1 else ''}" if window is not None else "", True),
        ("Status", status, False),
        ("Corrigendum", clean(raw.get("corrigendum")), False),
        ("Where", ", ".join(places), False),
        ("Estimated value", money(t.estimated_value) if t.estimated_value is not None
         else "not published on the listing", True),
        # EMD, contract period and office are read off the bid document by
        # app/enrich.py; no listing publishes them.
        ("EMD (bid security)", money(raw.get("emd_amount")) if raw.get("emd_amount") else "",
         True),
        ("Quantity", f"{int(qty):,}" if qty.isdigit() and int(qty) > 1 else "", True),
        ("Contract period", clean(raw.get("contract_period")), False),
        ("Buying office", clean(raw.get("office")), False),
        ("Ministry", clean(raw.get("ministry")), False),
        ("Source", source_name(t) or "unknown", False),
        ("Listing position", clean(raw.get("serial")).rstrip("."), True),
        ("First collected",
         t.first_seen_at.strftime("%d %b %Y") if t.first_seen_at else "", True),
        ("Last updated",
         t.last_updated_at.strftime("%d %b %Y") if t.last_updated_at else "", True),
        ("Portal record no.", clean(raw.get("internal_id")), True),
    ]


# Already on a row's meta lines (buyer, closing date, ID, published, department),
# or bookkeeping that means nothing to a bidder skimming a list.
_NOT_IN_PREVIEW = {
    "Tender ID", "Buyer", "Department", "Published", "Status", "Listing position",
    "First collected", "Last updated", "Portal record no.",
}


def preview_facts(t: Tender, today: date | None = None) -> list[tuple[str, str]]:
    """The facts a list row shows under its meta lines: everything known, nothing
    repeated, and no "not stated" placeholders."""
    return [
        (label, value) for label, value, _ in tender_facts(t, today)
        if value and label not in _NOT_IN_PREVIEW and not value.startswith("not ")
        and value != "unknown"
    ]


if __name__ == "__main__":
    t = Tender(
        external_ref="GEM/2026/B/1", title="Mens Casual Shirt for Bhopal", source_url="u",
        organization="Odisha", deadline=date(2026, 9, 20), published_date=date(2026, 9, 5),
        raw_payload={"end": "20-09-2026 3:00 PM", "quantity": "4960",
                     "emd_amount": 37200.0, "contract_period": "3 Months",
                     "organisation_chain": "A||B",
                     "corrigendum": "--"},
    )
    got = dict(preview_facts(t, date(2026, 9, 16)))
    assert got["EMD (bid security)"] == "Rs 37,200", got
    assert got["Quantity"] == "4,960"
    assert got["Bids close"] == "20-09-2026 3:00 PM"
    assert got["Bidding window"] == "15 days"
    assert got["Organisation chain"] == "A > B"
    assert "Corrigendum" not in got and "Estimated value" not in got and "Buyer" not in got
    assert money(1.5e7) == "Rs 1.50 crore" and money(2e5) == "Rs 2 lakh"
    print("ok")
