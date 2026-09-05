"""companies: bidder profiles for tender matching

Revision ID: 0002
Revises: 0001
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "companies",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("contact_email", sa.Text),
        sa.Column("sectors", JSONType),
        sa.Column("keywords", JSONType),
        sa.Column("districts", JSONType),
        sa.Column("min_lead_days", sa.Integer, server_default="7"),
        sa.Column("max_project_value", sa.Numeric),
        # Written from Python (naive UTC); no server_default, because the DB
        # clock is server-local and would disagree with it.
        sa.Column("created_at", sa.DateTime),
        sa.Column("updated_at", sa.DateTime),
    )


def downgrade() -> None:
    op.drop_table("companies")
