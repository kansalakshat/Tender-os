"""tenders.states: each tender's states, worked out once when it is saved

No portal states a tender's state, so it is read off the tender's own texts
(title, buyer, department, office, organisation chain) plus its state portal.
That used to be a regex over every row on every state filter and every home
page, which took minutes on the live corpus. Now it is stored once per row and
a state filter is a lookup (app/geo.py state_clause).

This migration adds the column and fills it for the tenders still open today,
reading only those five short texts per row. Closed tenders stay NULL: the
filter falls back to the old text search for them, and only an "include
closed" search ever reaches them.

Run it BEFORE deploying the code that reads the column.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.orm import Session

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenders", sa.Column("states", sa.Text, nullable=True))
    # Imported here, not at the top: the app's rule (geo.tender_states) is the
    # one this fills with, so the filled rows match what new saves write.
    from app.geo import fill_states
    from app.models import today_ist
    with Session(bind=op.get_bind()) as db:
        n = fill_states(db, only_open_on=today_ist())
    print(f"0015: stored states for {n} open tenders")


def downgrade() -> None:
    op.drop_column("tenders", "states")
