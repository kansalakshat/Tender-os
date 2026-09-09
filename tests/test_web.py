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
from app.web import days_left, rank_class

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
