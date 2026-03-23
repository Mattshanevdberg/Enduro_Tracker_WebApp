"""add epoch mirror columns (phase A)

Revision ID: 1f4d6c5b3a2e
Revises: bf71d77a9db6
Create Date: 2026-01-12 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from datetime import datetime, timezone
from typing import Any, Iterable


# revision identifiers, used by Alembic.
revision: str = "1f4d6c5b3a2e"
down_revision: Union[str, Sequence[str], None] = "bf71d77a9db6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _to_epoch(value: Any) -> int | None:
    """
    Convert a DB DateTime value to epoch seconds (UTC).

    We treat stored values as UTC. If the DB returns naive datetimes or strings,
    we attach UTC and then convert to epoch seconds.
    """
    if value is None:
        return None

    dt: datetime | None = None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str):
        # SQLite commonly stores as "YYYY-MM-DD HH:MM:SS[.ffffff]" or ISO8601.
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            return None
    else:
        # Unexpected type (e.g., numeric); refuse to guess.
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return int(dt.timestamp())


def _backfill_epochs(
    conn: sa.Connection,
    table: str,
    pk_col: str,
    dt_cols: Iterable[tuple[str, str]],
) -> None:
    """
    Backfill epoch columns for a table.

    dt_cols: list of (datetime_col, epoch_col) pairs.
    """
    # Build a select that retrieves the PK and all datetime columns in one pass.
    select_cols = [pk_col] + [dt for dt, _ in dt_cols]
    sel = sa.text(
        f"SELECT {', '.join(select_cols)} FROM {table}"
    )
    rows = conn.execute(sel).fetchall()

    # Update per row; safe for SQLite and avoids DB-specific SQL for datetime parsing.
    for row in rows:
        pk_val = row[0]
        for idx, (dt_col, epoch_col) in enumerate(dt_cols, start=1):
            epoch_val = _to_epoch(row[idx])
            if epoch_val is None:
                continue
            conn.execute(
                sa.text(
                    f"UPDATE {table} SET {epoch_col} = :epoch WHERE {pk_col} = :pk"
                ),
                {"epoch": epoch_val, "pk": pk_val},
            )


def upgrade() -> None:
    """Add nullable epoch mirror columns for Phase A migration and backfill them."""
    # IngestRaw
    op.add_column("ingest_raw", sa.Column("received_at_epoch", sa.Integer(), nullable=True))
    op.add_column("ingest_raw", sa.Column("processed_at_epoch", sa.Integer(), nullable=True))

    # Point
    op.add_column("points", sa.Column("received_at_epoch", sa.Integer(), nullable=True))

    # Race
    op.add_column("races", sa.Column("starts_at_epoch", sa.Integer(), nullable=True))
    op.add_column("races", sa.Column("ends_at_epoch", sa.Integer(), nullable=True))

    # RaceRider
    op.add_column("race_riders", sa.Column("start_time_rfid_epoch", sa.Integer(), nullable=True))
    op.add_column("race_riders", sa.Column("finish_time_rfid_epoch", sa.Integer(), nullable=True))
    op.add_column("race_riders", sa.Column("start_time_pi_epoch", sa.Integer(), nullable=True))
    op.add_column("race_riders", sa.Column("finish_time_pi_epoch", sa.Integer(), nullable=True))

    # Caches + history
    op.add_column("leaderboard_cache", sa.Column("updated_at_epoch", sa.Integer(), nullable=True))
    op.add_column("track_cache", sa.Column("updated_at_epoch", sa.Integer(), nullable=True))
    op.add_column("leaderboard_hist", sa.Column("updated_at_epoch", sa.Integer(), nullable=True))
    op.add_column("track_hist", sa.Column("updated_at_epoch", sa.Integer(), nullable=True))

    # --- Phase B: Backfill epoch mirrors from existing DateTime values -----------
    conn = op.get_bind()
    _backfill_epochs(
        conn,
        table="ingest_raw",
        pk_col="id",
        dt_cols=[
            ("received_at", "received_at_epoch"),
            ("processed_at", "processed_at_epoch"),
        ],
    )
    _backfill_epochs(
        conn,
        table="points",
        pk_col="id",
        dt_cols=[
            ("received_at", "received_at_epoch"),
        ],
    )
    _backfill_epochs(
        conn,
        table="races",
        pk_col="id",
        dt_cols=[
            ("starts_at", "starts_at_epoch"),
            ("ends_at", "ends_at_epoch"),
        ],
    )
    _backfill_epochs(
        conn,
        table="race_riders",
        pk_col="id",
        dt_cols=[
            ("start_time_rfid", "start_time_rfid_epoch"),
            ("finish_time_rfid", "finish_time_rfid_epoch"),
            ("start_time_pi", "start_time_pi_epoch"),
            ("finish_time_pi", "finish_time_pi_epoch"),
        ],
    )
    _backfill_epochs(
        conn,
        table="leaderboard_cache",
        pk_col="category_id",
        dt_cols=[
            ("updated_at", "updated_at_epoch"),
        ],
    )
    _backfill_epochs(
        conn,
        table="track_cache",
        pk_col="race_rider_id",
        dt_cols=[
            ("updated_at", "updated_at_epoch"),
        ],
    )
    _backfill_epochs(
        conn,
        table="leaderboard_hist",
        pk_col="id",
        dt_cols=[
            ("updated_at", "updated_at_epoch"),
        ],
    )
    _backfill_epochs(
        conn,
        table="track_hist",
        pk_col="id",
        dt_cols=[
            ("updated_at", "updated_at_epoch"),
        ],
    )


def downgrade() -> None:
    """Remove epoch mirror columns (Phase A rollback)."""
    op.drop_column("track_hist", "updated_at_epoch")
    op.drop_column("leaderboard_hist", "updated_at_epoch")
    op.drop_column("track_cache", "updated_at_epoch")
    op.drop_column("leaderboard_cache", "updated_at_epoch")
    op.drop_column("race_riders", "finish_time_pi_epoch")
    op.drop_column("race_riders", "start_time_pi_epoch")
    op.drop_column("race_riders", "finish_time_rfid_epoch")
    op.drop_column("race_riders", "start_time_rfid_epoch")
    op.drop_column("races", "ends_at_epoch")
    op.drop_column("races", "starts_at_epoch")
    op.drop_column("points", "received_at_epoch")
    op.drop_column("ingest_raw", "processed_at_epoch")
    op.drop_column("ingest_raw", "received_at_epoch")
