"""add processed flags and unique index

Revision ID: 3536b47710cb
Revises: 
Create Date: 2025-10-14 15:41:27.457302

"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.engine.reflection import Inspector

# revision identifiers, used by Alembic.
revision = "0001_processed_flags_unique_idx"
down_revision = None
branch_labels = None
depends_on = None


def _has_column(bind, table_name, column_name) -> bool:
    insp = Inspector.from_engine(bind)
    cols = [c["name"] for c in insp.get_columns(table_name)]
    return column_name in cols


def _has_index(bind, table_name, index_name) -> bool:
    insp = Inspector.from_engine(bind)
    for idx in insp.get_indexes(table_name):
        if idx.get("name") == index_name:
            return True
    return False


def upgrade():
    bind = op.get_bind()

    # --- ingest_raw: add processed_at, parse_error if missing ---
    if _has_column(bind, "ingest_raw", "processed_at") is False:
        op.add_column(
            "ingest_raw",
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        )

    if _has_column(bind, "ingest_raw", "parse_error") is False:
        op.add_column(
            "ingest_raw",
            sa.Column("parse_error", sa.Text(), nullable=True),
        )

    # --- points: ensure unique index on (device_id, t_epoch) ---
    idx_name = "ux_points_device_time"
    if _has_index(bind, "points", idx_name) is False:
        # Works on SQLite/Postgres. On Postgres you may prefer a UNIQUE CONSTRAINT;
        # a UNIQUE INDEX is sufficient and simpler here.
        op.create_index(idx_name, "points", ["device_id", "t_epoch"], unique=True)


def downgrade():
    bind = op.get_bind()

    # Reverse unique index on points
    idx_name = "ux_points_device_time"
    if _has_index(bind, "points", idx_name):
        op.drop_index(idx_name, table_name="points")

    # NOTE: SQLite cannot drop columns easily without table rebuild.
    # For portability we will NO-OP on column removal in downgrade to avoid destructive actions.
    # If you really need to remove columns, implement a table copy (CREATE TABLE AS ..., copy data).
    # Example: leave processed_at/parse_error in place.
    pass