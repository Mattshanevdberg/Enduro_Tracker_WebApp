"""add rfid worker bookkeeping

Revision ID: c8d9e0f1a2b3
Revises: 9b4f1b2c3d4e
Create Date: 2026-05-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: Union[str, Sequence[str], None] = "9b4f1b2c3d4e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    """Return the current column names for one table."""
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    """Return the current index names for one table."""
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def upgrade() -> None:
    """Upgrade schema with RFID worker processed markers."""
    columns = _column_names("ingest_rfid")
    if "processed_at_epoch" not in columns:
        op.add_column("ingest_rfid", sa.Column("processed_at_epoch", sa.Integer(), nullable=True))
    if "process_error" not in columns:
        op.add_column("ingest_rfid", sa.Column("process_error", sa.Text(), nullable=True))

    if "ix_ingest_rfid_processed_at_epoch" not in _index_names("ingest_rfid"):
        op.create_index("ix_ingest_rfid_processed_at_epoch", "ingest_rfid", ["processed_at_epoch"], unique=False)


def downgrade() -> None:
    """Downgrade schema by removing RFID worker processed markers."""
    if "ix_ingest_rfid_processed_at_epoch" in _index_names("ingest_rfid"):
        op.drop_index("ix_ingest_rfid_processed_at_epoch", table_name="ingest_rfid")

    columns = _column_names("ingest_rfid")
    if "process_error" in columns:
        op.drop_column("ingest_rfid", "process_error")
    if "processed_at_epoch" in columns:
        op.drop_column("ingest_rfid", "processed_at_epoch")
