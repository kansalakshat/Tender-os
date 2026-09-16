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


# ---- answers that only mean something after enrichment ----

def _tender(value=None, **raw):
    class T:
        title = "Supply of transformers"
        estimated_value = value
        raw_payload = raw
    return T()


def _company(**kw):
    class C:
        registrations = kw.pop("registrations", [])
        years_in_business = None
        annual_turnover = None
        largest_similar_work = None
        bid_capacity = kw.pop("bid_capacity", None)
        emd_budget = kw.pop("emd_budget", None)
    return C()


def _item(items, label):
    return next((i for i in items if i.label == label), None)


def test_emd_is_checked_against_the_budget():
    from decimal import Decimal
    from app.eligibility import checklist

    tender = _tender(raw_payload_emd=None, emd_amount=180000.0)
    tight = _item(checklist(tender, _company(emd_budget=Decimal("100000"))),
                  "Earnest money deposit")
    assert tight is not None and tight.ok is False
    assert "exceed" in tight.detail

    roomy = _item(checklist(tender, _company(emd_budget=Decimal("300000"))),
                  "Earnest money deposit")
    assert roomy.ok is True


def test_registered_mse_is_exempt_from_emd_regardless_of_budget():
    from app.eligibility import checklist

    item = _item(checklist(_tender(emd_amount=999999.0),
                           _company(registrations=["mse"])), "Earnest money deposit")
    assert item.ok is True
    assert "exempt" in item.detail.lower()


def test_bid_capacity_is_checked_against_the_tender_value():
    from decimal import Decimal
    from app.eligibility import checklist

    tender = _tender(value=Decimal("900000"))
    small = _item(checklist(tender, _company(bid_capacity=Decimal("500000"))),
                  "Fits your bid capacity")
    assert small.ok is False
    big = _item(checklist(tender, _company(bid_capacity=Decimal("2000000"))),
                "Fits your bid capacity")
    assert big.ok is True


def test_unanswered_questions_ask_rather_than_fail_silently():
    from decimal import Decimal
    from app.eligibility import checklist

    items = checklist(_tender(value=Decimal("900000"), emd_amount=1000.0), _company())
    assert "Add your EMD budget" in _item(items, "Earnest money deposit").detail
    assert "Add how much work" in _item(items, "Fits your bid capacity").detail


def test_nothing_appears_when_the_tender_has_no_enriched_data():
    """An un-enriched tender must not sprout empty checks."""
    from app.eligibility import checklist

    items = checklist(_tender(), _company())
    assert _item(items, "Earnest money deposit") is None
    assert _item(items, "Contract period") is None
    assert _item(items, "Fits your bid capacity") is None
