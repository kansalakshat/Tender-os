"""tenders a user saved for later

ON DELETE CASCADE is the whole design decision here. Expired tenders are purged
daily (RETENTION_DAYS=0, app/retention.py), and a plain foreign key would make
that purge fail the first time anyone saved a tender that later closed -- the
ingest would start failing every night over a bookmark. A saved tender that
closes simply leaves the list, which is also what a bidder would expect.

Revision ID: 0012
Revises: 0011
"""
from alembic import op
import sqlalchemy as sa

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "wishlist_items",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("user_id", sa.Integer,
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("tender_id", sa.Integer,
                  sa.ForeignKey("tenders.id", ondelete="CASCADE"), nullable=False),
        sa.Column("created_at", sa.DateTime, nullable=False),
        # One save per tender per person. Without it a double-click, or two tabs,
        # silently stores the same tender twice and the list shows it twice.
        sa.UniqueConstraint("user_id", "tender_id", name="uq_wishlist_user_tender"),
    )
    # The list is always read as "everything this user saved, newest first".
    op.create_index("ix_wishlist_user", "wishlist_items", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_wishlist_user", table_name="wishlist_items")
    op.drop_table("wishlist_items")
