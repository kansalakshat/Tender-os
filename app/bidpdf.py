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
from urllib.parse import urlsplit

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
    # Labels that follow the terms read below. The ministry boundary has to be
    # the whole label: the ministry itself is found by searching the tail of it
    # ("State Name"), so a bare "Ministry" here ends that value before it
    # starts -- which is a covered regression, tests/test_bidpdf.py.
    r"Ministry/State Name|Type of Bid|RA Quali|Advisory Bank|Financial Document|"
    r"Arbitration Clause|Mediation Clause|Do you want|Minimum number of bids|"
    r"Number of|Relevant Categories|Searched|Bid Opening Date|"
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


_HINDI_GAP = re.compile(
    r"([ऀ-ॿ][^\s/]*)\s+[^\sA-Za-z/]{1,3}(?=\s+[^\s/]*[ऀ-ॿ])"
)


def _clean(text: str) -> str:
    """ASCII residue, with the marks the Devanagari leaves behind removed."""
    # A Devanagari word goes whole, with anything glued to it: the fonts map
    # some glyphs to ASCII ("मूQयांकन", "'पSीकरण"), and dropping only the
    # non-ASCII characters left those letters behind as fake words. Stops at
    # "/", which is what joins a Hindi label to its English half ("है/MSE").
    # A short number or mark between two Hindi words is part of the Hindi
    # ("टनओ% वर (3 वष2 का)" is "turnover (3 years)"), so it goes with them.
    text = _HINDI_GAP.sub(r"\1", text)
    text = _HINDI_GAP.sub(r"\1", text)      # twice: matches cannot overlap
    text = re.sub(r"[^\s/]*[ऀ-ॿ][^\s/]*", " ", text)
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


def _yes_no(value: str | None) -> str | None:
    """These fields are a yes or a no; everything after it is glyph residue."""
    if not value:
        return None
    m = re.match(r"\s*(Yes|No)\b", value, re.I)
    return m.group(1).capitalize() if m else None


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


# The attachments a bid document points at. They are never in the extracted
# text -- pypdf reads visible glyphs, and these live in /Annots link objects, so
# a text regex finds nothing. Six sampled documents carried 4 to 26 links each.
#
# Host and path, because the same host serves several kinds of file: the label
# is what a bidder needs to decide whether to open it.
_LINK_LABELS = (
    ("mkp.gem.gov.in", "catalog_support_document", "Technical specification"),
    ("fulfilment.gem.gov.in", "slafds", "SLA / annexure"),
    ("admin.gem.gov.in", "gtc", "General terms & conditions"),
    ("bidplus.gem.gov.in", "downloadOmppdfile", "Bid attachment"),
)
# Rule #7 again, and it is not theoretical: one sampled URL was
# ".../ANNEXBK_<uuid>_poa3@dpsdae.gov.in.pdf" -- the buyer's address in the
# filename. A URL carrying one is dropped whole rather than rewritten, because a
# link with the address snipped out no longer resolves anyway.
_URL_CONTACT = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")


def _label_for(url: str) -> str:
    host = urlsplit(url).hostname or ""
    for want_host, marker, label in _LINK_LABELS:
        if host == want_host and marker in url:
            return label
    return host or "Attachment"


def links_from_reader(reader) -> list[dict]:
    """[{label, url}] for the documents this bid points at, in page order.

    Takes an open reader, not bytes: parsing is the expensive part of reading a
    PDF, and this used to build a second PdfReader over the same document, so
    every tender was parsed twice on every pass.

    Deduplicated on the URL: a multi-page document repeats the same terms link
    on every page, and twelve identical rows is not information.
    """
    out: list[dict] = []
    seen: set[str] = set()
    for page in reader.pages:
        try:
            annots = page.get("/Annots") or []
        except Exception:
            continue
        for ref in annots:
            try:
                uri = (ref.get_object().get("/A") or {}).get("/URI")
            except Exception:
                continue
            if not uri or not isinstance(uri, str):
                continue
            uri = uri.strip()
            if not uri.startswith(("http://", "https://")) or uri in seen:
                continue
            if _URL_CONTACT.search(uri):
                continue
            seen.add(uri)
            out.append({"label": _label_for(uri), "url": uri})
    return out


def extract_links(data: bytes) -> list[dict]:
    """Links from raw bytes, for a caller with no reader of its own."""
    try:
        from pypdf import PdfReader

        return links_from_reader(PdfReader(io.BytesIO(data)))
    except Exception as exc:
        log.warning("bid pdf unreadable for links: %s", exc)
        return []


def parse_bid_pdf(data: bytes) -> dict:
    """Bid-document bytes -> the fields worth keeping. Missing keys are absent.

    Never raises on a malformed PDF: a bad document must not kill an ingest run,
    it just means this tender keeps the listing's thinner data.
    """
    try:
        import pypdfium2 as pdfium
        from pypdf import PdfReader

        # Text from pdfium (C), not pypdf: pypdf's extract_text is pure Python
        # and cost seconds of CPU per bid, which capped a backlog pass at a
        # couple of hundred documents a minute across every core. pdfium is
        # ~20x faster on the same documents.
        doc = pdfium.PdfDocument(data)
        try:
            raw = "\n".join(
                doc[i].get_textpage().get_text_range() for i in range(len(doc))
            )
        finally:
            doc.close()
        # Links stay on pypdf: reading annotations parses no content stream,
        # so it is cheap.
        found_links = links_from_reader(PdfReader(io.BytesIO(data)))
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

    # Terms a bidder decides on before reading anything else. All of these are
    # plain labelled fields in the same text; none has a column, so they ride in
    # raw_payload like emd_amount does. Sampling showed EMD and the estimated
    # value are absent from most documents -- GeM simply does not print them for
    # every bid -- so these are often the only hard terms on offer.
    for key, label in (
        ("bid_type", "Type of Bid"),
        # The full label, not a prefix: "Bid Opening Date" alone leaves the
        # "/Time" half of the label at the front of the value.
        ("offer_validity", "Bid Offer Validity From End Date"),
        ("bid_opening", "Bid Opening Date/Time"),
    ):
        val = _field(flat, label)
        if val:
            out[key] = val
    for key, label in (
        ("mse_relaxation", "MSE Relaxation for Turnover"),
        ("startup_relaxation", "Startup Relaxation for Turnover"),
    ):
        val = _yes_no(_field(flat, label))
        if val:
            out[key] = val

    out["links"] = found_links
    return out
