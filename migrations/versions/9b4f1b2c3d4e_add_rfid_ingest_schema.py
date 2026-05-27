"""add rfid ingest schema

Revision ID: 9b4f1b2c3d4e
Revises: 438e4bd69220
Create Date: 2026-05-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "9b4f1b2c3d4e"
down_revision: Union[str, Sequence[str], None] = "438e4bd69220"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_names() -> set[str]:
    """Return the current public table names for defensive migration checks."""
    inspector = sa.inspect(op.get_bind())
    return set(inspector.get_table_names())


def _column_names(table_name: str) -> set[str]:
    """Return the current column names for one table."""
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def _index_names(table_name: str) -> set[str]:
    """Return the current index names for one table."""
    inspector = sa.inspect(op.get_bind())
    return {index["name"] for index in inspector.get_indexes(table_name)}


def _unique_constraint_names(table_name: str) -> set[str]:
    """Return the current unique constraint names for one table."""
    inspector = sa.inspect(op.get_bind())
    return {
        constraint["name"]
        for constraint in inspector.get_unique_constraints(table_name)
        if constraint["name"]
    }


def upgrade() -> None:
    """Upgrade schema with RFID ingest storage and RFID review flags."""
    # Add the optional EPC mapping to devices first because ingest_rfid links to
    # it at the ORM layer. This is nullable so existing devices remain valid.
    if "epc_id" not in _column_names("devices"):
        op.add_column("devices", sa.Column("epc_id", sa.String(length=128), nullable=True))

    # Enforce one registered device per non-null EPC. PostgreSQL permits multiple
    # NULL values in a unique constraint, which is the desired state while older
    # devices have not been assigned RFID tags yet.
    if "ux_devices_epc_id" not in _unique_constraint_names("devices"):
        op.create_unique_constraint("ux_devices_epc_id", "devices", ["epc_id"])

    # Create the durable RFID ingest table for clean databases. The defensive
    # check keeps this migration usable on dev databases where init_db/create_all
    # may already have created this table before Alembic was stamped.
    if "ingest_rfid" not in _table_names():
        op.create_table(
            "ingest_rfid",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("epc", sa.String(length=128), nullable=False),
            sa.Column("rssi", sa.Float(), nullable=True),
            sa.Column("ant", sa.String(length=64), nullable=True),
            sa.Column("time_stamp_epoch", sa.Integer(), nullable=True),
            sa.Column("reader_id", sa.String(length=64), nullable=True),
            sa.Column("avg_rssi", sa.Float(), nullable=True),
            sa.Column("received_at_epoch", sa.Integer(), nullable=True),
            sa.PrimaryKeyConstraint("id"),
        )

    # Add indexes used by future RFID worker lookups. These are checked by name
    # so the migration is safe when create_all has already created them in dev.
    ingest_rfid_indexes = _index_names("ingest_rfid")
    if "ix_ingest_rfid_epc" not in ingest_rfid_indexes:
        op.create_index("ix_ingest_rfid_epc", "ingest_rfid", ["epc"], unique=False)
    if "ix_ingest_rfid_time_stamp_epoch" not in ingest_rfid_indexes:
        op.create_index("ix_ingest_rfid_time_stamp_epoch", "ingest_rfid", ["time_stamp_epoch"], unique=False)
    if "ix_ingest_rfid_reader_id" not in ingest_rfid_indexes:
        op.create_index("ix_ingest_rfid_reader_id", "ingest_rfid", ["reader_id"], unique=False)

    # Existing race_riders rows need a concrete false value, so the column is
    # added with a temporary server default and then returned to model parity.
    if "multiple_rfid_flag" not in _column_names("race_riders"):
        op.add_column(
            "race_riders",
            sa.Column("multiple_rfid_flag", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
        op.alter_column("race_riders", "multiple_rfid_flag", server_default=None)


def downgrade() -> None:
    """Downgrade schema by removing RFID ingest storage and RFID review flags."""
    if "multiple_rfid_flag" in _column_names("race_riders"):
        op.drop_column("race_riders", "multiple_rfid_flag")

    if "ingest_rfid" in _table_names():
        ingest_rfid_indexes = _index_names("ingest_rfid")
        if "ix_ingest_rfid_reader_id" in ingest_rfid_indexes:
            op.drop_index("ix_ingest_rfid_reader_id", table_name="ingest_rfid")
        if "ix_ingest_rfid_time_stamp_epoch" in ingest_rfid_indexes:
            op.drop_index("ix_ingest_rfid_time_stamp_epoch", table_name="ingest_rfid")
        if "ix_ingest_rfid_epc" in ingest_rfid_indexes:
            op.drop_index("ix_ingest_rfid_epc", table_name="ingest_rfid")
        op.drop_table("ingest_rfid")

    if "ux_devices_epc_id" in _unique_constraint_names("devices"):
        op.drop_constraint("ux_devices_epc_id", "devices", type_="unique")

    if "epc_id" in _column_names("devices"):
        op.drop_column("devices", "epc_id")
