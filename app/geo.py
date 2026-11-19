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

from datetime import datetime

from sqlalchemy import case, func, or_

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


# How re.IGNORECASE compares two letters, taken from the regex engine itself:
# its own one-letter lowercase (so "İ" is i, where str.lower() makes two
# letters), plus the extra pairs it treats as equal ("ı" and i, "ſ" and s).
# _walk uses these so it finds exactly what the regex finds.
try:
    from _sre import unicode_tolower as _sre_lower
except ImportError:                                  # not CPython: close enough
    def _sre_lower(c: int) -> int:
        low = chr(c).lower()
        return ord(low) if len(low) == 1 else c
try:
    from re._casefix import _EXTRA_CASES
except ImportError:
    _EXTRA_CASES = {}


@lru_cache(maxsize=4096)
def _letters(ch: str) -> tuple[str, ...]:
    """Every trie letter `ch` can stand for under re.IGNORECASE."""
    low = _sre_lower(ord(ch))
    return (chr(low),) + tuple(chr(c) for c in _EXTRA_CASES.get(low, ()))


def _build_trie(words) -> dict:
    trie: dict = {}
    for w in words:
        node = trie
        for ch in w:
            node = node.setdefault(chr(_sre_lower(ord(ch))), {})
        node[""] = w                        # the place key that ends here
    return trie


def _trie_pattern(trie: dict) -> str:
    """One regex for many words, shared prefixes factored out ("West Godavari"
    and "West Bengal" share "West "), so the engine does not try 800 names one
    by one at every letter. A word that is also a prefix of a longer one is
    made optional, and regex is greedy, so the longer one is tried first."""
    end = "" in trie
    alts = [re.escape(ch) + _trie_pattern(sub) for ch, sub in sorted(trie.items()) if ch]
    if not alts:
        return ""
    body = alts[0] if len(alts) == 1 else "(?:" + "|".join(alts) + ")"
    return "(?:" + body + ")?" if end else body


# Every place name as a prefix tree, and one regex built from it. The regex only
# finds where names are; _walk then reads every name in each spot, as SQL does.
_TRIE = _build_trie(_PLACE)
# Stands in for a NULL last_updated_at when comparing it (_store_states).
_EPOCH = datetime(1970, 1, 1)
_ANY_FAST = re.compile(r"\b(" + _trie_pattern(_TRIE) + r")\b", re.I)


def _is_word(text: str, i: int) -> bool:
    return 0 <= i < len(text) and (text[i].isalnum() or text[i] == "_")


def _walk(text: str, start: int):
    """Every place key spelled from `start` that ends on a word boundary, the
    shorter ones too ("Lakhimpur" inside "Lakhimpur Kheri"). Letters compare as
    the regex's own re.IGNORECASE compares them (_letters)."""
    node, i = _TRIE, start
    while i < len(text):
        nxt = None
        for ch in _letters(text[i]):
            nxt = node.get(ch)
            if nxt is not None:
                break
        if nxt is None:
            return
        node, i = nxt, i + 1
        if "" in node and _is_word(text, i - 1) != _is_word(text, i):
            yield node[""]


@lru_cache(maxsize=50_000)
def states_named(text: str | None) -> frozenset[str]:
    """Every state a piece of text names, outright or through one of its
    districts: what state_clause() finds, state by state, read in Python.

    The regex finds each spot that holds a name; inside that spot every word
    start is walked through the tree, so a shorter or overlapping name counts
    too, exactly as each state's own SQL search would find it. Cached: buyer,
    department and office names repeat across thousands of tenders."""
    text = text or ""
    found = set()
    for m in _ANY_FAST.finditer(text):
        for p in range(m.start(), m.end()):
            if _is_word(text, p) != _is_word(text, p - 1):     # a word boundary
                found.update(_PLACE[k][0] for k in _walk(text, p))
    return frozenset(found)


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


def tender_states(source: str | None, title, organization, department, office, chain) -> str:
    """The states a tender is in, as stored in tenders.states: its own state
    portal's, plus every state or district its text names -- the rule
    state_clause() has always used, worked out once instead of per query."""
    found = set(states_named_all(title, organization, department, office, chain))
    if PORTAL_STATE.get(source or ""):
        found.add(PORTAL_STATE[source])
    return "|" + "".join(st + "|" for st in sorted(found))


def states_named_all(*texts) -> frozenset[str]:
    out: set[str] = set()
    for x in texts:
        if x:
            out |= states_named(str(x))
    return frozenset(out)


def states_of(t: Tender, source: str | None) -> str:
    """tender_states() for a row: the same five texts state_clause() reads."""
    raw = t.raw_payload if isinstance(t.raw_payload, dict) else {}
    return tender_states(source, t.title, t.organization, t.department,
                         raw.get("office"), raw.get("organisation_chain"))


def state_clause(state: str, source_ids: list[int], dialect: str):
    """Tenders in a state: its own portal's, or naming it or one of its districts.
    A tender naming two states is found under both, though its card shows neither.

    A row whose states are stored is a plain lookup in that short column. Only
    a row not worked out yet (states NULL: an old closed tender, or one saved by
    a fetcher still on older code) falls back to the regex over its texts. CASE,
    so the database never runs the regex on a row that has the answer stored."""
    text = _texts_match(_word_re(_names(state), dialect), dialect)
    old = or_(Tender.source_id.in_(source_ids), text) if source_ids else text
    return case((Tender.states.is_not(None), Tender.states.like(f"%|{state}|%")), else_=old)


def _store_states(db, rows, only_null: bool) -> int:
    """Write computed states, but only where the row is still as it was read:
    the same last_updated_at (enrichment and the fetcher move it when they
    change a row), and in fill mode still NULL. So a save that lands between
    our SELECT and this UPDATE is never overwritten with older membership.
    rows: (id, states, last_updated_at as read). Returns rows written."""
    from sqlalchemy import bindparam, update
    if not rows:
        return 0
    # The table, not the ORM class: with a list of rows the ORM switches to its
    # "update by primary key" mode, which drops these WHERE conditions.
    tbl = Tender.__table__
    stmt = (update(tbl)
            .where(tbl.c.id == bindparam("b_id"),
                   func.coalesce(tbl.c.last_updated_at, _EPOCH) == bindparam("b_seen"))
            .values(states=bindparam("b_states")))
    if only_null:
        stmt = stmt.where(tbl.c.states.is_(None))
    # One executemany per batch: psycopg pipelines it, so a 2,000-row batch is
    # a few round trips, not 2,000 (the migration may run from far from Neon).
    result = db.connection().execute(stmt, [{"b_id": i, "b_states": states, "b_seen": seen or _EPOCH}
                                            for i, states, seen in rows])
    db.commit()
    return max(result.rowcount or 0, 0)


def fill_states(db, only_open_on=None, batch: int = 2000, limit: int | None = None,
                refresh: bool = False) -> int:
    """Work out and store tenders.states from each row's five short texts.

    Default: only rows with none yet (NULL) -- the migration and the daily cron.
    refresh=True: every row, writing only where the stored value is wrong. Run
    it once after every fetcher is on this code: a fetcher on older code that
    changed a row's text after the migration left that row's states stale, and
    a NULL-only fill would never look at it again.

    only_open_on=a date: only tenders still open that day. Pages by id, so a row
    skipped because it changed meanwhile is not read again in the same pass
    (the next run picks it up). Returns rows written."""
    from sqlalchemy import select
    from .models import Source
    names = dict(db.execute(select(Source.id, Source.name)).all())
    q = select(Tender.id, Tender.states, Tender.last_updated_at, Tender.source_id, Tender.title,
               Tender.organization, Tender.department, Tender.raw_payload["office"].as_string(),
               Tender.raw_payload["organisation_chain"].as_string()).order_by(Tender.id)
    if not refresh:
        q = q.where(Tender.states.is_(None))
    if only_open_on is not None:
        q = q.where(or_(Tender.deadline.is_(None), Tender.deadline >= only_open_on))
    written, read, last_id = 0, 0, 0
    while limit is None or read < limit:
        rows = db.execute(q.where(Tender.id > last_id)
                          .limit(batch if limit is None else min(batch, limit - read))).all()
        if not rows:
            break
        last_id, read = rows[-1][0], read + len(rows)
        todo = []
        for i, current, seen, src, *texts in rows:
            states = tender_states(names.get(src), *texts)
            if states != current:
                todo.append((i, states, seen))
        written += _store_states(db, todo, only_null=not refresh)
    return written


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
