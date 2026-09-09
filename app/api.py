from __future__ import annotations

import hmac
import logging
import os
import time
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path

from fastapi import Body, Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from . import mailer, oauth, security
from .auth import (
    AuthError,
    authenticate,
    clear_session_cookie,
    current_user,
    make_verification_token,
    read_verification_token,
    register,
    set_session_cookie,
    user_from_google,
)
from .connectors import REGISTRY
from .db import SessionLocal, get_db
from .matching import MP_DISTRICTS, SECTOR_LABELS, STATES, find_matches
from .models import Company, ConnectorRun, Source, Tender, User
from .models import utcnow
from .retention import DEFAULT_RETENTION_DAYS, purge_expired
from .schemas import (
    CompanyIn,
    CompanyOut,
    MatchOut,
    Page,
    SourceOut,
    TenderDetailOut,
    TenderOut,
)
from .web import router as web_router

log = logging.getLogger(__name__)

# Swagger's default page is served by FastAPI itself, which leaves no room for
# our stylesheet or our nav. Turning the built-in route off and rendering the
# same helper by hand gets both, without reimplementing anything Swagger does.
DOCS_PATHS = {"/docs", "/redoc", "/docs/oauth2-redirect"}

app = FastAPI(
    docs_url=None,
    title="Tender OS",
    version="0.1.0",
    description=(
        "Aggregated public procurement notices from official Indian government "
        "sources. Every record carries its source and licence -- see GET /sources. "
        "GeM (gem.gov.in) is not and will not be a source: its robots.txt "
        "disallows automated access, so that data requires a data-sharing "
        "agreement rather than a scraper."
    ),
)


@app.middleware("http")
async def harden(request: Request, call_next):
    """One nonce per response, and the headers that make the CSP meaningful.

    The nonce is put on request.state before the handler runs so page() can stamp
    it onto every <script> and <style> tag it emits.
    """
    nonce = security.make_nonce()
    request.state.csp_nonce = nonce
    response = await call_next(request)
    # Read off the app rather than hard-coded, so moving docs_url moves the
    # exemption with it.
    is_docs = request.url.path in DOCS_PATHS
    policy = security.csp_docs() if is_docs else security.csp(nonce)
    response.headers.setdefault("Content-Security-Policy", policy)
    for header, value in security.SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if security.https_only():
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
        )
    return response


# The font, the animation library and the icons are served from our own origin,
# not a CDN. That is what keeps the strict CSP intact: script-src 'self' and the
# default-src 'self' fallback for font-src already allow these, so nothing here
# needs 'unsafe-inline' or a third-party host allow-listed.
_STATIC = Path(__file__).resolve().parent.parent / "static"
if _STATIC.is_dir():
    app.mount("/static", StaticFiles(directory=_STATIC), name="static")

DOCS_BAR = """<div class=tos-bar><div class=wrap>
 <a class=tos-brand href="/">
  <svg class=tos-mark viewBox="0 0 32 32" aria-hidden="true">
   <rect width="32" height="32" fill="#2A1608"/>
   <g fill="#EFD7A5"><rect x="7" y="9" width="18" height="3"/>
   <rect x="7" y="14.5" width="12" height="3"/></g>
   <rect x="7" y="20" width="6" height="3" fill="#8EA439"/>
  </svg>
  <b>Tender</b><span>OS</span></a>
 <nav class=tos-nav><a href="/">Home</a><a href="/matches">Matches</a>
 <a href="/browse">Browse</a><a class=on href="/docs">API</a></nav>
</div></div>"""


@app.get("/docs", include_in_schema=False)
def docs() -> HTMLResponse:
    """Swagger UI wearing the site's stylesheet, plus the site's own nav.

    swagger_css_url points at /static, which the docs CSP already allows under
    'self' -- no new host, and the strict policy on every other page is
    untouched.
    """
    # swagger_css_url REPLACES Swagger's own stylesheet rather than adding to
    # it, which leaves the page with no layout at all. Ours goes in as a second
    # sheet after it, so it overrides rather than removes.
    page = get_swagger_ui_html(
        openapi_url=app.openapi_url,
        title=f"{app.title} API",
        swagger_favicon_url="/static/favicon.svg",
    ).body.decode()
    page = page.replace(
        "</head>", '<link rel="stylesheet" href="/static/docs.css"></head>', 1
    )
    return HTMLResponse(page.replace("<body>", "<body>" + DOCS_BAR, 1))


app.include_router(web_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/sources", response_model=list[SourceOut])
def list_sources(db: Session = Depends(get_db)):
    """Sources with their compliance metadata -- transparency for our own users."""
    return list(db.execute(select(Source).order_by(Source.name)).scalars())


@app.get("/tenders", response_model=Page)
def list_tenders(
    db: Session = Depends(get_db),
    q: str | None = Query(None, description="Substring match on title"),
    category: str | None = None,
    status: str | None = None,
    department: str | None = None,
    organization: str | None = None,
    source_id: int | None = None,
    published_from: date | None = None,
    published_to: date | None = None,
    deadline_from: date | None = None,
    deadline_to: date | None = None,
    min_value: Decimal | None = None,
    max_value: Decimal | None = None,
    include_duplicates: bool = Query(
        False, description="Include rows already linked to an earlier duplicate"
    ),
    include_closed: bool = Query(
        False, description="Include tenders whose deadline has already passed"
    ),
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    sort: str = Query("deadline", pattern="^(deadline|published_date|first_seen_at)$"),
):
    filters = []
    if q:
        filters.append(Tender.title.ilike(f"%{q}%"))
    if category:
        filters.append(Tender.category == category)
    if status:
        filters.append(Tender.status == status)
    if department:
        filters.append(Tender.department.ilike(f"%{department}%"))
    if organization:
        filters.append(Tender.organization.ilike(f"%{organization}%"))
    if source_id is not None:
        filters.append(Tender.source_id == source_id)
    if published_from:
        filters.append(Tender.published_date >= published_from)
    if published_to:
        filters.append(Tender.published_date <= published_to)
    if deadline_from:
        filters.append(Tender.deadline >= deadline_from)
    if deadline_to:
        filters.append(Tender.deadline <= deadline_to)
    if min_value is not None:
        filters.append(Tender.estimated_value >= min_value)
    if max_value is not None:
        filters.append(Tender.estimated_value <= max_value)
    if not include_duplicates:
        filters.append(Tender.duplicate_of.is_(None))
    if not include_closed:
        # Compared against today, not against Tender.status: status is derived
        # once at ingest (schemas.py) and is therefore stale the morning after a
        # tender closes. The deadline is the only field that stays true.
        # A null deadline is unknown, not expired -- the same rule retention.py
        # follows -- so those rows stay visible.
        filters.append(
            or_(Tender.deadline.is_(None), Tender.deadline >= date.today())
        )

    total = db.execute(
        select(func.count()).select_from(Tender).where(*filters)
    ).scalar_one()
    order = {
        "deadline": Tender.deadline.asc(),
        "published_date": Tender.published_date.desc(),
        "first_seen_at": Tender.first_seen_at.desc(),
    }[sort]
    rows = db.execute(
        select(Tender).where(*filters).order_by(order, Tender.id).limit(limit).offset(offset)
    ).scalars()
    return Page(
        total=total,
        limit=limit,
        offset=offset,
        items=[TenderOut.model_validate(r) for r in rows],
    )


@app.get("/tenders/{tender_id}", response_model=TenderDetailOut)
def get_tender(tender_id: int, db: Session = Depends(get_db)):
    row = db.get(Tender, tender_id)
    if row is None:
        raise HTTPException(status_code=404, detail="tender not found")
    return row


# ---- Company profiles & matching ----

TOP_BUYERS = 40


@app.get("/questionnaire")
def questionnaire(db: Session = Depends(get_db)) -> dict:
    """The answer options, served from the taxonomy so the form and the API can
    never drift apart.

    Buyers are read from the corpus rather than hard-coded: they are exactly the
    strings in `tenders.organization`, so what the form offers is guaranteed to be
    something that can actually match.
    """
    buyers = db.execute(
        select(Tender.organization, func.count().label("n"))
        .where(Tender.organization.is_not(None))
        .group_by(Tender.organization)
        .order_by(func.count().desc())
        .limit(TOP_BUYERS)
    ).all()
    return {
        "sectors": [{"key": k, "label": v} for k, v in sorted(SECTOR_LABELS.items())],
        "districts": list(MP_DISTRICTS),
        "states": list(STATES),
        "buyers": [{"name": name, "tenders": n} for name, n in buyers],
    }


# ---- Accounts ----

def _owned(company: Company, user: User | None) -> Company:
    """Every saved profile belongs to exactly one account and is private to it.

    This used to let an unowned profile be read by anyone with the id. Company
    ids are sequential, so that was an enumerable leak of company names and
    contact addresses -- walking /companies/1,2,3 returned real people's email
    addresses. Signed-out visitors now preview through POST /match, which scores
    a profile without ever storing it.
    """
    if user is None or company.user_id is None or company.user_id != user.id:
        # 404 rather than 403: telling a stranger the id exists is itself a leak.
        raise HTTPException(status_code=404, detail="company not found")
    return company


def _send_verification(user: User) -> bool:
    """Never lets a mail failure break signup: the account exists either way and
    the address can be confirmed later from the banner."""
    try:
        return mailer.send_verification(user.email, make_verification_token(user.id))
    except Exception:
        log.exception("could not send verification mail to %s", user.email)
        return False


@app.post("/auth/signup", status_code=201)
def signup(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    email: str = Body(..., embed=True),
    password: str = Body(..., embed=True),
):
    security.enforce(
        f"signup:{security.client_ip(request)}",
        security.SIGNUP_LIMIT, security.SIGNUP_WINDOW,
        "too many accounts created from this address; try again later",
    )
    try:
        user = register(db, email, password)
    except AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    set_session_cookie(response, user.id)
    _send_verification(user)
    return {"id": user.id, "email": user.email, "email_verified": user.email_verified}


@app.post("/auth/resend-verification")
def resend_verification(request: Request, user: User | None = Depends(current_user)):
    if user is None:
        raise HTTPException(status_code=401, detail="sign in first")
    # Without this, one account is a free mail cannon pointed at its own address.
    security.enforce(
        f"verify:{user.id}", security.MAIL_LIMIT, security.MAIL_WINDOW,
        "too many verification emails requested; try again later",
    )
    if user.email_verified:
        return {"status": "already verified"}
    delivered = _send_verification(user)
    return {
        "status": "sent" if delivered else "written to outbox.log",
        # True only when a real mail server took it; the UI says so plainly rather
        # than claiming "check your inbox" when nothing was ever sent.
        "delivered": delivered,
    }


@app.get("/auth/verify")
def verify_email(token: str, db: Session = Depends(get_db)):
    """Clicked from the email. Confirms the address and signs the person in, so a
    link opened on a phone does not dead-end on a login form."""
    user_id = read_verification_token(token)
    user = db.get(User, user_id) if user_id else None
    if user is None:
        return RedirectResponse("/?verify=invalid", status_code=303)
    user.email_verified = True
    db.commit()
    response = RedirectResponse("/?verify=ok", status_code=303)
    set_session_cookie(response, user.id)
    return response


# ---- Google sign-in ----

@app.get("/auth/google")
def google_start():
    if not oauth.configured():
        raise HTTPException(status_code=404, detail="Google sign-in is not configured")
    state = oauth.make_state()
    response = RedirectResponse(oauth.authorize_url(state), status_code=303)
    # The state is echoed by Google in the URL; keeping a copy in a cookie means a
    # forged callback needs both, which an attacker crafting a link does not have.
    response.set_cookie(
        "oauth_state", state, max_age=oauth.STATE_TTL_SECONDS, httponly=True,
        samesite="lax",
    )
    return response


@app.get("/auth/google/callback")
def google_callback(
    request: Request,
    db: Session = Depends(get_db),
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
):
    if error:                       # the user pressed Cancel on the consent screen
        return RedirectResponse("/login?error=cancelled", status_code=303)
    cookie_state = request.cookies.get("oauth_state") or ""
    if (
        not code
        or not oauth.check_state(state)
        or not hmac.compare_digest(state or "", cookie_state)
    ):
        return RedirectResponse("/login?error=state", status_code=303)
    try:
        claims = oauth.exchange_code(code)
        user = user_from_google(
            db,
            sub=claims["sub"],
            email=claims["email"],
            email_verified=bool(claims.get("email_verified")),
        )
    except (oauth.OAuthError, AuthError) as exc:
        log.warning("google sign-in failed: %s", exc)
        return RedirectResponse("/login?error=google", status_code=303)
    response = RedirectResponse("/", status_code=303)
    set_session_cookie(response, user.id)
    response.delete_cookie("oauth_state")
    return response


@app.post("/auth/login")
def login(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
    email: str = Body(..., embed=True),
    password: str = Body(..., embed=True),
):
    # Keyed on address AND account: one attacker cannot lock every user out by
    # spraying their emails, and one account cannot be sprayed from one host.
    ip = security.client_ip(request)
    key = f"login:{ip}:{(email or '').strip().lower()}"
    security.enforce(
        key, security.LOGIN_LIMIT, security.LOGIN_WINDOW,
        "too many sign-in attempts; try again in a few minutes",
    )
    try:
        user = authenticate(db, email, password)
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc))
    security.reset_key(key)          # a success clears the failure budget
    set_session_cookie(response, user.id)
    return {"id": user.id, "email": user.email}


@app.post("/auth/logout")
def logout(response: Response):
    clear_session_cookie(response)
    return {"status": "signed out"}


@app.get("/me")
def me(db: Session = Depends(get_db), user: User | None = Depends(current_user)):
    """Who am I, and which profile is mine. Drives the form's pre-fill."""
    if user is None:
        return {"user": None, "company": None}
    company = db.execute(
        select(Company).where(Company.user_id == user.id).order_by(Company.id.desc())
    ).scalars().first()
    return {
        "user": {
            "id": user.id,
            "email": user.email,
            "email_verified": user.email_verified,
            "via_google": user.google_sub is not None,
        },
        "company": CompanyOut.model_validate(company) if company else None,
        "smtp_configured": mailer.smtp_configured(),
    }


def _apply(company: Company, payload: CompanyIn) -> Company:
    # CompanyIn has no user_id field, so ownership cannot be set from a request body.
    for field, value in payload.model_dump().items():
        setattr(company, field, value)
    company.updated_at = utcnow()
    return company


@app.post("/companies", response_model=CompanyOut, status_code=201)
def create_company(
    payload: CompanyIn,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    """Saves the signed-in account's one profile, overwriting rather than piling
    up a new row per edit. Signing in is required: an unowned profile could not be
    protected by anything, and it stores a company name and contact address."""
    if user is None:
        raise HTTPException(
            status_code=401,
            detail="sign in to save a profile, or use POST /match to score one "
                   "without saving",
        )
    company = db.execute(
        select(Company).where(Company.user_id == user.id).order_by(Company.id.desc())
    ).scalars().first()
    if company is None:
        company = Company(user_id=user.id)
        db.add(company)
    _apply(company, payload)
    db.commit()
    return company


@app.get("/companies/{company_id}", response_model=CompanyOut)
def get_company(
    company_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    return _owned(company, user)


@app.put("/companies/{company_id}", response_model=CompanyOut)
def update_company(
    company_id: int,
    payload: CompanyIn,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
):
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    _apply(_owned(company, user), payload)
    db.commit()
    return company


@app.get("/companies/{company_id}/matches", response_model=list[MatchOut])
def company_matches(
    company_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
    limit: int = Query(50, ge=1, le=200),
):
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    _owned(company, user)
    return [
        MatchOut(score=score, reasons=reasons, tender=TenderOut.model_validate(t))
        for score, reasons, t in find_matches(db, company, limit=limit)
    ]


@app.post("/match", response_model=list[MatchOut])
def match_preview(
    request: Request,
    payload: CompanyIn,
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
):
    """Score a profile without saving it. This is the no-account path, so nothing
    it receives is persisted -- no row, no company name, no contact address.

    Rate limited because it is both unauthenticated and the most expensive
    endpoint here: it scans every open tender and runs regexes over each one.
    """
    security.enforce(
        f"match:{security.client_ip(request)}", 30, 300,
        "too many preview requests; sign in or try again shortly",
    )
    return [
        MatchOut(score=score, reasons=reasons, tender=TenderOut.model_validate(t))
        for score, reasons, t in find_matches(db, payload, limit=limit)
    ]


@app.get("/runs")
def list_runs(db: Session = Depends(get_db), limit: int = Query(20, ge=1, le=200)):
    """Recent connector runs -- how you notice a source started refusing us."""
    rows = db.execute(
        select(ConnectorRun).order_by(ConnectorRun.started_at.desc()).limit(limit)
    ).scalars()
    return [
        {
            "source": r.source_name,
            "status": r.status,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "fetched": r.fetched,
            "new": r.new,
            "updated": r.updated,
            "errors": r.errors,
            "message": r.message,
        }
        for r in rows
    ]


def _require_cron_secret(request: Request) -> None:
    """Shared by both cron endpoints -- one door, one lock.

    No secret configured -> 503. An endpoint that writes to the database must
    never be reachable by default because someone forgot a variable.
    """
    secret = os.getenv("CRON_SECRET", "").strip()
    if not secret:
        log.error("cron endpoint called but CRON_SECRET is not configured")
        raise HTTPException(status_code=503, detail="CRON_SECRET is not configured")
    if not hmac.compare_digest(
        request.headers.get("authorization", ""), f"Bearer {secret}"
    ):
        log.warning("cron: rejected a request with a bad or missing secret")
        raise HTTPException(status_code=401, detail="unauthorized")


@app.get("/cron/ingest")
def cron_ingest(request: Request):
    """Fetch newly published tenders. Called daily by Vercel Cron.

    A full backfill cannot run here -- 3,200 pages at 3s is hours, against a
    300s function ceiling. An incremental run can: CPPP's listing is sorted by
    publication date descending, so `since` makes fetch_batch stop as soon as it
    reaches a record older than the window, which for one day is ~17 pages.

    Two bounds keep it inside the ceiling rather than hoping:

      * max_pages, so a connector whose listing is not date-sorted (the GePNIC
        ones are ordered by closing date) cannot walk forever.
      * a wall-clock deadline passed INTO each connector, so it stops mid-crawl
        rather than only between sources. Checking between connectors was not
        enough: CPPP is first in the registry and 40 pages at 3s is 120s on its
        own, so the between-check never got the chance to fire and the function
        was killed at the ceiling with no response body at all.

    Nothing is lost when it stops early -- rows commit in batches as they
    arrive, and the next run's overlapping `since` window re-reads whatever this
    one did not reach.
    """
    _require_cron_secret(request)

    budget = float(os.getenv("INGEST_BUDGET_SECONDS", "240"))
    since_hours = int(os.getenv("INGEST_SINCE_HOURS", "48"))
    max_pages = int(os.getenv("INGEST_MAX_PAGES", "25"))
    since = utcnow() - timedelta(hours=since_hours)

    started = time.monotonic()
    deadline = started + budget
    results, unreached = [], []
    for name, connector_cls in REGISTRY.items():
        if time.monotonic() >= deadline:
            unreached.append(name)
            continue
        connector = connector_cls()
        if hasattr(connector, "max_pages"):
            connector.max_pages = max_pages
        try:
            summary = connector.run(since=since, deadline=deadline)
            results.append({
                "source": name, "status": summary.status,
                "fetched": summary.fetched, "new": summary.new,
                "updated": summary.updated, "skipped": summary.skipped,
                "errors": summary.errors, "message": summary.message,
            })
        except Exception as exc:                      # one source must not sink the run
            log.exception("cron/ingest: %s failed", name)
            results.append({"source": name, "status": "error", "message": str(exc)})
        finally:
            connector.close()

    return {
        "ran": results,
        "not_reached": unreached,          # ran out of budget; next run picks them up
        "seconds": round(time.monotonic() - started, 1),
        "since_hours": since_hours,
    }


@app.get("/cron/purge")
def cron_purge(request: Request):
    """Delete expired tenders. Meant to be called once a day by Vercel Cron.

    `app/scheduler.py` holds the same job on a timer, but serverless has no
    always-on process to hold a timer in -- so on Vercel the trigger has to
    arrive as an HTTP request. Vercel Cron sends `Authorization: Bearer
    $CRON_SECRET` on every scheduled call; that shared secret is the only thing
    standing between this URL and anyone who can guess a path, so:

      * no secret configured -> refuse (503). An endpoint that deletes rows must
        never be reachable by default just because someone forgot a variable.
      * wrong secret -> 401, compared with hmac.compare_digest so a timing
        difference cannot be used to feel out the right value.

    The response says what it did, because a cron job whose only record is a
    202 is a cron job nobody notices has stopped working.
    """
    _require_cron_secret(request)

    if DEFAULT_RETENTION_DAYS is None:
        return {
            "deleted": 0,
            "detail": "RETENTION_DAYS is unset, so nothing is deleted",
        }
    deleted = purge_expired(session_factory=SessionLocal)
    log.info("cron/purge deleted %d tender(s)", deleted)
    return {"deleted": deleted, "retention_days": DEFAULT_RETENTION_DAYS}
