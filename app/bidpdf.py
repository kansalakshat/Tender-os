"""Pull the facts a GeM bid PDF carries that its listing card does not.

The card on /all-bids truncates: 81% of titles arrive as "Some Item, Other It..."
and neither the value nor the EMD appears at all. The bid document has the full
item list, the buying organisation (not just its ministry), the estimated value
and the EMD, so one fetch per tender turns a stub into something worth reading --
and gives dedup a full title to match on, which truncated stubs never reach.

The PDFs interleave Hindi and English glyph-by-glyph, so the extracted text comes
out with stray marks between runs. Everything here works on the ASCII residue
after the Devanagari is dropped, which is why the patterns look tolerant of junk
between a label and its value.
"""
from __future__ import annotations

import io
import logging
import re

log = logging.getLogger(__name__)

# Label -> the value that follows it. Kept deliberately narrow: each stops at the
# next label rather than running on, because the residue between fields is noise.
_STOP = (
    r"Bid Number|Dated|Bid End Date|Bid Opening|Bid Offer Validity|"
    r"Department Name|Organisation Name|Office Name|Item Category|Similar Category|"
    r"Contract Period|Total Quantity|EMD Amount|ePBG|MSE |Estimated Bid Value|"
    r"Splitting|Evaluation Method|Document required|Bid Details|Bid to RA|"
    r"Time allowed|Bid Participation|Past Performance|Startup|Primary product|"
    # Sections that follow the item list. Without these the title swallowed GeM's
    # category-search dump ("GeMARPTS ... Searched Strings used in ...") and the
    # turnover and experience criteria.
    r"GeMARPTS|Minimum Average Annual Turnover|OEM Average Turnover|"
    r"Years of Past Experience|"
    # Rule #7: the document prints the buyer's grievance contacts. Stopping here
    # keeps them out of every field, so no personal data reaches the database.
    r"Contact details|Email id|email id|Grievance"
)
# Belt and braces for rule #7: a value that still looks like a contact is dropped
# whole rather than trimmed, because a half-scrubbed email is still an email.
_CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+|\b\d{10}\b", re.I)
_MONEY = re.compile(r"^[\d,]+(?:\.\d+)?$")
# Glyph noise left where Devanagari was, stripped from the end of a value. Two
# shapes only, and both deliberately conservative: tokens of pure punctuation
# ("% %", ". .", "./") or a doubled number ("3 3 1 1"), and a doubled single
# letter ("W W", "h h"). A lone number is kept: "Water Tight hatch 9" is a value.
#
# It used to strip any trailing run of one- and two-character tokens, which ate
# real values: "Office Of Dg Ns M" became "Office", because every token in the
# name is short. Abbreviated buyer names are common, so short != noise.
_TRAILING_NOISE = re.compile(r"(?:\s+(?:[^A-Za-z0-9\s]{1,3}|(\d{1,2})\s+\1(?!\S)))+\s*$")
_DOUBLED_LETTER = re.compile(r"(?:\s+([A-Za-z])\s+\1\b)+\s*$")


def _clean(text: str) -> str:
    """ASCII residue, with the marks the Devanagari leaves behind removed."""
    ascii_only = re.sub(r"[^\x00-\x7F]+", " ", text)
    # Leftovers look like: "& &", "' '", "W W [ [", "( (". Drop the punctuation
    # runs and the control characters the extractor emits between glyph runs.
    ascii_only = re.sub(r"[&'\"\[\]{}|~^`*_()<>]+", " ", ascii_only)
    ascii_only = "".join(ch if ch >= " " and ch != "\x7f" else " " for ch in ascii_only)
    return re.sub(r"\s+", " ", ascii_only).strip()


def _field(flat: str, label: str) -> str | None:
    m = re.search(
        re.escape(label) + r"\s*[:/]?\s*(.*?)(?=\s*(?:" + _STOP + r")|$)",
        flat,
    )
    if not m:
        return None
    val = m.group(1).strip(" :/-")
    prev = None
    while prev != val:
        prev = val
        val = _DOUBLED_LETTER.sub("", val).strip()
        val = _TRAILING_NOISE.sub("", val).strip()
    if _CONTACT.search(val):
        return None
    return val or None


def _money(value: str | None) -> float | None:
    """GeM writes plain integers; anything wordy is boilerplate, not a number."""
    if not value:
        return None
    m = re.search(r"\b([\d,]{3,})\b", value)
    if not m or not _MONEY.match(m.group(1)):
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_bid_pdf(data: bytes) -> dict:
    """Bid-document bytes -> the fields worth keeping. Missing keys are absent.

    Never raises on a malformed PDF: a bad document must not kill an ingest run,
    it just means this tender keeps the listing's thinner data.
    """
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data))
        raw = "\n".join(page.extract_text() or "" for page in reader.pages)
    except Exception as exc:
        log.warning("bid pdf unreadable: %s", exc)
        return {}

    flat = _clean(raw)
    if not flat:
        return {}

    out: dict[str, object] = {}
    # The full item list -- this is the untruncated title.
    items = _field(flat, "Item Category")
    if items:
        out["item_category"] = items
    for key, label in (
        ("organisation", "Organisation Name"),
        ("office", "Office Name"),
        ("department", "Department Name"),
        ("contract_period", "Contract Period"),
        ("quantity", "Total Quantity"),
    ):
        val = _field(flat, label)
        if val:
            out[key] = val

    # The document's label is "Ministry/State Name". Matching on "Ministry" alone
    # finds it, but _STOP also lists "Ministry", so the lookahead ends the value
    # before it starts -- match the tail of the label instead.
    ministry = _field(flat, "State Name")
    if ministry:
        out["ministry"] = ministry

    emd = _money(_field(flat, "EMD Amount"))
    if emd is not None:
        out["emd_amount"] = emd
    value = _money(_field(flat, "Estimated Bid Value"))
    if value is not None:
        out["estimated_value"] = value
    return out
