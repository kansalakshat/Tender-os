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
