"""The HTML pages: browse the corpus, or answer the questionnaire once and get
matches on every visit after that.

Routes and the data behind each page live here. The markup is in templates/
(Jinja2, autoescaped), the styling in static/css/site.css and the behaviour in
static/js/. Pages post/read JSON from the existing API with a few lines of
fetch, so there is no python-multipart, no second parsing path, and no filter
logic duplicated out of app/api.py.

Nothing loads from a CDN, and nothing is inline: the site CSP is default-src
'self', so the fonts, the stylesheet, the scripts, the animation library and the
icon set are all served from our own origin under /static.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import parse_qs, quote

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from . import cppp_relay, oauth, security
from .auth import current_user
from .db import get_db
from .districts import DISTRICTS
from .eligibility import REGISTRATIONS, checklist, needs_check
from .facts import money, preview_facts, tender_facts
from .matching import (
    SECTOR_LABELS,
    STATES,
    derive_districts,
    derive_sectors,
    derive_states,
    hydrate,
    match_digest,
    sector_terms,
)
from .models import Company, ConnectorRun, Source, Tender, User

log = logging.getLogger(__name__)

router = APIRouter()

_ROOT = Path(__file__).resolve().parent.parent
_STATIC = _ROOT / "static"


# Phosphor Regular, one family for the whole site, read off disk once at import
# and inlined. An <svg><use href="/static/..."> would be a request per icon and
# is blocked cross-document in some browsers; inlining also lets each glyph
# inherit currentColor, and the theme with it.
_ICON_DIR = _STATIC / "icons"
_ICONS: dict[str, str] = {}
if _ICON_DIR.is_dir():
    for _f in _ICON_DIR.glob("*.svg"):
        _ICONS[_f.stem] = re.sub(
            r"^<svg[^>]*>|</svg>$", "", _f.read_text(encoding="utf-8").strip()
        )


def icon(name: str, cls: str = "i") -> Markup:
    """Inline one Phosphor glyph. Decorative by default: every icon here sits
    beside its own text label, so it is hidden from screen readers rather than
    announced twice."""
    return Markup(
        f'<svg class="{cls}" viewBox="0 0 256 256" fill="currentColor" '
        f'aria-hidden=true focusable=false>{_ICONS.get(name, "")}</svg>'
    )


def asset(path: str) -> str:
    """URL for a file under /static, versioned by its modification time, so a
    deploy that changes site.js is fetched fresh instead of served from cache."""
    try:
        return f"/static/{path}?v={int((_STATIC / path).stat().st_mtime)}"
    except OSError:
        return f"/static/{path}"


# A ranked-list mark: three rules of decreasing width. Inline data: URI because
# img-src is 'self' data: and a favicon is somewhere CSS cannot reach.
FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'"
    "%3E%3Crect width='32' height='32' fill='%238EA439'/%3E%3Cg fill='%232A1608'"
    "%3E%3Crect x='7' y='9' width='18' height='3'/%3E%3Crect x='7' y='14.5'"
    " width='12' height='3'/%3E%3C/g%3E%3Crect x='7' y='20' width='6' height='3'"
    " fill='%23EFD7A5'/%3E%3C/svg%3E"
)


def days_left(deadline: date | None, today: date | None = None) -> tuple[str, str]:
    """The gutter label for a tender, and its rail weight. Mirrors left() in
    static/js/browse.js, which does the same job on rows fetched by JSON."""
    if deadline is None:
        return "", "s0"
    n = (deadline - (today or date.today())).days
    if n < 0:
        return "closed", "s0"
    if n == 0:
        return "today", "s3"
    return f"{n}d", "s3" if n <= 3 else "s2" if n <= 10 else "s1"


def rank_class(score: int, top: int) -> str:
    """Which of three weights the row's left rail gets.

    Relative to the best score on the page, not an absolute cutoff: scores are
    IDF-weighted sums with no fixed ceiling, so 40 is a strong match under one
    profile and a weak one under another. Ranking is the whole product, so it
    gets the one piece of non-text encoding on the page.
    """
    if top <= 0:
        return "s0"
    share = score / top
    return "s3" if share >= 0.75 else "s2" if share >= 0.45 else "s1"


def _summary(t: Tender, sectors: list[str], places: list[str], left: str) -> str:
    """One plain sentence, assembled from fields we already hold.

    Nothing here is fetched or invented: the trade comes from the same
    title classifier the matcher uses, the place from the same state and
    district lists, and the rest is the listing's own data.
    """
    trade = sectors[0].lower() if sectors else "procurement"
    where = f" in {places[0]}" if places else ""
    buyer = t.organization or "an unnamed buyer"
    when = {
        "": "",
        "closed": " Bidding has closed.",
        "today": " Bids close today.",
    }.get(left, f" Bids close in {left.replace('d', ' days')}.")

    # Enrichment reads these off the bid PDF (app/enrich.py). Most listings do not
    # publish a value, so each part appears only when we actually have it rather
    # than printing "not stated" three times.
    raw = t.raw_payload if isinstance(t.raw_payload, dict) else {}
    extra = ""
    if t.estimated_value is not None:
        extra += f" Worth about {money(t.estimated_value)}"
        emd = raw.get("emd_amount")
        if isinstance(emd, (int, float)):
            extra += f", with an EMD of {money(emd)}"
        extra += "."
    qty = raw.get("quantity")
    if qty and str(qty).strip().isdigit() and int(qty) > 1:
        extra += f" {int(qty):,} units."
    period = raw.get("contract_period")
    if period:
        extra += f" Contract runs {period}."
    return f"A {trade} notice from {buyer}{where}.{when}{extra}"


def summary_for(t: Tender) -> str:
    """The one-line summary, for anywhere a tender is listed."""
    sectors = sorted(SECTOR_LABELS[k] for k in derive_sectors(t.title))
    places = sorted(derive_states(t.title) | derive_districts(t.title))
    return _summary(t, sectors, places, days_left(t.deadline)[0])


# ---- templates --------------------------------------------------------------

_env = Environment(
    loader=FileSystemLoader(_ROOT / "templates"),
    autoescape=select_autoescape(["html"]),
    trim_blocks=False,
)
_env.globals.update(
    icon=icon, asset=asset, favicon=FAVICON, days_left=days_left,
    rank_class=rank_class, summary_for=summary_for, preview_facts=preview_facts,
)
_env.filters["num"] = lambda n: f"{n:,}"
_env.filters["urlquote"] = quote


def render(name: str, **context) -> str:
    return _env.get_template(name).render(**context)


def extra_line(t: Tender) -> str:
    """Tender ID, publication date and the buyer's office, under a row's main
    meta line. The markup is the extra_line macro in templates/_macros.html."""
    return str(_env.get_template("_macros.html").module.extra_line(t))


def _profile_of(db: Session, user: User | None) -> Company | None:
    if user is None:
        return None
    return db.execute(
        select(Company).where(Company.user_id == user.id).order_by(Company.id.desc())
    ).scalars().first()


# ---- landing ---------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
def home(request: Request, db: Session = Depends(get_db),
         user: User | None = Depends(current_user)):
    """The home page, whether or not you are signed in.

    It used to redirect straight to your matches once you had a profile, which
    meant the corpus figures, the closing-soonest list and the buyer index were
    unreachable the moment you had an account. Matches now have their own
    address instead.

    Signed in with no profile is the one case that cannot stay here: signing up
    through Google, or a questionnaire that failed validation during signup,
    both land on an account with nothing to score against, and this page would
    have shown the signed-out landing to someone who is signed in. Ask the
    questions instead -- /profile already knows to say "Finish your profile".
    """
    profile = _profile_of(db, user)
    if user is not None and profile is None:
        return RedirectResponse("/profile", status_code=303)
    if profile is not None:
        return _welcome_signed_in(db, profile)
    return _welcome(db)


def _last_collected(db: Session):
    # "Updated daily" would be a claim; the last successful run is a fact, and it
    # is the one that goes stale visibly if the scheduler process is not running.
    return db.scalar(
        select(func.max(ConnectorRun.finished_at)).where(ConnectorRun.status == "ok")
    )


def _welcome_signed_in(db: Session, company: Company) -> str:
    """The home page for someone who has answered the questions."""
    today = date.today()
    # Everything below comes from one cached digest, so a page load does not
    # re-score the corpus. Only the six rows actually printed are fetched.
    d = match_digest(db, company, today)
    n_open = db.scalar(
        select(func.count()).select_from(Tender).where(
            Tender.duplicate_of.is_(None),
            or_(Tender.deadline.is_(None), Tender.deadline >= today),
        )
    )
    c_soon = db.scalar(
        select(func.count()).select_from(Tender).where(
            Tender.duplicate_of.is_(None),
            Tender.deadline.between(today, today + timedelta(7)),
        )
    )
    score_of = {tid: sc for sc, _r, tid in d.scored}
    soon_ids = list(d.by_deadline[:6])
    rows_by_id = hydrate(db, soon_ids)
    soon = [(rows_by_id[i], score_of.get(i, 0), needs_check(rows_by_id[i], company))
            for i in soon_ids if i in rows_by_id]
    short = company.name if len(company.name) <= 22 else company.name[:21] + "…"
    return render(
        "home_signed_in.html", title=f"Home | {company.name}", company=company,
        short=short, d=d, n_open=n_open, n_soon=d.closing_within_7, c_soon=c_soon,
        n_sources=db.scalar(select(func.count()).select_from(Source)),
        last=_last_collected(db), soon=soon, buyers=d.buyers[:8], today=today,
    )


@router.get("/matches")
def my_matches(db: Session = Depends(get_db),
               user: User | None = Depends(current_user)):
    """A stable address for "my matches", so the nav can link to it without
    knowing anybody's company id."""
    if user is None:
        return RedirectResponse("/login", status_code=303)
    profile = _profile_of(db, user)
    if profile is None:
        # Signed in but never finished the questionnaire (e.g. it failed
        # validation during signup). Ask for it now rather than showing an
        # empty matches page.
        return RedirectResponse("/profile", status_code=303)
    return RedirectResponse(f"/c/{profile.id}", status_code=303)


def _welcome(db: Session) -> str:
    """The landing figures, counted rather than claimed.

    Same "open" rule as GET /tenders: the deadline decides, not tenders.status,
    which is derived once at ingest and is stale the morning after a tender
    closes. A hard-coded "8,000+" was wrong within weeks; this cannot be.
    """
    today = date.today()
    is_open = or_(Tender.deadline.is_(None), Tender.deadline >= today)
    n_open, n_soon = db.execute(
        select(
            func.count().filter(is_open),
            func.count().filter(Tender.deadline.between(today, today + timedelta(7))),
        ).select_from(Tender).where(Tender.duplicate_of.is_(None))
    ).one()
    n_buyers = db.scalar(
        select(func.count(distinct(Tender.organization)))
        .where(Tender.organization.is_not(None), Tender.duplicate_of.is_(None),
               is_open)
    )
    # Real notices beat any amount of describing them, and they are the same rows
    # /browse would show at the top of its default sort.
    soonest = db.execute(
        select(Tender)
        .where(Tender.duplicate_of.is_(None), Tender.deadline >= today)
        .order_by(Tender.deadline.asc(), Tender.id.asc())
        .limit(6)
    ).scalars().all()
    buyers = db.execute(
        select(Tender.organization, func.count())
        .where(Tender.organization.is_not(None), Tender.duplicate_of.is_(None),
               is_open)
        .group_by(Tender.organization)
        .order_by(func.count().desc())
        .limit(8)
    ).all()
    return render(
        "home.html", title="Find tenders", n_open=n_open, n_soon=n_soon,
        n_sources=db.scalar(select(func.count()).select_from(Source)),
        n_buyers=n_buyers, last=_last_collected(db), soonest=soonest,
        buyers=buyers, today=today,
    )


# ---- the questionnaire, asked once ----------------------------------------

def _questionnaire(db: Session) -> dict:
    """Option lists for templates/_profile_fields.html."""
    # Offered from the corpus, not a hard-coded list: the option text is exactly the
    # string stored in tenders.organization, so anything offered here can match.
    top = db.execute(
        select(Tender.organization, func.count())
        .where(Tender.organization.is_not(None))
        .group_by(Tender.organization)
        .order_by(func.count().desc())
        .limit(40)
    ).all()
    return {
        "sectors": sorted(SECTOR_LABELS.items(), key=lambda kv: kv[1]),
        "states": STATES,
        "districts": [(st, sorted(ds)) for st, ds in DISTRICTS.items()],
        "top_buyers": top,
        "registrations": list(REGISTRATIONS.items()),
        "google_configured": oauth.configured(),
    }


@router.get("/signup", response_class=HTMLResponse)
def signup_page(db: Session = Depends(get_db)) -> str:
    return render("signup.html", title="Create account", **_questionnaire(db))


@router.get("/login", response_class=HTMLResponse)
def login_page() -> str:
    return render("login.html", title="Sign in", google_configured=oauth.configured())


@router.get("/profile", response_class=HTMLResponse)
def profile_page(db: Session = Depends(get_db),
                 user: User | None = Depends(current_user)):
    """Edit the answers given at signup. Signed out this is the no-account preview
    path -- answer, see matches by link, decide whether to sign up afterwards."""
    existing = _profile_of(db, user)
    if user is None:
        heading, hint = "Try it without an account", (
            "Answers are scored against every open tender and shown below. Nothing "
            "is stored, so create an account if you want them saved."
        )
    elif existing is None:
        heading, hint = "Finish your profile", "One step left, then you are done."
    else:
        heading, hint = "Edit your answers", "Saving overwrites your saved profile."
    return render("profile.html", title=heading, heading=heading, hint=hint,
                  **_questionnaire(db))


# ---- matches ---------------------------------------------------------------

# key -> (label, the order it starts in). Every key sorts the whole match list,
# not just the 50 rows printed, so "highest EMD" really is the highest.
MATCH_SORTS = {
    "score": ("Best match", "desc"),
    "deadline": ("Closing date", "asc"),
    "published": ("Published date", "desc"),
    "window": ("Days given to bid", "desc"),
    "value": ("Estimated value", "desc"),
    "emd": ("EMD amount", "desc"),
    "quantity": ("Quantity", "desc"),
    "distance": ("Distance from you", "asc"),
    "buyer": ("Buyer name", "asc"),
    "title": ("Title", "asc"),
}


def _number(v):
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return None


def sort_matches(db: Session, company: Company, scored, key: str, order: str):
    """Re-order (score, reasons, id) triples. Rows with no value for the key go
    last in both directions; ties keep best-match order.

    Distance has no coordinates behind it: a named district of yours is nearest,
    then a state of yours, then everything that names neither.
    """
    if key == "score":
        return list(scored) if order == "desc" else list(reversed(scored))
    ids = [tid for _s, _r, tid in scored]
    cols = {}
    if key != "distance" and ids:
        cols = {r.id: r for r in db.execute(
            select(Tender.id, Tender.deadline, Tender.published_date, Tender.title,
                   Tender.estimated_value, Tender.organization, Tender.raw_payload)
            .where(Tender.id.in_(ids))
        )}
    mine = set(company.districts or [])

    def value(reasons, tid):
        if key == "distance":
            loc = next((r for r in reasons if r.startswith("location: ")), "")
            places = set(loc.removeprefix("location: ").split(", ")) if loc else set()
            return 2 if not places else 0 if places & mine else 1
        r = cols.get(tid)
        if r is None:
            return None
        raw = r.raw_payload if isinstance(r.raw_payload, dict) else {}
        if key == "deadline":
            return r.deadline
        if key == "published":
            return r.published_date
        if key == "window":
            return (r.deadline - r.published_date).days if r.deadline and r.published_date else None
        if key == "value":
            return _number(r.estimated_value)
        if key == "emd":
            return _number(raw.get("emd_amount")) or None
        if key == "quantity":
            return _number(raw.get("quantity")) or None
        if key == "buyer":
            return (r.organization or "").strip().lower() or None
        return (r.title or "").strip().lower() or None

    keyed = [(value(reasons, tid), item) for item in scored for _s, reasons, tid in [item]]
    have = sorted((kv for kv in keyed if kv[0] is not None), key=lambda kv: kv[0],
                  reverse=order == "desc")
    return [item for _v, item in have] + [item for v, item in keyed if v is None]


@router.get("/c/{company_id}", response_class=HTMLResponse)
def results(
    company_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
    sort: str = "score",
    order: str = "",
) -> str:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    # Same rule as api._owned: signed in, and it is yours. 404, not 403, so the
    # existence of an id is not confirmed to a stranger.
    if user is None or company.user_id is None or company.user_id != user.id:
        raise HTTPException(status_code=404, detail="company not found")
    sort = sort if sort in MATCH_SORTS else "score"
    order = order if order in ("asc", "desc") else MATCH_SORTS[sort][1]
    d = match_digest(db, company)
    scored = sort_matches(db, company, d.scored, sort, order)[:50]
    rows_by_id = hydrate(db, [tid for _s, _r, tid in scored])
    shown = [
        (rows_by_id[tid], score, list(reasons), needs_check(rows_by_id[tid], company))
        for score, reasons, tid in scored
        if tid in rows_by_id      # purged between scoring and this render
    ]
    return render("matches.html", title="Matches", company=company, total=d.total,
                  shown=shown, top=max((sc for sc, _r, _t in scored), default=0),
                  sorts=[(k, v[0]) for k, v in MATCH_SORTS.items()],
                  sort=sort, order=order)


# ---- one tender --------------------------------------------------------

# Their advanced search, which opens cold and has a Tender ID box next to its
# CAPTCHA. The visitor pastes the ID and types the CAPTCHA themselves. The detail
# pages are hotlink-protected, so the tender id is what makes a notice findable.
CPPP_SEARCH = ("https://eprocure.gov.in/eprocure/app"
               "?page=FrontEndAdvancedSearch&service=page")


def _fit(db: Session, company: Company | None, t: Tender, today: date) -> str:
    """Whether this tender is one of the signed-in company's matches, and why.

    Read from the cached digest, not a fresh match_score: a standalone score has
    no corpus to weigh terms against, so it would disagree with the number the
    matches page showed for the same row.
    """
    hit = None
    if company is not None:
        d = match_digest(db, company, today)
        hit = next(((s, r) for s, r, tid in d.scored if tid == t.id), None)
    return render("_fit.html", company=company, hit=hit)


def _related(db: Session, t: Tender, today: date) -> str:
    """Same tender on other sources, more from this buyer, and similar work."""
    others = (Tender.id != t.id, Tender.duplicate_of.is_(None),
              Tender.deadline >= today)

    twin = Tender.duplicate_of == t.id
    if t.duplicate_of:
        twin = or_(twin, Tender.id == t.duplicate_of)
    sections = [{
        "heading": "Same tender on other sources",
        "rows": db.scalars(select(Tender).where(twin, Tender.id != t.id)).all(),
    }]

    if t.organization:
        q = select(Tender).where(Tender.organization == t.organization, *others)
        n = db.scalar(select(func.count()).select_from(q.subquery()))
        sections.append({
            "heading": f"More open tenders from this buyer ({n:,})",
            "rows": db.scalars(q.order_by(Tender.deadline).limit(5)).all(),
            "link": f"/browse?organization={quote(t.organization)}",
            "link_text": "See all from this buyer",
        })

    # The rarest sector term in the title, measured against open tenders. The
    # commonest ones ("maintenance", "repair") appear in most notices and would
    # make every tender "similar" to every other -- see build_idf.
    terms = sorted({x for s in derive_sectors(t.title) for x in sector_terms(s, t.title)})
    counts = {}
    if terms:
        # One query for every term: each round trip to the database costs more
        # than the count itself.
        got = db.execute(select(*(
            func.count().filter(Tender.title.ilike(f"%{term}%")) for term in terms
        )).where(*others)).one()
        counts = {term: c for term, c in zip(terms, got) if c}
    if counts:
        term = min(counts, key=counts.get)
        sections.append({
            "heading": f"Other open tenders mentioning '{term}' ({counts[term]:,})",
            "rows": db.scalars(select(Tender).where(Tender.title.ilike(f"%{term}%"), *others)
                               .order_by(Tender.deadline).limit(5)).all(),
            "link": f"/browse?q={quote(term)}",
            "link_text": "See all",
        })
    return render("_related.html", sections=sections, today=today)


def _document_for(db: Session, t: Tender) -> tuple[str, str] | None:
    """(url, source_name) for a downloadable document, or None.

    A tender's own document_url wins. Failing that, look at the cross-source
    duplicates link_duplicates() found: CPPP and the state portals gate their
    detail pages behind a CAPTCHA and so carry no document, but the very same
    notice republished on GeM has a PDF that answers an ordinary GET. Showing
    that twin's document is what lets a CPPP tender skip the copy-the-ID dance.
    """
    if t.document_url:
        src = db.get(Source, t.source_id) if t.source_id else None
        return t.document_url, (src.name if src else "the source portal")

    twins = db.execute(
        select(Tender).where(
            or_(Tender.duplicate_of == t.id,
                Tender.id == t.duplicate_of) if t.duplicate_of
            else Tender.duplicate_of == t.id,
            Tender.document_url.is_not(None),
        ).limit(1)
    ).scalars().first()
    if twins is None:
        return None
    src = db.get(Source, twins.source_id) if twins.source_id else None
    return twins.document_url, (src.name if src else "another portal")


@router.get("/t/{tender_id}", response_class=HTMLResponse)
def tender_detail(
    tender_id: int, db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
) -> str:
    """Everything the aggregator holds for one notice, on our own page."""
    t = db.get(Tender, tender_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tender not found")
    src = db.get(Source, t.source_id) if t.source_id else None

    raw = t.raw_payload if isinstance(t.raw_payload, dict) else {}
    sectors = sorted(SECTOR_LABELS[k] for k in derive_sectors(t.title))
    places = sorted(derive_states(t.title) | derive_districts(t.title))

    today = date.today()
    company = _profile_of(db, user)
    label, _rank = days_left(t.deadline, today)
    facts = tender_facts(t, today)
    found = _document_for(db, t)
    return render(
        "tender.html", title=t.title[:60], t=t, company=company,
        company_answered=company is not None and not (
            company.years_in_business is None and company.annual_turnover is None
            and company.largest_similar_work is None and company.bid_capacity is None
            and company.emd_budget is None and not company.registrations),
        checklist=checklist(t, company), summary=_summary(t, sectors, places, label),
        sectors=sectors, facts=facts, days_label=label,
        document={"url": found[0], "source": found[1]} if found else None,
        cppp_box=bool(src and src.name == "CPPP" and cppp_relay.is_detail_url(raw.get("url"))),
        ref=t.external_ref or "not recorded",
        source_name=src.name if src else "the source portal",
        licence=src.license if src else "see /sources",
        search_url=CPPP_SEARCH if not src or src.name == "CPPP" else src.base_url,
    )


@router.get("/t/{tender_id}/extras")
def tender_extras(
    tender_id: int, db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
) -> dict:
    """The slow parts of the tender page, fetched by its script after it shows:
    the signed-in company's fit and the related-tender lists."""
    t = db.get(Tender, tender_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tender not found")
    today = date.today()
    return {"fit": _fit(db, _profile_of(db, user), t, today),
            "more": _related(db, t, today)}


# ---- CPPP lookup: the visitor types CPPP's CAPTCHA here, we fetch the tender ---

def _cppp_tender(db: Session, tender_id: int) -> Tender:
    t = db.get(Tender, tender_id)
    src = db.get(Source, t.source_id) if t and t.source_id else None
    raw = t.raw_payload if t and isinstance(t.raw_payload, dict) else {}
    if (t is None or src is None or src.name != "CPPP"
            or not cppp_relay.is_detail_url(raw.get("url"))):
        raise HTTPException(status_code=404, detail="no CPPP link for this tender")
    return t


def _cppp_url(t: Tender) -> str:
    return t.raw_payload["url"]


def _cppp_captcha_page(t: Tender, cap: cppp_relay.Captcha) -> str:
    return render("cppp_captcha.html", title="Open on CPPP", t=t, captcha=cap)


def _cppp_failed(t: Tender, problem: str = "") -> HTMLResponse:
    return HTMLResponse(
        render("cppp_failed.html", title="CPPP unavailable", t=t, problem=problem,
               search_url=CPPP_SEARCH),
        status_code=404 if problem else 502,
    )


# CPPP's HTML is served from our origin, so it must not run script or reach our
# cookies: `sandbox` gives it an opaque origin, script-src 'none' stops its JS.
CPPP_PAGE_CSP = ("sandbox allow-popups allow-popups-to-escape-sandbox; "
                 "default-src https://eprocure.gov.in data:; "
                 "style-src https://eprocure.gov.in 'unsafe-inline'; "
                 "script-src 'none'; form-action https://eprocure.gov.in")


def _cppp_limit(request: Request) -> None:
    # Every request here costs CPPP a hit from our server's address.
    security.enforce(f"cppp:{security.client_ip(request)}", 30, 3600,
                     "Too many CPPP lookups. Try again in an hour.")


@router.get("/t/{tender_id}/cppp", response_class=HTMLResponse)
def cppp_captcha(request: Request, tender_id: int, db: Session = Depends(get_db)):
    t = _cppp_tender(db, tender_id)
    _cppp_limit(request)
    try:
        return _cppp_captcha_page(t, cppp_relay.start(_cppp_url(t)))
    except cppp_relay.LinkRejected:
        log.exception("CPPP rejected the tender link")
        return _cppp_failed(t, "CPPP no longer opens this tender's link")
    except (httpx.HTTPError, cppp_relay.RelayError, RuntimeError):
        log.exception("CPPP lookup failed to start")
        return _cppp_failed(t)


@router.get("/t/{tender_id}/cppp/captcha")
def cppp_captcha_json(request: Request, tender_id: int, db: Session = Depends(get_db)):
    """The CAPTCHA for the box on the tender page, loaded by its script."""
    t = _cppp_tender(db, tender_id)
    _cppp_limit(request)
    try:
        cap = cppp_relay.start(_cppp_url(t))
    except cppp_relay.LinkRejected:
        log.exception("CPPP rejected the tender link")
        return JSONResponse({"error": "CPPP no longer opens this tender's link."}, 404)
    except (httpx.HTTPError, cppp_relay.RelayError, RuntimeError):
        log.exception("CPPP lookup failed to start")
        return JSONResponse({"error": "CPPP is not answering. Try New image."}, 502)
    return {"state": cap.state, "image": cap.image}


@router.post("/t/{tender_id}/cppp", response_class=HTMLResponse)
async def cppp_open(request: Request, tender_id: int, db: Session = Depends(get_db)):
    t = _cppp_tender(db, tender_id)
    _cppp_limit(request)
    form = parse_qs((await request.body()).decode(errors="replace"))
    state = form.get("state", [""])[0]
    captcha = form.get("captcha", [""])[0].strip()[:20]
    try:
        got = await run_in_threadpool(cppp_relay.submit, state, _cppp_url(t), captcha)
    except cppp_relay.LinkRejected:
        log.exception("CPPP rejected the tender link")
        return _cppp_failed(t, "CPPP no longer opens this tender's link")
    except (httpx.HTTPError, cppp_relay.RelayError, RuntimeError):
        log.exception("CPPP lookup failed")
        return _cppp_failed(t)
    if isinstance(got, cppp_relay.Captcha):
        return _cppp_captcha_page(t, got)
    return HTMLResponse(cppp_relay.with_base(got.html, str(escape(got.url))),
                        headers={"Content-Security-Policy": CPPP_PAGE_CSP})


# ---- corpus browser --------------------------------------------------------

@router.get("/browse", response_class=HTMLResponse)
def browse(db: Session = Depends(get_db)) -> str:
    """Read-only view of the whole corpus. Filtering happens in GET /tenders --
    this page is the form around it, not a second query path."""
    sources = db.execute(select(Source).order_by(Source.name)).scalars().all()
    return render("browse.html", title="Browse tenders", sources=sources)
