"""The vendor-code paperwork note, on every preview.

The text lives in three renderers -- the row macro, the tender page and the
JavaScript that draws JSON rows -- so the thing worth testing is that they do
not drift apart, and that the note never claims to be quoting the tender.
"""
import re
from datetime import date, timedelta
from pathlib import Path

from app.models import Tender
from app.web import _env, days_left

ITEMS = ["Copy of PAN Card.", "Copy of GSTIN.", "Copy of Cancelled Cheque.",
         "Copy of EFT Mandate duly certified by Bank."]
ROOT = Path(__file__).resolve().parent.parent


def _row_html():
    t = Tender(id=1, external_ref="X", title="Supply of switchgear", source_url="u",
               organization="PWD", deadline=date.today() + timedelta(days=20))
    tpl = _env.from_string(
        "{% from '_macros.html' import row %}"
        "{% set g, r = days_left(t.deadline, today) %}{{ row(g, r, t) }}")
    return tpl.render(t=t, today=date.today(), days_left=days_left)


def test_every_row_carries_the_note():
    html = _row_html()
    for item in ITEMS:
        assert item in html, item


def test_the_note_does_not_claim_to_come_from_the_tender():
    """It is a standing requirement, not text read out of this notice. Saying
    otherwise would have the site asserting something it never checked."""
    html = _row_html()
    assert "not read from this" in html


def test_it_is_collapsed_by_default():
    """The same four lines on fifty rows would bury the tenders themselves."""
    html = _row_html()
    assert "<details" in html
    assert not re.search(r"<details[^>]*\bopen\b", html)


def test_the_javascript_rows_say_exactly_the_same_thing():
    """/browse draws its rows in script, so a note added only to the template
    would be missing from the main listing page."""
    js = (ROOT / "static" / "js" / "site.js").read_text(encoding="utf-8")
    for item in ITEMS:
        assert item in js, item
    for name in ("browse.js", "profile.js"):
        drawn = (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
        assert "VENDOR_DOCS" in drawn, name
