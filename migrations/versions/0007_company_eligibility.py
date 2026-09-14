"""eligibility answers on companies

Optional answers checked against each tender's typical criteria; see
app/eligibility.py. They never filter matches.

Revision ID: 0007
Revises: 0006
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.add_column("companies", sa.Column("years_in_business", sa.Integer))
    op.add_column("companies", sa.Column("annual_turnover", sa.Numeric))
    op.add_column("companies", sa.Column("largest_similar_work", sa.Numeric))
    op.add_column("companies", sa.Column("registrations", JSONType))
    # ORM defaults only fire on insert; existing rows get the empty list too.
    op.execute("UPDATE companies SET registrations='[]'")


def downgrade() -> None:
    for column in ("registrations", "largest_similar_work", "annual_turnover",
                   "years_in_business"):
        op.drop_column("companies", column)
