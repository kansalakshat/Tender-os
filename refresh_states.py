r"""Recompute tenders.states for every open tender, fixing any that are wrong.

Run it ONCE, after the migration (0015) and after every machine that runs the
fetchers is on the new code. Until then a fetcher on older code can change a
tender's title or buyer without updating its states, and the normal fill only
looks at tenders that have no states at all.

    python refresh_states.py

It reads five short texts per open tender, writes only the rows whose stored
states are wrong, and never overwrites a row that changed while it ran.
"""
from app.db import SessionLocal
from app.geo import fill_states
from app.models import today_ist

if __name__ == "__main__":
    with SessionLocal() as db:
        n = fill_states(db, only_open_on=today_ist(), refresh=True)
    print(f"refresh_states: fixed {n} open tenders")
