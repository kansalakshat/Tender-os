"""indexes for the two query shapes EXPLAIN showed doing real work

Revision ID: 0005
Revises: 0004

Measured on 4,332 rows before adding these:
  * `title ILIKE '%q%'`  -> Seq Scan, 21 ms. A btree cannot serve a leading
    wildcard, so this needs a trigram GIN index.
  * `ORDER BY deadline, id LIMIT 25 OFFSET 4000` -> 74 ms with
    `Sort Method: external merge  Disk: 5584kB` -- Postgres sorted whole
    1,350-byte rows and spilled to disk. An index on the sort key lets it walk
    in order instead.

The listing filter (`duplicate_of IS NULL AND deadline >= ...`) was already a
3.5 ms Seq Scan and got no index: at this table size a scan beats an index
lookup, and an index nobody uses still costs time on every insert.

Postgres-only. SQLite (used by the tests) builds its schema from the models with
create_all and never runs this.
"""
from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

# Every API listing defaults to include_duplicates=False, so the partial indexes
# match the query the app actually issues and stay smaller than full ones.
LIVE = "duplicate_of IS NULL"


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return

    # pg_trgm ships with Postgres but is not enabled by default.
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_tenders_title_trgm "
        "ON tenders USING gin (title gin_trgm_ops)"
    )
    # One per sort option offered by GET /tenders, so no sort ever spills to disk.
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_tenders_live_deadline "
        f"ON tenders (deadline, id) WHERE {LIVE}"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_tenders_live_published "
        f"ON tenders (published_date DESC, id) WHERE {LIVE}"
    )
    op.execute(
        f"CREATE INDEX IF NOT EXISTS idx_tenders_live_seen "
        f"ON tenders (first_seen_at DESC, id) WHERE {LIVE}"
    )
    # Keeps the planner's row estimates honest right after a bulk ingest.
    op.execute("ANALYZE tenders")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for name in (
        "idx_tenders_live_seen",
        "idx_tenders_live_published",
        "idx_tenders_live_deadline",
        "idx_tenders_title_trgm",
    ):
        op.execute(f"DROP INDEX IF EXISTS {name}")
    # pg_trgm is left installed: other things may rely on it and dropping an
    # extension is not ours to decide.
