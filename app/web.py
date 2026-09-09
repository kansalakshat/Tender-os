"""Bare-bones HTML: browse the corpus, or answer the questionnaire once and get
matches on every visit after that.

No jinja2 and no python-multipart: pages post/read JSON from the existing API
with a few lines of fetch, so there is no second parsing path, no new dependency,
and no filter logic duplicated out of app/api.py.

The questionnaire markup lives in one string (PROFILE_FIELDS) used by both the
signup page and the edit page, so the two can never drift apart.

Styling is one nonced style block in HEAD. Nothing loads from a CDN: the site
CSP is default-src 'self', so the font, the animation library and the icon set
are self-hosted under /static and served from our own origin, which keeps the
policy strict rather than forcing an allow-listed third party.
"""
from __future__ import annotations

import re
from datetime import date, timedelta
from html import escape
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import distinct, func, or_, select
from sqlalchemy.orm import Session

from . import oauth
from .auth import current_user
from .db import get_db
from .matching import MP_DISTRICTS, SECTOR_LABELS, STATES, find_matches
from .models import Company, ConnectorRun, Source, Tender, User

router = APIRouter()


# Phosphor Regular, one family for the whole site, read off disk once at import
# and inlined. An <svg><use href="/static/..."> would be a request per icon and
# is blocked cross-document in some browsers; inlining also lets each glyph
# inherit currentColor, and the theme with it.
_ICON_DIR = Path(__file__).resolve().parent.parent / "static" / "icons"
_ICONS: dict[str, str] = {}
if _ICON_DIR.is_dir():
    for _f in _ICON_DIR.glob("*.svg"):
        _ICONS[_f.stem] = re.sub(
            r"^<svg[^>]*>|</svg>$", "", _f.read_text(encoding="utf-8").strip()
        )


def icon(name: str, cls: str = "i") -> str:
    """Inline one Phosphor glyph. Decorative by default: every icon here sits
    beside its own text label, so it is hidden from screen readers rather than
    announced twice."""
    return (
        f'<svg class="{cls}" viewBox="0 0 256 256" fill="currentColor" '
        f'aria-hidden=true focusable=false>{_ICONS.get(name, "")}</svg>'
    )


# Templates name a glyph with a placeholder and page() resolves it in one place,
# so no template imports anything and no page can ship an unsubstituted token.
ICON_SLOTS = {
    "__I_SEARCH__": "magnifying-glass",
    "__I_ARROW__": "arrow-right",
    "__I_UPRIGHT__": "arrow-up-right",
    "__I_CLOCK__": "clock-countdown",
    "__I_BUILDINGS__": "buildings",
    "__I_LEFT__": "caret-left",
    "__I_RIGHT__": "caret-right",
}


# A ranked-list mark: three rules of decreasing width. Inline data: URI because
# img-src is 'self' data: and a favicon is somewhere CSS cannot reach.
FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'"
    "%3E%3Crect width='32' height='32' fill='%230A0A0B'/%3E%3Cg fill='%23F5F2EC'"
    "%3E%3Crect x='7' y='9' width='18' height='3'/%3E%3Crect x='7' y='14.5'"
    " width='12' height='3'/%3E%3Crect x='7' y='20' width='6' height='3'"
    "/%3E%3C/g%3E%3C/svg%3E"
)


HEAD = """<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport
 content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<meta name=description content="Open Indian government tender notices, collected
 from official procurement portals and ranked against what your company does.">
<meta name=color-scheme content="light dark">
<meta name=theme-color content="#F5F2EC" media="(prefers-color-scheme:light)">
<meta name=theme-color content="#0A0A0B" media="(prefers-color-scheme:dark)">
<link rel=icon href="__FAVICON__">
<style>
 /* Self-hosted from /static, so the strict CSP stays intact: font-src falls back
    to default-src 'self'. Geist carries 800 weight in one variable file, which is
    what makes a 120px headline possible without a second download. */
 @font-face{font-family:Geist;src:url(/static/fonts/Geist.woff2) format("woff2");
   font-weight:100 900;font-display:swap;font-style:normal}
 @font-face{font-family:"Geist Mono";
   src:url(/static/fonts/GeistMono.woff2) format("woff2");
   font-weight:100 900;font-display:swap;font-style:normal}

 /* Brutalist editorial: newsprint ground, true-black rules, one electric accent.
    Nothing is rounded and nothing floats; structure comes from rule weight alone.
    Every text pair below clears 4.5:1 in both themes. */
 :root{
  color-scheme:light dark;
  --paper:#F5F2EC; --panel:#FFFDF8; --ink:#0A0A0B; --muted:#55555A;
  --accent:#1B24FF; --accent-h:#000AE0; --on-accent:#FFFFFF;
  --rule:#0A0A0B; --hair:rgba(10,10,11,.18);
  --danger:#B01206; --ok:#0A6B3D;
  --b:2px; --b2:3px;
  --sans:Geist,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"Geist Mono",ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace;
 }
 @media (prefers-color-scheme:dark){:root{
  --paper:#0A0A0B; --panel:#141416; --ink:#F5F2EC; --muted:#9C9CA2;
  --accent:#8F95FF; --accent-h:#B4B8FF; --on-accent:#0A0A0B;
  --rule:#F5F2EC; --hair:rgba(245,242,236,.22);
  --danger:#FF8A7A; --ok:#7FD3A6;
 }}
 *{box-sizing:border-box}
 a,button,input,select,label{touch-action:manipulation;
   -webkit-tap-highlight-color:transparent}
 html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
 body{margin:0;background:var(--paper);color:var(--ink);
      font:400 16px/1.5 var(--sans);letter-spacing:-.008em;
      -webkit-font-smoothing:antialiased;
      min-height:100dvh;display:flex;flex-direction:column}
 .wrap{max-width:78rem;margin:0 auto;padding:0 1.5rem;width:100%}
 main{display:block;flex:1}
 @media (max-width:34rem){.wrap{padding:0 1rem}}

 .skip{position:absolute;left:-9999px}
 .skip:focus{position:fixed;left:1rem;top:1rem;z-index:9;background:var(--accent);
   color:#FFF;border:var(--b2) solid var(--ink);padding:.7rem 1rem;
   text-decoration:none;font:600 .8rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.1em}

 /* --- type ------------------------------------------------------------------ */
 h1{margin:0;font-weight:800;text-transform:uppercase;line-height:.87;
    letter-spacing:-.045em;font-size:clamp(2.3rem,.4rem + 5.9vw,5.75rem)}
 h2{margin:0;font-weight:700;text-transform:uppercase;letter-spacing:-.02em;
    line-height:1;font-size:clamp(1.4rem,1rem + 1.5vw,2.25rem)}
 p{margin:0;text-wrap:pretty}
 .lede{font-size:clamp(1.02rem,.95rem + .45vw,1.35rem);line-height:1.4;
   max-width:46ch}
 .hint{color:var(--muted);font-size:.9rem;max-width:52ch}
 .kicker,.eyebrow{font:500 .72rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.16em;color:var(--muted)}
 a{color:var(--accent);text-underline-offset:3px;text-decoration-thickness:2px}
 a:hover{color:var(--accent-h)}
 code{font:.88em var(--mono);background:var(--ink);color:var(--paper);padding:.1em .3em}
 :focus-visible{outline:var(--b2) solid var(--accent);outline-offset:2px}
 .nowrap{white-space:nowrap}
 .sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
   clip:rect(0 0 0 0);white-space:nowrap;border:0}

 /* --- header ---------------------------------------------------------------- */
 .top{position:sticky;top:0;z-index:8;background:var(--paper);
   border-bottom:var(--b2) solid var(--rule)}
 .top .wrap{display:flex;align-items:center;gap:0;min-height:4.5rem;flex-wrap:wrap}
 .brand{display:inline-flex;align-items:center;gap:.65rem;color:var(--ink);
   text-decoration:none;font:800 1.05rem/1 var(--sans);text-transform:uppercase;
   letter-spacing:-.03em;white-space:nowrap;padding-right:2.25rem}
 .brand span{color:var(--muted);font-weight:500;letter-spacing:.02em}
 .mark{flex:none;width:1.6rem;height:1.6rem;display:block}
 .mark .bg{fill:var(--ink)} .mark .fg{fill:var(--paper)}
 .top nav{display:flex;align-items:center;gap:1.6rem}
 .top nav a{color:var(--ink);text-decoration:none;font:500 .78rem/1 var(--mono);
   text-transform:uppercase;letter-spacing:.12em;padding:.45rem 0;
   border-bottom:var(--b2) solid transparent;transition:color .12s}
 .top nav a:hover{color:var(--accent)}
 .top nav a[aria-current=page]{border-bottom-color:var(--accent);color:var(--accent)}
 .acct{margin-left:auto;display:flex;align-items:center;gap:1.25rem;
   font:500 .78rem/1 var(--mono);text-transform:uppercase;letter-spacing:.1em}
 .acct a{color:var(--ink);text-decoration:none}
 .acct a.btn{color:var(--paper)}
 .acct a.btn:hover{color:#FFF}
 .acct a:hover{color:var(--accent)}
 @media (max-width:52rem){
  .top .wrap{padding-block:.7rem;gap:.5rem 1.4rem;min-height:0}
  .brand{padding-right:0;font-size:.98rem}
  .acct{width:100%;justify-content:flex-end;margin:0}
 }

 /* --- controls: hard edges, no radius anywhere ------------------------------ */
 button,.btn{display:inline-flex;align-items:center;justify-content:center;
   min-height:3rem;padding:0 1.35rem;width:auto;border-radius:0;cursor:pointer;
   text-decoration:none;border:var(--b) solid var(--ink);background:var(--ink);
   color:var(--paper);font:600 .78rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.11em;transition:background .12s,border-color .12s,color .12s}
 button:hover,.btn:hover{background:var(--accent);border-color:var(--accent);
   color:#FFF}
 button:active,.btn:active{transform:translateY(2px)}
 button:disabled{opacity:.3;cursor:not-allowed;transform:none}
 .ghost{background:transparent;color:var(--ink)}
 .ghost:hover{background:var(--ink);border-color:var(--ink);color:var(--paper)}
 .btn.sm{min-height:2.5rem;padding:0 1rem;font-size:.72rem}
 button .i{margin-right:.5rem}
 .pager{display:flex;gap:.75rem;padding:2rem 0 0}
 .pager button .i{margin:0}
 .pager #next .i{margin-left:.5rem}

 input,select,textarea{font:inherit;color:var(--ink);background:var(--panel);
   border:var(--b) solid var(--ink);border-radius:0;padding:.7rem .8rem;
   min-height:3rem;width:100%}
 select[multiple],textarea{min-height:0}
 input::placeholder{color:var(--muted)}
 input[type=checkbox]{width:1.15rem;height:1.15rem;min-height:0;padding:0;
   accent-color:var(--accent)}

 /* --- hero ------------------------------------------------------------------ */
 .hero{border-bottom:var(--b2) solid var(--rule)}
 .hero .wrap{padding-block:clamp(1.75rem,3.5vw,3rem) 0}
 .heroTop{display:grid;gap:1.75rem;align-items:end;
   padding-block:clamp(1.25rem,3vw,2.5rem)}
 @media (min-width:64rem){
  .heroTop{grid-template-columns:minmax(0,1fr) auto;gap:3.5rem}
 }
 .bignum{border-top:var(--b2) solid var(--rule);padding-top:.9rem;min-width:12rem}
 @media (min-width:64rem){.bignum{text-align:right}}
 .bignum b{display:block;font:700 clamp(2.4rem,1rem + 4.2vw,4.75rem)/.85 var(--mono);
   letter-spacing:-.05em;font-variant-numeric:tabular-nums}
 .bignum em{display:block;margin-top:.7rem;font-style:normal;
   font:500 .72rem/1 var(--mono);text-transform:uppercase;letter-spacing:.16em;
   color:var(--muted)}
 .heroCopy{display:grid;gap:1.75rem;padding-block:clamp(1.5rem,3vw,2.25rem);
   border-top:var(--b2) solid var(--rule)}
 @media (min-width:64rem){
  .heroCopy{grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:3.5rem;
    align-items:center}
 }
 .search{display:flex;border:var(--b2) solid var(--ink);background:var(--panel)}
 .search input{border:0;min-height:0;height:3.9rem;font-size:1.05rem;
   padding:0 1.1rem;background:transparent}
 .search input:focus-visible{outline:0;background:var(--paper)}
 .search button{border:0;border-left:var(--b2) solid var(--ink);min-height:0;
   padding:0 1.75rem;flex:none}
 @media (max-width:30rem){
  .search{flex-direction:column}
  .search button{width:100%;border-left:0;border-top:var(--b2) solid var(--ink);
    min-height:3.2rem}
 }

 /* --- stats: blocks divided by rules, not cards ----------------------------- */
 .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(10.5rem,1fr));
   margin:0;border-top:var(--b2) solid var(--rule)}
 .stats > div{padding:1.4rem 1.25rem 1.75rem;border-left:var(--b) solid var(--hair)}
 .stats > div:first-child{border-left:0;padding-left:0}
 .stats dt{font:500 .7rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.16em;color:var(--muted)}
 .stats dd{margin:.8rem 0 0;font-variant-numeric:tabular-nums;letter-spacing:-.045em;
   font:700 clamp(1.9rem,1rem + 2.4vw,3.1rem)/.85 var(--mono)}

 /* --- sections and lists ---------------------------------------------------- */
 .sec{padding-block:clamp(2.5rem,5vw,4rem)}
 .sec + .sec{border-top:var(--b2) solid var(--rule)}
 .secHead{display:flex;align-items:baseline;justify-content:space-between;
   gap:1.5rem;flex-wrap:wrap;margin:0 0 1.75rem}

 .rows{list-style:none;margin:0;padding:0;border-top:var(--b2) solid var(--rule)}
 .row{position:relative;display:grid;grid-template-columns:5.25rem minmax(0,1fr);
   gap:1.25rem;padding:1.15rem 0 1.25rem .9rem;
   border-bottom:var(--b) solid var(--hair)}
 .row::before{content:"";position:absolute;left:0;top:1.15rem;bottom:1.25rem;
   width:4px;background:var(--accent);opacity:0}
 .row.s1::before{opacity:.25} .row.s2::before{opacity:.6} .row.s3::before{opacity:1}
 .row:hover{background:var(--panel)}
 .n{font:600 .78rem/1.6 var(--mono);text-transform:uppercase;letter-spacing:.08em;
   font-variant-numeric:tabular-nums;color:var(--ink)}
 .row.s0 .n,.browse .n{color:var(--muted)}
 .t{color:var(--ink);text-decoration:none;font-weight:600;font-size:1.02rem;
   line-height:1.3;letter-spacing:-.015em;text-wrap:pretty}
 .t:hover{color:var(--accent);text-decoration:underline}
 .m{margin:.4rem 0 0;color:var(--muted);font-size:.8rem;max-width:none;
   font-family:var(--mono);letter-spacing:.01em}
 .m time,.m .v{font-variant-numeric:tabular-nums}
 .tags{display:flex;flex-wrap:wrap;gap:.35rem;margin:.6rem 0 0;padding:0;
   list-style:none}
 .tag{font:500 .68rem/1 var(--mono);text-transform:uppercase;letter-spacing:.08em;
   color:var(--ink);border:var(--b) solid var(--hair);padding:.35rem .45rem}
 .count{margin:0 0 1.25rem;font:500 .78rem/1.4 var(--mono);text-transform:uppercase;
   letter-spacing:.12em;color:var(--muted);font-variant-numeric:tabular-nums}
 @media (max-width:34rem){
  .row{grid-template-columns:minmax(0,1fr);gap:.3rem;padding-left:.8rem}
  .n{font-size:.7rem;color:var(--muted)}
 }

 /* the panel is a bordered block with an inverted head, not a floating card */
 .panel{border:var(--b2) solid var(--ink);background:var(--panel)}
 .panel h2{display:flex;align-items:center;justify-content:space-between;gap:1rem;
   margin:0;padding:.9rem 1.1rem;background:var(--ink);color:var(--paper);
   font-size:.78rem;font-family:var(--mono);font-weight:600;letter-spacing:.12em}
 .panel h2 .lbl{display:inline-flex;align-items:center}
 .panel h2 span{font-variant-numeric:tabular-nums;opacity:.75}
 .panel .rows{border-top:0}
 .panel .row{padding-left:1.1rem;padding-right:1.1rem;
   grid-template-columns:4.5rem minmax(0,1fr)}
 .panel .row:last-of-type{border-bottom:0}
 .more{display:flex;align-items:center;justify-content:space-between;
   padding:1rem 1.1rem;border-top:var(--b) solid var(--ink);text-decoration:none;
   font:600 .76rem/1 var(--mono);text-transform:uppercase;letter-spacing:.11em;
   color:var(--ink)}
 .more:hover{background:var(--accent);color:#FFF}
 .more .i{transition:transform .15s}
 .more:hover .i{transform:translateX(4px)}

 /* buyers, numbered like an index */
 .buyerGrid{counter-reset:b;display:grid;list-style:none;margin:0;padding:0;
   grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));gap:0 3rem}
 .buyerGrid a{display:grid;grid-template-columns:2.25rem minmax(0,1fr) auto;
   align-items:baseline;gap:.85rem;padding:1rem .25rem;color:var(--ink);
   text-decoration:none;border-top:var(--b) solid var(--hair);
   transition:background .12s,padding-left .12s}
 .buyerGrid a:hover{background:var(--panel);padding-left:.7rem;color:var(--accent)}
 .buyerGrid a::before{counter-increment:b;content:counter(b,decimal-leading-zero);
   font:500 .74rem/1 var(--mono);color:var(--accent);letter-spacing:.06em}
 .buyerGrid span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
   font-size:.98rem;font-weight:500;letter-spacing:-.012em}
 .buyerGrid b{font:700 .95rem/1 var(--mono);font-variant-numeric:tabular-nums}

 /* --- forms ----------------------------------------------------------------- */
 form{margin:0}
 fieldset{border:0;border-top:var(--b2) solid var(--rule);margin:2.5rem 0 0;
   padding:1.5rem 0 0}
 legend{padding:0 .8rem 0 0;font:700 .95rem/1 var(--sans);text-transform:uppercase;
   letter-spacing:-.01em}
 .f{display:block;margin:1.25rem 0 0;max-width:38rem}
 .f > span{display:block;margin:0 0 .5rem;font:500 .72rem/1 var(--mono);
   text-transform:uppercase;letter-spacing:.12em;color:var(--muted)}
 .checks{display:grid;grid-template-columns:repeat(auto-fill,minmax(14rem,1fr));
   gap:0 1rem;margin:.5rem 0 0}
 .checks label,.chk label{display:flex;align-items:center;gap:.7rem;
   min-height:2.9rem;padding:0 .45rem;font-size:.94rem;cursor:pointer;
   margin-left:-.45rem}
 .checks label:hover,.chk label:hover{background:var(--panel)}
 .chk label{display:inline-flex;padding-left:0;margin-left:0}
 .actions{display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap;margin:2rem 0 0}
 .divider{margin:1.5rem 0;font:500 .72rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.14em;color:var(--muted)}
 .filters{display:grid;gap:.9rem;align-items:end;margin:0 0 1.5rem;
   grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) auto auto auto}
 .filters .f{margin:0;max-width:none}
 @media (max-width:52rem){.filters{grid-template-columns:1fr;align-items:stretch}}

 /* --- states ---------------------------------------------------------------- */
 .notice{margin:1.25rem 0 0;padding:.9rem 1.1rem;font-size:.9rem;max-width:none;
   background:var(--panel);border:var(--b) solid var(--ink);
   border-left:var(--b2) solid var(--accent)}
 .notice.good{border-left-color:var(--ok)}
 pre#err{margin:1.5rem 0 0;padding:.9rem 1.1rem;overflow-x:auto;white-space:pre-wrap;
   font:.82rem/1.5 var(--mono);color:var(--danger);background:var(--panel);
   border:var(--b) solid var(--danger)}
 pre#err:empty{display:none}
 .empty{padding:3rem 1.5rem;text-align:center;border:var(--b2) dashed var(--hair)}
 .empty p{margin:0 auto;max-width:34ch;color:var(--muted)}
 .sk .b{height:1rem;background:var(--hair);animation:pulse 1.4s ease-in-out infinite}
 .sk .b.s{height:.75rem;max-width:20rem;margin-top:.6rem}
 .sk .b.g{width:2.5rem}
 @keyframes pulse{50%{opacity:.35}}

 /* --- footer: the page inverts ---------------------------------------------- */
 .foot{background:var(--ink);color:var(--paper);
   border-top:var(--b2) solid var(--rule)}
 .foot .wrap{display:grid;gap:2.5rem 3rem;padding-block:3.5rem 2.5rem;
   grid-template-columns:minmax(0,1.6fr) repeat(2,minmax(0,1fr))}
 @media (max-width:52rem){
  .foot .wrap{grid-template-columns:1fr 1fr}
  .fbrand{grid-column:1/-1}
 }
 .foot .brand{color:var(--paper)}
 .foot .brand span{color:var(--paper);opacity:.6}
 .foot .mark .bg{fill:var(--paper)} .foot .mark .fg{fill:var(--ink)}
 .fbrand p{margin:1rem 0 0;max-width:34ch;font-size:.9rem;opacity:.72}
 .foot h2{margin:0 0 1rem;font:500 .72rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.16em;color:var(--paper);opacity:.6}
 .foot ul{display:grid;gap:.7rem;margin:0;padding:0;list-style:none}
 .foot a{color:var(--paper);text-decoration:none;font-size:.95rem}
 .foot a:hover{color:var(--paper);text-decoration:underline;
   text-decoration-thickness:2px}
 .fnote{border-top:var(--b) solid rgba(245,242,236,.25)}
 .fnote .wrap{display:block;padding-block:1.5rem 2.5rem;font-size:.78rem;
   font-family:var(--mono);letter-spacing:.01em}
 .fnote p{margin:0;max-width:80ch;opacity:.6}

 .i{width:1.05em;height:1.05em;flex:none;vertical-align:-.16em}
 .search button .i{width:1.15em;height:1.15em}
 .secHead .i,.panel h2 .i{margin-right:.55rem}
 .hint .i{margin-left:.25rem}
 main h2{display:flex;align-items:center}

 @media (prefers-reduced-motion:reduce){
  html{scroll-behavior:auto}
  *{animation-duration:.01ms !important;animation-iteration-count:1 !important;
    transition-duration:.01ms !important}
 }
</style>
<a class=skip href="#main">Skip to content</a>
<header class=top><div class=wrap>
 <a class=brand href="/">
  <svg class=mark viewBox="0 0 32 32" aria-hidden=true focusable=false>
   <rect class=bg width="32" height="32" rx="8"/>
   <g class=fg><rect x="8" y="9" width="16" height="2.6" rx="1.3"/>
   <rect x="8" y="14.7" width="11" height="2.6" rx="1.3"/>
   <rect x="8" y="20.4" width="6" height="2.6" rx="1.3"/></g>
  </svg>
  <b>Tender</b><span>Aggregator</span></a>
 <nav aria-label="Main"><a href="/">Matches</a><a href="/browse">Browse</a>
 <a href="/docs">API</a></nav>
 <span class=acct id=me></span>
</div></header>
__HERO__
<div class=wrap><div id=flash></div></div>
<main id=main>
<script>
// Everything below writes into innerHTML, so anything originating from the
// database goes through this first. Input validation rejects markup in an email
// address; this is the second line, for whatever validation ever misses.
function esc(s){return String(s??'').replace(/[<>&"']/g,
  c=>({'<':'&lt;','>':'&gt;','&':'&amp;','"':'&quot;',"'":'&#39;'}[c]));}

function flash(html, kind){
  document.getElementById('flash').innerHTML =
    '<div class="notice '+(kind||'warn')+'">'+html+'</div>';
}

// Which nav item is the page you are on. Cheaper than threading the route
// through page() on every handler, and it cannot fall out of sync with the URL.
for(const a of document.querySelectorAll('.top nav a')){
  if(a.getAttribute('href') === location.pathname) a.setAttribute('aria-current','page');
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
    : '<a href="/login">Sign in</a>'
      +'<a class="btn sm" href="/signup">Get ranked matches</a>';
  // Nagged, not blocked: an unconfirmed address must not lock anyone out of the
  // matches they already answered for. Flip REQUIRE_EMAIL_VERIFICATION to change.
  if(d.user && !d.user.email_verified && !_q.get('verify')){
    flash('Please confirm <b>'+esc(d.user.email)+'</b>. '
          +'<a href="#" data-action="resend">Resend the link</a>.'
          +(d.smtp_configured ? '' : ' <i>(no mail server configured here, so '
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


# Motion. Loaded last, after the content, and every animation lives inside a
# gsap.matchMedia() branch that simply does not exist under prefers-reduced-motion
# -- so the reduced-motion path is "no animation was ever created", not "an
# animation was created and then suppressed".
MOTION = """<script src="/static/js/gsap.min.js"></script>
<script src="/static/js/ScrollTrigger.min.js"></script>
<script>
if(window.gsap){
  gsap.registerPlugin(ScrollTrigger);
  const mm = gsap.matchMedia();

  mm.add('(prefers-reduced-motion: no-preference)', () => {

    // Hero: one timeline, staggered, no scroll involvement. This is the first
    // thing anyone sees, so it runs immediately rather than on a trigger.
    const intro = gsap.timeline({defaults:{ease:'power2.out', duration:.7}});
    intro.from('.hero .eyebrow',{y:14, opacity:0, duration:.5})
         .from('.hero h1',{y:26, opacity:0},'-=.3')
         .from('.hero .lede',{y:20, opacity:0},'-=.5')
         .from('.hero .search',{y:20, opacity:0},'-=.5')
         .from('.hero .hint',{y:14, opacity:0},'-=.55')
         .from('.panel',{y:34, opacity:0, duration:.8},'-=.75')
         .from('.panel li',{y:12, opacity:0, duration:.45, stagger:.07},'-=.5');

    // Reveal on scroll, but only ever hide what is already off-screen. Hiding
    // in CSS and un-hiding in JS is the version that leaves a page blank when
    // anything upstream fails; this way the worst case is no animation.
    const below = [...document.querySelectorAll('.reveal')].filter(
      el => el.getBoundingClientRect().top > window.innerHeight * 0.92);
    if(below.length){
      gsap.set(below,{opacity:0, y:18});
      // One batch, so a long list staggers together instead of firing a
      // separate trigger per row.
      ScrollTrigger.batch(below, {
        start: 'top 92%',
        once: true,
        onEnter: b => gsap.to(b,{opacity:1, y:0, duration:.6, stagger:.06,
                                 ease:'power2.out', overwrite:true})
      });
    }

    // The wash drifts a little slower than the page. Transform only.
    gsap.to('.hero',{backgroundPositionY:'22%', ease:'none',
      scrollTrigger:{trigger:'.hero', start:'top top', end:'bottom top', scrub:.6}});

    // Counters. The figures are the product's whole claim, so they count up
    // once, on arrival, from the value already rendered in the HTML.
    document.querySelectorAll('.stats dd').forEach(el => {
      const target = parseFloat(el.textContent.replace(/,/g,''));
      if(!isFinite(target)) return;
      const box = {v:0};
      gsap.to(box,{v:target, duration:1.4, ease:'power2.out',
        scrollTrigger:{trigger:el, start:'top 92%', once:true},
        onUpdate(){ el.textContent = Math.round(box.v).toLocaleString(); }});
    });

    return () => gsap.set('.reveal',{clearProps:'opacity,transform'});
  });

  // Fonts land after first paint and change every element's height.
  document.fonts && document.fonts.ready.then(() => ScrollTrigger.refresh());
}
</script>"""


FOOT = """</main>
<footer class=foot>
<div class=wrap>
 <div class=fbrand>
  <a class=brand href="/">
   <svg class=mark viewBox="0 0 32 32" aria-hidden=true focusable=false>
    <rect class=bg width="32" height="32" rx="8"/>
    <g class=fg><rect x="8" y="9" width="16" height="2.6" rx="1.3"/>
    <rect x="8" y="14.7" width="11" height="2.6" rx="1.3"/>
    <rect x="8" y="20.4" width="6" height="2.6" rx="1.3"/></g>
   </svg>
   <b>Tender</b><span>Aggregator</span></a>
  <p>Open procurement notices from official Indian government portals, ranked
     against what your company actually does.</p>
 </div>
 <div>
  <h2>Find work</h2>
  <ul><li><a href="/browse">Browse all tenders</a></li>
   <li><a href="/signup">Get ranked matches</a></li>
   <li><a href="/login">Sign in</a></li></ul>
 </div>
 <div>
  <h2>The data</h2>
  <ul><li><a href="/sources">Sources and licences</a></li>
   <li><a href="/docs">API reference</a></li>
   <li><a href="/runs">Collection history</a></li></ul>
 </div>
</div>
<div class=fnote><div class=wrap><p>Every record carries the portal it came from
 and that portal's licence. GeM is deliberately absent: its robots.txt disallows
 automated access, so that data needs an agreement, not a scraper.</p></div></div>
</footer>__MOTION__"""


# Rendered only when GOOGLE_CLIENT_ID/SECRET are set. An always-visible button that
# 404s is worse than no button, so unconfigured means absent, not broken.
GOOGLE_BUTTON = """<p><a class="btn ghost" href="/auth/google">Continue with Google</a></p>
<p class=divider>or __ALTERNATIVE__</p>"""


def google_block(alternative: str) -> str:
    if not oauth.configured():
        return ""
    return GOOGLE_BUTTON.replace("__ALTERNATIVE__", alternative)


def page(title: str, body: str, nonce: str = "", hero: str = "") -> str:
    """Render a page and nonce its inline script/style blocks.

    Every <script> and <style> here is written by us and lives in this file, so
    they can be allow-listed individually. That is what lets the CSP refuse
    'unsafe-inline': markup injected into a page has no valid nonce and will not
    execute, whatever gets past input validation.

    `hero` is rendered full-bleed between the header and <main>, because a band
    that has to run edge to edge cannot live inside the centred column.
    """
    html = (
        HEAD.replace("__TITLE__", title)
        .replace("__FAVICON__", FAVICON)
        .replace("__HERO__", hero)
        # The landing brings its own <section> wrappers because its blocks are
        # full-bleed and ruled; every other page is one plain column.
        + (body if body.lstrip().startswith("<section")
           else f'<section class=sec><div class=wrap>{body}</div></section>')
        + FOOT.replace("__MOTION__", MOTION)
    )
    # One place resolves the icon placeholders, so a template can name a glyph
    # without importing anything and no page can ship an unsubstituted one.
    for token, name in ICON_SLOTS.items():
        if token in html:
            html = html.replace(token, icon(name))
    if nonce:
        html = html.replace("<script>", f'<script nonce="{nonce}">')
        html = html.replace("<style>", f'<style nonce="{nonce}">')
    return html


def nonce_of(request: Request) -> str:
    return getattr(request.state, "csp_nonce", "")


def days_left(deadline: date | None, today: date | None = None) -> tuple[str, str]:
    """The gutter label for a tender, and its rail weight. Mirrors left() in the
    browse page's script, which has to do the same job on rows fetched by JSON."""
    if deadline is None:
        return "", "s0"
    n = (deadline - (today or date.today())).days
    if n < 0:
        return "closed", "s0"
    if n == 0:
        return "today", "s3"
    return f"{n}d", "s3" if n <= 3 else "s2" if n <= 10 else "s1"


def row(gutter: str, rank: str, t: Tender, tags: str = "") -> str:
    """One list row. escape(): titles come from a scraped page and go straight
    into HTML."""
    link = escape(t.document_url or t.source_url)
    return (
        f'<li class="row {rank}"><div class=n>{escape(gutter)}</div><div>'
        f'<a class=t href="{link}">{escape(t.title)}</a>'
        f"<p class=m>{escape(t.organization or 'unnamed buyer')} &middot; "
        f"closes <time datetime='{t.deadline}'>{t.deadline}</time></p>{tags}</div></li>"
    )


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


# ---- the questionnaire, asked once ----------------------------------------
# No <form>, no submit button, no <script>: the two pages that use this supply
# their own, because signup has to create the account first and editing does not.

PROFILE_FIELDS = """<fieldset><legend>What you do</legend>
<label class=f><span>Company name (required)</span>
   <input name=name required autocomplete=organization></label>
<div class=f><span>Sectors you work in (required)</span>
   <div class=checks>__SECTORS__</div></div>
<label class=f><span>Keywords, comma separated (e.g. transformer, cable)</span>
   <input name=keywords autocomplete=off spellcheck=false></label>
<p class=hint>A keyword must appear in the tender title as a whole word.</p>
</fieldset>

<fieldset><legend>Where you work</legend>
<label class=f><span>States and UTs (hold Ctrl to pick several)</span>
   <select name=states multiple size=6>__STATES__</select></label>
<label class=f><span>Madhya Pradesh districts, comma separated</span>
   <input name=districts list=dl autocomplete=off spellcheck=false>
   <datalist id=dl>__DISTRICTS__</datalist></label>
</fieldset>

<fieldset><legend>Who you want to work with</legend>
<label class=f><span>Buyers you want (hold Ctrl to pick several)</span>
   <select name=buyers multiple size=8>__BUYERS__</select></label>
<label class=f><span>Buyers to never show</span>
   <select name=exclude_buyers multiple size=6>__BUYERS__</select></label>
<p class=hint>Excluding a buyer removes its tenders entirely. One buyer alone is
   over half the corpus, so this is the fastest way to cut noise.</p>
</fieldset>

<fieldset><legend>What to rule out</legend>
<label class=f><span>Never show tenders whose title contains, comma separated</span>
   <input name=exclude_keywords autocomplete=off spellcheck=false placeholder="e.g. scrap, auction&hellip;"></label>
<label class=f><span>Days you need to prepare a bid</span>
   <input name=min_lead_days autocomplete=off type=number value=7 min=0 max=365></label>
<label class=f><span>Max project value you can execute, INR</span>
   <input name=max_project_value autocomplete=off type=number min=0></label>
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
  const box = document.getElementById('err');
  box.textContent = typeof e==='string' ? e : JSON.stringify(e,null,1);
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}
// Same three weights as rank_class() on the server, so a scored preview and a
// saved results page read identically.
function rankClass(score, top){
  if(!top) return 's0';
  const share = score/top;
  return share>=0.75 ? 's3' : share>=0.45 ? 's2' : 's1';
}
function tags(list){
  return (list||[]).length
    ? '<ul class=tags>'+list.map(r=>'<li class=tag>'+esc(r)+'</li>').join('')+'</ul>' : '';
}
</script>"""


def _fill(markup: str, db: Session) -> str:
    """Populate the questionnaire's option lists from the taxonomy and the corpus."""
    boxes = "".join(
        f'<label><input type=checkbox name=sectors value="{k}"> {escape(v)}</label>'
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
    hero, body = _welcome(db)
    return page("Find tenders", body, nonce_of(request), hero=hero)


def _welcome(db: Session) -> tuple[str, str]:
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
    n_sources = db.scalar(select(func.count()).select_from(Source))
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
    panel = "".join(row(*days_left(t.deadline, today), t) for t in soonest)
    # Each one links to a filter GET /tenders already supports, so the grid is
    # navigation rather than decoration.
    buyers = "".join(
        f'<li class=reveal><a href="/browse?organization={quote(name)}">'
        f"<span>{escape(name)}</span><b>{n:,}</b></a></li>"
        for name, n in db.execute(
            select(Tender.organization, func.count())
            .where(Tender.organization.is_not(None), Tender.duplicate_of.is_(None),
                   is_open)
            .group_by(Tender.organization)
            .order_by(func.count().desc())
            .limit(8)
        ).all()
    )
    # "Updated daily" would be a claim; the last successful run is a fact, and it
    # is the one that goes stale visibly if the scheduler process is not running.
    last = db.scalar(
        select(func.max(ConnectorRun.finished_at)).where(ConnectorRun.status == "ok")
    )
    collected = (
        f" &middot; last collected {last.strftime('%d %b %Y')}" if last else ""
    )

    def fill(markup: str) -> str:
        return (
            markup.replace("__OPEN__", f"{n_open:,}")
            .replace("__SOON__", f"{n_soon:,}")
            .replace("__NSOURCES__", str(n_sources))
            .replace("__NBUYERS__", f"{n_buyers:,}")
            .replace("__COLLECTED__", collected)
            .replace("__PANEL__", panel)
            .replace("__BUYERS__", buyers)
        )

    return fill(HERO), fill(WELCOME)


# The hero is full-bleed, so it is rendered outside <main> by page(). The search
# box is a plain GET form to /browse: no JS, works before the script runs, and
# lands on a URL the browse page already knows how to restore.
HERO = """<section class=hero><div class=wrap>
<p class=eyebrow>__NSOURCES__ official portals__COLLECTED__</p>
<div class=heroTop>
 <h1>Find the tenders<br>worth your time</h1>
 <p class=bignum><b>__OPEN__</b><em>Open right now</em></p>
</div>
<div class=heroCopy>
 <p class=lede>Every open notice from Indian government procurement portals, in
 one place, ranked against what your company actually does.</p>
 <div>
  <form class=search action="/browse" method=get>
   <label class=sr for=hq>Search tender titles</label>
   <input id=hq name=q autocomplete=off spellcheck=false
      placeholder="Search titles, e.g. transformer&hellip;">
   <button>__I_SEARCH__Search</button>
  </form>
  <p class=hint>Or answer eight questions once and get every new tender scored
  for you. <a class=nowrap href="/signup">Create a free account__I_UPRIGHT__</a></p>
 </div>
</div>
<dl class=stats>
 <div><dt>Closing within 7 days</dt><dd>__SOON__</dd></div>
 <div><dt>Buyers listed</dt><dd>__NBUYERS__</dd></div>
 <div><dt>Official sources</dt><dd>__NSOURCES__</dd></div>
</dl>
</div></section>"""


# The hero panel already lists what is closing soonest, so repeating it here
# would be the same section twice. Buyers are the other axis people search on.
WELCOME = """<section class=sec><div class=wrap>
<div class="secHead reveal">
 <h2>Closing soonest</h2>
 <p class=kicker>__SOON__ close within seven days</p>
</div>
<div class="panel reveal">
 <h2><span class=lbl>__I_CLOCK__Live from the corpus</span>
 <span>__OPEN__ open</span></h2>
 <ol class=rows>__PANEL__</ol>
 <a class=more href="/browse">Browse every notice__I_ARROW__</a>
</div>
</div></section>

<section class=sec><div class=wrap>
<div class="secHead reveal">
 <h2>Who is buying</h2>
 <p class=kicker>Top buyers by open notices</p>
</div>
<p class="hint reveal">One buyer alone is over half the corpus, so starting from
the ones you already sell to is the fastest way to cut the noise.</p>
<ul class=buyerGrid>__BUYERS__</ul>
</div></section>"""


# ---- signup: account + questionnaire, one submit ---------------------------

SIGNUP = """<h1>Create your account</h1>
<p class=lede>Everything below is asked once. You will not be asked again.</p>
__GOOGLE__
<form id=f>

<fieldset><legend>Sign-in details</legend>
<label class=f><span>Email</span>
   <input name=email type=email required autocomplete=username spellcheck=false></label>
<label class=f><span>Password</span>
   <input name=password type=password required minlength=10
      autocomplete=new-password></label>
<p class=hint>At least 10 characters. Stored as a salted scrypt hash, never in
   plain text. We use your sign-in email as the contact address on the profile.</p>
</fieldset>

__PROFILE_FIELDS__

<p class=actions><button>Create account and show my matches</button></p>
</form>
<pre id=err role=alert></pre>
<p class=hint>Already have one? <a href="/login">Sign in</a>.</p>
__COLLECT_JS__
<script>
// If the account is created but the profile is rejected, a retry must not try to
// register the same email again -- it would fail with "already registered" and
// strand the user on a form they cannot submit.
let accountCreated = false;
const LABEL = 'Create account and show my matches';

function go(e){
  e.preventDefault();
  const f = e.target;
  const btn = f.querySelector('button');
  const body = collectProfile(f);
  body.contact_email = f.email.value.trim() || null;

  btn.disabled = true;
  btn.textContent = 'Creating your account…';

  const account = accountCreated ? Promise.resolve() :
    fetch('/auth/signup',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({email:f.email.value, password:f.password.value})})
     .then(r=>r.json().then(d=>{if(!r.ok) throw d.detail; accountCreated=true;}));

  account.then(()=>saveProfile(body))
         .then(d=>location.assign('/c/'+d.id))
         .catch(err=>{ showErr(err); btn.disabled=false; btn.textContent=LABEL; });
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
<label class=f><span>Email</span>
   <input name=email type=email required autocomplete=username spellcheck=false></label>
<label class=f><span>Password</span>
   <input name=password type=password required autocomplete=current-password></label>
<p class=hint>Takes you straight to your matches. Your answers are already saved.</p>
<p class=actions><button>Sign in</button></p>
</form>
<pre id=err role=alert></pre>
<p class=hint>No account yet? <a href="/signup">Create one</a>.</p>
<script>
function go(e){
  e.preventDefault();
  const f=e.target, btn=f.querySelector('button');
  btn.disabled = true;
  fetch('/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify({email:f.email.value,password:f.password.value})})
   .then(r=>r.json().then(d=>{
     if(r.ok) return location.assign('/');
     document.getElementById('err').textContent = d.detail;
     btn.disabled = false;
   }))
   .catch(()=>{
     document.getElementById('err').textContent =
       'Could not reach the server. Please try again.';
     btn.disabled = false;
   });
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
<p class=lede id=savehint>__HINT__</p>
<form id=f>
__PROFILE_FIELDS__
<fieldset><legend>Contact</legend>
<label class=f><span>Contact email</span>
   <input name=contact_email type=email autocomplete=email spellcheck=false></label>
</fieldset>
<p class=actions><button>Save and show my matches</button></p>
</form>
<pre id=err role=alert></pre>
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
  if(!matches.length){
    box.innerHTML = '<div class=empty><p>Nothing matched these answers. Broaden '
      + 'the sectors, drop a state, or lower the preparation days.</p></div>';
    box.scrollIntoView({behavior:'smooth',block:'nearest'});
    return;
  }
  const top = matches[0].score;
  box.innerHTML = '<h2>' + matches.length + ' matching tenders</h2><ol class=rows>'
    + matches.map(m =>
      '<li class="row '+rankClass(m.score, top)+'">'
      + '<div class=n>' + esc(m.score) + '</div><div>'
      + '<a class=t href="' + esc(m.tender.document_url || m.tender.source_url) + '">'
      + esc(m.tender.title) + '</a>'
      + '<p class=m>' + esc(m.tender.organization || 'unnamed buyer')
      + ' &middot; closes <time datetime="' + esc(m.tender.deadline) + '">'
      + esc(m.tender.deadline) + '</time></p>'
      + tags(m.reasons) + '</div></li>').join('')
    + '</ol><p class=hint>Nothing here was saved. '
    + '<a href="/signup">Create an account</a> to keep these answers.</p>';
  box.scrollIntoView({behavior:'smooth',block:'nearest'});
}

function go(e){
  e.preventDefault();
  const btn = e.target.querySelector('button');
  const body = collectProfile(e.target);
  btn.disabled = true;
  if(signedIn){
    saveProfile(body).then(d=>location.assign('/c/'+d.id))
      .catch(err=>{ showErr(err); btn.disabled=false; });
    return;
  }
  // No account: score without storing. POST /match persists nothing -- no row,
  // no company name, no contact address.
  fetch('/match?limit=25',{method:'POST',headers:{'Content-Type':'application/json'},
    body:JSON.stringify(body)})
   .then(r=>r.json().then(d=>{if(!r.ok) throw d.detail; renderPreview(d);}))
   .catch(showErr)
   .finally(()=>{ btn.disabled=false; });
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
            "is stored, so create an account if you want them saved."
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

    top = matches[0][0] if matches else 0
    rows = []
    for score, reasons, t in matches:
        chips = "".join(f"<li class=tag>{escape(r)}</li>" for r in reasons)
        rows.append(
            row(str(score), rank_class(score, top), t,
                f"<ul class=tags>{chips}</ul>" if chips else "")
        )
    body = (
        "<ol class=rows>" + "".join(rows) + "</ol>" if rows else
        "<div class=empty><p>No open tenders match this profile yet. Broaden the "
        "sectors, drop a state, or lower the preparation days on your "
        '<a href="/profile">answers</a>.</p></div>'
    )
    return page(
        "Matches",
        f"<h1>Matches for {escape(company.name)}</h1>"
        f"<p class=lede>{len(matches)} open tenders scored against your answers. "
        f'<a href="/profile">Edit answers</a></p>{body}',
        nonce_of(request),
    )


# ---- corpus browser --------------------------------------------------------

BROWSE = """<h1>All collected tenders</h1>
<p class=lede>Every notice in the corpus, closing soonest first. No account needed.</p>
<form id=f>
<div class=filters>
   <label class=f><span>Search titles</span>
     <input name=q autocomplete=off spellcheck=false placeholder="e.g. transformer&hellip;"></label>
   <label class=f><span>Buyer</span>
     <input name=organization autocomplete=off spellcheck=false
        placeholder="e.g. Railways&hellip;"></label>
   <label class=f><span>Source</span>
     <select name=source_id><option value="">Every source</option>__SOURCES__</select></label>
   <label class=f><span>Sort by</span>
     <select name=sort>
       <option value=deadline>Closing soonest</option>
       <option value=first_seen_at>Most recently collected</option>
       <option value=published_date>Most recently published</option>
     </select></label>
   <button>__I_SEARCH__Search</button>
</div>
<p class=chk><label><input type=checkbox name=open_only checked>
   Only tenders still open</label></p>
</form>
<p class=count id=count aria-live=polite>Loading tenders&hellip;</p>
<ol class=rows id=rows>
 <li class="row sk"><div class=n><div class="b g"></div></div>
   <div><div class=b></div><div class="b s"></div></div></li>
 <li class="row sk"><div class=n><div class="b g"></div></div>
   <div><div class=b></div><div class="b s"></div></div></li>
 <li class="row sk"><div class=n><div class="b g"></div></div>
   <div><div class=b></div><div class="b s"></div></div></li>
</ol>
<p class=pager><button class=ghost id=prev>__I_LEFT__Previous</button>
   <button class=ghost id=next>Next__I_RIGHT__</button></p>
<pre id=err role=alert></pre>
<script>
const LIMIT=25; let offset=0;
// esc() comes from the shared chrome in HEAD. Re-declaring it with const here
// threw "Identifier 'esc' has already been declared", which killed this whole
// script block -- the listing never rendered and the page sat on "loading...".
const inr=v=>v==null?'':' &middot; <span class=v>INR '
  +Number(v).toLocaleString('en-IN')+'</span>';

// The gutter carries the number the page is ranked by. Here that is time left,
// which is what the default sort orders on. Display only: whether a tender still
// counts as open is decided by the server against its own clock, not this one.
function left(d){
  if(!d) return ['', 's0'];
  const ms = Date.parse(d+'T00:00:00');
  if(isNaN(ms)) return ['', 's0'];
  // Midnight today, not Date.now(): measured from the current instant, a tender
  // closing tonight came out as -1 day and every row on the page read "closed".
  const today = new Date(); today.setHours(0,0,0,0);
  const n = Math.round((ms - today.getTime())/864e5);
  if(n < 0) return ['closed', 's0'];
  if(n === 0) return ['today', 's3'];
  return [n+'d', n<=3 ? 's3' : n<=10 ? 's2' : 's1'];
}

function go(e,off){
  if(e) e.preventDefault();
  offset = off ?? 0;
  const f=document.getElementById('f'), p=new URLSearchParams();
  if(f.q.value.trim()) p.set('q', f.q.value.trim());
  if(f.organization.value.trim()) p.set('organization', f.organization.value.trim());
  if(f.source_id.value) p.set('source_id', f.source_id.value);
  p.set('sort', f.sort.value);
  // GET /tenders hides past-deadline rows by default, against the server's
  // date. Reading the visitor's clock instead meant a wrong or simply
  // differently-zoned device decided what counted as still open.
  if(!f.open_only.checked) p.set('include_closed', 'true');
  p.set('limit', LIMIT); p.set('offset', offset);
  // The search lives in the address bar, so a reload, a bookmark or a pasted
  // link all come back to the same page of the same results.
  history.replaceState(null, '', p.toString() ? '?'+p : location.pathname);
  fetch('/tenders?'+p).then(r=>r.json()).then(d=>{
    document.getElementById('count').textContent =
      d.total.toLocaleString() + ' tenders, showing ' +
      (d.total?offset+1:0) + ' to ' + Math.min(offset+LIMIT, d.total);
    const rows = document.getElementById('rows');
    rows.className = 'rows browse';
    rows.innerHTML = d.items.map(t=>{
      const [label, rank] = left(t.deadline);
      return '<li class="row '+rank+'"><div class=n>'+esc(label)+'</div><div>'
        + '<a class=t href="'+esc(t.document_url||t.source_url)+'">'+esc(t.title)+'</a>'
        + '<p class=m>'+esc(t.organization||'unnamed buyer')+' &middot; closes '
        + '<time datetime="'+esc(t.deadline||'')+'">'
        + esc(t.deadline||'not stated')+'</time>'+inr(t.estimated_value)
        + '</p></div></li>';
    }).join('')
      || '<li class=empty><p>Nothing matched that search. Try a shorter word, or '
         + 'clear the source filter.</p></li>';
    document.getElementById('prev').disabled = offset===0;
    document.getElementById('next').disabled = offset+LIMIT >= d.total;
    document.getElementById('err').textContent = '';
  }).catch(()=>{
    document.getElementById('count').textContent = '';
    document.getElementById('err').textContent =
      'Could not load tenders. The server did not answer, please try again.';
  });
}
// Restore whatever the URL asks for before the first fetch, so a shared link
// opens on the same filters and the same page rather than on defaults.
function seed(){
  const f = document.getElementById('f'), q = new URLSearchParams(location.search);
  if(q.has('q')) f.q.value = q.get('q');
  if(q.has('organization')) f.organization.value = q.get('organization');
  if(q.has('source_id')) f.source_id.value = q.get('source_id');
  if(q.has('sort')) f.sort.value = q.get('sort');
  if(q.get('include_closed') === 'true') f.open_only.checked = false;
  return Math.max(0, parseInt(q.get('offset') || '0', 10) || 0);
}

document.getElementById('f').addEventListener('submit', e=>go(e,0));
prev.onclick=()=>go(null, Math.max(0, offset-LIMIT));
next.onclick=()=>go(null, offset+LIMIT);
go(null, seed());
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
