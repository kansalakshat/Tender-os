"""The browse page's markup: the filters, the quick categories and the
results table the script fills. The filtering itself is GET /tenders,
tested in test_tenderleo.py and test_api.py."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api import app, get_db
from app.matching import SECTOR_LABELS
from app.web import BROWSE_QUICK_SECTORS


@pytest.fixture
def client(session_factory):
    app.dependency_overrides[get_db] = lambda: session_factory()
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_browse_has_filters_categories_and_a_results_table(client):
    html = client.get("/browse").text
    side = html[html.index("<form id=f"):html.index("</form>")]
    for name in ("state", "city", "sector", "source_id", "closing", "organization", "open_only"):
        assert f"name={name}" in side, name
    assert "Advanced Filters" in side and "Apply Filters" in side
    # every quick category is a real sector key, so the API accepts it
    for key, _label in BROWSE_QUICK_SECTORS:
        assert key in SECTOR_LABELS and f'data-sector="{key}"' in html
    # the table the script fills, five columns
    head = re.findall(r"<th scope=col>([^<]+)</th>", html)
    assert head == ["Tender Title", "Buyer", "Closing Date", "Value", "Status"]
    assert "<tbody id=rows>" in html and "<nav class=b2pages id=pages" in html


def test_browse_script_reads_every_field_it_sends(client):
    """Each name the script sends to GET /tenders is a field on the page."""
    html = client.get("/browse").text
    js = (Path(__file__).parent.parent / "static" / "js" / "browse.js").read_text(encoding="utf-8")
    fields = re.search(r"const FIELDS = \[([^\]]+)\]", js).group(1)
    for name in re.findall(r"'(\w+)'", fields):
        assert f"name={name}" in html, name


def test_every_icon_the_scripts_draw_is_on_the_page(client):
    """ico(name) copies an icon out of the page's <template id=icons>; a name
    missing there draws nothing (the page arrows and the "opened" tick)."""
    html = client.get("/browse").text
    root = Path(__file__).parent.parent / "static" / "js"
    for script in ("browse.js", "site.js"):
        for name in re.findall(r"ico\('([\w-]+)'\)", (root / script).read_text(encoding="utf-8")):
            assert f"data-icon={name}>" in html, (script, name)


def test_opened_tenders_are_remembered_and_ticked():
    root = Path(__file__).parent.parent / "static" / "js"
    site = (root / "site.js").read_text(encoding="utf-8")
    assert "function markSeen(" in site and "function seenIds(" in site
    assert "markSeen(m[1])" in (root / "tender.js").read_text(encoding="utf-8")
    assert "seenIds()" in (root / "browse.js").read_text(encoding="utf-8")
    # India's date is built from named parts, never parsed from locale text
    assert "formatToParts" in site


def test_cancelled_tenders_never_read_open():
    js = (Path(__file__).parent.parent / "static" / "js" / "browse.js").read_text(encoding="utf-8")
    status = js[js.index("function status(t)"):js.index("function row(")]
    assert status.index("'cancelled'") < status.index("left(t.deadline)")
