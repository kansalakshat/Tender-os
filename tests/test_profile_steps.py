"""The profile page's steps: every question sits in a step, the steps the
script knows match the page, and the bar counts what the server counts."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app, get_db
from app.web import PROFILE_PARTS

ROOT = Path(__file__).parent.parent


@pytest.fixture
def client(session_factory):
    app.dependency_overrides[get_db] = lambda: session_factory()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_every_question_is_in_a_step(client):
    html = client.get("/profile").text
    form = html[html.index("<form id=f>"):html.index("</form>")]
    for fs in re.findall(r"<fieldset[^>]*>", form):
        assert "data-step=" in fs, fs
    steps = set(re.findall(r"data-step=(\w+)", form))
    links = re.findall(r'data-step-link="(\w+)"', html)
    assert steps == set(links) == {"basic", "business", "experience", "documents", "review"}
    # the steps the script walks are the ones on the page, in the page's order
    js = (ROOT / "static" / "js" / "profile-steps.js").read_text(encoding="utf-8")
    order = re.findall(r"'(\w+)'", re.search(r"const ORDER = \[([^\]]+)\]", js).group(1))
    assert order == links
    # without the script, the save button is there and nothing is hidden by default
    assert "<button class=hbtn id=pfsave>" in html


def test_the_bar_counts_the_same_answers_as_the_server():
    js = (ROOT / "static" / "js" / "profile-steps.js").read_text(encoding="utf-8")
    parts = re.search(r"const PARTS = \[(.*?)\];", js, re.S).group(1)
    assert parts.count("() =>") == len(PROFILE_PARTS) == 11


def test_signup_still_asks_everything_on_one_page(client):
    html = client.get("/signup").text
    assert 'name=registrations' in html and "data-step-link" not in html


def test_review_lists_every_answer_and_steps_check_the_browser_rules():
    js = (ROOT / "static" / "js" / "profile-steps.js").read_text(encoding="utf-8")
    review = js[js.index("const rows = ["):js.index("];", js.index("const rows = ["))]
    for name in ("name", "contact_email", "keywords", "exclude_keywords", "max_project_value",
                 "min_lead_days", "years_in_business", "annual_turnover", "largest_similar_work",
                 "bid_capacity", "emd_budget", "registrations", "strict", "sectors", "buyers",
                 "exclude_buyers", "states", "districts"):
        assert name in review, name
    assert "checkValidity()" in js and "reportValidity()" in js
