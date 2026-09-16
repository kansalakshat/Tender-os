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
from tests.conftest import page_scripts

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


_DETAIL = "https://eprocure.gov.in/cppp/tendersfullview/MTM4NzYzMTg=A13h1X"


def _cppp_link(session_factory, url=_DETAIL) -> int:
    with session_factory() as db:
        t = db.query(Tender).one()
        t.raw_payload = {"url": url} if url else {}
        db.commit()
        return t.id


def test_cppp_tender_page_carries_the_captcha_box_only_with_a_tender_link(
        client, session_factory):
    tid = _cppp_link(session_factory)
    html = client.get(f"/t/{tid}").text
    assert f'<form method=post action="/t/{tid}/cppp" target=_blank' in html
    assert f'data-captcha="/t/{tid}/cppp/captcha"' in html
    assert any("form.dataset.captcha" in js for js in page_scripts(html))
    assert "id=cpppSk class=skimg" in html   # a placeholder until the image lands
    _cppp_link(session_factory, None)
    html = client.get(f"/t/{tid}").text
    assert 'FrontEndAdvancedSearch&amp;service=page"' in html and "cpppForm" not in html
    assert client.get(f"/t/{tid}/cppp").status_code == 404
    assert client.get(f"/t/{tid}/cppp/captcha").status_code == 404
    _cppp_link(session_factory, "https://evil.example/cppp/tendersfullview/x")
    assert client.get(f"/t/{tid}/cppp").status_code == 404


_FORM = """<html><head></head><body>__MSG__
<form action="/cppp/tendersfullview/MTM4NzYzMTg=A13h1X" method="post"
      id="tenderfullview-tenders">
<input type="hidden" name="cid" value="MTM4NzYzMTg=A13h1X" />
<input type="hidden" name="captcha_sid" value="sid__N__" />
<input type="hidden" name="form_build_id" value="form-x" />
<input type="text" name="captcha_response" value="" />
<input type="submit" name="op" value="Submit" />
<input type="reset" value="Cancel" />
<img data-drupal-selector="edit-captcha-image" src="/cppp/image-captcha-generate/__N__/1">
</form></body></html>"""


def test_cppp_lookup_sends_the_humans_captcha_and_returns_the_tender(
        client, session_factory, monkeypatch):
    """Wrong CAPTCHA: a fresh image and a message. Right one: CPPP's full tender
    page, sandboxed. Nothing in here reads or guesses the CAPTCHA."""
    import base64
    import httpx
    from urllib.parse import parse_qs
    from app import cppp_relay

    tid = _cppp_link(session_factory)
    cppp_relay.forget_session()
    posts, images, listings = [], [], []

    def cppp(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "image-captcha-generate" in url:
            images.append(request.headers.get("cookie", ""))
            return httpx.Response(200, content=url.encode(),
                                  headers={"content-type": "image/png"})
        if url.endswith("/cppp/latestactivetendersnew/cpppdata"):
            listings.append(url)
            return httpx.Response(200, headers={"Set-Cookie": "SSESS=s1; Path=/"})
        if url != _DETAIL:
            return httpx.Response(404)
        if "SSESS=s1" not in request.headers.get("cookie", ""):
            return httpx.Response(200, html="<p>Invalid parameter</p>")
        if request.method == "GET":
            assert "eprocure.gov.in" in request.headers.get("referer", "")
            return httpx.Response(200, html=_FORM.replace("__MSG__", "")
                                  .replace("__N__", "0"))
        posts.append((parse_qs(request.content.decode()),
                      request.headers.get("cookie", "")))
        if posts[-1][0]["captcha_response"] != ["RIGHT1"]:
            return httpx.Response(200, html=_FORM.replace(
                "__MSG__", '<div role="alert">The answer you entered for the '
                "CAPTCHA was not correct.</div>").replace("__N__", "9"))
        return httpx.Response(200, html="<html><head><title>T</title></head>"
                                        "<body>Tender details</body></html>")

    monkeypatch.setattr(cppp_relay, "TRANSPORT", httpx.MockTransport(cppp))

    def img(n):
        raw = f"https://eprocure.gov.in/cppp/image-captcha-generate/{n}/1".encode()
        return "data:image/png;base64," + base64.b64encode(raw).decode()

    html = client.get(f"/t/{tid}/cppp").text
    assert f'src="{img(0)}"' in html and "SSESS=s1" in images[0]  # same session
    got = client.get(f"/t/{tid}/cppp/captcha").json()   # the tender page's box
    assert got["image"] == img(0) and got["state"]
    assert len(listings) == 1   # the slow listing is fetched once, not per CAPTCHA
    state = got["state"]

    html = client.post(f"/t/{tid}/cppp", data={"state": state, "captcha": "wrong"}).text
    form, cookie = posts[0]
    assert form["cid"] == ["MTM4NzYzMTg=A13h1X"] and form["captcha_sid"] == ["sid0"]
    assert form["captcha_response"] == ["wrong"] and form["op"] == ["Submit"]
    assert "SSESS=s1" in cookie
    assert "CAPTCHA was wrong" in html and f'src="{img(9)}"' in html
    state = re.search(r'name=state value="([^"]+)"', html).group(1)

    r = client.post(f"/t/{tid}/cppp", data={"state": state, "captcha": "RIGHT1"})
    assert posts[1][0]["captcha_sid"] == ["sid9"]
    assert "Tender details" in r.text
    assert f'<base href="{_DETAIL}">' in r.text
    assert r.headers["content-security-policy"].startswith("sandbox")

    n = len(posts)
    html = client.post(f"/t/{tid}/cppp", data={"state": "forged", "captcha": "x"}).text
    assert "expired" in html and len(posts) == n   # a forged state is never posted

    # CPPP accepting the CAPTCHA but refusing the link: our message, not its page.
    state = re.search(r'name=state value="([^"]+)"', client.get(f"/t/{tid}/cppp").text).group(1)
    monkeypatch.setattr(cppp_relay, "TRANSPORT", httpx.MockTransport(
        lambda r: httpx.Response(200, html="<p>Invalid parameter</p>")))
    r = client.post(f"/t/{tid}/cppp", data={"state": state, "captcha": "RIGHT1"})
    assert r.status_code == 404 and "no longer opens this tender" in r.text
    r = client.get(f"/t/{tid}/cppp")
    assert r.status_code == 404 and "no longer opens this tender" in r.text
    r = client.get(f"/t/{tid}/cppp/captcha")
    assert r.status_code == 404 and "no longer opens" in r.json()["error"]
