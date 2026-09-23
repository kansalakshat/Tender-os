"""operator switch and listing size per source

Two unrelated-looking columns that both exist because `active` cannot answer
either question. `active` is written by the connector on every run -- False when
robots refuses, True when it succeeds -- so it records health, not intent. An
operator who turns a source off needs a flag the next run will not overwrite.

`listing_total` is the portal's own count of what it publishes ("Showing 1 - 10
records of 45,720"). Without it the dashboard can say how many rows we hold but
not how many are left to collect, which is the number an operator actually
wants while a backfill runs.

Revision ID: 0010
Revises: 0009
"""
from alembic import op
import sqlalchemy as sa

revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # server_default, not just a Python default: the rows already in the table
    # need a value too, and every existing source was in use when this ran.
    op.add_column("sources", sa.Column(
        "enabled", sa.Boolean, nullable=False, server_default=sa.true()))
    # Nullable: only a portal that publishes a total has one, and only after a
    # run has read it. "Unknown" must stay distinguishable from "zero left".
    op.add_column("sources", sa.Column("listing_total", sa.Integer))
    op.add_column("sources", sa.Column("listing_total_at", sa.DateTime))


def downgrade() -> None:
    op.drop_column("sources", "listing_total_at")
    op.drop_column("sources", "listing_total")
    op.drop_column("sources", "enabled")
