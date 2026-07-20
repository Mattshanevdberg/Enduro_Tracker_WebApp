"""add device availability and category administration fields

Revision ID: c9e6a4b13d8f
Revises: b8d5f3a02c7e
Create Date: 2026-07-20 00:00:00.000000

This migration completes the durable schema required for device availability
and race-specific category administration, then removes the obsolete category
label from the race-independent Rider profile.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "c9e6a4b13d8f"
down_revision: Union[str, Sequence[str], None] = "b8d5f3a02c7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add availability/category administration state and remove Rider.category.

    Existing devices are deliberately marked returned and active. Existing
    category order is derived deterministically from category id within each
    race, while normalized names reuse the already-enforced lowercase identity.
    """
    op.add_column(
        "devices",
        sa.Column(
            "returned",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )
    op.add_column(
        "devices",
        sa.Column(
            "active",
            sa.Boolean(),
            server_default=sa.true(),
            nullable=False,
        ),
    )

    op.add_column(
        "categories",
        sa.Column("name_normalized", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "categories",
        sa.Column("display_order", sa.Integer(), nullable=True),
    )
    op.add_column(
        "categories",
        sa.Column(
            "archived",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
    )
    op.execute(sa.text("UPDATE categories SET name_normalized = lower(name)"))
    op.execute(
        sa.text(
            """
            WITH ordered_categories AS (
                SELECT
                    id,
                    row_number() OVER (
                        PARTITION BY race_id
                        ORDER BY id
                    ) AS generated_order
                FROM categories
            )
            UPDATE categories AS category
            SET display_order = ordered_categories.generated_order
            FROM ordered_categories
            WHERE ordered_categories.id = category.id
            """
        )
    )
    op.alter_column(
        "categories",
        "name_normalized",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.alter_column(
        "categories",
        "display_order",
        existing_type=sa.Integer(),
        server_default="1",
        nullable=False,
    )
    op.drop_index("ux_categories_race_name_ci", table_name="categories")
    op.create_unique_constraint(
        "ux_categories_race_name_normalized",
        "categories",
        ["race_id", "name_normalized"],
    )
    op.create_check_constraint(
        "ck_categories_name_normalized",
        "categories",
        "name_normalized = lower(name)",
    )
    op.create_check_constraint(
        "ck_categories_display_order_positive",
        "categories",
        "display_order >= 1",
    )
    op.create_index(
        "ix_categories_race_archive_order",
        "categories",
        ["race_id", "archived", "display_order"],
        unique=False,
    )

    op.drop_column("riders", "category")


def downgrade() -> None:
    """Restore the former Rider/category/device schema without removed data."""
    op.add_column(
        "riders",
        sa.Column("category", sa.String(length=64), nullable=True),
    )

    op.drop_index("ix_categories_race_archive_order", table_name="categories")
    op.drop_constraint(
        "ck_categories_display_order_positive",
        "categories",
        type_="check",
    )
    op.drop_constraint(
        "ck_categories_name_normalized",
        "categories",
        type_="check",
    )
    op.drop_constraint(
        "ux_categories_race_name_normalized",
        "categories",
        type_="unique",
    )
    op.create_index(
        "ux_categories_race_name_ci",
        "categories",
        [sa.text("race_id"), sa.text("lower(name)")],
        unique=True,
    )
    op.drop_column("categories", "archived")
    op.drop_column("categories", "display_order")
    op.drop_column("categories", "name_normalized")

    op.drop_column("devices", "active")
    op.drop_column("devices", "returned")
