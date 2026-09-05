from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.matching import (
    DEFAULT_MIN_LEAD_DAYS,
    derive_districts,
    derive_sectors,
    find_matches,
    match_score,
)
from app.models import Source, Tender
from app.schemas import CompanyIn

TODAY = date(2026, 8, 1)


def profile(**kw):
    kw.setdefault("name", "Acme")
    kw.setdefault("sectors", ["electrical_power"])
    kw.setdefault("min_lead_days", 0)
    return CompanyIn(**kw)


def tender(**kw):
    kw.setdefault("title", "Bifurcation of 11 kv Dhangaon AG Feeder")
    kw.setdefault("deadline", TODAY + timedelta(days=30))
    kw.setdefault("status", "open")
    kw.setdefault("organization", "MP Paschim Kshetra Vidyut Vitran Company Ltd")
    kw.setdefault("department", "EE, Mandsaur STC Division, Mandsaur")
    kw.setdefault("estimated_value", None)
    kw.setdefault("external_ref", "X1")
    kw.setdefault("source_url", "https://mptenders.gov.in/x")
    return Tender(**kw)


# ---- taxonomy, against real titles off the live MP listing ----

@pytest.mark.parametrize(
    "title,expected",
    [
        ("ESTIMATE OF 11KV HATHIPURA AG FEEDER BIFURCATION", "electrical_power"),
        ("THIS ESTIMATE IS CREATED FOR ADDITIONAL 3 15 MVA PTR", "electrical_power"),
        ("ONLINE TENDER FOR HIRING OF LIGHT MOTOR VEHICLE TATA 407", "vehicle_hire"),
        ("CONSTRUCTION OF PAVER BLOCK ROAD AT WARD NO. 14", "civil_construction"),
        ("Tender for medicine items", "medical_pharma"),
        ("stationary items", "office_supplies"),
        ("Pesticide 20 percentage Chlorpyrifos E C", "agriculture"),
        ("Procurement of Flame arrestor for hydrogen vent line", "industrial_supply"),
        ("Auction of Scrap", "scrap_auction"),
        ("AMCACServicing at DHEoffice", "maintenance_amc"),
    ],
)
def test_derive_sectors_on_real_titles(title, expected):
    assert expected in derive_sectors(title)


def test_busbar_is_not_a_vehicle():
    """'33 kv BUS STAND' is a locality being electrified, not a bus contract."""
    sectors = derive_sectors(
        "an estimate framed for double supply of 33 kv BUS STAND feeder"
    )
    assert "electrical_power" in sectors
    assert "vehicle_hire" not in sectors


def test_ag_feeder_is_electrical_not_agriculture():
    sectors = derive_sectors("Estimate for 11kv Rajla irrigation bifurcation feeder")
    assert sectors == {"electrical_power"}


def test_buyer_department_does_not_set_the_sector():
    """A medical college's scrap auction is scrap work, not a pharma contract."""
    score, reasons = match_score(
        profile(sectors=["medical_pharma"], keywords=["medicine"]),
        tender(
            title="Auction of Scrap",
            organization="Directorate of Medical Education",
            department="Netaji Subhash Chandra Bose Medical College - Jabalpur",
        ),
        today=TODAY,
    ) or (0, [])
    assert score == 0


def test_derive_districts_is_word_bounded():
    assert derive_districts("EE, Mandsaur STC Division") == {"Mandsaur"}
    assert derive_districts("Dhariwal Enterprises") == set()


# ---- hard filters ----

def test_no_signal_means_no_match():
    assert match_score(profile(sectors=["medical_pharma"]), tender(), today=TODAY) is None


def test_deadline_too_soon_is_excluded():
    t = tender(deadline=TODAY + timedelta(days=2))
    assert match_score(profile(min_lead_days=7), t, today=TODAY) is None
    assert match_score(profile(min_lead_days=1), t, today=TODAY) is not None


def test_closed_and_undated_tenders_are_excluded():
    assert match_score(profile(), tender(deadline=None), today=TODAY) is None
    assert match_score(profile(), tender(status="closed"), today=TODAY) is None


def test_value_over_capacity_excluded_but_unknown_value_is_kept():
    p = profile(max_project_value=Decimal("500000"))
    assert match_score(p, tender(estimated_value=Decimal("900000")), today=TODAY) is None
    assert match_score(p, tender(estimated_value=Decimal("100000")), today=TODAY)
    # The null case is the one that matters: 100% of live rows have no value.
    assert match_score(p, tender(estimated_value=None), today=TODAY)


def test_default_lead_days_applies_when_unset():
    p = profile()
    p.min_lead_days = None
    t = tender(deadline=TODAY + timedelta(days=DEFAULT_MIN_LEAD_DAYS - 1))
    assert match_score(p, t, today=TODAY) is None


# ---- scoring ----

def test_district_and_keyword_raise_the_score():
    base = match_score(profile(), tender(), today=TODAY)[0]
    with_extras = match_score(
        profile(districts=["Mandsaur"], keywords=["bifurcation"]), tender(), today=TODAY
    )[0]
    assert with_extras > base


def test_reasons_explain_the_match():
    _, reasons = match_score(
        profile(districts=["Mandsaur"], keywords=["bifurcation"]), tender(), today=TODAY
    )
    joined = " ".join(reasons)
    assert "Electrical" in joined and "Mandsaur" in joined and "bifurcation" in joined


def test_keyword_needs_a_word_boundary():
    """'ac' must not match inside 'contract'."""
    hit = match_score(
        profile(sectors=[], keywords=["ac"]),
        tender(title="Contract for supply of cable"),
        today=TODAY,
    )
    assert hit is None


# ---- query ----

def test_find_matches_ranks_and_filters(db):
    src = Source(name="MP", base_url="https://mptenders.gov.in")
    db.add(src)
    db.flush()
    strong = tender(title="11 kv feeder bifurcation at Mandsaur", external_ref="A")
    weak = tender(title="Supply of 33kv transformer", department="EE, Bhopal",
                  organization="MPPKVVCL", external_ref="B")
    irrelevant = tender(title="Tender for medicine items", external_ref="C")
    for t in (strong, weak, irrelevant):
        t.source_id = src.id
        db.add(t)
    db.commit()

    out = find_matches(db, profile(districts=["Mandsaur"]), today=TODAY)
    titles = [t.title for _, _, t in out]
    assert "Tender for medicine items" not in titles
    assert out[0][2].title == strong.title      # district bonus puts it first
    assert out[0][0] > out[1][0]


def test_find_matches_skips_linked_duplicates(db):
    src = Source(name="MP", base_url="https://mptenders.gov.in")
    db.add(src)
    db.flush()
    first = tender(external_ref="A")
    first.source_id = src.id
    db.add(first)
    db.flush()
    dupe = tender(external_ref="B")
    dupe.source_id = src.id
    dupe.duplicate_of = first.id
    db.add(dupe)
    db.commit()

    assert [t.id for _, _, t in find_matches(db, profile(), today=TODAY)] == [first.id]


# ---- exclusions: hard filters, not deductions ----

def test_exclude_keyword_disqualifies_outright():
    p = profile(keywords=["transformer"], exclude_keywords=["scrap"])
    assert match_score(p, tender(title="Supply of 11 kv transformer"), today=TODAY)
    assert match_score(p, tender(title="Scrap transformer auction"), today=TODAY) is None


def test_exclude_buyer_disqualifies_outright():
    p = profile(exclude_buyers=["Ministry of Railways"])
    assert match_score(p, tender(), today=TODAY)
    assert match_score(
        p, tender(organization="Ministry of Railways"), today=TODAY
    ) is None


def test_exclude_buyer_matches_inside_a_longer_chain():
    """GePNIC packs 'Dept||Division||Office' into one field."""
    p = profile(exclude_buyers=["Ministry of Railways"])
    assert match_score(
        p,
        tender(organization="X", department="Ministry of Railways - South Central"),
        today=TODAY,
    ) is None


# ---- new positive signals ----

def test_state_raises_the_score_and_is_word_bounded():
    base = match_score(profile(), tender(), today=TODAY)[0]
    hit = match_score(
        profile(states=["Madhya Pradesh"]),
        tender(department="Chief Engineer, Madhya Pradesh Circle"),
        today=TODAY,
    )[0]
    assert hit > base
    miss = match_score(profile(states=["Goa"]), tender(department="Goanna Ltd"), today=TODAY)[0]
    assert miss == base


def test_preferred_buyer_raises_the_score():
    base = match_score(profile(), tender(), today=TODAY)[0]
    hit = match_score(
        profile(buyers=["NTPC Limited"]), tender(organization="NTPC Limited"), today=TODAY
    )[0]
    assert hit > base


def test_geography_is_paid_once_not_twice():
    """A district and its state are the same fact; awarding both double-counts."""
    one = match_score(
        profile(districts=["Mandsaur"]), tender(), today=TODAY
    )[0]
    both = match_score(
        profile(districts=["Mandsaur"], states=["Madhya Pradesh"]),
        tender(department="EE, Mandsaur, Madhya Pradesh"),
        today=TODAY,
    )[0]
    assert both == one


# ---- lead time is reported, never scored ----

def test_deadline_distance_does_not_change_the_score():
    near = match_score(profile(), tender(deadline=TODAY + timedelta(days=8)), today=TODAY)
    far = match_score(profile(), tender(deadline=TODAY + timedelta(days=200)), today=TODAY)
    assert near[0] == far[0]
    assert "closes in 8 days" in " ".join(near[1])


# ---- informativeness: the fix for the 82% pile-up ----

def _corpus(db, titles):
    src = Source(name="MP", base_url="https://mptenders.gov.in")
    db.add(src)
    db.flush()
    for i, title in enumerate(titles):
        t = tender(title=title, external_ref=f"R{i}")
        t.source_id = src.id
        db.add(t)
    db.commit()


def test_a_rare_term_outranks_a_ubiquitous_one(db):
    """'maintenance' matched 375 of 449 live rows and scored like a rare hit.

    Both tenders below match one of the profile's sectors on exactly one term, so
    under the old flat award they tied. The rare term must now win.
    """
    _corpus(db, ["Annual maintenance of office block"] * 20
                + ["Supply of Chlorpyrifos 20 percent EC"])
    out = find_matches(
        db, profile(sectors=["maintenance_amc", "agriculture"]), today=TODAY
    )
    assert "Chlorpyrifos" in out[0][2].title
    assert out[0][0] > out[-1][0]


def test_reasons_name_the_term_that_drove_the_match(db):
    _corpus(db, ["Annual maintenance of office block"] * 5
                + ["Supply of Chlorpyrifos 20 percent EC"])
    out = find_matches(
        db, profile(sectors=["maintenance_amc", "agriculture"]), today=TODAY
    )
    assert "chlorpyrifos" in " ".join(out[0][1]).lower()


def test_ubiquitous_keyword_is_worth_less_than_a_rare_one(db):
    """Same shape, but for a keyword the company chose rather than a sector term."""
    _corpus(db, ["11 kv feeder cable at site"] * 20 + ["11 kv feeder chlorpyrifos store"])
    out = find_matches(db, profile(keywords=["cable", "chlorpyrifos"]), today=TODAY)
    assert "chlorpyrifos" in out[0][2].title.lower()
