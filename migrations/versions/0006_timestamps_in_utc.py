"""tenders.first_seen_at / last_updated_at were on the database clock, not UTC

They were the only columns in the schema using server_default=func.now(), which
is the DATABASE server's local clock. Everything else uses Python's utcnow(). On
a server running IST that put them 5h30m ahead of every other timestamp, so
"rows created during this connector run" -- first_seen_at compared against
connector_runs.started_at -- silently matched nothing.

last_updated_at was worse: server-local when a row was inserted and UTC when
BaseConnector._upsert touched it, inside one column.

The shift below is the server's own UTC offset, so on a database that was
already running UTC it evaluates to zero and this migration is a no-op -- which
is correct, because there was nothing wrong with those rows.

last_updated_at cannot be corrected the same way: for any given row we cannot
tell whether it was last written on insert (local) or on update (UTC). It is
set to the corrected first_seen_at instead. Nothing reads it -- the API exposes
it and nothing sorts or filters on it -- so a consistent approximation is worth
more here than a mixed-clock value that cannot be reasoned about at all.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

# localtimestamp - (now() at time zone 'utc') == the server's UTC offset
OFFSET = "(localtimestamp - (now() AT TIME ZONE 'utc'))"


def upgrade() -> None:
    op.alter_column("tenders", "first_seen_at", server_default=None)
    op.alter_column("tenders", "last_updated_at", server_default=None)
    op.execute(
        f"UPDATE tenders SET first_seen_at = first_seen_at - {OFFSET} "
        f"WHERE first_seen_at IS NOT NULL"
    )
    op.execute("UPDATE tenders SET last_updated_at = first_seen_at")


def downgrade() -> None:
    op.execute(
        f"UPDATE tenders SET first_seen_at = first_seen_at + {OFFSET}, "
        f"last_updated_at = last_updated_at + {OFFSET} WHERE first_seen_at IS NOT NULL"
    )
    op.alter_column("tenders", "first_seen_at", server_default=sa.text("now()"))
    op.alter_column("tenders", "last_updated_at", server_default=sa.text("now()"))
