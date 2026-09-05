"""email verification + Google sign-in

Revision ID: 0004
Revises: 0003
"""
from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "email_verified", sa.Boolean, nullable=False, server_default=sa.false()
        ),
    )
    op.add_column("users", sa.Column("google_sub", sa.Text, nullable=True))
    op.create_unique_constraint("uq_users_google_sub", "users", ["google_sub"])
    # An account created through Google has no password and never will, so the
    # column can no longer be NOT NULL. Every password check treats None as
    # "cannot sign in this way" rather than crashing (app/auth.py).
    op.alter_column("users", "password_hash", existing_type=sa.Text, nullable=True)


def downgrade() -> None:
    # Rows that only ever signed in with Google have no password to restore, so
    # NOT NULL cannot come back while they exist. Fail loudly instead of inventing
    # a hash nobody can use.
    orphans = op.get_bind().execute(
        sa.text("SELECT count(*) FROM users WHERE password_hash IS NULL")
    ).scalar()
    if orphans:
        raise RuntimeError(
            f"{orphans} account(s) have no password (Google sign-in). Delete or give "
            "them a password before downgrading past 0004."
        )
    op.alter_column("users", "password_hash", existing_type=sa.Text, nullable=False)
    op.drop_constraint("uq_users_google_sub", "users", type_="unique")
    op.drop_column("users", "google_sub")
    op.drop_column("users", "email_verified")
