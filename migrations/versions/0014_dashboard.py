"""personal dashboard: saved searches, and tenders marked as being bid on

Revision ID: 0014
Revises: 0013
"""
from alembic import op
import sqlalchemy as sa

revision = "0014"
down_revision = "0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "saved_searches",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer, sa.ForeignKey("users.id", ondelete="CASCADE"),
                  nullable=False),
        sa.Column("name", sa.Text, nullable=False),
        sa.Column("query", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime),
    )
    op.create_index("ix_saved_search_user", "saved_searches", ["user_id", "created_at"])
    op.add_column("wishlist_items", sa.Column("participating", sa.Boolean, nullable=False,
                                              server_default="false"))


def downgrade() -> None:
    op.drop_column("wishlist_items", "participating")
    op.drop_index("ix_saved_search_user", table_name="saved_searches")
    op.drop_table("saved_searches")
