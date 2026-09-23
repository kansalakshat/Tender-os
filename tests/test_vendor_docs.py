"""The vendor-code paperwork note.

On a tender's own page, not on list rows. It is identical for every tender, so
repeating it down a list crowds out the things that differ between them, which
is the only reason to read a list. Someone opening one tender sees it at the
point they would act on it.
"""
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


def test_list_rows_do_not_carry_it():
    html = _row_html()
    for item in ITEMS:
        assert item not in html, f"{item} should not be on a list row"
    assert "vendordocs" not in html


def test_the_tender_page_macro_still_has_every_line():
    """Removed from rows, not from the site: the text itself must survive."""
    tpl = _env.from_string("{% from '_macros.html' import vendor_docs %}{{ vendor_docs() }}")
    html = tpl.render()
    assert "Bidder shall submit the following documents" in html
    for item in ITEMS:
        assert item in html, item


def test_it_is_shown_outright_not_behind_a_control():
    tpl = _env.from_string("{% from '_macros.html' import vendor_docs %}{{ vendor_docs() }}")
    html = tpl.render()
    assert "<details" not in html and "<summary" not in html


def test_the_row_renderers_no_longer_draw_it():
    """/browse and the match preview draw rows in script; a stale copy there
    would put the note back on the lists it was taken off."""
    for name in ("site.js", "browse.js", "profile.js"):
        js = (ROOT / "static" / "js" / name).read_text(encoding="utf-8")
        assert "VENDOR_DOCS" not in js, name
        assert "Copy of PAN Card" not in js, name
