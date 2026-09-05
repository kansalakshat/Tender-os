"""Bare-bones HTML: browse the corpus, or answer the questionnaire once and get
matches on every visit after that.

No jinja2 and no python-multipart: pages post/read JSON from the existing API
with a few lines of fetch, so there is no second parsing path, no new dependency,
and no filter logic duplicated out of app/api.py.

The questionnaire markup lives in one string (PROFILE_FIELDS) used by both the
signup page and the edit page, so the two can never drift apart.
"""
from __future__ import annotations

from html import escape

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import oauth
from .auth import current_user
from .db import get_db
from .matching import MP_DISTRICTS, SECTOR_LABELS, STATES, find_matches
from .models import Company, Source, Tender, User

router = APIRouter()


# Shared chrome. Enough CSS to be readable on a projector, not a design system.
HEAD = """<!doctype html><meta charset=utf-8><meta name=viewport
 content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<style>
 body{font:15px/1.5 system-ui,sans-serif;max-width:52rem;margin:2rem auto;padding:0 1rem;color:#222}
 nav{border-bottom:1px solid #ddd;padding-bottom:.6rem;margin-bottom:1.4rem;
     display:flex;gap:1rem;align-items:baseline;flex-wrap:wrap}
 nav a{text-decoration:none;color:#06c} nav .me{margin-left:auto;color:#666;font-size:.9em}
 h1{font-size:1.4rem} h2{font-size:1.05rem;margin:1.6rem 0 .4rem}
 label{display:inline-block}
 input,select,button,textarea{font:inherit;padding:.3rem}
 fieldset{border:1px solid #ddd;margin:0 0 1rem;padding:.6rem 1rem}
 legend{color:#666;font-size:.9em;padding:0 .3rem}
 select[multiple]{width:100%;max-width:34rem}
 ol,ul{padding-left:1.4rem} li{margin:.7rem 0}
 small{color:#666} .score{display:inline-block;min-width:2.2rem;font-weight:700;color:#063}
 .hint{color:#777;font-size:.87em;margin:.15rem 0 0}
 #err,.err{color:#b00;white-space:pre-wrap}
 .banner{padding:.55rem .8rem;border-radius:4px;margin:0 0 1.2rem;font-size:.92em}
 .warn{background:#fff6e0;border:1px solid #e8cf90}
 .good{background:#e9f7ec;border:1px solid #a8d8b4}
 .gbtn{display:inline-block;padding:.45rem .9rem;border:1px solid #ccc;border-radius:4px;
       text-decoration:none;color:#222;background:#fff}
 .gbtn:hover{background:#f5f5f5}
 .divider{color:#999;margin:1rem 0;font-size:.9em}
 .cta{margin-top:1.6rem} .spaced{margin-top:2rem}
</style>
<div id=flash></div>
<nav><a href="/">My matches</a><a href="/browse">Browse all tenders</a>
<a href="/docs">API docs</a><span class=me id=me></span></nav>
<script>
// Everything below writes into innerHTML, so anything originating from the
// database goes through this first. Input validation rejects markup in an email
// address; this is the second line, for whatever validation ever misses.
function esc(s){return String(s??'').replace(/[<>&"']/g,
  c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));}

function flash(html, kind){
  document.getElementById('flash').innerHTML =
    '<div class="banner '+(kind||'warn')+'">'+html+'</div>';
}

function resend(){
  fetch('/auth/resend-verification',{method:'POST'}).then(r=>r.json()).then(d=>{
    flash(d.delivered
      ? 'Sent. Check your inbox.'
      : 'This server has no mail credentials, so the link was written to '
        +'<code>outbox.log</code> in the project folder.', 'good');
  });
}

// Messages carried back on a redirect (verification click, OAuth failure).
const _q = new URLSearchParams(location.search);
if(_q.get('verify')==='ok') flash('Email address confirmed. Thanks.', 'good');
if(_q.get('verify')==='invalid')
  flash('That confirmation link is invalid or has expired. '
       +'<a href="#" data-action="resend">Send a new one</a>.');
if(_q.get('error')==='state')
  flash('That sign-in link expired or did not come from here. Please try again.');
if(_q.get('error')==='google') flash('Google sign-in did not complete. Please try again.');
if(_q.get('error')==='cancelled') flash('Google sign-in was cancelled.');

// Every page shows who you are, so "did my save go to my account?" is never a guess.
fetch('/me').then(r=>r.json()).then(d=>{
  const el=document.getElementById('me');
  el.innerHTML = d.user
    ? esc(d.user.email)+' &middot; <a href="/profile">edit answers</a> &middot; '
      +'<a href="#" data-action="signout">sign out</a>'
    : '<a href="/login">sign in</a> &middot; <a href="/signup">create account</a>';
  // Nagged, not blocked: an unconfirmed address must not lock anyone out of the
  // matches they already answered for. Flip REQUIRE_EMAIL_VERIFICATION to change.
  if(d.user && !d.user.email_verified && !_q.get('verify')){
    flash('Please confirm <b>'+esc(d.user.email)+'</b>. '
          +'<a href="#" data-action="resend">Resend the link</a>.'
          +(d.smtp_configured ? '' : ' <i>(no mail server configured here &mdash; '
            +'the link goes to <code>outbox.log</code>)</i>'));
  }
});
function signout(){
  fetch('/auth/logout',{method:'POST'}).then(()=>location.assign('/'));}

// Inline onclick="" attributes are blocked by our own Content-Security-Policy:
// a nonce whitelists a <script> block, it does NOT whitelist attribute handlers.
// One delegated listener covers links that are inserted later via innerHTML.
document.addEventListener('click', function(e){
  const el = e.target.closest('[data-action]');
  if(!el) return;
  e.preventDefault();
  if(el.dataset.action === 'signout') signout();
  if(el.dataset.action === 'resend') resend();
});

</script>"""


# Rendered only when GOOGLE_CLIENT_ID/SECRET are set. An always-visible button that
# 404s is worse than no button, so unconfigured means absent, not broken.
GOOGLE_BUTTON = """<p><a class=gbtn href="/auth/google">Continue with Google</a></p>
<p class=divider>&mdash; or __ALTERNATIVE__ &mdash;</p>"""


def google_block(alternative: str) -> str:
    if not oauth.configured():
        return ""
    return GOOGLE_BUTTON.replace("__ALTERNATIVE__", alternative)


def page(title: str, body: str, nonce: str = "") -> str:
    """Render a page and nonce its inline script/style blocks.

    Every <script> and <style> here is written by us and lives in this file, so
    they can be allow-listed individually. That is what lets the CSP refuse
    'unsafe-inline': markup injected into a page has no valid nonce and will not
    execute, whatever gets past input validation.
    """
    html = HEAD.replace("__TITLE__", title) + body
    if nonce:
        html = html.replace("<script>", f'<script nonce="{nonce}">')
        html = html.replace("<style>", f'<style nonce="{nonce}">')
    return html


def nonce_of(request: Request) -> str:
    return getattr(request.state, "csp_nonce", "")


# ---- the questionnaire, asked once ----------------------------------------
# No <form>, no submit button, no <script>: the two pages that use this supply
# their own, because signup has to create the account first and editing does not.

PROFILE_FIELDS = """<fieldset><legend>What you do</legend>
<p>Company name*<br><input name=name required size=40></p>
<p>Sectors you work in*<br>__SECTORS__</p>
<p>Keywords &mdash; comma separated (e.g. transformer, cable)<br>
   <input name=keywords size=50></p>
<p class=hint>A keyword must appear in the tender title as a whole word.</p>
</fieldset>

<fieldset><legend>Where you work</legend>
<p>States / UTs &mdash; hold Ctrl to pick several<br>
   <select name=states multiple size=6>__STATES__</select></p>
<p>Madhya Pradesh districts &mdash; comma separated<br>
   <input name=districts size=50 list=dl>
   <datalist id=dl>__DISTRICTS__</datalist></p>
</fieldset>

<fieldset><legend>Who you want to work with</legend>
<p>Buyers you want &mdash; hold Ctrl to pick several<br>
   <select name=buyers multiple size=8>__BUYERS__</select></p>
<p>Buyers to never show &mdash; hold Ctrl to pick several<br>
   <select name=exclude_buyers multiple size=6>__BUYERS__</select></p>
<p class=hint>Excluding a buyer removes its tenders entirely. One buyer alone is
   over half the corpus, so this is the fastest way to cut noise.</p>
</fieldset>

<fieldset><legend>What to rule out</legend>
<p>Never show tenders whose title contains &mdash; comma separated<br>
   <input name=exclude_keywords size=50 placeholder="e.g. scrap, auction"></p>
<p>Days you need to prepare a bid<br>
   <input name=min_lead_days type=number value=7 min=0 max=365></p>
<p>Max project value you can execute, INR<br>
   <input name=max_project_value type=number min=0></p>
<p class=hint>Value is not published on any listing page we are allowed to read, so
   this answer has no effect yet. It starts working when detail-page ingest lands.</p>
</fieldset>"""


# Shared by both pages. Reads whatever profile inputs are present -- the signup
# page omits contact_email (it would be asking for an address twice), so every
# lookup tolerates a missing field rather than assuming the full form.
COLLECT_JS = """<script>
function collectProfile(f){
  const el=n=>f.elements[n];
  const v=n=>el(n)?el(n).value.trim():'';
  const csv=n=>{const x=v(n); return x?x.split(',').map(s=>s.trim()).filter(Boolean):[];};
  const picked=n=>el(n)?[...el(n).selectedOptions].map(o=>o.value):[];
  return {
    name:v('name'),
    contact_email:v('contact_email')||null,
    sectors:[...f.querySelectorAll('input[name=sectors]:checked')].map(c=>c.value),
    keywords:csv('keywords'),
    districts:csv('districts'),
    states:picked('states'),
    buyers:picked('buyers'),
    exclude_keywords:csv('exclude_keywords'),
    exclude_buyers:picked('exclude_buyers'),
    min_lead_days:parseInt(v('min_lead_days')||'7',10),
    max_project_value:v('max_project_value')||null
  };
}
function saveProfile(body){
  return fetch('/companies',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})
   .then(r=>r.json().then(d=>{if(!r.ok) throw d.detail; return d;}));
}
function showErr(e){
  document.getElementById('err').textContent =
    typeof e==='string' ? e : JSON.stringify(e,null,1);
}
</script>"""


def _fill(markup: str, db: Session) -> str:
    """Populate the questionnaire's option lists from the taxonomy and the corpus."""
    boxes = "".join(
        f'<label><input type=checkbox name=sectors value="{k}"> {escape(v)}</label><br>'
        for k, v in sorted(SECTOR_LABELS.items(), key=lambda kv: kv[1])
    )
    districts = "".join(f'<option value="{d}">' for d in MP_DISTRICTS)
    states = "".join(f"<option>{escape(s)}</option>" for s in STATES)
    # Offered from the corpus, not a hard-coded list: the option text is exactly the
    # string stored in tenders.organization, so anything offered here can match.
    buyers = "".join(
        f'<option value="{escape(name)}">{escape(name)} ({n})</option>'
        for name, n in db.execute(
            select(Tender.organization, func.count())
            .where(Tender.organization.is_not(None))
            .group_by(Tender.organization)
            .order_by(func.count().desc())
            .limit(40)
        ).all()
    )
    # .replace, not .format: the inline <script> is full of literal braces.
    return (
        markup.replace("__SECTORS__", boxes)
        .replace("__DISTRICTS__", districts)
        .replace("__STATES__", states)
        .replace("__BUYERS__", buyers)
    )


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
    """Signed in with answers already given, this is your matches -- the questions
    are asked once, at signup, and never again unless you ask to edit them."""
    profile = _profile_of(db, user)
    if profile is not None:
        return RedirectResponse(f"/c/{profile.id}", status_code=303)
    if user is not None:
        # Signed in but never finished the questionnaire (e.g. it failed validation
        # during signup). Ask for it now rather than showing an empty matches page.
        return RedirectResponse("/profile", status_code=303)
    return page("Find tenders", WELCOME, nonce_of(request))


WELCOME = """<h1>Find the tenders worth your time</h1>
<p>8,000+ live notices from official Indian government procurement portals, ranked
against what your company actually does.</p>
<p>You answer the questionnaire <b>once</b>, when you create your account. After
that, signing in takes you straight to your matches.</p>
<p class=cta>
  <a href="/signup"><button>Create account &amp; answer once</button></a>
  &nbsp; <a href="/login">I already have an account</a></p>
<p class="hint spaced">Just looking? <a href="/browse">Browse every
collected tender</a> without an account.</p>"""


# ---- signup: account + questionnaire, one submit ---------------------------

SIGNUP = """<h1>Create your account</h1>
<p class=hint>Everything below is asked once. You will not be asked again.</p>
__GOOGLE__
<form id=f>

<fieldset><legend>Sign-in details</legend>
<p>Email<br><input name=email type=email required size=34 autocomplete=username></p>
<p>Password<br><input name=password type=password required size=34
   autocomplete=new-password></p>
<p class=hint>At least 10 characters. Stored as a salted scrypt hash, never in
   plain text. We use your sign-in email as the contact address on the profile.</p>
</fieldset>

__PROFILE_FIELDS__

<p><button>Create account &amp; show my matches</button></p>
</form>
<pre id=err></pre>
<p>Already have one? <a href="/login">Sign in</a>.</p>
__COLLECT_JS__
<script>
// If the account is created but the profile is rejected, a retry must not try to
// register the same email again -- it would fail with "already registered" and
// strand the user on a form they cannot submit.
let accountCreated = false;

function go(e){
  e.preventDefault();
  const f = e.target;
  const body = collectProfile(f);
  body.contact_email = f.email.value.trim() || null;

  const account = accountCreated ? Promise.resolve() :
    fetch('/auth/signup',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email:f.email.value, password:f.password.value})})
     .then(r=>r.json().then(d=>{if(!r.ok) throw d.detail; accountCreated=true;}));

  account.then(()=>saveProfile(body))
         .then(d=>location.assign('/c/'+d.id))
         .catch(showErr);
}
document.getElementById('f').addEventListener('submit', go);
</script>"""


@router.get("/signup", response_class=HTMLResponse)
def signup_page(request: Request, db: Session = Depends(get_db)) -> str:
    body = (
        SIGNUP.replace("__PROFILE_FIELDS__", PROFILE_FIELDS)
        .replace("__COLLECT_JS__", COLLECT_JS)
        .replace("__GOOGLE__", google_block("sign up with an email address"))
    )
    return page("Create account", _fill(body, db), nonce_of(request))


# ---- login -----------------------------------------------------------------

LOGIN = """<h1>Sign in</h1>
__GOOGLE__
<form id=f>
<p>Email<br><input name=email type=email required size=34 autocomplete=username></p>
<p>Password<br><input name=password type=password required size=34
   autocomplete=current-password></p>
<p class=hint>Takes you straight to your matches. Your answers are already saved.</p>
<p><button>Sign in</button></p>
</form>
<p class=err id=err></p>
<p>No account yet? <a href="/signup">Create one</a>.</p>
<script>
function go(e){
  e.preventDefault();
  const f=e.target;
  fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email:f.email.value,password:f.password.value})})
   .then(r=>r.json().then(d=>r.ok?location.assign('/')
     :document.getElementById('err').textContent=d.detail));
}
document.getElementById('f').addEventListener('submit', go);
</script>"""


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request) -> str:
    return page(
        "Sign in",
        LOGIN.replace("__GOOGLE__", google_block("sign in with a password")),
        nonce_of(request),
    )


# ---- editing the answers (the deliberate second visit) ---------------------

EDIT = """<h1>__HEADING__</h1>
<p class=hint id=savehint>__HINT__</p>
<form id=f>
__PROFILE_FIELDS__
<fieldset><legend>Contact</legend>
<p>Contact email<br><input name=contact_email type=email size=40></p>
</fieldset>
<p><button>Save &amp; show my matches</button></p>
</form>
<pre id=err></pre>
<div id=preview></div>
__COLLECT_JS__
<script>
// Pre-fill, so "change one answer" is never "type all of them again".
fetch('/me').then(r=>r.json()).then(d=>{
  const c = d.company; if(!c) return;
  const f = document.getElementById('f');
  f.name.value=c.name||''; f.contact_email.value=c.contact_email||'';
  f.keywords.value=(c.keywords||[]).join(', ');
  f.districts.value=(c.districts||[]).join(', ');
  f.exclude_keywords.value=(c.exclude_keywords||[]).join(', ');
  f.min_lead_days.value=c.min_lead_days??7;
  f.max_project_value.value=c.max_project_value??'';
  (c.sectors||[]).forEach(s=>{
    const b=f.querySelector('input[name=sectors][value="'+s+'"]'); if(b)b.checked=true;});
  [['states',c.states],['buyers',c.buyers],['exclude_buyers',c.exclude_buyers]]
    .forEach(([n,vals])=>[...f.elements[n].options].forEach(
      o=>{if((vals||[]).includes(o.value))o.selected=true;}));
});

let signedIn = false;
fetch('/me').then(r=>r.json()).then(d=>{ signedIn = !!d.user; });

function renderPreview(matches){
  const box = document.getElementById('preview');
  if(!matches.length){ box.innerHTML = '<p>Nothing matched. Broaden the sectors, '
    + 'drop a state, or lower the preparation days.</p>'; return; }
  box.innerHTML = '<h2>' + matches.length + ' matching tender(s)</h2><ol>' + matches.map(m =>
    '<li><span class=score>' + m.score + '</span> &mdash; <a href="'
    + esc(m.tender.document_url || m.tender.source_url) + '">' + esc(m.tender.title) + '</a><br>'
    + '<small>' + esc(m.tender.organization || '') + ' &middot; closes ' + esc(m.tender.deadline)
    + ' &middot; ' + esc((m.reasons||[]).join('; ')) + '</small></li>').join('') + '</ol>'
    + '<p class=hint>Nothing here was saved. <a href="/signup">Create an account</a> '
    + 'to keep these answers.</p>';
  box.scrollIntoView({behavior:'smooth'});
}

function go(e){
  e.preventDefault();
  const body = collectProfile(e.target);
  if(signedIn){
    saveProfile(body).then(d=>location.assign('/c/'+d.id)).catch(showErr);
    return;
  }
  // No account: score without storing. POST /match persists nothing -- no row,
  // no company name, no contact address.
  fetch('/match?limit=25',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})
   .then(r=>r.json().then(d=>{if(!r.ok) throw d.detail; renderPreview(d);}))
   .catch(showErr);
}
document.getElementById('f').addEventListener('submit', go);
</script>"""


@router.get("/profile", response_class=HTMLResponse)
def profile_page(request: Request, db: Session = Depends(get_db),
                 user: User | None = Depends(current_user)):
    """Edit the answers given at signup. Signed out this is the no-account preview
    path -- answer, see matches by link, decide whether to sign up afterwards."""
    existing = _profile_of(db, user)
    if user is None:
        heading, hint = "Try it without an account", (
            "Answers are scored against every open tender and shown below. Nothing "
            "is stored -- create an account if you want them saved."
        )
    elif existing is None:
        heading, hint = "Finish your profile", "One step left, then you are done."
    else:
        heading, hint = "Edit your answers", "Saving overwrites your saved profile."
    body = (
        EDIT.replace("__PROFILE_FIELDS__", PROFILE_FIELDS)
        .replace("__COLLECT_JS__", COLLECT_JS)
        .replace("__HEADING__", heading)
        .replace("__HINT__", hint)
    )
    return page(heading, _fill(body, db), nonce_of(request))


# ---- matches ---------------------------------------------------------------

@router.get("/c/{company_id}", response_class=HTMLResponse)
def results(
    request: Request,
    company_id: int,
    db: Session = Depends(get_db),
    user: User | None = Depends(current_user),
) -> str:
    company = db.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail="company not found")
    # Same rule as api._owned: signed in, and it is yours. 404, not 403, so the
    # existence of an id is not confirmed to a stranger.
    if user is None or company.user_id is None or company.user_id != user.id:
        raise HTTPException(status_code=404, detail="company not found")
    matches = find_matches(db, company, limit=50)

    rows = []
    for score, reasons, t in matches:
        # escape(): titles come from a scraped page and go straight into HTML.
        link = escape(t.document_url or t.source_url)
        rows.append(
            f'<li><span class=score>{score}</span> &mdash; '
            f'<a href="{link}">{escape(t.title)}</a><br>'
            f"<small>{escape(t.organization or '')} &middot; "
            f"closes {t.deadline} &middot; {escape('; '.join(reasons))}</small></li>"
        )
    body = "<ol>" + "".join(rows) + "</ol>" if rows else (
        "<p>No open tenders match this profile yet. Broaden the sectors, drop a "
        "state, or lower the preparation days.</p>"
    )
    return page(
        "Matches",
        f"<h1>Matches for {escape(company.name)}</h1>"
        f'<p>{len(matches)} match(es). <a href="/profile">Edit answers</a></p>{body}',
        nonce_of(request),
    )


# ---- corpus browser --------------------------------------------------------

BROWSE = """<h1>All collected tenders</h1>
<form id=f>
<p><input name=q size=34 placeholder="search titles, e.g. transformer">
   <select name=source_id><option value="">every source</option>__SOURCES__</select>
   <select name=sort>
     <option value=deadline>closing soonest</option>
     <option value=first_seen_at>most recently collected</option>
     <option value=published_date>most recently published</option>
   </select>
   <button>Search</button></p>
<p><label><input type=checkbox name=open_only checked> only tenders still open</label></p>
</form>
<p id=count>loading&hellip;</p>
<ol id=rows start=1></ol>
<p><button id=prev>&larr; prev</button> <button id=next>next &rarr;</button></p>
<pre id=err></pre>
<script>
const LIMIT=25; let offset=0;
const esc=s=>String(s??'').replace(/[<>&"]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;'}[c]));
const inr=v=>v==null?'':' &middot; INR '+Number(v).toLocaleString('en-IN');
function go(e,off){
  if(e) e.preventDefault();
  offset = off ?? 0;
  const f=document.getElementById('f'), p=new URLSearchParams();
  if(f.q.value.trim()) p.set('q', f.q.value.trim());
  if(f.source_id.value) p.set('source_id', f.source_id.value);
  p.set('sort', f.sort.value);
  // Sorting by deadline with closed rows in would bury the useful ones.
  if(f.open_only.checked) p.set('deadline_from', new Date().toISOString().slice(0,10));
  p.set('limit', LIMIT); p.set('offset', offset);
  fetch('/tenders?'+p).then(r=>r.json()).then(d=>{
    document.getElementById('count').innerHTML =
      d.total.toLocaleString() + ' tenders &middot; showing ' +
      (d.total?offset+1:0) + '-' + Math.min(offset+LIMIT, d.total);
    document.getElementById('rows').setAttribute('start', offset+1);
    document.getElementById('rows').innerHTML = d.items.map(t=>
      '<li><a href="'+esc(t.document_url||t.source_url)+'">'+esc(t.title)+'</a><br>'+
      '<small>'+esc(t.organization||'unnamed buyer')+' &middot; closes '+
      (t.deadline||'not stated')+inr(t.estimated_value)+'</small></li>').join('')
      || '<p>Nothing matched.</p>';
    document.getElementById('prev').disabled = offset===0;
    document.getElementById('next').disabled = offset+LIMIT >= d.total;
  }).catch(e=>document.getElementById('err').textContent=e);
}
document.getElementById('f').addEventListener('submit', e=>go(e,0));
prev.onclick=()=>go(null, Math.max(0, offset-LIMIT));
next.onclick=()=>go(null, offset+LIMIT);
go(null,0);
</script>"""


@router.get("/browse", response_class=HTMLResponse)
def browse(request: Request, db: Session = Depends(get_db)) -> str:
    """Read-only view of the whole corpus. Filtering happens in GET /tenders --
    this page is the form around it, not a second query path."""
    opts = "".join(
        f'<option value="{s.id}">{escape(s.name)}</option>'
        for s in db.execute(select(Source).order_by(Source.name)).scalars()
    )
    return page("Browse tenders", BROWSE.replace("__SOURCES__", opts), nonce_of(request))
