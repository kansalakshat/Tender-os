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
def test_inline_style_and_script_are_nonced(client, path):
    """A bare <style> or <script> would be silently dropped by the site CSP,
    which is how the browse page lost its listing once before."""
    html = client.get(path, follow_redirects=True).text
    assert "<style>" not in html and "<script>" not in html
    assert 'nonce="' in html


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
    })
    assert "Matches your profile, score" in client.get(f"/t/{ids['a']}").text
