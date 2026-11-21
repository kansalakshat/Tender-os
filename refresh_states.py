r"""Work out tenders.states for every open tender, fixing any that are wrong.

Run it twice:

1. Right after `alembic upgrade head` (0015), BEFORE deploying the code that
   reads the column. The migration only adds the column; this fills it, so the
   home page does not have to work out 46,000 tenders' states on its own.
2. Once more after every machine that runs the fetchers is on the new code.
   Until then a fetcher on older code can change a tender's title or buyer
   without updating its states, and the daily fill only looks at NULL rows.

    python refresh_states.py

It reads five short texts per open tender, writes only the rows whose stored
states are missing or wrong, and never overwrites a row that changed while it ran.
"""
from app.db import SessionLocal
from app.geo import fill_states
from app.models import today_ist

if __name__ == "__main__":
    with SessionLocal() as db:
        n = fill_states(db, only_open_on=today_ist(), refresh=True)
    print(f"refresh_states: fixed {n} open tenders")
