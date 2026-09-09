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
from collections import Counter
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
from .matching import (
    MP_DISTRICTS,
    SECTOR_LABELS,
    STATES,
    derive_districts,
    derive_sectors,
    derive_states,
    find_matches,
    hydrate,
    match_digest,
)
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
    "__I_DOWN__": "caret-down",
    "__I_TARGET__": "target",
    "__I_SIGNOUT__": "sign-out",
    "__I_SLIDERS__": "sliders-horizontal",
}


# A ranked-list mark: three rules of decreasing width. Inline data: URI because
# img-src is 'self' data: and a favicon is somewhere CSS cannot reach.
FAVICON = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'"
    "%3E%3Crect width='32' height='32' fill='%238EA439'/%3E%3Cg fill='%232A1608'"
    "%3E%3Crect x='7' y='9' width='18' height='3'/%3E%3Crect x='7' y='14.5'"
    " width='12' height='3'/%3E%3C/g%3E%3Crect x='7' y='20' width='6' height='3'"
    " fill='%23EFD7A5'/%3E%3C/svg%3E"
)


HEAD = """<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport
 content="width=device-width,initial-scale=1"><title>__TITLE__</title>
<meta name=description content="Open Indian government tender notices, collected
 from official procurement portals and ranked against what your company does.">
<meta name=color-scheme content="light dark">
<meta name=theme-color content="#8EA439" media="(prefers-color-scheme:light)">
<meta name=theme-color content="#8EA439" media="(prefers-color-scheme:dark)">
<link rel=icon href="__FAVICON__">
<style>
 /* Self-hosted from /static, so the strict CSP stays intact: font-src falls back
    to default-src 'self'. Nunito is the informal one: rounded terminals and a
    soft bowl, but it still runs to 1000 weight in a single variable file, so the
    display headline stays heavy without a second download. Space Mono carries
    the numbers -- it only ships 400 and 700, and both are declared here so the
    browser picks a real face instead of faux-bolding a 500. */
 @font-face{font-family:Quicksand;
   src:url(/static/fonts/Quicksand.woff2) format("woff2");
   font-weight:300 700;font-display:swap;font-style:normal}
 @font-face{font-family:Nunito;src:url(/static/fonts/Nunito.woff2) format("woff2");
   font-weight:200 1000;font-display:swap;font-style:normal}
 @font-face{font-family:"Space Mono";
   src:url(/static/fonts/SpaceMono-400.woff2) format("woff2");
   font-weight:400;font-display:swap;font-style:normal}
 @font-face{font-family:"Space Mono";
   src:url(/static/fonts/SpaceMono-700.woff2) format("woff2");
   font-weight:700;font-display:swap;font-style:normal}

 /* Brutalist editorial: newsprint ground, true-black rules, one electric accent.
    Nothing is rounded and nothing floats; structure comes from rule weight alone.
    Every text pair below clears 4.5:1 in both themes. */
 :root{
  color-scheme:light dark;
  /* Green is the field the whole site sits on. Reading happens on sand sheets
     laid over it, and the emphasis blocks are brown with white type.
     --ink #2A1608 is chosen to clear 4.5:1 on BOTH grounds (6.18 on the green,
     12.27 on the sand) so one text colour works everywhere. White cannot go on
     the green (2.79:1) and neither can the sand (1.99:1), which is why white
     only ever appears on the brown fill (10.14:1). */
  --paper:#8EA439; --panel:#EFD7A5; --ink:#2A1608; --muted:#42230A;
  --block:#653511; --on-block:#FFFFFF;
  --accent:#653511; --accent-h:#7F4517; --on-accent:#FFFFFF;
  --rule:#2A1608; --hair:rgba(42,22,8,.30);
  --danger:#9B2C0F; --ok:#3F6B1E;
  --b:2px; --b2:3px;
  /* Coconut husk. Two feTurbulence fields -- stretched strands for the coir
     fibre, fine grain for the pitting -- plus three low-alpha gradient
     layers for the weave and the patchiness. All of it is one inline data:
     URI, which img-src 'self' data: already allows, so the texture costs no
     request and no CSP change. Kept under 7% alpha so the brown type keeps
     its contrast on the darkest patches. */
  --husk:
    radial-gradient(80% 40% at 12% 8%, rgba(255,251,235,.075), transparent 72%),
    radial-gradient(65% 45% at 88% 72%, rgba(42,22,8,.07), transparent 72%),
    radial-gradient(45% 60% at 55% 40%, rgba(42,22,8,.035), transparent 75%),
    url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%27300%27%20height%3D%27300%27%3E%3Cfilter%20id%3D%27a%27%20x%3D%270%27%20y%3D%270%27%20width%3D%27100%25%27%20height%3D%27100%25%27%3E%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%270.005%200.22%27%20numOctaves%3D%272%27%20seed%3D%2711%27%20stitchTiles%3D%27stitch%27%2F%3E%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%270%27%2F%3E%3C%2Ffilter%3E%3Cfilter%20id%3D%27b%27%20x%3D%270%27%20y%3D%270%27%20width%3D%27100%25%27%20height%3D%27100%25%27%3E%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%270.009%200.7%27%20numOctaves%3D%275%27%20seed%3D%2729%27%20stitchTiles%3D%27stitch%27%2F%3E%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%270%27%2F%3E%3C%2Ffilter%3E%3Cfilter%20id%3D%27c%27%20x%3D%270%27%20y%3D%270%27%20width%3D%27100%25%27%20height%3D%27100%25%27%3E%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%271.1%201.1%27%20numOctaves%3D%272%27%20seed%3D%275%27%20stitchTiles%3D%27stitch%27%2F%3E%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%270%27%2F%3E%3C%2Ffilter%3E%3Crect%20width%3D%27300%27%20height%3D%27300%27%20filter%3D%27url%28%23a%29%27%20opacity%3D%270.3%27%2F%3E%3Crect%20width%3D%27300%27%20height%3D%27300%27%20filter%3D%27url%28%23b%29%27%20opacity%3D%270.44%27%2F%3E%3Crect%20width%3D%27300%27%20height%3D%27300%27%20filter%3D%27url%28%23c%29%27%20opacity%3D%270.13%27%2F%3E%3C%2Fsvg%3E");
  /* the pale upper husk: same grain, dialled right back so it never fights
     the type sitting on it */
  --flesh:
    radial-gradient(70% 50% at 20% 10%, rgba(255,253,246,.55), transparent 70%),
    url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%27260%27%20height%3D%27260%27%3E%3Cfilter%20id%3D%27a%27%20x%3D%270%27%20y%3D%270%27%20width%3D%27100%25%27%20height%3D%27100%25%27%3E%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%270.007%200.45%27%20numOctaves%3D%273%27%20seed%3D%2717%27%20stitchTiles%3D%27stitch%27%2F%3E%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%270%27%2F%3E%3C%2Ffilter%3E%3Cfilter%20id%3D%27b%27%20x%3D%270%27%20y%3D%270%27%20width%3D%27100%25%27%20height%3D%27100%25%27%3E%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%270.95%200.95%27%20numOctaves%3D%272%27%20seed%3D%2741%27%20stitchTiles%3D%27stitch%27%2F%3E%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%270%27%2F%3E%3C%2Ffilter%3E%3Crect%20width%3D%27260%27%20height%3D%27260%27%20filter%3D%27url%28%23a%29%27%20opacity%3D%270.22%27%2F%3E%3Crect%20width%3D%27260%27%20height%3D%27260%27%20filter%3D%27url%28%23b%29%27%20opacity%3D%270.1%27%2F%3E%3C%2Fsvg%3E");
  /* dry shell: closer grain, lit from the top left */
  --shell:
    radial-gradient(75% 55% at 15% 5%, rgba(255,240,214,.10), transparent 70%),
    url("data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20width%3D%27240%27%20height%3D%27240%27%3E%3Cfilter%20id%3D%27a%27%20x%3D%270%27%20y%3D%270%27%20width%3D%27100%25%27%20height%3D%27100%25%27%3E%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%270.01%200.55%27%20numOctaves%3D%274%27%20seed%3D%2723%27%20stitchTiles%3D%27stitch%27%2F%3E%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%270%27%2F%3E%3C%2Ffilter%3E%3Cfilter%20id%3D%27b%27%20x%3D%270%27%20y%3D%270%27%20width%3D%27100%25%27%20height%3D%27100%25%27%3E%3CfeTurbulence%20type%3D%27fractalNoise%27%20baseFrequency%3D%271.4%201.4%27%20numOctaves%3D%272%27%20seed%3D%2761%27%20stitchTiles%3D%27stitch%27%2F%3E%3CfeColorMatrix%20type%3D%27saturate%27%20values%3D%270%27%2F%3E%3C%2Ffilter%3E%3Crect%20width%3D%27240%27%20height%3D%27240%27%20filter%3D%27url%28%23a%29%27%20opacity%3D%270.26%27%2F%3E%3Crect%20width%3D%27240%27%20height%3D%27240%27%20filter%3D%27url%28%23b%29%27%20opacity%3D%270.14%27%2F%3E%3C%2Fsvg%3E");
  --r:8px; --r-lg:16px; --r-sm:5px;
  --display:Quicksand,Nunito,system-ui,-apple-system,"Segoe UI",sans-serif;
  --sans:Nunito,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  --mono:"Space Mono",ui-monospace,"SF Mono","Cascadia Mono",Menlo,Consolas,monospace;
 }
 @media (prefers-color-scheme:dark){:root{
  --paper:#8EA439; --panel:#EFD7A5; --ink:#2A1608; --muted:#42230A;
  --block:#653511; --on-block:#FFFFFF;
  --accent:#653511; --accent-h:#7F4517; --on-accent:#FFFFFF;
  --rule:#2A1608; --hair:rgba(42,22,8,.30);
  --danger:#9B2C0F; --ok:#3F6B1E;
 }}
 *{box-sizing:border-box}
 /* the hidden attribute must win over any display rule we write */
 [hidden]{display:none !important}
 a,button,input,select,label{touch-action:manipulation;
   -webkit-tap-highlight-color:transparent}
 html{-webkit-text-size-adjust:100%;scroll-behavior:smooth}
 body{margin:0;background-color:var(--paper);background-image:var(--husk);
      color:var(--ink);
      font:500 18px/1.62 var(--sans);letter-spacing:0;
      -webkit-font-smoothing:antialiased;
      min-height:100dvh;display:flex;flex-direction:column}
 .wrap{max-width:78rem;margin:0 auto;padding:0 1.5rem;width:100%}
 main{display:block;flex:1;padding:2rem 1.5rem 3.5rem}
 @media (max-width:34rem){main{padding:1.25rem 1rem 2.5rem}}
 @media (max-width:34rem){.wrap{padding:0 1rem}}

 .skip{position:absolute;left:-9999px}
 .skip:focus{position:fixed;left:1rem;top:1rem;z-index:9;background:var(--ink);
   color:var(--on-accent);border:var(--b2) solid var(--ink);padding:.7rem 1rem;
   text-decoration:none;font:700 .88rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.05em;border-radius:var(--r)}

 /* --- type ------------------------------------------------------------------ */
 h1{margin:0;font-family:var(--display);font-weight:700;
    text-transform:uppercase;line-height:.94;letter-spacing:-.022em;
    font-size:clamp(2.3rem,.4rem + 5.9vw,5.7rem)}
 h2{margin:0;font-family:var(--display);font-weight:700;text-transform:uppercase;
    letter-spacing:-.008em;line-height:1.05;
    font-size:clamp(1.45rem,1rem + 1.6vw,2.35rem)}
 p{margin:0;text-wrap:pretty}
 .lede{font-size:clamp(1.02rem,.95rem + .45vw,1.35rem);line-height:1.4;
   max-width:46ch;font-weight:600}
 .hint{color:var(--muted);font-size:1.08rem;max-width:52ch;font-weight:600}
 .kicker,.eyebrow{font:700 .92rem/1.35 var(--mono);text-transform:uppercase;
   letter-spacing:.1em;color:var(--muted)}
 a{color:var(--accent);text-underline-offset:3px;text-decoration-thickness:2px}
 .hero a,.top .acct a,.eyebrow a{color:var(--ink);
   text-decoration-color:var(--block)}
 .hero a:hover,.top .acct a:hover{color:var(--ink);
   text-decoration-color:var(--ink)}
 a:hover{color:var(--accent-h)}
 code{font:.88em var(--mono);background:var(--ink);color:var(--paper);padding:.1em .3em}
 :focus-visible{outline:var(--b2) solid var(--accent);outline-offset:2px;
   border-radius:var(--r-sm)}
 .nowrap{white-space:nowrap}
 .sr{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;
   clip:rect(0 0 0 0);white-space:nowrap;border:0}

 /* --- header ---------------------------------------------------------------- */
 .top{position:sticky;top:0;z-index:8;background-color:var(--paper);
   background-image:var(--husk);border-bottom:var(--b2) solid var(--rule)}
 .top .wrap{display:flex;align-items:center;gap:0;min-height:4.75rem;
   flex-wrap:wrap}
 .brand{display:inline-flex;align-items:center;gap:.65rem;color:var(--ink);
   text-decoration:none;font:700 1.15rem/1 var(--display);text-transform:uppercase;
   letter-spacing:-.03em;white-space:nowrap}
 .brand span{color:var(--muted);font-weight:500;letter-spacing:.02em}
 .mark{flex:none;width:1.6rem;height:1.6rem;display:block;border-radius:var(--r-sm);
   overflow:hidden}
 .mark .bg{fill:var(--ink)} .mark .fg{fill:var(--paper)}
 .mark .fg2{fill:var(--panel)}
 /* Pills rather than underlines: four items in a row of mono capitals read as
    one undifferentiated block, and the active one needs to win clearly. */
 .top nav{display:flex;align-items:center;gap:.2rem;margin-left:.4rem;
   padding-left:1.3rem;border-left:var(--b) solid var(--hair)}
 .top nav a{display:inline-flex;align-items:center;min-height:2.4rem;
   padding:0 .8rem;border-radius:var(--r);color:var(--muted);text-decoration:none;
   font:700 .9rem/1 var(--mono);text-transform:uppercase;letter-spacing:.05em;
   transition:background .12s,color .12s}
 .top nav a:hover{background:var(--panel);color:var(--ink)}
 .top nav a[aria-current=page]{background:var(--ink);color:var(--on-accent)}
 @media (max-width:52rem){
  .top nav{margin-left:0;padding-left:0;border-left:0;flex-wrap:wrap}
 }
 .acct{margin-left:auto;display:flex;align-items:center;gap:1.25rem;
   font:700 .93rem/1 var(--mono);text-transform:uppercase;letter-spacing:.06em}
 .acct a{color:var(--ink);text-decoration:none}
 .acct a.btn{color:var(--on-accent)}
 .acct a.btn:hover{color:var(--ink)}
 .acct a:hover{color:var(--accent)}
 @media (max-width:52rem){
  .top .wrap{padding-block:.7rem;gap:.5rem 1.4rem;min-height:0}
  .brand{padding-right:0;font-size:.98rem}
  .acct{width:100%;justify-content:flex-end;margin:0}
 }

 /* --- controls: hard edges, no radius anywhere ------------------------------ */
 button,.btn{display:inline-flex;align-items:center;justify-content:center;
   min-height:3rem;padding:0 1.35rem;width:auto;border-radius:var(--r);cursor:pointer;
   text-decoration:none;border:var(--b) solid var(--ink);background:var(--ink);
   color:var(--on-accent);font:700 .94rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.06em;transition:background .12s,border-color .12s,color .12s}
 button:hover,.btn:hover{background:var(--paper);border-color:var(--ink);
   color:var(--ink)}
 button:active,.btn:active{transform:translateY(2px)}
 button:disabled{opacity:.3;cursor:not-allowed;transform:none}
 .ghost{background:transparent;color:var(--ink);border-color:var(--ink)}
 .ghost:hover{background:var(--ink);border-color:var(--ink);color:var(--on-accent)}
 .btn.sm{min-height:2.7rem;padding:0 1.1rem;font-size:.87rem}

 /* --- account menu ---------------------------------------------------------- */
 .usermenu{position:relative}
 .userbtn{display:inline-flex;align-items:center;gap:.55rem;min-height:2.6rem;
   padding:0 .6rem 0 .45rem;background:transparent;color:var(--ink);
   border:var(--b) solid transparent;border-radius:var(--r);
   font:700 .82rem/1 var(--mono);text-transform:none;letter-spacing:0}
 .userbtn:hover{background:var(--panel);border-color:var(--rule);color:var(--ink)}
 .userbtn[aria-expanded=true]{background:var(--panel);border-color:var(--rule)}
 .avatar{display:grid;place-items:center;width:1.9rem;height:1.9rem;flex:none;
   border-radius:var(--r-sm);background:var(--ink);color:var(--on-accent);
   font:700 .78rem/1 var(--mono);letter-spacing:0}
 .uname{max-width:11rem;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
   font-family:var(--sans);font-weight:700;font-size:.95rem}
 .userbtn .i{width:.85em;height:.85em;opacity:.7;margin:0}
 /* display:grid beats the hidden attribute's UA display:none, so the panel
    would stay on screen with only its property flipped. Restate it. */
 .menu[hidden]{display:none}
 .menu{position:absolute;right:0;top:calc(100% + .55rem);z-index:9;
   min-width:15rem;padding:.4rem;display:grid;gap:.1rem;
   background-color:var(--panel);background-image:var(--flesh);
   border:var(--b2) solid var(--rule);border-radius:var(--r-lg);
   box-shadow:0 10px 30px -12px rgba(42,22,8,.55)}
 .menu{text-transform:none;letter-spacing:0}
 .menuhead{padding:.6rem .65rem .7rem;margin:0;display:grid;gap:.2rem;
   border-bottom:var(--b) solid var(--hair)}
 .menuhead b{font:700 .98rem/1.2 var(--sans)}
 .menumail{font:700 .9rem/1.35 var(--mono);color:var(--muted);word-break:break-all}
 .menuwarn{margin-top:.3rem;font:700 .74rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.05em;color:var(--danger)}
 .menu a,.menu button{display:flex;align-items:center;gap:.6rem;width:100%;
   min-height:2.9rem;padding:0 .65rem;border:0;border-radius:var(--r);
   background:transparent;color:var(--ink);text-decoration:none;
   font:600 1.02rem/1 var(--sans);text-transform:none;letter-spacing:0;
   justify-content:flex-start}
 .menu a:hover,.menu button:hover{background:var(--paper);color:var(--ink)}
 .menu .i{opacity:.75;margin:0}
 @media (max-width:52rem){.menu{right:auto;left:0}}
 button .i{margin-right:.5rem}
 .pager{display:flex;gap:.75rem;padding:2rem 0 0}
 .pager button .i{margin:0}
 .pager #next .i{margin-left:.5rem}

 input,select,textarea{font:inherit;color:var(--ink);background:var(--panel);
   border:var(--b) solid var(--ink);border-radius:var(--r);padding:.7rem .8rem;
   min-height:3rem;width:100%}
 select[multiple],textarea{min-height:0}
 input::placeholder{color:var(--muted)}
 input[type=checkbox]{width:1.15rem;height:1.15rem;min-height:0;padding:0;
   border-radius:var(--r-sm);
   accent-color:var(--accent)}

 /* --- hero ------------------------------------------------------------------ */
 .hero{border-bottom:var(--b2) solid var(--rule)}
 .hero .wrap{padding-block:clamp(1.75rem,3.5vw,3rem) 0}
 .hero .stats{margin-inline:calc(50% - 50vw);padding-inline:max(1.5rem,50vw - 39rem)}
 @media (max-width:34rem){.hero .stats{padding-inline:1rem}}
 .heroTop{display:grid;gap:1.75rem;align-items:end;
   padding-block:clamp(1.25rem,3vw,2.5rem)}
 @media (min-width:64rem){
  .heroTop{grid-template-columns:minmax(0,1fr) auto;gap:3.5rem}
 }
 .bignum{border-top:var(--b2) solid var(--rule);padding-top:.9rem;min-width:12rem}
 @media (min-width:64rem){.bignum{text-align:right}}
 .bignum b{display:block;font:700 clamp(2.4rem,1rem + 4.2vw,4.75rem)/.85 var(--mono);
   letter-spacing:-.05em;font-variant-numeric:tabular-nums}
 .bignum em{display:block;margin-top:.8rem;font-style:normal;
   font:700 .92rem/1.35 var(--mono);text-transform:uppercase;letter-spacing:.1em;
   color:var(--muted)}
 .heroCopy{display:grid;gap:1.75rem;padding-block:clamp(1.5rem,3vw,2.25rem);
   border-top:var(--b2) solid var(--rule)}
 @media (min-width:64rem){
  .heroCopy{grid-template-columns:minmax(0,1.05fr) minmax(0,1fr);gap:3.5rem;
    align-items:center}
 }
 .search{display:flex;border:var(--b2) solid var(--ink);background:var(--panel);
   border-radius:var(--r-lg);overflow:hidden}
 .search input{border:0;border-radius:0;min-height:0;height:3.9rem;
   font-size:1.05rem;padding:0 1.1rem;background:transparent}
 .search input:focus-visible{outline:0;background:#FFFDF6}
 .search button{border:0;border-radius:0;border-left:var(--b2) solid var(--ink);
   min-height:0;padding:0 1.75rem;flex:none}
 @media (max-width:30rem){
  .search{flex-direction:column}
  .search button{width:100%;border-left:0;border-top:var(--b2) solid var(--ink);
    min-height:3.2rem}
 }

 /* --- stats: blocks divided by rules, not cards ----------------------------- */
 /* The counterweight to the green field: a full-bleed brown band carrying
    the white type. */
 .stats{display:grid;grid-template-columns:repeat(auto-fit,minmax(10.5rem,1fr));
   margin:0;background-color:var(--block);background-image:var(--shell);
   color:var(--on-block);
   border-top:var(--b2) solid var(--rule);border-bottom:var(--b2) solid var(--rule)}
 .stats > div{padding:1.5rem 1.35rem 1.75rem;
   border-left:var(--b) solid rgba(255,255,255,.28)}
 .stats > div:first-child{border-left:0}
 .stats dt{font:700 .9rem/1.35 var(--mono);text-transform:uppercase;
   letter-spacing:.09em;color:var(--on-block);opacity:.9}
 .stats dd{margin:.8rem 0 0;font-variant-numeric:tabular-nums;letter-spacing:-.045em;
   font:700 clamp(1.9rem,1rem + 2.4vw,3.1rem)/.85 var(--mono)}
 /* the corpus figures are the headline claim, so they get the display size */
 .stats.big dd{font-size:clamp(2.4rem,1rem + 3.6vw,4.5rem)}
 .stats.big dt{font-size:.95rem}
 .stats .mine dt{opacity:.72}
 .stats .mine dd{font-size:clamp(1.9rem,1rem + 2.4vw,3.2rem);opacity:.92}
 .badge{display:inline-block;margin-right:.7rem;padding:.4rem .65rem;
   border-radius:var(--r-sm);background:var(--ink);color:var(--on-accent);
   font:700 .8rem/1 var(--mono);text-transform:uppercase;letter-spacing:.1em}
 .hero .lede b{font-weight:700;color:var(--ink)}

 /* --- sections and lists ---------------------------------------------------- */
 /* Each section is a sand sheet laid on the green, with the field showing
    through between them. Green is what you see around everything. */
 .sec{padding:0}
 .sec + .sec{margin-top:1.75rem}
 .sec > .wrap{background-color:var(--panel);background-image:var(--flesh);
   border:var(--b2) solid var(--rule);
   border-radius:var(--r-lg);padding:clamp(1.6rem,3.2vw,2.75rem)}
 .flow > * + *{margin-top:1.6rem}
 .flow > h1 + .lede{margin-top:1.1rem}
 .flow > .lede + form,.flow > form + *{margin-top:2.25rem}
 .flow > h1{margin-bottom:0}
 .secHead{display:flex;align-items:baseline;justify-content:space-between;
   gap:1.5rem;flex-wrap:wrap;margin:0 0 1.75rem}

 .rows{list-style:none;margin:0;padding:0;border-top:var(--b2) solid var(--rule)}
 .row{position:relative;display:grid;grid-template-columns:5.25rem minmax(0,1fr);
   gap:1.25rem;padding:1.15rem 0 1.25rem .9rem;
   border-bottom:var(--b) solid var(--hair)}
 .row::before{content:"";position:absolute;left:0;top:1.15rem;bottom:1.25rem;
   width:4px;background:var(--accent);opacity:0;border-radius:4px}
 .row.s1::before{opacity:.25} .row.s2::before{opacity:.6} .row.s3::before{opacity:1}
 .row:hover{background:var(--panel)}
 .n{font:700 .94rem/1.5 var(--mono);text-transform:uppercase;letter-spacing:.04em;
   font-variant-numeric:tabular-nums;color:var(--ink)}
 .row.s0 .n,.browse .n{color:var(--muted)}
 .t{color:var(--ink);text-decoration:none;font-weight:700;font-size:1.16rem;
   line-height:1.34;letter-spacing:-.008em;text-wrap:pretty}
 .t:hover{color:var(--accent);text-decoration:underline}
 .m{margin:.5rem 0 0;color:var(--muted);font-size:.97rem;max-width:none;
   font-family:var(--mono);font-weight:700;letter-spacing:0}
 .m time,.m .v{font-variant-numeric:tabular-nums}
 .tags{display:flex;flex-wrap:wrap;gap:.35rem;margin:.6rem 0 0;padding:0;
   list-style:none}
 .tag{font:700 .85rem/1 var(--mono);text-transform:uppercase;letter-spacing:.04em;
   color:var(--ink);border:var(--b) solid var(--hair);padding:.35rem .45rem;
   border-radius:var(--r-sm)}
 .count{margin:0 0 1.25rem;font:700 .95rem/1.4 var(--mono);text-transform:uppercase;
   letter-spacing:.06em;color:var(--muted);font-variant-numeric:tabular-nums}
 @media (max-width:34rem){
  .row{grid-template-columns:minmax(0,1fr);gap:.3rem;padding-left:.8rem}
  .n{font-size:.7rem;color:var(--muted)}
 }

 /* the panel is a bordered block with an inverted head, not a floating card */
 .panel{border:var(--b2) solid var(--ink);background-color:var(--panel);
   background-image:var(--flesh);
   border-radius:var(--r-lg);overflow:hidden}
 .panel h2{display:flex;align-items:center;justify-content:space-between;gap:1rem;
   margin:0;padding:.9rem 1.1rem;background-color:var(--ink);
   background-image:var(--shell);color:var(--on-accent);
   font-size:.94rem;font-family:var(--mono);font-weight:700;letter-spacing:.06em}
 .panel h2 .lbl{display:inline-flex;align-items:center}
 .panel h2 span{font-variant-numeric:tabular-nums;opacity:.75}
 .panel .rows{border-top:0}
 .panel .row{padding-left:1.1rem;padding-right:1.1rem;
   grid-template-columns:4.5rem minmax(0,1fr)}
 .panel .row:last-of-type{border-bottom:0}
 .more{display:flex;align-items:center;justify-content:space-between;
   padding:1rem 1.1rem;border-top:var(--b) solid var(--ink);text-decoration:none;
   font:700 .93rem/1 var(--mono);text-transform:uppercase;letter-spacing:.06em;
   color:var(--ink)}
 .more:hover{background:var(--paper);color:var(--ink)}
 .more .i{transition:transform .15s}
 .more:hover .i{transform:translateX(4px)}

 /* buyers, numbered like an index */
 .capList{list-style:none;margin:0;padding:0;display:grid;gap:0}
 .capList li{display:grid;grid-template-columns:auto minmax(0,1fr);gap:1.25rem;
   padding:1.5rem 0;border-top:var(--b) solid var(--hair)}
 .capList li:first-child{border-top:0;padding-top:.5rem}
 .capnum{font:700 1rem/1 var(--mono);color:var(--accent);
   font-variant-numeric:tabular-nums;padding-top:.2rem}
 .capList h3{margin:0 0 .5rem;font-family:var(--display);font-weight:700;
   text-transform:uppercase;letter-spacing:-.01em;
   font-size:clamp(1.1rem,.95rem + .6vw,1.45rem)}
 .capList p{max-width:64ch;color:var(--muted);font-weight:500}
 .corpusstat dt{opacity:.75}
 @media (max-width:34rem){
  .capList li{grid-template-columns:minmax(0,1fr);gap:.4rem}
 }
 .buyerGrid{counter-reset:b;display:grid;list-style:none;margin:0;padding:0;
   grid-template-columns:repeat(auto-fit,minmax(19rem,1fr));gap:0 3rem}
 .buyerGrid a{display:grid;grid-template-columns:2.25rem minmax(0,1fr) auto;
   align-items:baseline;gap:.85rem;padding:1rem .25rem;color:var(--ink);
   text-decoration:none;border-top:var(--b) solid var(--hair);
   transition:background .12s,padding-left .12s}
 .buyerGrid a:hover{background:var(--panel);padding-left:.7rem;color:var(--accent);
   border-radius:var(--r-sm)}
 .buyerGrid a::before{counter-increment:b;content:counter(b,decimal-leading-zero);
   font:700 .92rem/1 var(--mono);color:var(--accent);letter-spacing:.02em}
 .buyerGrid span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;
   font-size:1.12rem;font-weight:600;letter-spacing:-.005em}
 .buyerGrid b{font:700 1.1rem/1 var(--mono);font-variant-numeric:tabular-nums}

 /* --- one tender ------------------------------------------------------------ */
 .backlink{margin:0 0 1.5rem}
 .backlink a{display:inline-flex;align-items:center;gap:.4rem;color:var(--ink);
   text-decoration:none;font:700 .9rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.06em}
 .backlink a:hover{color:var(--accent);text-decoration:underline}
 h1.detailHead{font-size:clamp(1.55rem,1rem + 1.9vw,2.6rem);line-height:1.1;
   text-transform:none;letter-spacing:-.012em;margin:.5rem 0 0;max-width:26ch}
 .facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(15rem,1fr));
   gap:0;margin:2rem 0 0;border-top:var(--b2) solid var(--rule)}
 .facts > div{padding:1rem .25rem 1.1rem;border-bottom:var(--b) solid var(--hair)}
 .facts dt{font:700 .87rem/1.35 var(--mono);text-transform:uppercase;
   letter-spacing:.08em;color:var(--muted)}
 .facts dd{margin:.4rem 0 0;font-size:1.12rem;font-weight:700;
   letter-spacing:-.005em}
 .facts dd.mono{font-family:var(--mono);font-weight:700;font-size:1.05rem;
   font-variant-numeric:tabular-nums;word-break:break-word}
 .chipish{font:700 .82rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.05em;border:var(--b) solid var(--hair);padding:.28rem .38rem;
   border-radius:var(--r-sm);margin-left:.35rem}
 .sourcebox{margin:2.5rem 0 0;padding:clamp(1.25rem,2.5vw,1.85rem);
   border:var(--b2) solid var(--rule);border-radius:var(--r-lg);
   background-color:var(--paper);background-image:var(--husk)}
 .sourcebox h2{font-size:1.15rem;margin:0 0 .7rem}
 .sourcebox p{margin:.6rem 0 0;max-width:60ch;font-weight:600}
 .idrow{display:flex;align-items:center;gap:.75rem;flex-wrap:wrap;
   margin-top:1.1rem !important}
 .summary{margin:1.1rem 0 0;max-width:58ch;color:var(--muted)}
 .rowsum{margin:.5rem 0 0;max-width:68ch;font-size:1.05rem;color:var(--muted);
   font-weight:600}
 .sectortags{margin-top:1.1rem}
 .idval{font:700 1.02rem/1 var(--mono);background:var(--panel);
   border:var(--b) solid var(--rule);border-radius:var(--r);
   padding:.72rem .85rem;word-break:break-all}

 /* --- forms ----------------------------------------------------------------- */
 form{margin:0}
 fieldset{border:0;border-top:var(--b2) solid var(--rule);margin:2.5rem 0 0;
   padding:1.5rem 0 0}
 legend{padding:0 .8rem 0 0;font:700 1.08rem/1 var(--display);text-transform:uppercase;
   letter-spacing:-.01em}
 .f{display:block;margin:1.25rem 0 0;max-width:38rem}
 .f > span{display:block;margin:0 0 .5rem;font:700 .82rem/1.3 var(--mono);
   text-transform:uppercase;letter-spacing:.06em;color:var(--muted)}
 .checks{display:grid;grid-template-columns:repeat(auto-fill,minmax(14rem,1fr));
   gap:0 1rem;margin:.5rem 0 0}
 .fhead{display:flex;align-items:baseline;justify-content:space-between;
   gap:1rem;margin:0 0 .55rem}
 .fhead label,.flabel{font:700 .9rem/1.35 var(--mono);text-transform:uppercase;
   letter-spacing:.06em;color:var(--muted)}
 .clearbtn{flex:none;min-height:0;padding:.35rem .6rem;background:transparent;
   color:var(--muted);border:var(--b) solid transparent;border-radius:var(--r-sm);
   font:700 .8rem/1 var(--mono);text-transform:uppercase;letter-spacing:.05em}
 .clearbtn:hover{background:var(--ink);color:var(--on-accent);border-color:var(--ink)}
 .fhint{margin:.55rem 0 0;color:var(--muted);font-size:1rem;max-width:52ch;
   font-weight:600}

 /* A checkbox list, not a <select multiple>: one click per value, no Ctrl. */
 .multi{max-width:38rem;background:var(--panel);overflow:hidden;
   border:var(--b) solid var(--ink);border-radius:var(--r)}
 .multifilter{border:0;border-bottom:var(--b) solid var(--hair);border-radius:0;
   min-height:2.8rem;background:transparent;font-size:.95rem}
 .multifilter:focus-visible{outline-offset:-3px}
 .multilist{max-height:16rem;overflow-y:auto;padding:.3rem}
 /* same trap as the account menu: an author display rule outranks the
    hidden attribute, so filtered-out rows would stay on screen */
 .multilist label[hidden],.nomatch[hidden]{display:none}
 .multilist label{display:flex;align-items:center;gap:.7rem;min-height:2.9rem;
   padding:0 .6rem;border-radius:var(--r-sm);cursor:pointer;font-size:1.05rem}
 .multilist label:hover{background:var(--paper)}
 .multilist label:has(input:checked){background:var(--paper);font-weight:700}
 .multilist label span{flex:1;min-width:0}
 .multilist label b{flex:none;font:700 .88rem/1 var(--mono);color:var(--muted);
   font-variant-numeric:tabular-nums}
 .nomatch{margin:0;padding:.9rem .6rem;color:var(--muted);font-size:1rem}

 .checks label,.chk label{display:flex;align-items:center;gap:.7rem;
   min-height:3.1rem;padding:0 .45rem;font-size:1.08rem;cursor:pointer;
   margin-left:-.45rem}
 .checks label:hover,.chk label:hover{background:var(--panel);
   border-radius:var(--r-sm)}
 .chk label{display:inline-flex;padding-left:0;margin-left:0}
 .actions{display:flex;align-items:center;gap:1.5rem;flex-wrap:wrap;margin:2rem 0 0}
 .divider{margin:1.5rem 0;font:700 .9rem/1 var(--mono);text-transform:uppercase;
   letter-spacing:.08em;color:var(--muted)}
 .filters{display:grid;gap:.9rem;align-items:end;margin:0 0 1.5rem;
   grid-template-columns:minmax(0,1.4fr) minmax(0,1fr) auto auto auto}
 .filters .f{margin:0;max-width:none}
 @media (max-width:52rem){.filters{grid-template-columns:1fr;align-items:stretch}}

 /* --- states ---------------------------------------------------------------- */
 .notice{margin:1.25rem 0 0;padding:.95rem 1.15rem;font-size:1rem;max-width:none;
   font-weight:600;
   border-radius:var(--r);
   background:var(--panel);border:var(--b) solid var(--ink);
   border-left:var(--b2) solid var(--accent)}
 .notice.good{border-left-color:var(--ok)}
 pre#err{margin:1.5rem 0 0;padding:.9rem 1.1rem;overflow-x:auto;white-space:pre-wrap;
   border-radius:var(--r);
   font:.98rem/1.55 var(--mono);color:var(--danger);background:var(--panel);
   border:var(--b) solid var(--danger)}
 pre#err:empty{display:none}
 .empty{padding:3rem 1.5rem;text-align:center;border:var(--b2) dashed var(--hair);
   border-radius:var(--r-lg)}
 .empty p{margin:0 auto;max-width:34ch;color:var(--muted);font-weight:600}
 .sk .b{height:1rem;background:var(--hair);animation:pulse 1.4s ease-in-out infinite}
 .sk .b.s{height:.75rem;max-width:20rem;margin-top:.6rem}
 .sk .b.g{width:2.5rem}
 @keyframes pulse{50%{opacity:.35}}

 /* --- footer: the page inverts ---------------------------------------------- */
 /* --- interaction ----------------------------------------------------------- */
 .row{transition:background .2s ease, transform .2s ease}
 .row:hover{transform:translateX(5px)}
 .row::before{transition:opacity .2s ease, transform .2s ease;
   transform-origin:center}
 .row:hover::before{transform:scaleX(1.75)}
 .t{transition:color .18s ease}
 .row:hover .t{color:var(--accent)}
 .row:hover .n{color:var(--accent)}
 .n{transition:color .18s ease}

 .sec > .wrap{transition:box-shadow .25s ease, transform .25s ease}
 .panel{transition:transform .25s ease, box-shadow .25s ease}
 .panel:hover{transform:translateY(-3px);
   box-shadow:0 14px 32px -18px rgba(42,22,8,.6)}

 .stats > div{transition:background .22s ease}
 .stats > div:hover{background:rgba(255,255,255,.08)}
 .stats dd{transition:transform .22s ease;transform-origin:left center}
 .stats > div:hover dd{transform:scale(1.045)}

 .capList li{transition:background .22s ease}
 .capList li:hover{background:var(--paper)}
 .capList h3{transition:color .2s ease, transform .2s ease;
   transform-origin:left center;display:inline-block}
 .capList li:hover h3{color:var(--accent);transform:scale(1.03)}
 .capnum{transition:transform .25s ease, color .2s ease;
   transform-origin:left center;display:inline-block}
 .capList li:hover .capnum{transform:scale(1.35) translateX(2px)}

 .buyerGrid b{transition:transform .2s ease;transform-origin:right center;
   display:inline-block}
 .buyerGrid a:hover b{transform:scale(1.15)}

 button,.btn{transition:background .16s ease, border-color .16s ease,
   color .16s ease, transform .12s ease}
 button:hover,.btn:hover{transform:translateY(-2px)}
 button:active,.btn:active{transform:translateY(1px)}

 /* footer links grow an underline from the left rather than blinking one on */
 .foot a{position:relative;text-decoration:none}
 .foot a::after{content:"";position:absolute;left:0;right:0;bottom:-3px;height:2px;
   background:currentColor;transform:scaleX(0);transform-origin:left;
   transition:transform .22s ease}
 .foot a:hover{text-decoration:none}
 .foot a:hover::after{transform:scaleX(1)}

 .tag{transition:background .18s ease, color .18s ease, transform .18s ease}
 .row:hover .tag{transform:translateY(-1px)}

 /* the bar condenses once you leave the top of the page */
 .top{transition:min-height .25s ease, box-shadow .25s ease}
 .top .wrap{transition:min-height .25s ease}
 .top.scrolled .wrap{min-height:3.6rem}
 .top.scrolled{box-shadow:0 8px 24px -18px rgba(42,22,8,.75)}
 .brand,.mark{transition:transform .25s ease}
 .top.scrolled .mark{transform:scale(.88)}

 .foot{background-color:var(--ink);background-image:var(--shell);
   color:var(--on-accent);border-top:var(--b2) solid var(--rule)}
 .foot .wrap{display:grid;gap:2.5rem 3rem;padding-block:3.5rem 2.5rem;
   grid-template-columns:minmax(0,1.6fr) repeat(2,minmax(0,1fr))}
 @media (max-width:52rem){
  .foot .wrap{grid-template-columns:1fr 1fr}
  .fbrand{grid-column:1/-1}
 }
 .foot .brand{color:var(--on-accent)}
 .foot .brand span{color:var(--on-accent);opacity:.65}
 .foot .mark .bg{fill:var(--on-accent)} .foot .mark .fg{fill:var(--ink)}
 .foot .mark .fg2{fill:var(--paper)}
 .fbrand p{margin:1rem 0 0;max-width:34ch;font-size:1.08rem;opacity:.82;
   font-weight:500}
 .foot h2{margin:0 0 1.1rem;font:700 .92rem/1.35 var(--mono);text-transform:uppercase;
   letter-spacing:.09em;color:var(--on-accent);opacity:.72}
 .foot ul{display:grid;gap:.7rem;margin:0;padding:0;list-style:none}
 .foot a{color:var(--on-accent);text-decoration:none;font-size:1.1rem}
 .foot a:hover{color:var(--on-accent);text-decoration:underline;
   text-decoration-thickness:2px}
 .fnote{border-top:var(--b) solid rgba(255,255,255,.28)}
 .fnote .wrap{display:block;padding-block:1.5rem 2.5rem;font-size:.94rem;
   font-family:var(--mono);letter-spacing:0;line-height:1.6}
 .fnote p{margin:0;max-width:80ch;opacity:.7;font-weight:700}

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
   <rect class=bg width="32" height="32"/>
   <g class=fg><rect x="7" y="9" width="18" height="3"/>
   <rect x="7" y="14.5" width="12" height="3"/></g>
   <rect class=fg2 x="7" y="20" width="6" height="3"/>
  </svg>
  <b>Tender</b><span>OS</span></a>
 <nav aria-label="Main"><a href="/">Home</a><a href="/matches">Matches</a>
 <a href="/browse">Browse</a><a href="/docs">API</a></nav>
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
const _path = location.pathname;
const _section =
  _path.startsWith('/c/') ? '/matches' :
  _path.startsWith('/t/') ? '/browse' :
  _path === '/profile'    ? '/matches' : _path;
for(const a of document.querySelectorAll('.top nav a')){
  if(a.getAttribute('href') === _section) a.setAttribute('aria-current','page');
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
  if(!d.user){
    el.innerHTML = '<a href="/login">Sign in</a>'
      + '<a class="btn sm" href="/signup">Get ranked matches</a>';
    return;
  }
  // Signed in: a real account menu, not a row of loose links. The label is the
  // company name when there is one, because that is what the person recognises;
  // the email stays in the menu where it is still checkable.
  const co = d.company && d.company.name ? d.company.name : '';
  const label = co || d.user.email.split('@')[0];
  const initials = (co
      ? co.trim().split(' ').filter(Boolean).slice(0,2).map(w=>w[0])
      : [d.user.email[0]]).join('').toUpperCase();
  const warn = d.user.email_verified ? ''
    : '<span class=menuwarn>Email not confirmed</span>';
  el.innerHTML =
    '<div class=usermenu>'
    + '<button class=userbtn type=button data-action=usermenu'
    +   ' aria-haspopup=true aria-expanded=false>'
    +   '<span class=avatar aria-hidden=true>'+esc(initials)+'</span>'
    +   '<span class=uname>'+esc(label)+'</span>'
    +   '__I_DOWN__'
    + '</button>'
    + '<div class=menu hidden>'
    +   '<p class=menuhead><b>'+esc(label)+'</b>'
    +     '<span class=menumail>'+esc(d.user.email)+'</span>'+warn+'</p>'
    +   '<a href="/matches">__I_TARGET__My matches</a>'
    +   '<a href="/profile">__I_SLIDERS__Edit answers</a>'
    +   '<button type=button data-action=signout>__I_SIGNOUT__Sign out</button>'
    + '</div>'
    + '</div>';
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

// clipboard needs a secure context; over plain http on a LAN address it is
// undefined, so fall back to selecting the id for a manual copy.
function copyId(btn){
  const text = btn.dataset.copy || '';
  const done = () => {
    const was = btn.textContent;
    btn.textContent = 'Copied';
    setTimeout(() => { btn.textContent = was; }, 1400);
  };
  if(navigator.clipboard && window.isSecureContext){
    navigator.clipboard.writeText(text).then(done).catch(select);
  } else { select(); }
  function select(){
    const el = document.getElementById('tid');
    if(!el) return;
    const r = document.createRange();
    r.selectNodeContents(el);
    const sel = getSelection();
    sel.removeAllRanges();
    sel.addRange(r);
  }
}

// Inline onclick="" attributes are blocked by our own Content-Security-Policy:
// a nonce whitelists a <script> block, it does NOT whitelist attribute handlers.
// One delegated listener covers links that are inserted later via innerHTML.
// One question's answer, wiped. Works for a text box, a number, and a whole
// checkbox list, because every control in a group shares the same name.
function clearField(btn){
  const form = btn.closest('form');
  const name = btn.dataset.clear;
  if(!form || !name) return;
  form.querySelectorAll('[name="'+name+'"]').forEach(el=>{
    if(el.type === 'checkbox' || el.type === 'radio') el.checked = false;
    else if(el.tagName === 'SELECT') [...el.options].forEach(o=>{o.selected=false;});
    else el.value = '';
  });
  const group = btn.closest('.f');
  const filter = group && group.querySelector('.multifilter');
  if(filter){ filter.value = ''; applyFilter(filter); }
  const first = group && group.querySelector('input,select,textarea');
  if(first) first.focus();
}

// Type-to-narrow on the long lists. Hiding the label rather than removing it
// keeps anything already ticked selected while it is filtered out of view.
function applyFilter(inp){
  const list = inp.parentNode.querySelector('.multilist');
  if(!list) return;
  const q = inp.value.trim().toLowerCase();
  let shown = 0;
  list.querySelectorAll('label').forEach(l=>{
    const hit = !q || l.textContent.toLowerCase().includes(q);
    l.hidden = !hit;
    if(hit) shown++;
  });
  let none = list.querySelector('.nomatch');
  if(!shown && !none){
    none = document.createElement('p');
    none.className = 'nomatch';
    none.textContent = 'Nothing matches that.';
    list.appendChild(none);
  } else if(none){
    none.hidden = shown > 0;
  }
}
document.addEventListener('input', function(e){
  if(e.target.classList && e.target.classList.contains('multifilter'))
    applyFilter(e.target);
});

// The district list only covers Madhya Pradesh -- that is the whole list the
// matcher can recognise in a tender title -- so asking for a district while
// some other state is selected offers choices that could never match. Show the
// question only when MP is one of the states, and drop any stale ticks with it.
function syncDistricts(){
  const box = document.getElementById('districtField');
  if(!box) return;
  const mp = [...document.querySelectorAll('input[name="states"]')]
    .some(c => c.checked && c.value === 'Madhya Pradesh');
  box.hidden = !mp;
  if(!mp){
    box.querySelectorAll('input[name="districts"]:checked')
       .forEach(c => { c.checked = false; });
  }
}
document.addEventListener('change', function(e){
  if(e.target && e.target.name === 'states') syncDistricts();
});
document.addEventListener('DOMContentLoaded', syncDistricts);

function closeMenus(except){
  document.querySelectorAll('.usermenu').forEach(m => {
    if(m === except) return;
    const b = m.querySelector('.userbtn'), p = m.querySelector('.menu');
    if(b) b.setAttribute('aria-expanded','false');
    if(p) p.hidden = true;
  });
}

function toggleMenu(btn){
  const wrap = btn.closest('.usermenu');
  const panel = wrap.querySelector('.menu');
  const open = panel.hidden;
  closeMenus(wrap);
  panel.hidden = !open;
  btn.setAttribute('aria-expanded', String(open));
}

// A click anywhere else closes the menu; Escape does too, and returns focus to
// the button so the keyboard does not get stranded.
document.addEventListener('click', function(e){
  if(!e.target.closest('.usermenu')) closeMenus(null);
});
document.addEventListener('keydown', function(e){
  if(e.key !== 'Escape') return;
  const open = document.querySelector('.usermenu .menu:not([hidden])');
  if(!open) return;
  closeMenus(null);
  const b = open.parentNode.querySelector('.userbtn');
  if(b) b.focus();
});

document.addEventListener('click', function(e){
  const el = e.target.closest('[data-action]');
  if(!el) return;
  e.preventDefault();
  if(el.dataset.action === 'usermenu') return toggleMenu(el);
  if(el.dataset.action === 'clear') return clearField(el);
  if(el.dataset.action === 'signout') signout();
  if(el.dataset.action === 'resend') resend();
  if(el.dataset.action === 'copy') copyId(el);
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

    // Section headings arrive from the left, which reads as the page turning
    // rather than everything fading in the same way.
    document.querySelectorAll('.secHead').forEach(h => {
      gsap.from(h, {
        x: -34, opacity: 0, duration: .7, ease: 'power3.out',
        scrollTrigger: { trigger: h, start: 'top 88%', once: true }
      });
    });

    // The figures count up; the cells they sit in step in under them.
    document.querySelectorAll('.stats').forEach(band => {
      gsap.from(band.children, {
        y: 24, opacity: 0, duration: .55, stagger: .08, ease: 'power2.out',
        scrollTrigger: { trigger: band, start: 'top 92%', once: true }
      });
    });

    // Capability rows come in one after another with their numbers.
    const caps = document.querySelectorAll('.capList li');
    if (caps.length) {
      gsap.from(caps, {
        x: -22, opacity: 0, duration: .6, stagger: .09, ease: 'power3.out',
        scrollTrigger: { trigger: caps[0].parentNode, start: 'top 85%', once: true }
      });
    }

    // Rows inside the live panel deal themselves out.
    document.querySelectorAll('.panel .rows').forEach(list => {
      gsap.from(list.children, {
        y: 14, opacity: 0, duration: .45, stagger: .05, ease: 'power2.out',
        scrollTrigger: { trigger: list, start: 'top 90%', once: true }
      });
    });

    // Reveal on scroll, but only ever hide what is already off-screen. Hiding
    // in CSS and un-hiding in JS is the version that leaves a page blank when
    // anything upstream fails; this way the worst case is no animation.
    const below = [...document.querySelectorAll('.reveal')].filter(
      el => el.getBoundingClientRect().top > window.innerHeight * 0.92);
    if(below.length){
      gsap.set(below,{opacity:0, y:26, scale:.99});
      // One batch, so a long list staggers together instead of firing a
      // separate trigger per row.
      ScrollTrigger.batch(below, {
        start: 'top 92%',
        once: true,
        onEnter: b => gsap.to(b,{opacity:1, y:0, scale:1, duration:.65,
                                 stagger:.07, ease:'power3.out', overwrite:true})
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

  // Outside matchMedia on purpose: condensing the bar is a state change, and
  // reduced motion only needs to skip the transition, not the behaviour.
  ScrollTrigger.create({
    start: 'top -64', end: 99999,
    toggleClass: { targets: '.top', className: 'scrolled' }
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
    <rect class=bg width="32" height="32"/>
    <g class=fg><rect x="7" y="9" width="18" height="3"/>
    <rect x="7" y="14.5" width="12" height="3"/></g>
    <rect class=fg2 x="7" y="20" width="6" height="3"/>
   </svg>
   <b>Tender</b><span>OS</span></a>
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
           else f'<section class=sec><div class="wrap flow">{body}</div></section>')
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


def row(gutter: str, rank: str, t: Tender, tags: str = "",
        summary: bool = False) -> str:
    """One list row. escape(): titles come from a scraped page and go straight
    into HTML.

    The title opens our own page rather than the source portal. eprocure.gov.in
    serves "Invalid Url" for any tendersfullview link that arrives without a
    Referer from its own domain, and a browser cannot be made to send that from
    here, so linking out directly was a dead end on every row.
    """
    return (
        f'<li class="row {rank}"><div class=n>{escape(gutter)}</div><div>'
        f'<a class=t href="/t/{t.id}">{escape(t.title)}</a>'
        + (f"<p class=rowsum>{escape(summary_for(t))}</p>" if summary else "")
        + f"<p class=m>{escape(t.organization or 'unnamed buyer')} &middot; "
        + f"closes <time datetime='{t.deadline}'>{t.deadline}</time></p>{tags}"
        + "</div></li>"
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

# Every question carries its own Clear button, and the multi-value questions are
# checkbox lists rather than <select multiple>: a native multi-select needs
# Ctrl-click to add a second value, which is the single most common way people
# lose answers on a form like this.
PROFILE_FIELDS = """<fieldset><legend>What you do</legend>

<div class=f>
 <div class=fhead><label for=f_name>Company name (required)</label>
  <button type=button class=clearbtn data-action=clear data-clear=name>Clear</button></div>
 <input id=f_name name=name required autocomplete=organization>
</div>

<div class=f>
 <div class=fhead><span class=flabel>Sectors you work in (required)</span>
  <button type=button class=clearbtn data-action=clear data-clear=sectors>Clear</button></div>
 <div class=checks>__SECTORS__</div>
</div>

<div class=f>
 <div class=fhead><label for=f_keywords>Keywords, comma separated</label>
  <button type=button class=clearbtn data-action=clear data-clear=keywords>Clear</button></div>
 <input id=f_keywords name=keywords autocomplete=off spellcheck=false
    placeholder="e.g. transformer, cable&hellip;">
 <p class=fhint>A keyword must appear in the tender title as a whole word.</p>
</div>
</fieldset>

<fieldset><legend>Where you work</legend>

<div class=f>
 <div class=fhead><span class=flabel>States and UTs</span>
  <button type=button class=clearbtn data-action=clear data-clear=states>Clear</button></div>
 <div class=multi>
  <input class=multifilter type=search autocomplete=off
     aria-label="Filter states" placeholder="Filter states&hellip;">
  <div class=multilist>__STATES__</div>
 </div>
 <p class=fhint>Click as many as you like. No need to hold Ctrl.</p>
</div>

<div class=f id=districtField hidden>
 <div class=fhead><span class=flabel>Madhya Pradesh districts</span>
  <button type=button class=clearbtn data-action=clear data-clear=districts>Clear</button></div>
 <div class=multi>
  <input class=multifilter type=search autocomplete=off
     aria-label="Filter districts" placeholder="Filter districts&hellip;">
  <div class=multilist>__DISTRICTS__</div>
 </div>
 <p class=fhint>Districts are Madhya Pradesh only for now, so this question
    appears when you pick that state.</p>
</div>
</fieldset>

<fieldset><legend>Who you want to work with</legend>

<div class=f>
 <div class=fhead><span class=flabel>Buyers you want</span>
  <button type=button class=clearbtn data-action=clear data-clear=buyers>Clear</button></div>
 <div class=multi>
  <input class=multifilter type=search autocomplete=off
     aria-label="Filter buyers" placeholder="Filter buyers&hellip;">
  <div class=multilist>__BUYERS__</div>
 </div>
</div>

<div class=f>
 <div class=fhead><span class=flabel>Buyers to never show</span>
  <button type=button class=clearbtn data-action=clear data-clear=exclude_buyers>Clear</button></div>
 <div class=multi>
  <input class=multifilter type=search autocomplete=off
     aria-label="Filter buyers to exclude" placeholder="Filter buyers&hellip;">
  <div class=multilist>__XBUYERS__</div>
 </div>
 <p class=fhint>Excluding a buyer removes its tenders entirely. One buyer alone is
    over half the corpus, so this is the fastest way to cut noise.</p>
</div>
</fieldset>

<fieldset><legend>What to rule out</legend>

<div class=f>
 <div class=fhead><label for=f_xkw>Never show tenders whose title contains</label>
  <button type=button class=clearbtn data-action=clear data-clear=exclude_keywords>Clear</button></div>
 <input id=f_xkw name=exclude_keywords autocomplete=off spellcheck=false
    placeholder="e.g. scrap, auction&hellip;">
</div>

<div class=f>
 <div class=fhead><label for=f_lead>Days you need to prepare a bid</label>
  <button type=button class=clearbtn data-action=clear data-clear=min_lead_days>Clear</button></div>
 <input id=f_lead name=min_lead_days autocomplete=off type=number value=7 min=0 max=365>
</div>

<div class=f>
 <div class=fhead><label for=f_val>Max project value you can execute, INR</label>
  <button type=button class=clearbtn data-action=clear data-clear=max_project_value>Clear</button></div>
 <input id=f_val name=max_project_value autocomplete=off type=number min=0>
 <p class=fhint>Value is not published on any listing page we are allowed to read,
    so this answer has no effect yet. It starts working when detail-page ingest
    lands.</p>
</div>
</fieldset>"""


# Shared by both pages. Reads whatever profile inputs are present -- the signup
# page omits contact_email (it would be asking for an address twice), so every
# lookup tolerates a missing field rather than assuming the full form.
COLLECT_JS = """<script>
function collectProfile(f){
  const el=n=>f.elements[n];
  const v=n=>el(n)?el(n).value.trim():'';
  const csv=n=>{const x=v(n); return x?x.split(',').map(s=>s.trim()).filter(Boolean):[];};
  const picked=n=>[...f.querySelectorAll('input[name="'+n+'"]:checked')]
                    .map(c=>c.value);
  return {
    name:v('name'),
    contact_email:v('contact_email')||null,
    sectors:picked('sectors'),
    keywords:csv('keywords'),
    districts:picked('districts'),
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
        f'<label><input type=checkbox name=sectors value="{k}">'
        f"<span>{escape(v)}</span></label>"
        for k, v in sorted(SECTOR_LABELS.items(), key=lambda kv: kv[1])
    )
    districts = "".join(
        f'<label><input type=checkbox name=districts value="{escape(d)}">'
        f"<span>{escape(d)}</span></label>"
        for d in sorted(MP_DISTRICTS)
    )
    states = "".join(
        f'<label><input type=checkbox name=states value="{escape(st)}">'
        f"<span>{escape(st)}</span></label>"
        for st in STATES
    )
    # Offered from the corpus, not a hard-coded list: the option text is exactly the
    # string stored in tenders.organization, so anything offered here can match.
    top = list(db.execute(
            select(Tender.organization, func.count())
            .where(Tender.organization.is_not(None))
            .group_by(Tender.organization)
            .order_by(func.count().desc())
            .limit(40)
    ).all())

    def buyer_boxes(field: str) -> str:
        return "".join(
            f'<label><input type=checkbox name={field} value="{escape(name)}">'
            f"<span>{escape(name)}</span><b>{n:,}</b></label>"
            for name, n in top
        )
    # .replace, not .format: the inline <script> is full of literal braces.
    return (
        markup.replace("__SECTORS__", boxes)
        .replace("__DISTRICTS__", districts)
        .replace("__STATES__", states)
        .replace("__BUYERS__", buyer_boxes("buyers"))
        .replace("__XBUYERS__", buyer_boxes("exclude_buyers"))
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
    """The home page, whether or not you are signed in.

    It used to redirect straight to your matches once you had a profile, which
    meant the corpus figures, the closing-soonest list and the buyer index were
    unreachable the moment you had an account. Matches now have their own
    address instead.
    """
    profile = _profile_of(db, user)
    if profile is not None:
        hero, body = _welcome_signed_in(db, profile)
        return page(f"Home | {profile.name}", body, nonce_of(request), hero=hero)
    hero, body = _welcome(db)
    return page("Find tenders", body, nonce_of(request), hero=hero)


# The claims here are all things the code actually does; the numbers are read
# from the live corpus rather than typed in. "Scored in milliseconds" is the
# warm digest path measured on this machine, not a target.
CAPS = """<section class=sec><div class=wrap>
<div class="secHead reveal">
 <h2>AI Smart Bidding</h2>
 <p class=kicker>The intelligence layer</p>
</div>
<ol class=capList>
 <li class=reveal>
  <p class=capnum>01</p>
  <div><h3>AI-powered relevance engine</h3>
  <p>Every one of __OPEN__ open notices is scored against your answers on each
  run. Terms are weighted by how rare they are across the live corpus, so a word
  like &ldquo;maintenance&rdquo; that sits on most government tenders cannot fake
  a match the way a rare one like &ldquo;transformer&rdquo; should.</p></div>
 </li>
 <li class=reveal>
  <p class=capnum>02</p>
  <div><h3>Always-on data intelligence</h3>
  <p>__NSOURCES__ official portals on a scheduled crawl, with a time budget so a
  slow source cannot stall the run. Notices republished across portals are
  collapsed rather than counted twice.</p></div>
 </li>
 <li class=reveal>
  <p class=capnum>03</p>
  <div><h3>Full-chain provenance</h3>
  <p>Each notice carries the portal it came from and that portal&rsquo;s licence.
  robots.txt is checked per source and refused sources stay out, which is why GeM
  is absent by design rather than by omission.</p></div>
 </li>
 <li class=reveal>
  <p class=capnum>04</p>
  <div><h3>Millisecond ranking at scale</h3>
  <p>A full pass over the corpus is cached per profile and reused until your
  answers or the data change, so a repeat load is a lookup rather than a
  re-scoring of __OPEN__ rows.</p></div>
 </li>
</ol>
</div></section>"""


# ---- the signed-in home ------------------------------------------------------

HERO_ME = """<section class=hero><div class=wrap><div class=heroTop>
<div>
 <p class=eyebrow><span class=badge>AI Smart Bidding</span>
 Signed in as __COMPANY____COLLECTED__</p>
 <h1>Tenders that<br>fit __SHORT__</h1>
</div>
 <p class=bignum><b>__NMATCH__</b><em>Matches for you</em></p>
</div>
<div class=heroCopy>
 <p class=lede>AI-scored against the answers you gave. __SOONLINE__ Drawn from
 <b>__OPEN__</b> tenders tracked across __NSOURCES__ official portals.</p>
 <div>
  <form class=search action="/browse" method=get>
   <label class=sr for=hq>Search tender titles</label>
   <input id=hq name=q autocomplete=off spellcheck=false
      placeholder="Search all __OPEN__ notices&hellip;">
   <button>__I_SEARCH__Search</button>
  </form>
  <p class=hint>Not the right things? <a class=nowrap href="/profile">Edit your
  answers__I_UPRIGHT__</a></p>
 </div>
</div>
<dl class="stats big">
 <div><dt>Tenders tracked</dt><dd>__OPEN__</dd></div>
 <div><dt>Closing this week</dt><dd>__CSOON__</dd></div>
 <div class=mine><dt>Matched to you</dt><dd>__NMATCH__</dd></div>
 <div class=mine><dt>Yours closing in 7 days</dt><dd>__NSOON__</dd></div>
</dl>
</div></section>"""


WELCOME_ME = """<section class=sec><div class=wrap>
<div class="secHead reveal">
 <h2>Closing soonest for you</h2>
 <p class=kicker>__NSOON__ of your matches close within seven days</p>
</div>
__CLOSING__
</div></section>

<section class=sec><div class=wrap>
<div class="secHead reveal">
 <h2>Who is buying</h2>
 <p class=kicker>The buyers behind your matches</p>
</div>
<p class="hint reveal">Counted across your matches only, not the whole corpus.</p>
<ul class=buyerGrid>__BUYERS__</ul>
</div></section>"""


def _welcome_signed_in(db: Session, company: Company) -> tuple[str, str]:
    """The home page for someone who has answered the questions.

    Everything on it is their own result set: the notices closing soonest are
    their matches re-sorted by deadline rather than by score, and the buyer
    index counts only the buyers that actually appear in those matches.
    """
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
    n_soon = d.closing_within_7
    c_soon = db.scalar(
        select(func.count()).select_from(Tender).where(
            Tender.duplicate_of.is_(None),
            Tender.deadline.between(today, today + timedelta(7)),
        )
    )
    score_of = {tid: sc for sc, _r, tid in d.scored}

    soon_ids = list(d.by_deadline[:6])
    rows_by_id = hydrate(db, soon_ids)
    if soon_ids:
        rows = "".join(
            row(*days_left(rows_by_id[i].deadline, today), rows_by_id[i],
                f"<ul class=tags><li class=tag>Score {score_of.get(i, 0)}</li></ul>")
            for i in soon_ids if i in rows_by_id
        )
        closing = (
            '<div class="panel reveal">'
            "<h2><span class=lbl>__I_CLOCK__Your matches, by deadline</span>"
            f"<span>{d.total:,} scored</span></h2>"
            f"<ol class=rows>{rows}</ol>"
            '<a class=more href="/matches">See all your matches__I_ARROW__</a>'
            "</div>"
        )
    else:
        closing = (
            '<div class="empty reveal"><p>Nothing matches your answers yet. '
            'Broaden the sectors, drop a state, or lower the preparation days on '
            'your <a href="/profile">answers</a>.</p></div>'
        )

    n_buyers = len(d.buyers)
    counts = d.buyers[:8]
    buyers = "".join(
        f'<li class=reveal><a href="/browse?organization={quote(name)}">'
        f"<span>{escape(name)}</span><b>{n:,}</b></a></li>"
        for name, n in counts
    )

    n_sources = db.scalar(select(func.count()).select_from(Source))
    last = db.scalar(
        select(func.max(ConnectorRun.finished_at)).where(ConnectorRun.status == "ok")
    )
    collected = f" &middot; collected {last.strftime('%d %b')}" if last else ""
    short = company.name if len(company.name) <= 22 else company.name[:21] + "\u2026"
    soonline = (
        f"{n_soon:,} of them close within seven days."
        if n_soon else "None of them close in the next seven days."
    )

    def fill(markup: str) -> str:
        return (
            markup.replace("__COMPANY__", escape(company.name))
            .replace("__SHORT__", escape(short))
            .replace("__COLLECTED__", collected)
            .replace("__NMATCH__", f"{d.total:,}")
            .replace("__NSOON__", f"{n_soon:,}")
            .replace("__NBUYERS__", f"{n_buyers:,}")
            .replace("__OPEN__", f"{n_open:,}")
            .replace("__CSOON__", f"{c_soon:,}")
            .replace("__NSOURCES__", str(n_sources))
            .replace("__SOONLINE__", soonline)
            .replace("__CLOSING__", closing)
            .replace("__BUYERS__", buyers)
        )

    return fill(HERO_ME), fill(WELCOME_ME) + fill(CAPS)


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

    return fill(HERO), fill(WELCOME) + fill(CAPS)


# The hero is full-bleed, so it is rendered outside <main> by page(). The search
# box is a plain GET form to /browse: no JS, works before the script runs, and
# lands on a URL the browse page already knows how to restore.
HERO = """<section class=hero><div class=wrap>
<p class=eyebrow><span class=badge>AI Smart Bidding</span>__NSOURCES__ official portals__COLLECTED__</p>
<div class=heroTop>
 <h1>Find the tenders<br>worth your time</h1>
 <p class=bignum><b>__OPEN__</b><em>Open right now</em></p>
</div>
<div class=heroCopy>
 <p class=lede>Every open notice from Indian government procurement portals, in
 one place, ranked by an AI relevance engine against what your company actually
 does.</p>
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
<dl class="stats big">
 <div><dt>Tenders tracked</dt><dd>__OPEN__</dd></div>
 <div><dt>Closing this week</dt><dd>__SOON__</dd></div>
 <div><dt>Buyers listed</dt><dd>__NBUYERS__</dd></div>
 <div><dt>Official portals</dt><dd>__NSOURCES__</dd></div>
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
  f.exclude_keywords.value=(c.exclude_keywords||[]).join(', ');
  f.min_lead_days.value=c.min_lead_days??7;
  f.max_project_value.value=c.max_project_value??'';
  [['sectors',c.sectors],['states',c.states],['districts',c.districts],
   ['buyers',c.buyers],['exclude_buyers',c.exclude_buyers]].forEach(([n,vals])=>{
    const want = new Set(vals||[]);
    f.querySelectorAll('input[name="'+n+'"]').forEach(b=>{ b.checked = want.has(b.value); });
  });
  syncDistricts();
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
      + '<a class=t href="/t/' + esc(m.tender.id) + '">'
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
    d = match_digest(db, company)
    total = d.total
    shown = d.scored[:50]
    rows_by_id = hydrate(db, [tid for _s, _r, tid in shown])

    top = shown[0][0] if shown else 0
    rows = []
    for score, reasons, tid in shown:
        t = rows_by_id.get(tid)
        if t is None:      # purged between scoring and this render
            continue
        chips = "".join(f"<li class=tag>{escape(r)}</li>" for r in reasons)
        rows.append(
            row(str(score), rank_class(score, top), t,
                f"<ul class=tags>{chips}</ul>" if chips else "",
                summary=True)
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
        + f"<p class=lede>{total:,} open tenders match your answers"
        + (f", best {len(rows)} shown. " if total > len(rows) else ". ")
        + f'<a href="/profile">Edit answers</a></p>{body}',
        nonce_of(request),
    )


# ---- one tender --------------------------------------------------------

# Their search page, which is the only thing on eprocure that opens cold. The
# detail pages are hotlink-protected AND behind a CAPTCHA, so we can neither
# link to them nor fetch them; the tender id is what makes a notice findable.
CPPP_SEARCH = "https://eprocure.gov.in/cppp/tendersearch"


def _clean(v: str | None) -> str:
    """The listing prints an em-dash placeholder in empty cells."""
    v = (v or "").strip()
    return "" if v in {"", "--", "-", "NA", "N/A"} else v


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
    return f"A {trade} notice from {buyer}{where}.{when}"


def summary_for(t: Tender) -> str:
    """The one-line summary, for anywhere a tender is listed."""
    sectors = sorted(SECTOR_LABELS[k] for k in derive_sectors(t.title))
    places = sorted(derive_states(t.title) | derive_districts(t.title))
    return _summary(t, sectors, places, days_left(t.deadline)[0])


def _fact(label: str, value: str, mono: bool = False) -> str:
    if not value:
        return ""
    cls = ' class=mono' if mono else ""
    return f"<div><dt>{escape(label)}</dt><dd{cls}>{value}</dd></div>"


DETAIL = """<div class=backlink><a href="__BACK__">__I_LEFT__Back to all tenders</a></div>
<p class=kicker>__BUYER__</p>
<h1 class=detailHead>__TITLE__</h1>
<p class="lede summary">__SUMMARY__</p>
__SECTORTAGS__
<dl class=facts>__FACTS__</dl>
<div class=sourcebox>
 <h2>Find this notice on the source portal</h2>
 <p>__SOURCE_NAME__ does not allow other sites to link straight to a tender
 page, so the link below opens its search form. Paste the tender ID into it.</p>
 <p class=idrow><span class=idval id=tid>__REF__</span>
   <button class=ghost data-action=copy data-copy="__REF__">Copy ID</button>
   <a class=btn target=_blank rel="noopener noreferrer"
      href="__SEARCH__">Open __SOURCE_NAME____I_UPRIGHT__</a></p>
 <p class=hint>Licence: __LICENCE__. We store the notice, never the bid
 documents.</p>
</div>"""


@router.get("/t/{tender_id}", response_class=HTMLResponse)
def tender_detail(
    request: Request, tender_id: int, db: Session = Depends(get_db)
) -> str:
    """Everything the aggregator holds for one notice, on our own page.

    Deliberately only what we already collected: the portal's own detail page
    is behind a CAPTCHA, so there is nothing further to fetch and nothing here
    is republished beyond the listing fields.
    """
    t = db.get(Tender, tender_id)
    if t is None:
        raise HTTPException(status_code=404, detail="tender not found")
    src = db.get(Source, t.source_id) if t.source_id else None

    # The connector keeps the whole scraped listing row, so the closing *time*,
    # the bid-opening date, the corrigendum flag and the buyer's own reference
    # number are already here -- they just never had a column of their own.
    raw = t.raw_payload if isinstance(t.raw_payload, dict) else {}
    sectors = sorted(SECTOR_LABELS[k] for k in derive_sectors(t.title))
    places = sorted(derive_states(t.title) | derive_districts(t.title))

    label, _rank = days_left(t.deadline)
    # the listing carries a closing *time*, which the deadline column drops
    closes = (
        f"<time datetime='{t.deadline}'>"
        f"{escape(_clean(raw.get('closing')) or str(t.deadline))}</time>"
        + (f" <span class=chipish>{escape(label)}</span>" if label else "")
        if t.deadline else "not stated"
    )
    value = (
        f"INR {t.estimated_value:,.0f}" if t.estimated_value is not None
        else "not published on the listing"
    )
    facts = "".join([
        _fact("Tender ID", escape(t.external_ref or ""), mono=True),
        _fact("Buyer's reference", escape(_clean(raw.get("reference_no"))), mono=True),
        _fact("Buyer", escape(t.organization or "unnamed buyer")),
        _fact("Department", escape(t.department or "")),
        _fact("Bids close", closes, mono=True),
        _fact("Bids opened", escape(_clean(raw.get("opening"))), mono=True),
        _fact("Published", escape(_clean(raw.get("published"))
                                  or str(t.published_date or "not stated")), mono=True),
        _fact("Corrigendum", escape(_clean(raw.get("corrigendum")))),
        _fact("Where", escape(", ".join(places))),
        _fact("Estimated value", escape(value), mono=True),
        _fact("Source", escape(src.name if src else "unknown")),
        _fact("Listing position", escape(_clean(raw.get("serial")).rstrip(".")),
              mono=True),
        _fact("First collected",
              escape(t.first_seen_at.strftime("%d %b %Y")) if t.first_seen_at else "",
              mono=True),
    ])
    body = (
        DETAIL.replace("__TITLE__", escape(t.title))
        .replace("__BUYER__", escape(t.organization or "unnamed buyer"))
        .replace("__FACTS__", facts)
        .replace("__REF__", escape(t.external_ref or "not recorded"))
        .replace("__SOURCE_NAME__", escape(src.name if src else "the source portal"))
        .replace("__LICENCE__", escape(src.license if src else "see /sources"))
        .replace("__SEARCH__", CPPP_SEARCH if src and src.name == "CPPP"
                 else escape(src.base_url if src else CPPP_SEARCH))
        .replace("__BACK__", "/browse")
        .replace("__SUMMARY__", escape(_summary(t, sectors, places, label)))
        .replace("__SECTORTAGS__",
                 "<ul class='tags sectortags'>"
                 + "".join(f"<li class=tag>{escape(x)}</li>" for x in sectors)
                 + "</ul>" if sectors else "")
    )
    return page(f"{t.title[:60]}", body, nonce_of(request))


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
        + '<a class=t href="/t/'+esc(t.id)+'">'+esc(t.title)+'</a>'
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
