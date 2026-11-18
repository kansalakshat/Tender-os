"""The tender page's showcase layout: the match ring, and the tab markup the
script turns into tabs. The page's content is tested in test_web.py and
test_tenderleo.py."""
from pathlib import Path

from app import web


def test_match_ring_shows_the_score():
    html = web.render("_fit.html", company=object(), hit=(87, ["Your sector"]))
    assert 'class="ring p85" data-score="87"><b>87%</b>' in html
    assert "High Match" in html and "Matches your profile, score 87" in html
    low = web.render("_fit.html", company=object(), hit=(22, []))
    assert "ring p20" in low and "Low Match" in low
    assert "Not one of your matches" in web.render("_fit.html", company=object(), hit=None)
    assert web.render("_fit.html", company=None, hit=None).strip() == ""


def test_every_tab_has_a_panel_and_the_script_runs_them():
    tpl = (Path(__file__).parent.parent / "templates" / "tender.html").read_text(encoding="utf-8")
    for name in ("overview", "eligibility", "documents", "ai"):
        assert f"data-tab={name}>" in tpl and f"data-panel={name}" in tpl, name
    js = (Path(__file__).parent.parent / "static" / "js" / "tender.js").read_text(encoding="utf-8")
    assert "[data-tabs]" in js and "window.print()" in js
    # signed out, the empty fit must not wipe the sign-up prompt in the ring card
    assert "if (d.fit)" in js


def test_tabs_slide_but_not_for_reduced_motion():
    css = (Path(__file__).parent.parent / "static" / "css" / "site.css").read_text(encoding="utf-8")
    slide = css[css.index("@media (prefers-reduced-motion:no-preference){\n .txpanel"):]
    assert ".txpanel.slide-r{animation:txin-r" in slide.split("}\n}")[0]
    assert ".txbar{" in css
