"""The HTML pages are string templates with __PLACEHOLDER__ substitution, so the
failure mode is a placeholder that nobody filled in and a page that ships with
`__SECTORS__` printed on it. These render every page and look for that.

Also pinned here: the ranking rail's three buckets, and the house rule that no
em-dash reaches a rendered page.
"""
import re

import pytest
from fastapi.testclient import TestClient

from app.api import app, get_db
from app.models import Source, Tender
from app.web import days_left, extra_line, rank_class
from tests.conftest import STATIC, page_scripts

from datetime import date, timedelta


PAGES = ["/", "/browse", "/signup", "/login", "/profile"]


@pytest.fixture
def client(session_factory):
    with session_factory() as db:
        src = Source(
            name="CPPP", base_url="https://eprocure.gov.in",
            license="public-published", robots_txt_allowed=True,
        )
        db.add(src)
        db.flush()
        db.add_all([
            Tender(source_id=src.id, external_ref="a",
                   title="Supply of 11 kv distribution transformers",
                   organization="MP Poorv Kshetra Vidyut Vitaran",
                   deadline=date.today() + timedelta(days=9), status="open",
                   source_url="https://eprocure.gov.in/a"),
        ])
        db.commit()

    app.dependency_overrides[get_db] = lambda: session_factory()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_signed_in_home_has_no_unfilled_placeholder(client):
    """The personalised home fills a different template from the public one, so
    it needs its own guard -- __NSOURCES__ shipped to the page once already."""
    client.post("/auth/signup", json={"email": "ph@example.invalid",
                                      "password": "coconut-husk-2026"})
    client.post("/companies", json={
        "name": "Placeholder Test Co", "sectors": ["civil_construction"],
        "keywords": [], "states": [], "districts": [], "buyers": [],
        "exclude_keywords": [], "exclude_buyers": [], "min_lead_days": 0,
        "max_project_value": None,
    })
    html = client.get("/", follow_redirects=True).text
    assert not re.findall(r"__[A-Z_]+__", html)
    assert "Placeholder Test Co" in html        # really the signed-in template


def test_home_moves_only_the_logo_strip_and_gently(client):
    """Most readers are 50 to 60: no 3D model and no animation library. The one
    thing that moves, the logo strip, stops under the pointer or keyboard and
    stands still for anyone who asked for reduced motion."""
    html = client.get("/").text
    for gone in ("hero3d", "gsap", "motion.js", "strip.js"):
        assert gone not in html, gone
    css = (STATIC / "css" / "site.css").read_text(encoding="utf-8")
    assert ".lstrip:hover .ltrack,.lstrip:focus-within .ltrack{animation-play-state:paused}" in css
    reduced = css[css.rindex("@media (prefers-reduced-motion:reduce)"):]
    assert ".ltrack{animation:none}" in reduced


@pytest.mark.parametrize("path", PAGES)
def test_no_unfilled_placeholder(client, path):
    html = client.get(path, follow_redirects=True).text
    left = re.findall(r"__[A-Z_]+__", html)
    assert not left, f"{path} shipped with {left}"


@pytest.mark.parametrize("path", PAGES)
def test_no_em_dash(client, path):
    html = client.get(path, follow_redirects=True).text
    assert "&mdash;" not in html and "—" not in html


@pytest.mark.parametrize("path", PAGES)
def test_no_inline_style_or_script(client, path):
    """A bare <style> or <script> would be silently dropped by the site CSP,
    which is how the browse page lost its listing once before. Styles and scripts
    are files under /static, which script-src/style-src 'self' allow."""
    html = client.get(path, follow_redirects=True).text
    assert "<style" not in html
    assert re.findall(r"<script\b[^>]*>", html)
    assert all('src="/static/' in tag for tag in re.findall(r"<script\b[^>]*>", html))
    assert page_scripts(html)          # every linked file exists
    assert '<link rel=stylesheet href="/static/css/site.css?v=' in html


def test_days_left_counts_whole_days_not_hours():
    """A tender closing tonight is closing today, not yesterday. Measuring from
    the current instant instead of midnight made every row read 'closed'."""
    t = date(2026, 9, 9)
    assert days_left(t, t) == ("today", "s3")
    assert days_left(t - timedelta(days=1), t) == ("closed", "s0")
    assert days_left(t + timedelta(days=3), t) == ("3d", "s3")
    assert days_left(t + timedelta(days=9), t) == ("9d", "s2")
    assert days_left(t + timedelta(days=40), t) == ("40d", "s1")
    assert days_left(None, t) == ("", "s0")


def test_rank_class_buckets_relative_to_the_best_score_on_the_page():
    assert rank_class(80, 100) == "s3"      # >= 75% of the top score
    assert rank_class(50, 100) == "s2"      # >= 45%
    assert rank_class(10, 100) == "s1"
    assert rank_class(40, 40) == "s3"       # the top row is always full weight
    assert rank_class(0, 0) == "s0"         # nothing scored, no rail


def test_extra_line_shows_id_date_and_only_a_distinct_department():
    t = Tender(external_ref="2026_X_1", title="t", organization="Railways",
               department="Railways", published_date=date(2026, 9, 1),
               source_url="u")
    html = extra_line(t)
    assert "ID 2026_X_1" in html and "2026-09-01" in html
    assert "Railways" not in html           # repeats the buyer, so skipped
    t.department = "<b>Bhopal Division</b>"
    assert "&lt;b&gt;Bhopal Division" in extra_line(t)  # scraped text is escaped
    assert extra_line(Tender(external_ref="", title="t", source_url="u")) == ""


def test_tender_page_shows_fit_duplicates_buyer_and_similar(client, session_factory):
    """The detail page pulls related context around one notice: the company's
    own score, the same notice on another source, the buyer's other open work,
    and open tenders sharing its rarest sector term."""
    from app.matching import derive_sectors
    soon = date.today() + timedelta(days=20)
    with session_factory() as db:
        a = db.query(Tender).filter_by(external_ref="a").one()
        other_src = Source(name="MP eProcurement", base_url="https://mptenders.gov.in")
        db.add(other_src)
        db.flush()
        twin = Tender(source_id=other_src.id, external_ref="twin", title=a.title,
                      organization=a.organization, deadline=a.deadline,
                      duplicate_of=a.id, source_url="u")
        same_buyer = Tender(source_id=a.source_id, external_ref="b",
                            title="Painting of office building",
                            organization=a.organization, deadline=soon, source_url="u")
        similar = Tender(source_id=a.source_id, external_ref="c",
                         title="Repair of distribution transformers at Rewa",
                         organization="Someone Else", deadline=soon, source_url="u")
        closed = Tender(source_id=a.source_id, external_ref="d",
                        title="Old transformers", organization=a.organization,
                        deadline=date.today() - timedelta(days=1), source_url="u")
        db.add_all([twin, same_buyer, similar, closed])
        db.commit()
        ids = {k: v.id for k, v in
               dict(a=a, twin=twin, b=same_buyer, c=similar, d=closed).items()}
        sectors = sorted(derive_sectors(a.title))
    assert sectors, "fixture title must classify into a sector"

    html = client.get(f"/t/{ids['a']}").text
    assert not re.findall(r"__[A-Z_]+__", html)
    # The slow parts arrive from /extras after the page shows, not inline.
    assert f'data-src="/t/{ids['a']}/extras"' in html and "Same tender on other" not in html
    assert any("dataset.src" in js for js in page_scripts(html))
    # ...and the page is never blank where they will land.
    assert re.search(r'<div id=moreSlot [^>]*><section class="more sk" aria-busy=true>', html)
    assert "Checking this tender against your profile" not in html  # anonymous
    extras = client.get(f"/t/{ids['a']}/extras").json()
    html = extras["fit"] + extras["more"]
    assert "Same tender on other sources" in html and f'/t/{ids["twin"]}"' in html
    assert "More open tenders from this buyer (1)" in html and f'/t/{ids["b"]}"' in html
    assert "Other open tenders mentioning" in html and f'/t/{ids["c"]}"' in html
    assert f'/t/{ids["d"]}"' not in html          # closed tenders are not suggested
    assert "class=fit" not in html                # anonymous: no profile to fit

    client.post("/auth/signup", json={"email": "fit@example.invalid",
                                      "password": "coconut-husk-2026"})
    client.post("/companies", json={
        "name": "Fit Co", "sectors": sectors, "keywords": [], "states": [],
        "districts": [], "buyers": [], "exclude_keywords": [], "exclude_buyers": [],
        "min_lead_days": 0, "max_project_value": None,
        "years_in_business": 5, "registrations": ["mse"],
    })
    assert "Matches your profile, score" in client.get(f"/t/{ids['a']}/extras").json()["fit"]
    html = client.get(f"/t/{ids['a']}").text
    assert "Checking this tender against your profile" in html
    assert "Checked against your answers" in html and "Yours: 5 years" in html
    assert "MSE or startup benefits" in html
    me = client.get("/me").json()["company"]
    assert me["registrations"] == ["mse"] and me["years_in_business"] == 5


_TID = "2026_AIMSD_931033_1"


def _cppp_id(session_factory, tender_id=_TID) -> int:
    with session_factory() as db:
        t = db.query(Tender).one()
        t.raw_payload = {"tender_id": tender_id}
        db.commit()
        return t.id


def test_cppp_tender_page_has_the_captcha_box_when_the_id_can_be_searched(client, session_factory):
    tid = _cppp_id(session_factory)
    html = client.get(f"/t/{tid}").text
    assert f'<form method=post action="/t/{tid}/cppp" target=_blank' in html
    assert f'data-captcha="/t/{tid}/cppp/captcha"' in html
    assert any("form.dataset.captcha" in js for js in page_scripts(html))
    assert '<a class="btn ghost" href="#cpppForm">' in html   # View on portal
    _cppp_id(session_factory, "1")      # an id no search could tell apart
    html = client.get(f"/t/{tid}").text
    assert 'FrontEndAdvancedSearch&amp;service=page"' in html and "cpppForm" not in html
    assert client.get(f"/t/{tid}/cppp").status_code == 404
    assert client.get(f"/t/{tid}/cppp/captcha").status_code == 404


@pytest.mark.parametrize("name,base,expected", [
    ("GeM", "https://bidplus.gem.gov.in", "https://bidplus.gem.gov.in/all-bids"),
    ("UP eProcurement", "https://etender.up.nic.in", "https://etender.up.nic.in"),
])
def test_view_on_portal_opens_the_portal_in_a_new_tab(client, session_factory, name, base, expected):
    with session_factory() as db:
        t = db.query(Tender).one()
        src = db.get(Source, t.source_id)
        src.name, src.base_url = name, base
        db.commit()
        tid = t.id
    html = client.get(f"/t/{tid}").text
    assert f'<a class="btn ghost" href="{expected}" target=_blank rel="noopener noreferrer">' in html


_IMAGE = "data:image/png;base64,__N__"
_FORM = """<html><head></head><body>
<form id="TenderAdvancedSearch" method="post" action="/eprocure/app">
<input type="hidden" name="tokenSecret" value="tok__N__" />
<input type="text" name="tenderId" value="" />
<select name="TenderType"><option value="0">-Select-</option><option value="1">Open</option></select>
<input type="checkbox" name="ndaAllowed" />
<input type="text" name="captchaText" value="" />
<input type="submit" name="submit" value="Search" />
<img id="captchaImage" src="data:image/png;base64,__N__">
</form></body></html>"""
_RESULTS = """<html><body><table class="list_table" id="table">
<tr><td>S.No</td><td>Title and Ref.No./Tender ID</td></tr>
<tr><td>1</td><td><a href="/eprocure/app?component=x&sp=other">[Other]</a>[R][2026_AIMSD_931033_12]</td></tr>
<tr><td>2</td><td><a href="/eprocure/app?component=x&sp=this">[Gloves]</a>[R][2026_AIMSD_931033_1]</td></tr>
</table></body></html>"""


def test_cppp_lookup_sends_the_humans_captcha_and_returns_the_tender(
        client, session_factory, monkeypatch):
    """Wrong CAPTCHA: a fresh image and a message. Right one: CPPP's own page for
    exactly this tender, sandboxed. Nothing in here reads or guesses the CAPTCHA."""
    import httpx
    from urllib.parse import parse_qs
    from app import cppp_relay

    tid = _cppp_id(session_factory)
    posts = []

    def cppp(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if request.method == "GET" and "sp=this" in url:
            assert "JSESSIONID=s1" in request.headers["cookie"]    # the same session
            return httpx.Response(200, html="<html><head></head><body>Tender details</body></html>")
        if request.method == "GET":
            assert url == cppp_relay.FORM_URL
            return httpx.Response(200, html=_FORM.replace("__N__", "0"),
                                  headers={"Set-Cookie": "JSESSIONID=s1; Path=/"})
        form = parse_qs(request.content.decode())
        posts.append(form)
        if form["captchaText"] != ["RIGHT1"]:
            return httpx.Response(200, html=_FORM.replace("__N__", "9").replace(
                "<body>", '<body><td class="error"><b>Invalid Captcha! Please Enter '
                          'Correct Captcha.</b></td>'))
        return httpx.Response(200, html=_RESULTS)

    monkeypatch.setattr(cppp_relay, "TRANSPORT", httpx.MockTransport(cppp))

    got = client.get(f"/t/{tid}/cppp/captcha").json()
    assert got["image"] == _IMAGE.replace("__N__", "0") and got["state"]

    html = client.post(f"/t/{tid}/cppp", data={"state": got["state"], "captcha": "wrong"}).text
    assert posts[0]["tenderId"] == [_TID] and posts[0]["TenderType"] == ["1"]
    assert posts[0]["captchaText"] == ["wrong"] and posts[0]["tokenSecret"] == ["tok0"]
    assert posts[0]["submit"] == ["Search"] and "ndaAllowed" not in posts[0]
    assert "CAPTCHA was wrong" in html and _IMAGE.replace("__N__", "9") in html
    state = re.search(r'name=state value="([^"]+)"', html).group(1)

    r = client.post(f"/t/{tid}/cppp", data={"state": state, "captcha": "RIGHT1"})
    assert posts[1]["tokenSecret"] == ["tok9"]
    assert "Tender details" in r.text       # sp=this, not the longer id above it
    assert 'sp=this">' in r.text and "<base href=" in r.text
    assert r.headers["content-security-policy"].startswith("sandbox")

    n = len(posts)
    html = client.post(f"/t/{tid}/cppp", data={"state": "forged", "captcha": "x"}).text
    assert "expired" in html and len(posts) == n   # a forged state is never posted


def test_cppp_lookup_tries_limited_tenders_once_then_says_it_is_not_listed(
        client, session_factory, monkeypatch):
    import httpx
    from urllib.parse import parse_qs
    from app import cppp_relay

    tid = _cppp_id(session_factory)
    types = []

    def cppp(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, html=_FORM.replace("__N__", "0"))
        types.append(parse_qs(request.content.decode())["TenderType"][0])
        # A right CAPTCHA that finds nothing still gets the form back, with no error.
        return httpx.Response(200, html=_FORM.replace("__N__", "5"))

    monkeypatch.setattr(cppp_relay, "TRANSPORT", httpx.MockTransport(cppp))
    state = client.get(f"/t/{tid}/cppp/captcha").json()["state"]
    html = client.post(f"/t/{tid}/cppp", data={"state": state, "captcha": "RIGHT1"}).text
    assert "limited tenders" in html
    state = re.search(r'name=state value="([^"]+)"', html).group(1)
    r = client.post(f"/t/{tid}/cppp", data={"state": state, "captcha": "RIGHT2"})
    assert types == ["1", "2"]
    assert r.status_code == 404 and "does not list this tender" in r.text


def test_shared_serves_stale_while_one_thread_refreshes(monkeypatch):
    """A visitor after expiry gets the old value at once, never the database wait."""
    import threading
    from contextlib import nullcontext

    from app import db as web

    calls, gate = [], threading.Event()

    def compute(db):
        calls.append(db)
        if len(calls) > 1:
            gate.wait(5)
        return len(calls)

    monkeypatch.setattr(web, "SessionLocal", lambda: nullcontext("fresh"))
    assert web.shared("req", ("k",), 60, compute) == 1
    web._SHARED[("k",)] = (0, 1)                     # expire it
    assert web.shared("req", ("k",), 60, compute) == 1   # stale, not blocked
    assert web.shared("req", ("k",), 60, compute) == 1   # still one refresh only
    gate.set()
    for t in threading.enumerate():
        if t is not threading.current_thread() and t.daemon:
            t.join(5)
    assert calls == ["req", "fresh"]
    assert web.shared("req", ("k",), 60, compute) == 2


def test_asset_version_follows_content_not_mtime(tmp_path, monkeypatch):
    """Vercel gives every deployed file the same mtime; the version must still
    move when the file does, or browsers keep an "immutable" old stylesheet."""
    import os
    from app import web
    monkeypatch.setattr(web, "_STATIC", tmp_path)
    f = tmp_path / "a.css"
    f.write_text("old")
    os.utime(f, (1540000000, 1540000000))
    before = web.asset("a.css")
    f.write_text("new")
    os.utime(f, (1540000000, 1540000000))
    web._digest.cache_clear()   # a new deploy is a new process
    assert web.asset("a.css") != before


def test_buyer_strip_and_page_show_only_that_buyers_open_tenders(client, session_factory):
    """The strip lists every buyer with an open notice; a click is an exact
    match on the buyer, closing soonest first, and never shows a closed one."""
    with session_factory() as db:
        src = db.query(Source).first()
        today = date.today()
        db.add_all([
            Tender(source_id=src.id, external_ref="b1", title="Pump sets later",
                   organization="Indian Army", deadline=today + timedelta(days=20),
                   source_url="u1"),
            Tender(source_id=src.id, external_ref="b2", title="Boots sooner",
                   organization="Indian Army", deadline=today + timedelta(days=2),
                   source_url="u2"),
            Tender(source_id=src.id, external_ref="b3", title="Old closed notice",
                   organization="Indian Army", deadline=today - timedelta(days=3),
                   source_url="u3"),
            Tender(source_id=src.id, external_ref="b4", title="Navy radar",
                   organization="Indian Army Navy Wing", deadline=today + timedelta(days=5),
                   source_url="u4"),
        ])
        db.commit()
    strip = dict(client.get("/buyers/strip").json())
    assert strip["Indian Army"] == 2 and "MP Poorv Kshetra Vidyut Vitaran" in strip

    html = client.get("/buyer", params={"name": "Indian Army"}).text
    assert html.index("Boots sooner") < html.index("Pump sets later")   # closing soonest
    assert "Old closed notice" not in html and "Navy radar" not in html  # exact, open only
    assert "sign in</a> to use it" in html     # relevance needs a profile
    assert client.get("/buyer").status_code == 404
    assert '/buyer?name=' in client.get("/").text   # the strip links here

    # Signed in with a profile that wants pumps: relevance puts the later-closing
    # pump tender first, closing soonest still puts the boots first.
    client.post("/auth/signup", json={"email": "buyer@example.invalid",
                                      "password": "coconut-husk-2026"})
    client.post("/companies", json={
        "name": "Pump Co", "sectors": [], "keywords": ["pump"], "states": [],
        "districts": [], "buyers": [], "exclude_keywords": [], "exclude_buyers": [],
        "min_lead_days": 0, "max_project_value": None,
    })
    rel = client.get("/buyer", params={"name": "Indian Army"}).text
    assert rel.index("Pump sets later") < rel.index("Boots sooner")
    soon = client.get("/buyer", params={"name": "Indian Army", "sort": "deadline"}).text
    assert soon.index("Boots sooner") < soon.index("Pump sets later")


def test_healthz_answers_without_the_database():
    """The keep-warm ping must not wake Neon, so /healthz takes no session."""
    r = TestClient(app).get("/healthz")
    assert r.status_code == 200 and r.json() == {"ok": True}
    route = next(r for r in app.routes if getattr(r, "path", "") == "/healthz")
    assert not route.dependant.dependencies   # no get_db, so no connection
