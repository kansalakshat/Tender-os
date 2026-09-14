"""index the referencing side of tenders.duplicate_of

Revision ID: 0008
Revises: 0007

Postgres indexes a foreign key's target (tenders.id, the primary key) but not
the column that references it. So every row deleted from tenders makes Postgres
check that no other row's duplicate_of points at it, and with no index that
check is a scan of the whole table. Purging 5,335 expired rows from a 25.7k-row
table ran for several minutes: one full scan per deleted row. The purge's own
"clear duplicate_of pointers first" UPDATE filters on the same column.

A plain index rather than a partial `WHERE duplicate_of IS NOT NULL` one: the
column is almost always null, but a plain btree is the shape the foreign-key
check is guaranteed to use, and at this table size the nulls cost next to
nothing.

CONCURRENTLY, outside the migration transaction, so building it does not block
an ingest that is writing to tenders at the same time.

Postgres-only, like 0005. SQLite tests build from the models, which declare it.
"""
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_tenders_duplicate_of "
            "ON tenders (duplicate_of)"
        )


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("DROP INDEX CONCURRENTLY IF EXISTS idx_tenders_duplicate_of")
