"""The About page: its live figures, its sections and its links."""
import re
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import web
from app.api import app, get_db
from app.models import ConnectorRun, Source, Tender

TODAY = date(2026, 11, 18)
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def about(session_factory, monkeypatch):
    monkeypatch.setattr(web, "today_ist", lambda: TODAY)
    with session_factory() as db:
        cppp = Source(name="CPPP", base_url="https://eprocure.gov.in", license="p", robots_txt_allowed=True)
        raj = Source(name="Rajasthan eProcurement", base_url="https://eproc.rajasthan.gov.in",
                     license="p", robots_txt_allowed=True)
        db.add_all([cppp, raj]); db.flush()
        for ref, org, days in (("a", "Ministry of Railways", 5), ("b", "PWD Jaipur", 9),
                               ("c", "PWD Jaipur", 2), ("old", "Indian Army", -3)):
            db.add(Tender(source_id=cppp.id, external_ref=ref, source_url="u" + ref, title="Work " + ref,
                          organization=org, deadline=TODAY + timedelta(days=days)))
        db.add(ConnectorRun(source_id=cppp.id, source_name="CPPP", status="ok", started_at=datetime(2026, 11, 17, 20, 0),
                            finished_at=datetime(2026, 11, 17, 20, 30)))
        db.commit()
    app.dependency_overrides[get_db] = lambda: session_factory()
    try:
        yield TestClient(app).get("/about").text
    finally:
        app.dependency_overrides.clear()


def test_about_figures_are_counted(about):
    stats = about[about.index("<dl class=abstats>"):about.index("</dl>")]
    assert "<dt>Open tenders right now</dt><dd>3</dd>" in stats        # the closed one is not open
    assert "<dt>Government buyers</dt><dd>2</dd>" in stats
    assert "<dt>Official portals</dt><dd>2</dd>" in stats
    # 20:30 UTC on the 17th is the 18th in India
    assert "Last collected 18 Nov 2026" in stats
    assert "1 state eProcurement sites" in about


def test_about_has_every_section_in_order(about):
    heads = re.findall(r"<h2>(.*?)</h2>", about)
    assert heads[:6] == ["The problem, and our answer", "How Tenderleo works",
                         "Built for the businesses behind India&rsquo;s growth",
                         "Where the data comes from", "Questions people ask",
                         "Find your next tender today"]
    assert "id=how" in about                       # the home page links here
    assert about.count("<details") == 5 and "<details open>" in about


def test_about_links_go_somewhere_real(about):
    for href in ('href="/browse?sector=civil_construction"', 'href="/browse?sector=industrial_supply"',
                 'href="/browse?sector=manpower_security"', 'href="/signup"', 'href="/sources"'):
        assert href in about, href
    assert 'href="#' not in about.split("<main", 1)[1]
    assert "[N]" not in about and "[2 hours ago]" not in about


def test_about_keys_are_real_sectors(about):
    for key in re.findall(r"/browse\?sector=(\w+)", about):
        assert key in web.SECTOR_LABELS, key


def test_about_has_no_inline_styles():
    html = (ROOT / "templates" / "about.html").read_text(encoding="utf-8")
    assert "style=" not in html and "<script" not in html


def test_signed_in_visitors_are_not_asked_to_create_an_account(about):
    """The page is the same for everyone; site.js removes what is only for
    signed-out visitors once /me says someone is signed in. The demo stays."""
    cta = about[about.index("class=abcta id=start"):]
    cta = cta[:cta.index("</div>", cta.index("class=abctas"))]
    assert '<a class="abbtn" href="/signup" data-signedout>Create free account</a>' in cta
    demo = cta[cta.index("Book a demo") - 120:cta.index("Book a demo")]
    assert "data-signedout" not in demo
    js = (ROOT / "static" / "js" / "site.js").read_text(encoding="utf-8")
    signed_in = js[js.index("fetch('/me')"):]
    signed_in = signed_in[signed_in.index("if(!d.user){"):]
    after_signout_branch = signed_in[signed_in.index("return;"):]
    assert "querySelectorAll('[data-signedout]')) e.remove()" in after_signout_branch
