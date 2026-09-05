"""Company profile -> tender matching.

Why this is title-driven rather than category-driven: `tenders.category` and
`tenders.estimated_value` are null on 100% of rows from both live connectors.
CPPP and MP eProcurement publish neither on the listing page (value lives on a
per-tender detail page; category is never published as a field at all). So the
sector a tender belongs to has to be *derived from its title*, and any scoring
that depends on a tender's declared value has to degrade to "unknown does not
disqualify" -- the same rule `dedup.values_match` already follows.

The weights below are calibration knobs, not constants of nature. They were set
against the live MP listing; retune them when the corpus grows.
"""
from __future__ import annotations

import math
import re
from functools import lru_cache
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .models import Tender

# ---- Sector taxonomy -------------------------------------------------------
# Derived from the real mptenders.gov.in listing, not invented. A tender may
# land in more than one sector (a solar EPC contract is both power and civil),
# which is more honest than forcing a single label.

SECTOR_PATTERNS: dict[str, tuple[str, str]] = {
    "electrical_power": (
        "Electrical & power infrastructure",
        r"kv\b|kva\b|mva\b|\bptr\b|\bbay\b|feeder|bifurcat|substation|sub-station|"
        r"\bs/s\b|transformer|electrificat|transmission|\bht\b|\blt\b|switchgear|"
        r"solar|power\s+(?:project|plant|supply)|electric",
    ),
    "vehicle_hire": (
        "Vehicle hiring & transport",
        # No \bbus\b: in MP power tenders "BUS" is a busbar, and "Bus Stand" is a
        # locality being electrified. Neither is a vehicle contract.
        r"vehicle|bolero|\bjeep\b|pickup|sedan|\bcar\b|\btaxi\b|tata\s*407|"
        r"light\s+motor|transport",
    ),
    "civil_construction": (
        "Civil construction & roads",
        r"construction|\broad\b|paver|building|civil|bridge|culvert|drain|"
        r"boundary\s+wall|renovat|\brcc\b|earthwork|\bepc\b",
    ),
    "medical_pharma": (
        "Medical & pharmaceutical",
        r"medicine|medical|\bdrug|surgical|hospital|pharma|patholog|diagnostic|"
        r"x-?ray|ambulance",
    ),
    "it_services": (
        "IT, software & electronics",
        r"computer|software|laptop|printer|network|cctv|server|\bit\b|hardware|"
        r"scanner|\bups\b|website|digital",
    ),
    "office_supplies": (
        "Office supplies & furniture",
        r"station[ae]ry|furniture|watercooler|water\s+cooler|\bro\b|printing|"
        r"photocop|\bdesk\b|chair|cupboard",
    ),
    "maintenance_amc": (
        "Maintenance, AMC & servicing",
        r"\bamc\b|\bcamc\b|servicing|maintenance|\brepair|overhaul|upkeep",
    ),
    "agriculture": (
        "Agriculture & allied inputs",
        # No "irrigation": on this portal it nearly always means an AG (pump)
        # feeder, i.e. an electrical job. Real canal work still hits civil.
        r"pesticide|insecticide|fertili[sz]er|\bseed\b|chlorpyrifos|agricultur|"
        r"\bcrop\b|manure",
    ),
    "industrial_supply": (
        "Industrial supplies & spares",
        r"consumable|spare|flame\s+arrestor|steam\s+seal|\bvalve\b|\bpump\b|"
        r"bearing|boiler|turbine|\bpipe\b|compressor|lubricant",
    ),
    "manpower_security": (
        "Manpower, security & housekeeping",
        r"manpower|security|\bguard\b|housekeeping|outsourc|\blabour\b|"
        r"sanitation|cleaning|deployment\s+of",
    ),
    "scrap_auction": (
        "Scrap & asset disposal",
        r"\bscrap\b|auction|disposal|condemn",
    ),
}

_COMPILED = {
    key: (label, re.compile(pat, re.I)) for key, (label, pat) in SECTOR_PATTERNS.items()
}

SECTOR_LABELS = {key: label for key, (label, _) in SECTOR_PATTERNS.items()}

# Districts, for the "where do you operate" question. Matched against the
# organisation/department text, which is where GePNIC actually puts the place
# ("District Excise Office Gwalior", "Chief Engineer(R and M)-Jabalpur").
MP_DISTRICTS = (
    "Bhopal", "Indore", "Jabalpur", "Gwalior", "Ujjain", "Sagar", "Rewa", "Satna",
    "Ratlam", "Dewas", "Dhar", "Khargone", "Khandwa", "Chhindwara", "Sehore",
    "Vidisha", "Neemuch", "Mandsaur", "Shivpuri", "Guna", "Katni", "Singrauli",
    "Balaghat", "Betul", "Morena", "Bhind", "Datia", "Shahdol", "Chhatarpur",
    "Damoh", "Tikamgarh", "Seoni", "Narsinghpur", "Harda",
)

# States/UTs. MP_DISTRICTS alone was dead weight on the national corpus: 95% of
# rows come from CPPP, whose buyers are ministries that never name an MP district.
# Measured before this was added: 1 of 488 matches earned a district bonus.
STATES = (
    "Andhra Pradesh", "Arunachal Pradesh", "Assam", "Bihar", "Chhattisgarh", "Goa",
    "Gujarat", "Haryana", "Himachal Pradesh", "Jharkhand", "Karnataka", "Kerala",
    "Madhya Pradesh", "Maharashtra", "Manipur", "Meghalaya", "Mizoram", "Nagaland",
    "Odisha", "Punjab", "Rajasthan", "Sikkim", "Tamil Nadu", "Telangana", "Tripura",
    "Uttar Pradesh", "Uttarakhand", "West Bengal", "Delhi", "Jammu and Kashmir",
    "Ladakh", "Puducherry", "Chandigarh", "Andaman and Nicobar",
)

# ---- Weights (tune these, not the logic) -----------------------------------
# A flat "sector matched" bonus made the score a binary flag with noise on top:
# measured on the live corpus, 397 of 488 matches (81%) landed in one 10-point
# bucket. So the sector award is now split -- a base for matching at all, plus a
# strength term for *how emphatically* the title matches, which spreads rows that
# previously tied.
SECTOR_BASE = 18            # any sector overlap at all
SECTOR_INFORM = 32          # ...plus this much, scaled by how informative the hit was
SECTOR_EXTRA = 6            # per additional overlapping sector
SECTOR_EXTRA_CAP = 12
KEYWORD_WEIGHT = 12         # per distinct company keyword found in the title
KEYWORD_CAP = 36
GEO_WEIGHT = 12             # district or state named in the buyer/title text
BUYER_WEIGHT = 14           # a buyer the company said it wants to work with
DEFAULT_MIN_LEAD_DAYS = 7

# Lead time is deliberately NOT scored. It was worth up to 10 points for closing
# *later*, which is backwards -- the tender closing soonest is the one you must act
# on first -- and it added 10 points of noise to every row. It is the sort
# tiebreaker in find_matches instead, and still reported as a reason.


def derive_sectors(*texts: str | None) -> set[str]:
    """Which sectors a tender belongs to, read off its title/org/department."""
    blob = " ".join(t for t in texts if t)
    if not blob.strip():
        return set()
    return {key for key, (_, pat) in _COMPILED.items() if pat.search(blob)}


@lru_cache(maxsize=8192)
def sector_terms(sector: str, title: str | None) -> frozenset[str]:
    """The distinct terms of a sector's pattern that the title actually hit.

    Memoised, and returns a frozenset so a caller cannot corrupt the cache: within
    one search each (sector, title) is asked for twice -- once to build the corpus
    term frequencies, once to score and explain the row -- and the regex is the
    single most expensive thing in the request.
    """
    return frozenset(
        m.lower().strip() for m in _COMPILED[sector][1].findall(title or "") if m.strip()
    )


def build_idf(rows, sectors, keywords=()) -> dict[str, float]:
    """How informative each matched term is, over the candidate set.

    Measured on the live corpus: 375 of 449 sector matches hit exactly one term,
    and that term was nearly always "maintenance" or "repair" -- words that appear
    in a large share of all government tenders regardless of trade. Counting them
    the same as "chlorpyrifos" is what collapsed 82% of results into one bucket.

    Standard inverse document frequency, normalised to 0..1: a term matching almost
    every candidate scores ~0, a term matching one scores ~1.
    """
    df: dict[str, int] = {}
    for row in rows:
        seen: set[str] = set()
        for sec in sectors:
            seen |= sector_terms(sec, row.title)
        # The company's own keywords are measured the same way, or a keyword that
        # matches half the corpus would keep its full weight by simply being absent
        # from the table.
        seen.update(_keyword_hits(keywords, row.title))
        for term in seen:
            df[term] = df.get(term, 0) + 1
    n = max(len(rows), 1)
    ceiling = math.log(n + 1)
    if ceiling <= 0:
        return {t: 1.0 for t in df}
    return {t: min(1.0, math.log((n + 1) / c) / ceiling) for t, c in df.items()}


def term_informativeness(terms, idf) -> float:
    """1.0 when we have no corpus to compare against -- a lone tender is scored on
    its own terms, which is what match_score's standalone callers expect."""
    if not terms:
        return 0.0
    if not idf:
        return 1.0
    return max(idf.get(t, 1.0) for t in terms)


def derive_states(*texts: str | None) -> set[str]:
    blob = " ".join(t for t in texts if t)
    return {s for s in STATES if re.search(rf"\b{re.escape(s)}\b", blob, re.I)}


def _buyer_hits(wanted, *texts: str | None) -> list[str]:
    """Buyer names match as case-insensitive substrings, not word-bounded: the
    questionnaire offers the exact strings out of `tenders.organization`, and
    "Ministry of Railways" must still match inside a longer department chain.
    """
    blob = " ".join(t for t in texts if t).lower()
    return sorted({b for b in (wanted or []) if (b or "").strip() and b.strip().lower() in blob})


def derive_districts(*texts: str | None) -> set[str]:
    """Word-bounded: bare containment would find 'Dhar' inside 'Dhariwal'."""
    blob = " ".join(t for t in texts if t)
    return {
        d for d in MP_DISTRICTS
        if re.search(rf"\b{re.escape(d)}\b", blob, re.I)
    }


def _keyword_hits(keywords, title: str) -> list[str]:
    """Whole-word-ish containment. Plain substring would match 'ac' in 'contract'."""
    low = (title or "").lower()
    hits = []
    for kw in keywords or []:
        kw = (kw or "").strip().lower()
        if not kw:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", low):
            hits.append(kw)
    return hits


def match_score(
    profile, tender, today: date | None = None, idf: dict[str, float] | None = None
) -> tuple[int, list[str]] | None:
    """Score one tender against one company profile.

    Returns (score 0-100, human-readable reasons), or None when the tender is
    disqualified outright. `profile` is duck-typed: anything with sectors,
    keywords, districts, min_lead_days and max_project_value works, so both the
    Company ORM row and the CompanyIn pydantic model can be passed directly.
    """
    today = today or date.today()
    lead = profile.min_lead_days
    if lead is None:
        lead = DEFAULT_MIN_LEAD_DAYS

    # --- Hard filters: things you cannot bid on are not "weak matches". ---
    if tender.deadline is None:
        return None                      # historical/archival rows, not biddable
    days_left = (tender.deadline - today).days
    if days_left < lead:
        return None                      # already closed, or too soon to prepare
    if tender.status is not None and tender.status != "open":
        return None
    cap = getattr(profile, "max_project_value", None)
    if cap is not None and tender.estimated_value is not None:
        if Decimal(tender.estimated_value) > Decimal(cap):
            return None                  # beyond stated execution capacity
    # A null tender value never disqualifies -- see module docstring.

    # Exclusions are hard, not a penalty. "Never show me railway work" has to mean
    # never: on the live corpus one buyer (Ministry of Railways) is 58% of all rows,
    # so a company that does not do railways is otherwise reading mostly noise.
    if _keyword_hits(getattr(profile, "exclude_keywords", None), tender.title):
        return None
    if _buyer_hits(
        getattr(profile, "exclude_buyers", None), tender.organization, tender.department
    ):
        return None

    # --- Soft scoring ---
    score = 0
    reasons: list[str] = []

    # Title only, deliberately. The department says who is buying, not what is
    # being bought -- classifying by it makes a medical college's scrap auction
    # look like a pharma contract.
    # Only the company's own sector patterns are tested. derive_sectors() runs all
    # 11 and then throws away the ones the company does not work in -- for a
    # single-sector profile that is eleven regexes per row to use one.
    overlap = {
        sec for sec in (profile.sectors or [])
        if sec in _COMPILED and _COMPILED[sec][1].search(tender.title or "")
    }
    if overlap:
        # Per sector, not pooled: 'switchgear' explains an electrical match and must
        # not be offered as the reason a maintenance sector matched as well.
        per_sector = {sec: sector_terms(sec, tender.title) for sec in overlap}
        all_terms = frozenset().union(*per_sector.values()) if per_sector else frozenset()
        score += (
            SECTOR_BASE
            + round(SECTOR_INFORM * term_informativeness(all_terms, idf))
            + min(SECTOR_EXTRA * (len(overlap) - 1), SECTOR_EXTRA_CAP)
        )
        # Naming the term that drove the match is what lets a bidder tell a real
        # electrical job from a building repair that merely says "maintenance".
        for sec in sorted(overlap):
            terms = per_sector[sec]
            driver = max(terms, key=lambda t: (idf or {}).get(t, 1.0)) if terms else None
            reasons.append(
                f"sector: {SECTOR_LABELS[sec]}" + (f" (via '{driver}')" if driver else "")
            )

    hits = _keyword_hits(getattr(profile, "keywords", None), tender.title)
    if hits:
        # A keyword the company chose is weighted the same way: matching "cable"
        # across 2,000 tenders says less than matching "chlorpyrifos" across three.
        weight = sum(KEYWORD_WEIGHT * term_informativeness({h}, idf) for h in hits)
        score += round(min(weight, KEYWORD_CAP))
        reasons.append("keywords: " + ", ".join(sorted(hits)))

    # Sector or keyword is mandatory. Without one, every company sees every
    # tender and the whole thing is a listing page with extra steps.
    if not overlap and not hits:
        return None

    # Geography is one signal, awarded once: a district hit and its state hit are
    # the same fact stated twice, and paying for both double-counts it.
    places = []
    where = (tender.organization, tender.department, tender.title)
    if profile.districts:
        places += sorted(derive_districts(*where) & set(profile.districts))
    if getattr(profile, "states", None):
        places += sorted(derive_states(*where) & set(profile.states))
    if places:
        score += GEO_WEIGHT
        reasons.append("location: " + ", ".join(places))

    buyers = _buyer_hits(
        getattr(profile, "buyers", None), tender.organization, tender.department
    )
    if buyers:
        score += BUYER_WEIGHT
        reasons.append("buyer: " + ", ".join(buyers))

    # Reported, never scored -- see the note by the weights.
    reasons.append(f"closes in {days_left} day{'s' if days_left != 1 else ''}")

    return min(score, 100), reasons


def find_matches(
    db: Session,
    profile,
    limit: int = 50,
    today: date | None = None,
) -> list[tuple[int, list[str], Tender]]:
    """Rank open tenders for one profile, best first.

    The cheap, index-friendly filters (deadline, duplicate, status) run in SQL so
    Python only scores tenders that are actually biddable.

    ponytail: two linear scans with a regex per sector per row, and the term
    frequencies are recomputed per search rather than cached. Measured at ~8k rows
    it is well under a second; caching the document frequencies on the `tenders`
    table is the upgrade path if the corpus outgrows that.
    """
    today = today or date.today()
    lead = profile.min_lead_days
    if lead is None:
        lead = DEFAULT_MIN_LEAD_DAYS

    rows = db.execute(
        select(Tender)
        .where(Tender.duplicate_of.is_(None))
        .where(Tender.deadline.is_not(None))
        .where(Tender.deadline >= today + timedelta(days=lead))
        .where(or_(Tender.status.is_(None), Tender.status == "open"))
    ).scalars()

    rows = list(rows)
    # One extra pass over the candidates to learn which terms are common here. It
    # costs the same regexes the scoring pass runs anyway and is what stops a
    # near-universal word like "maintenance" from scoring like a rare one.
    idf = build_idf(rows, profile.sectors or [], getattr(profile, "keywords", None) or [])

    scored = []
    for row in rows:
        result = match_score(profile, row, today=today, idf=idf)
        if result is not None:
            scored.append((result[0], result[1], row))

    # Sort by score, then by soonest deadline: among equally good matches the
    # one closing first is the one you need to act on first.
    scored.sort(key=lambda s: (-s[0], s[2].deadline, s[2].id))
    return scored[:limit]
