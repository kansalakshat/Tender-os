"""The home page's state counts: the fast prefix-tree pattern must name exactly
the states the reference pattern (and so /browse?state=) does."""
import random

from app import geo


def ref(text):
    return {geo._PLACE[m.group(1).lower()][0] for m in geo._ANY.finditer(text)}


def test_every_place_name_is_found_by_both_patterns():
    for name, (state, _d) in geo._PLACE.items():
        for text in (name, f"Supply at {name.upper()} office", f"{name.title()}, works"):
            assert set(geo.states_named(text)) == ref(text) == {state}, text


def test_mixed_and_tricky_texts_agree():
    names = list(geo._PLACE)
    rng = random.Random(7)
    texts = ["West Godavari and East Godavari roads", "NEW DELHI", "Delhi-Mumbai expressway",
             "no place here", "", "Goa Shipyard, Vasco", "Raipur, Chhattisgarh"]
    for _ in range(500):
        texts.append(" and ".join(rng.sample(names, 3)) + " repair")
        texts.append(rng.choice(names) + "pur works")          # not a whole word
    for t in texts:
        assert set(geo.states_named(t)) == ref(t), t


def test_home_state_count_issues_no_regex_sql():
    import inspect
    from app import web
    src = inspect.getsource(web._state_counts)
    assert "state_clause" not in src and "states_named" in src
