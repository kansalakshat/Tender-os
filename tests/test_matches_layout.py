"""The matches page's showcase parts: profile completeness, the new-matches
trend and "Why These Match?". The matching itself is tested elsewhere."""
from datetime import date, timedelta
from types import SimpleNamespace

from app import web
from app.models import Source, Tender

TODAY = date(2026, 11, 19)


def blank(**kw):
    base = dict(sectors=[], keywords=[], states=[], districts=[], buyers=[],
                max_project_value=None, years_in_business=None, annual_turnover=None,
                largest_similar_work=None, bid_capacity=None, emd_budget=None, registrations=[])
    return SimpleNamespace(**{**base, **kw})


def test_profile_completeness_counts_each_answer_once():
    assert web.profile_complete(blank()) == 0
    assert web.profile_complete(blank(sectors=["it_services"], districts=["Indore"])) == 18
    full = blank(sectors=["x"], keywords=["y"], states=["Goa"], buyers=["z"], max_project_value=1,
                 years_in_business=0, annual_turnover=1, largest_similar_work=1,
                 bid_capacity=1, emd_budget=1, registrations=["msme"])
    assert web.profile_complete(full) == 100       # a 0 answer still counts as answered


def test_trend_is_new_matches_per_day(session_factory):
    with session_factory() as db:
        src = Source(name="CPPP", base_url="https://eprocure.gov.in", license="p", robots_txt_allowed=True)
        db.add(src)
        db.flush()
        rows = [Tender(source_id=src.id, external_ref=str(i), source_url=f"u{i}", title=f"t{i}",
                       published_date=TODAY - timedelta(days=back))
                for i, back in enumerate((0, 0, 1, 13, 14))]
        db.add_all(rows)
        db.commit()
        days = web._match_trend(db, [t.id for t in rows], TODAY)
    assert len(days) == 14 and days[-1] == 2 and days[-2] == 1 and days[0] == 1
    assert sum(days) == 4                          # 14 days back is outside the window


def test_why_counts_each_kind_once_per_tender():
    shown = [(None, 80, ["sector: IT", "sector: Power", "keywords: cctv", "closes in 3 days"], False),
             (None, 50, ["location: Goa"], True)]
    assert web._why_matched(shown) == [("Your business category matches", 1),
                                       ("Your keywords are in the title", 1),
                                       ("Preferred location", 1)]
