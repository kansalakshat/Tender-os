from decimal import Decimal
from types import SimpleNamespace as NS

from app.eligibility import checklist, needs_check


def tender(title, value=None):
    return NS(title=title, estimated_value=value)


def company(**kw):
    return NS(**{"registrations": [], "years_in_business": None,
                 "annual_turnover": None, "largest_similar_work": None, **kw})


def test_oem_clue_is_settled_by_the_companys_registration():
    t = tender("Comprehensive AMC of cell counter with OEM or authorised dealer")
    assert needs_check(t, company())
    assert not needs_check(t, company(registrations=["dealer"]))


def test_general_norms_alone_never_flag_a_row():
    """Value unpublished means norm items are always 'check'; flagging on them
    would flag every tender in the corpus."""
    assert not needs_check(tender("Painting of office building"),
                           company(years_in_business=1))


def test_published_value_is_held_to_the_80_and_30_percent_norms():
    t = tender("Construction of culvert", Decimal("1000000"))
    c = company(largest_similar_work=Decimal("800000"), annual_turnover=Decimal("200000"))
    items = {i.label: i for i in checklist(t, c)}
    assert items["Similar work, last 7 years"].ok          # 800k >= 80% of 1M
    assert not items["Average annual turnover"].ok         # 200k < 30% of 1M
    assert needs_check(t, c)


def test_title_false_positives_seen_in_the_corpus_are_not_clues():
    assert not needs_check(tender("SUPPLY OF GATE VALVE FOR BOILER STARTUP VENT"), company())
    assert not needs_check(tender("Manufacturing and Supply of Head Hardened Rails"), company())


def test_a_visitor_without_a_profile_still_gets_the_norms():
    labels = [i.label for i in checklist(tender("Painting"), None)]
    assert "Similar work, last 7 years" in labels and "Time in business" in labels
