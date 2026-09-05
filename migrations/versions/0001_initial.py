"""initial schema: sources, tenders, connector_runs

Revision ID: 0001
Revises:
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "sources",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("name", sa.Text, nullable=False, unique=True),
        sa.Column("base_url", sa.Text, nullable=False),
        sa.Column("license", sa.Text),
        sa.Column("robots_txt_checked_at", sa.DateTime),
        sa.Column("robots_txt_allowed", sa.Boolean),
        sa.Column("rate_limit_seconds", sa.Numeric, server_default="2"),
        sa.Column("active", sa.Boolean, server_default=sa.true()),
    )

    op.create_table(
        "tenders",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("sources.id")),
        sa.Column("external_ref", sa.Text, nullable=False),
        sa.Column("title", sa.Text, nullable=False),
        sa.Column("organization", sa.Text),
        sa.Column("department", sa.Text),
        sa.Column("category", sa.Text),
        sa.Column("estimated_value", sa.Numeric),
        sa.Column("currency", sa.String(8), server_default="INR"),
        sa.Column("published_date", sa.Date),
        sa.Column("deadline", sa.Date),
        sa.Column("status", sa.Text),
        sa.Column("document_url", sa.Text),
        sa.Column("source_url", sa.Text, nullable=False),
        sa.Column("first_seen_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("last_updated_at", sa.DateTime, server_default=sa.func.now()),
        sa.Column("raw_payload", JSONType),
        sa.Column("duplicate_of", sa.Integer, sa.ForeignKey("tenders.id")),
        sa.UniqueConstraint("source_id", "external_ref", name="uq_tenders_source_ref"),
    )
    op.create_index("idx_tenders_deadline", "tenders", ["deadline"])
    op.create_index("idx_tenders_category", "tenders", ["category"])
    op.create_index("idx_tenders_status", "tenders", ["status"])

    op.create_table(
        "connector_runs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("source_id", sa.Integer, sa.ForeignKey("sources.id")),
        sa.Column("source_name", sa.Text, nullable=False),
        sa.Column("started_at", sa.DateTime, nullable=False),
        sa.Column("finished_at", sa.DateTime),
        sa.Column("status", sa.Text, nullable=False),
        sa.Column("fetched", sa.Integer, server_default="0"),
        sa.Column("new", sa.Integer, server_default="0"),
        sa.Column("updated", sa.Integer, server_default="0"),
        sa.Column("errors", sa.Integer, server_default="0"),
        sa.Column("message", sa.Text),
    )
    op.create_index("idx_runs_started", "connector_runs", ["started_at"])


def downgrade() -> None:
    op.drop_table("connector_runs")
    op.drop_index("idx_tenders_status", table_name="tenders")
    op.drop_index("idx_tenders_category", table_name="tenders")
    op.drop_index("idx_tenders_deadline", table_name="tenders")
    op.drop_table("tenders")
    op.drop_table("sources")
