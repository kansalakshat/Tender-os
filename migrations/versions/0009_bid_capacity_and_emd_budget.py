"""bid capacity and EMD budget on companies

Two more optional eligibility answers, checked in app/eligibility.py. They only
became worth asking once app/enrich.py started reading the estimated value and
the EMD off each bid document -- before that there was no per-tender number to
check an answer against. Like the 0007 columns they never filter matches.

Revision ID: 0009
Revises: 0008
"""
from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # How much work the company can have running at once, and how much cash it
    # can have tied up in bid securities. Nullable: an unanswered question shows
    # as "add it to your profile", never as a failed check.
    op.add_column("companies", sa.Column("bid_capacity", sa.Numeric))
    op.add_column("companies", sa.Column("emd_budget", sa.Numeric))


def downgrade() -> None:
    op.drop_column("companies", "emd_budget")
    op.drop_column("companies", "bid_capacity")
