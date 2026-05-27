"""add rfid finish confirmation

Revision ID: d9e0f1a2b3c4
Revises: c8d9e0f1a2b3
Create Date: 2026-05-27 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d9e0f1a2b3c4"
down_revision: Union[str, Sequence[str], None] = "c8d9e0f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _column_names(table_name: str) -> set[str]:
    """Return the current column names for one table."""
    inspector = sa.inspect(op.get_bind())
    return {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
    """Upgrade schema with RFID finish confirmation flag."""
    columns = _column_names("race_riders")
    if "finish_time_rfid_confirmed" not in columns:
        op.add_column(
            "race_riders",
            sa.Column("finish_time_rfid_confirmed", sa.Boolean(), server_default=sa.false(), nullable=False),
        )
        op.alter_column("race_riders", "finish_time_rfid_confirmed", server_default=None)


def downgrade() -> None:
    """Downgrade schema by removing RFID finish confirmation flag."""
    columns = _column_names("race_riders")
    if "finish_time_rfid_confirmed" in columns:
        op.drop_column("race_riders", "finish_time_rfid_confirmed")
