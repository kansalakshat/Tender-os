"""Which state and city a tender is in, and the SQL that filters by them.

There is no state column: no listing publishes one. A state portal's own
tenders are that state's; anything else is placed by the states and districts
its texts name -- buyer, department, title, and the office and organisation
chain some portals add. The display (place_of) and the filter (state_clause,
city_clause) read the same texts for the same names, so a tender filed under a
state on its card is also found by that state's filter.

ponytail: the filter is a regex over five texts, a sequential scan per search.
Fine at tens of thousands of rows; a stored, indexed state column set at ingest
is the upgrade if /tenders?state= gets slow.
"""
from __future__ import annotations

import re
from functools import lru_cache

from sqlalchemy import or_

from .districts import DISTRICTS
from .facts import clean, source_name
from .matching import SECTOR_PATTERNS, STATES, district_names
from .models import Tender

# GePNIC portals that serve one state each (app/connectors/gepnic.py). CPPP,
# GeM and Defence Procurement are national and place nothing; DNH is not a
# state in matching.STATES.
PORTAL_STATE = {
    "MP eProcurement": "Madhya Pradesh", "HP eProcurement": "Himachal Pradesh",
    "Rajasthan eProcurement": "Rajasthan", "WB eProcurement": "West Bengal",
    "TN eProcurement": "Tamil Nadu", "Kerala eProcurement": "Kerala",
    "Assam eProcurement": "Assam", "Haryana eProcurement": "Haryana",
    "Punjab eProcurement": "Punjab", "UP eProcurement": "Uttar Pradesh",
    "Uttarakhand eProcurement": "Uttarakhand", "Manipur eProcurement": "Manipur",
    "Tripura eProcurement": "Tripura", "Arunachal eProcurement": "Arunachal Pradesh",
    "Odisha eProcurement": "Odisha", "Jharkhand eProcurement": "Jharkhand",
    "Chandigarh eProcurement": "Chandigarh", "Delhi eProcurement": "Delhi",
}

# District names that are also everyday words in tender text ("Mon" in dates,
# "Mandi" as in Krishi Upaj Mandi, "Krishna" in a person's name).
# ponytail: hand-kept; add to it when a wrong state shows on a card.
_DISTRICT_SKIP = {"mon", "mandi", "dang", "mau", "krishna", "tapi", "ntr", "una"}

# Which states each district name belongs to.
_owners: dict[str, set[str]] = {}
for _st, _ds in DISTRICTS.items():
    for _d in _ds:
        for _n in district_names(_d):
            _owners.setdefault(_n.lower(), set()).add(_st)
# Every usable place name -> (state, district or None). Left out: the skip list,
# and names two states share (Bilaspur, Hamirpur, Pratapgarh, Balrampur) -- a
# guess between them would be wrong half the time.
_PLACE: dict[str, tuple[str, str | None]] = {
    _n.lower(): (_st, _d)
    for _st, _ds in DISTRICTS.items() for _d in _ds for _n in district_names(_d)
    if len(_owners[_n.lower()]) == 1 and _n.lower() not in _DISTRICT_SKIP
}
_PLACE.update({_st.lower(): (_st, None) for _st in STATES})
del _owners
# Longest first, so "West Godavari" wins over a shorter name inside it.
_ANY = re.compile(r"\b(" + "|".join(
    re.escape(n) for n in sorted(_PLACE, key=len, reverse=True)) + r")\b", re.I)


def _texts(t: Tender) -> tuple:
    raw = t.raw_payload if isinstance(t.raw_payload, dict) else {}
    # The buyer names its place more reliably than a title does.
    return (t.organization, t.department, clean(raw.get("office")),
            clean(raw.get("organisation_chain")), t.title)


def _trie_pattern(words) -> str:
    """One regex for many words, shared prefixes factored out ("West Godavari"
    and "West Bengal" share "West "), so the engine does not try 800 names one
    by one at every letter. Longer words are still preferred: a word that is
    also a prefix of a longer one is made optional, and regex is greedy."""
    trie: dict = {}
    for w in words:
        node = trie
        for ch in w:
            node = node.setdefault(ch, {})
        node[""] = True
    def build(node) -> str:
        end = node.get("") is True
        alts = [re.escape(ch) + build(sub) for ch, sub in sorted(node.items()) if ch]
        if not alts:
            return ""
        body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
        return "(?:" + body + ")?" if end else body
    return build(trie)


# The same names as _ANY, as a prefix tree, for counting every open tender's
# states in Python (states_named). _ANY stays the reference it is tested against.
_ANY_FAST = re.compile(r"\b(" + _trie_pattern(_PLACE) + r")\b", re.I)


@lru_cache(maxsize=50_000)
def states_named(text: str | None) -> frozenset[str]:
    """Every state a piece of text names, outright or through one of its
    districts: the same names state_clause() matches, read in Python. Cached:
    buyer, department and office names repeat across thousands of tenders."""
    return frozenset(_PLACE[m.group(1).lower()][0] for m in _ANY_FAST.finditer(text or ""))


def place_of(t: Tender) -> tuple[str, str]:
    """(state, city) for a tender; either may be ''.

    The portal's state wins. Otherwise a state named outright, then the state of
    a district named. Only one answer is given: a tender naming two states (a
    road between them, a transfer from one to the other) is put in neither. The
    city is the first named district of that state.
    """
    hits = [_PLACE[m.group(1).lower()] for text in _texts(t) for m in _ANY.finditer(text or "")]
    state = PORTAL_STATE.get(source_name(t), "")
    if not state:
        named = {st for st, d in hits if d is None} or {st for st, _d in hits}
        state = next(iter(named)) if len(named) == 1 else ""
    city = next((d for st, d in hits if d and st == state), "")
    return state, city


def _word_re(names, dialect: str) -> str:
    # Postgres spells a word boundary \y; \b there is a backspace.
    b = r"\y" if dialect == "postgresql" else r"\b"
    return b + "(" + "|".join(re.escape(n) for n in names) + ")" + b


def _icase(col, pattern: str, dialect: str):
    """Case-insensitive regex match. Postgres takes the flag (~*); SQLite's
    REGEXP ignores flags= in practice, so the pattern carries (?i) itself."""
    if dialect == "postgresql":
        return col.regexp_match(pattern, flags="i")
    return col.regexp_match("(?i)" + pattern)


def _texts_match(pattern: str, dialect: str):
    cols = (Tender.title, Tender.organization, Tender.department,
            Tender.raw_payload["office"].as_string(),
            Tender.raw_payload["organisation_chain"].as_string())
    return or_(*(_icase(col, pattern, dialect) for col in cols))


def _names(state: str) -> list[str]:
    return [state] + [n for n, (st, d) in _PLACE.items() if st == state and d]


def state_clause(state: str, source_ids: list[int], dialect: str):
    """Tenders in a state: its own portal's, or naming it or one of its districts.
    A tender naming two states is found under both, though its card shows neither."""
    text = _texts_match(_word_re(_names(state), dialect), dialect)
    return or_(Tender.source_id.in_(source_ids), text) if source_ids else text


def city_clause(district: str, dialect: str):
    return _texts_match(_word_re(district_names(district), dialect), dialect)


def sector_clause(sector: str, dialect: str):
    """Tenders whose title is in a sector -- the same patterns matching uses."""
    pattern = SECTOR_PATTERNS[sector][1]
    if dialect == "postgresql":
        pattern = pattern.replace(r"\b", r"\y")
    return _icase(Tender.title, pattern, dialect)


if __name__ == "__main__":
    t = Tender(title="Balance work for construction of civil works at 132 KV GSS Katrathal, Sikar",
               organization="Rajasthan Rajya Vidyut Prasaran Nigam Limited")
    assert place_of(t) == ("Rajasthan", "Sikar"), place_of(t)
    t = Tender(title="Supply of shirts", organization="District Excise Office Gwalior")
    assert place_of(t) == ("Madhya Pradesh", "Gwalior"), place_of(t)
    assert place_of(Tender(title="Repair of pumps", organization="Ministry of Railways")) == ("", "")
    # a road between two states belongs to neither
    assert place_of(Tender(title="Four-laning from Rajasthan border to Gujarat border")) == ("", "")
    # everyday words and names two states share place nothing
    assert place_of(Tender(title="Shed at Krishi Upaj Mandi", organization="Mandi Board")) == ("", "")
    assert place_of(Tender(title="Water supply scheme, Bilaspur")) == ("", "")
    # GeM puts the buying office in the raw payload
    t = Tender(title="Office chairs", organization="Department of Health",
               raw_payload={"office": "CMHO Office Jabalpur"})
    assert place_of(t) == ("Madhya Pradesh", "Jabalpur"), place_of(t)
    assert _word_re(["Kota"], "postgresql") == r"\y(Kota)\y"
    print("ok")
