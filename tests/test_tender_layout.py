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


def test_cancelled_tender_shows_cancelled_not_a_countdown(session_factory, monkeypatch):
    from datetime import date, timedelta
    from fastapi.testclient import TestClient
    from app.api import app, get_db
    from app.models import Source, Tender
    today = date(2026, 11, 18)
    monkeypatch.setattr(web, "today_ist", lambda: today)
    with session_factory() as db:
        src = Source(name="CPPP", base_url="https://eprocure.gov.in", license="p", robots_txt_allowed=True)
        db.add(src); db.flush()
        for ref, status in (("x", "cancelled"), ("y", "open")):
            db.add(Tender(source_id=src.id, external_ref=ref, source_url="u" + ref, title="Road " + ref,
                          organization="PWD", deadline=today + timedelta(days=12), status=status))
        db.commit()
        ids = {t.external_ref: t.id for t in db.query(Tender)}
    app.dependency_overrides[get_db] = lambda: session_factory()
    try:
        c = TestClient(app)
        gone = c.get(f"/t/{ids['x']}").text
        due = gone[gone.index('<div class="txdue'):]
        due = due[:due.index("</div>")]
        assert "txdue shut" in due and "<b>Cancelled</b>" in due
        assert "Days" not in due and "<span>Left</span>" not in due
        live = c.get(f"/t/{ids['y']}").text
        assert "<b>12 Days</b><span>Left</span>" in live
    finally:
        app.dependency_overrides.clear()


def test_profile_steps_focus_the_step_and_recount_after_other_listeners():
    js = (Path(__file__).parent.parent / "static" / "js" / "profile-steps.js").read_text(encoding="utf-8")
    assert "first.tabIndex = -1;" in js and "first.focus({preventScroll: true});" in js
    assert "if (e.type === 'prefilled' && current === 'review') review();" in js
    assert "form.addEventListener(ev, recount)" in js
