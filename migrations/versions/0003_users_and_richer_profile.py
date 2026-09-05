"""users (login) + the extra questionnaire answers matching can actually use

Revision ID: 0003
Revises: 0002
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("email", sa.Text, nullable=False, unique=True),
        sa.Column("password_hash", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime),
        sa.Column("last_login_at", sa.DateTime),
    )
    # Nullable: existing anonymous profiles stay valid and keep working by link.
    op.add_column("companies", sa.Column("user_id", sa.Integer, nullable=True))
    op.create_foreign_key(
        "fk_companies_user", "companies", "users", ["user_id"], ["id"]
    )
    op.create_index("idx_companies_user", "companies", ["user_id"])

    for column in ("states", "buyers", "exclude_keywords", "exclude_buyers"):
        op.add_column("companies", sa.Column(column, JSONType))
    # Backfill: the ORM defaults only fire on insert, so rows written before this
    # migration would read back None and break `set(profile.states or [])`... which
    # tolerates None, but a real empty list is what every other list column holds.
    op.execute(
        "UPDATE companies SET states='[]', buyers='[]', "
        "exclude_keywords='[]', exclude_buyers='[]'"
    )


def downgrade() -> None:
    for column in ("exclude_buyers", "exclude_keywords", "buyers", "states"):
        op.drop_column("companies", column)
    op.drop_index("idx_companies_user", table_name="companies")
    op.drop_constraint("fk_companies_user", "companies", type_="foreignkey")
    op.drop_column("companies", "user_id")
    op.drop_table("users")
