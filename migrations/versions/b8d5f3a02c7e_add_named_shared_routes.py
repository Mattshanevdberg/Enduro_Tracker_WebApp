"""add named shared race routes

Revision ID: b8d5f3a02c7e
Revises: a7c4e2f91b6d
Create Date: 2026-07-16 00:00:00.000000

The system design gives organisers race-level route management. This migration
adds a durable descriptive Route.name so several categories can intentionally
select the same course while multiple courses within one race remain clear.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b8d5f3a02c7e"
down_revision: Union[str, Sequence[str], None] = "a7c4e2f91b6d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add, backfill, and constrain descriptive race-route names.

    Existing routes use the alphabetically first linked category followed by
    " Route". Routes without a category use their globally unique route id.
    Step 2's per-race category-name constraint makes the category-derived names
    unique within a race, allowing the new index to be applied safely.
    """
    op.add_column(
        "route",
        sa.Column("name", sa.String(length=128), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE route AS race_route
            SET name = COALESCE(
                (
                    SELECT MIN(category.name) || ' Route'
                    FROM categories AS category
                    WHERE category.route_id = race_route.id
                ),
                'Route ' || race_route.id::text
            )
            """
        )
    )
    op.alter_column(
        "route",
        "name",
        existing_type=sa.String(length=128),
        nullable=False,
    )
    op.create_check_constraint(
        "ck_route_name_trimmed_nonempty",
        "route",
        "name = trim(name) AND length(name) > 0",
    )
    op.create_index(
        "ux_route_race_name_ci",
        "route",
        [sa.text("race_id"), sa.text("lower(name)")],
        unique=True,
    )


def downgrade() -> None:
    """Remove descriptive route names and their supporting constraints."""
    op.drop_index("ux_route_race_name_ci", table_name="route")
    op.drop_constraint(
        "ck_route_name_trimmed_nonempty",
        "route",
        type_="check",
    )
    op.drop_column("route", "name")
