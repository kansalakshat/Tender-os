"""which answers are filters rather than preferences

Answers have always been preferences: a tender that misses one still appears,
ranked lower. That is right when the answer is a leaning ("we mostly do
electrical") and wrong when it is a boundary ("we cannot work outside MP").

Stored as a list of field names rather than a column per answer, because which
answers can be a boundary is a product question that will keep moving, and a
migration per change is a poor way to find that out.

Revision ID: 0011
Revises: 0010
"""
from alembic import op
import sqlalchemy as sa

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSON, matching the other list answers on this table. Empty list, not NULL:
    # "no boundaries" is the answer every existing profile gave by having been
    # created before the question existed.
    op.add_column("companies", sa.Column(
        "strict", sa.JSON, nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("companies", "strict")
