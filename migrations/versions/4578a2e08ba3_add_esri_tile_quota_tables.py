"""add esri tile quota tables

Revision ID: 4578a2e08ba3
Revises: 1cc053181c53
Create Date: 2026-07-13 12:35:58.356457

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "4578a2e08ba3"
down_revision: Union[str, Sequence[str], None] = "1cc053181c53"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Create durable Esri/map tile quota tables.

    Notes:
      This migration is written manually because the dev database had already
      created these tables through Base.metadata.create_all(), so Alembic
      autogenerate could not see them as new tables. Do not add the redundant
      ix_users_rider_id index here; users.rider_id is already covered by the
      existing ux_users_rider_id unique constraint.
    """
    inspector = sa.inspect(op.get_bind())

    if not inspector.has_table("map_tile_monthly_quota"):
        op.create_table(
            "map_tile_monthly_quota",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("billing_month", sa.String(length=7), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("estimated_tiles_used", sa.Integer(), nullable=False),
            sa.Column("monthly_limit", sa.Integer(), nullable=False),
            sa.Column("warning_threshold", sa.Integer(), nullable=False),
            sa.Column("hard_stop_threshold", sa.Integer(), nullable=False),
            sa.Column("warning_triggered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("hard_stop_triggered_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("hard_stop_active", sa.Boolean(), nullable=False),
            sa.Column("viewers_only_blocked", sa.Boolean(), nullable=False),
            sa.Column("override_active", sa.Boolean(), nullable=False),
            sa.Column("override_until", sa.DateTime(timezone=True), nullable=True),
            sa.Column("override_reason", sa.Text(), nullable=True),
            sa.Column("last_usage_rollup_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "billing_month",
                "provider",
                name="ux_map_tile_monthly_quota_month_provider",
            ),
        )
        op.create_index(
            op.f("ix_map_tile_monthly_quota_billing_month"),
            "map_tile_monthly_quota",
            ["billing_month"],
            unique=False,
        )

    if not inspector.has_table("map_tile_usage_sessions"):
        op.create_table(
            "map_tile_usage_sessions",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("session_key", sa.String(length=64), nullable=False),
            sa.Column("browser_cookie_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("role", sa.String(length=32), nullable=False),
            sa.Column("race_id", sa.Integer(), nullable=True),
            sa.Column("billing_month", sa.String(length=7), nullable=False),
            sa.Column("page_path", sa.String(length=512), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("session_started_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("session_last_seen_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("estimated_tiles_loaded", sa.Integer(), nullable=False),
            sa.Column("fallback_used", sa.Boolean(), nullable=False),
            sa.Column("blocked_reason", sa.String(length=64), nullable=True),
            sa.Column("user_agent_hash", sa.String(length=128), nullable=True),
            sa.Column("ip_hash", sa.String(length=128), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["race_id"], ["races.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_map_tile_usage_sessions_billing_month"),
            "map_tile_usage_sessions",
            ["billing_month"],
            unique=False,
        )
        op.create_index(
            op.f("ix_map_tile_usage_sessions_blocked_reason"),
            "map_tile_usage_sessions",
            ["blocked_reason"],
            unique=False,
        )
        op.create_index(
            op.f("ix_map_tile_usage_sessions_browser_cookie_id"),
            "map_tile_usage_sessions",
            ["browser_cookie_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_map_tile_usage_sessions_race_id"),
            "map_tile_usage_sessions",
            ["race_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_map_tile_usage_sessions_session_key"),
            "map_tile_usage_sessions",
            ["session_key"],
            unique=True,
        )
        op.create_index(
            op.f("ix_map_tile_usage_sessions_user_id"),
            "map_tile_usage_sessions",
            ["user_id"],
            unique=False,
        )

    if not inspector.has_table("map_tile_browser_blocks"):
        op.create_table(
            "map_tile_browser_blocks",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("browser_cookie_id", sa.String(length=64), nullable=False),
            sa.Column("user_id", sa.Integer(), nullable=True),
            sa.Column("reason", sa.String(length=64), nullable=False),
            sa.Column("tiles_at_block", sa.Integer(), nullable=True),
            sa.Column("blocked_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("blocked_until", sa.DateTime(timezone=True), nullable=False),
            sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("released_by_user_id", sa.Integer(), nullable=True),
            sa.Column("release_reason", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(["released_by_user_id"], ["users.id"]),
            sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index(
            op.f("ix_map_tile_browser_blocks_blocked_until"),
            "map_tile_browser_blocks",
            ["blocked_until"],
            unique=False,
        )
        op.create_index(
            op.f("ix_map_tile_browser_blocks_browser_cookie_id"),
            "map_tile_browser_blocks",
            ["browser_cookie_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_map_tile_browser_blocks_reason"),
            "map_tile_browser_blocks",
            ["reason"],
            unique=False,
        )
        op.create_index(
            op.f("ix_map_tile_browser_blocks_released_by_user_id"),
            "map_tile_browser_blocks",
            ["released_by_user_id"],
            unique=False,
        )
        op.create_index(
            op.f("ix_map_tile_browser_blocks_user_id"),
            "map_tile_browser_blocks",
            ["user_id"],
            unique=False,
        )


def downgrade() -> None:
    """
    Drop durable Esri/map tile quota tables.

    Notes:
      Tables are dropped in dependency-safe reverse order. Indexes and
      constraints created on these tables are removed with the tables.
    """
    inspector = sa.inspect(op.get_bind())

    if inspector.has_table("map_tile_browser_blocks"):
        op.drop_table("map_tile_browser_blocks")
    if inspector.has_table("map_tile_usage_sessions"):
        op.drop_table("map_tile_usage_sessions")
    if inspector.has_table("map_tile_monthly_quota"):
        op.drop_table("map_tile_monthly_quota")
