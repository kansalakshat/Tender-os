"""rate limits in the database, and revocable sessions

rate_limits: the login/signup throttle used to live in process memory, which on
Vercel is per instance and so throttled almost nothing.

users.session_epoch: part of each session cookie's stamp. Sign-out bumps it, so
a cookie copied before sign-out stops working.

Revision ID: 0013
Revises: 0012
"""
from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "rate_limits",
        sa.Column("key", sa.Text, primary_key=True),
        sa.Column("window_start", sa.Float, nullable=False),
        sa.Column("count", sa.Integer, nullable=False),
    )
    # Existing rows get 0, which app/auth.py leaves out of the stamp, so
    # sessions issued before this migration stay valid.
    op.add_column("users", sa.Column("session_epoch", sa.Integer, nullable=False,
                                     server_default="0"))


def downgrade() -> None:
    op.drop_column("users", "session_epoch")
    op.drop_table("rate_limits")
