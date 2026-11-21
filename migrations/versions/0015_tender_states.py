"""tenders.states: each tender's states, worked out once when it is saved

No portal states a tender's state, so it is read off the tender's own texts
(title, buyer, department, office, organisation chain) plus its state portal.
That used to be a regex over every row on every state filter and every home
page, which took minutes on the live corpus. Now it is stored once per row and
a state filter is a lookup (app/geo.py state_clause).

This migration only adds the column. It imports nothing from app/: a
migration must still run years from now, on a fresh database, after the app's
code has been renamed or rewritten. Filling the column is the app's job:

    alembic upgrade head
    python refresh_states.py      # fills every open tender, before the deploy

Closed tenders stay NULL: the filter falls back to the old text search for
them, and only an "include closed" search ever reaches them.

Revision ID: 0015
Revises: 0014
"""
from alembic import op
import sqlalchemy as sa

revision = "0015"
down_revision = "0014"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("tenders", sa.Column("states", sa.Text, nullable=True))


def downgrade() -> None:
    op.drop_column("tenders", "states")
