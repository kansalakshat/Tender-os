"""The home page's state counts. states_named() must find exactly the states
that each state's own search finds, the way state_clause() asks SQL one state
at a time: a name inside a longer one counts too."""
import random
import re

from app import geo

PER_STATE = {st: re.compile(r"\b(" + "|".join(re.escape(n) for n in sorted(geo._names(st), key=len, reverse=True))
                            + r")\b", re.I) for st in geo.STATES}


def ref(text):
    return {st for st, rx in PER_STATE.items() if rx.search(text)}


def test_regressions_from_review():
    # re.I matches "ſ" as "s", so this names Assam; it must not crash the count
    assert geo.states_named("aſsam") == {"Assam"} == ref("aſsam")
    # Assam's "Lakhimpur" sits inside Uttar Pradesh's "Lakhimpur Kheri"; SQL finds both
    assert geo.states_named("Lakhimpur Kheri") == {"Assam", "Uttar Pradesh"} == ref("Lakhimpur Kheri")


def test_every_place_name_agrees_with_the_per_state_search():
    for name in geo._PLACE:
        for text in (name, f"Supply at {name.upper()} office", f"{name.title()}, works"):
            assert set(geo.states_named(text)) == ref(text), text


def test_random_and_tricky_texts_agree():
    names = list(geo._PLACE)
    rng = random.Random(7)
    texts = ["West Godavari and East Godavari roads", "NEW DELHI", "Delhi-Mumbai expressway",
             "no place here", "", "Goa Shipyard, Vasco", "Raipur, Chhattisgarh", "ſikkim road"]
    for _ in range(600):
        texts.append(" and ".join(rng.sample(names, 3)) + " repair")
        texts.append(rng.choice(names) + "pur works")          # not a whole word
        a, b = rng.sample(names, 2)
        texts.append(a + " " + b)                               # names touching
    for t in texts:
        assert set(geo.states_named(t)) == ref(t), t


def test_home_state_count_reads_the_stored_column():
    import inspect
    from app import web
    src = inspect.getsource(web._state_counts)
    assert "state_clause" not in src and "group_by(Tender.states)" in src
