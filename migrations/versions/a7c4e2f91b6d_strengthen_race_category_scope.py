"""strengthen race and category scope

Revision ID: a7c4e2f91b6d
Revises: 4578a2e08ba3
Create Date: 2026-07-16 00:00:00.000000

The system design resolves a race entry through RaceRider -> Category -> Route
-> Race. This migration retains that navigation while repeating race_id on
Category and RaceRider so PostgreSQL can enforce same-race references and
race-level rider/device uniqueness without relying on application-only joins.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c4e2f91b6d"
down_revision: Union[str, Sequence[str], None] = "4578a2e08ba3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _violation_count(statement: str) -> int:
    """
    Count rows returned by one migration precondition query.

    Input Args:
      statement: SELECT statement whose rows each represent one violation.

    Output:
      Number of violating rows currently present in the database.

    Notes:
      Keeping the checks inside the migration prevents a future environment
      with different data from silently applying constraints to ambiguous race
      assignments, even though the current development audit is clean.
    """
    result = op.get_bind().execute(
        sa.text(f"SELECT COUNT(*) FROM ({statement}) AS violations")
    )
    return int(result.scalar_one())


def _require_clean(statement: str, message: str) -> None:
    """
    Abort the migration when a race-scope precondition is not satisfied.

    Input Args:
      statement: SELECT statement returning violating rows.
      message: actionable error explaining the required data cleanup.

    Output:
      None when the query returns no rows.

    Raises:
      RuntimeError when one or more conflicting rows exist.
    """
    if _violation_count(statement):
        raise RuntimeError(message)


def upgrade() -> None:
    """
    Add database-enforced race scope to categories and race entries.

    The upgrade backfills both new race_id columns from the existing durable
    Category -> Route -> Race chain before making them non-null. Composite
    foreign keys then guarantee that the repeated race ids cannot drift.
    """
    # PostgreSQL requires the exact composite target to be unique before
    # categories can reference a route and its owning race together.
    op.create_unique_constraint(
        "ux_route_id_race_id",
        "route",
        ["id", "race_id"],
    )

    op.add_column(
        "categories",
        sa.Column("race_id", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE categories AS category
            SET race_id = route.race_id
            FROM route
            WHERE route.id = category.route_id
            """
        )
    )
    _require_clean(
        "SELECT id FROM categories WHERE race_id IS NULL",
        "Cannot scope categories: one or more categories have no owning race.",
    )
    _require_clean(
        """
        SELECT id
        FROM categories
        WHERE name <> trim(name) OR length(name) = 0
        """,
        "Cannot enforce category names: trim whitespace and fill blank names first.",
    )
    _require_clean(
        """
        SELECT race_id, lower(trim(name))
        FROM categories
        GROUP BY race_id, lower(trim(name))
        HAVING COUNT(*) > 1
        """,
        "Cannot enforce category uniqueness: duplicate names exist within a race.",
    )

    op.alter_column(
        "categories",
        "race_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_index(
        "ix_categories_race_id",
        "categories",
        ["race_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_categories_race_id_races",
        "categories",
        "races",
        ["race_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "categories_route_id_fkey",
        "categories",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_categories_route_race",
        "categories",
        "route",
        ["route_id", "race_id"],
        ["id", "race_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "ux_categories_id_race_id",
        "categories",
        ["id", "race_id"],
    )
    op.drop_constraint(
        "ux_route_category_name",
        "categories",
        type_="unique",
    )
    op.create_check_constraint(
        "ck_categories_name_trimmed_nonempty",
        "categories",
        "name = trim(name) AND length(name) > 0",
    )
    op.create_index(
        "ux_categories_race_name_ci",
        "categories",
        [sa.text("race_id"), sa.text("lower(name)")],
        unique=True,
    )

    op.add_column(
        "race_riders",
        sa.Column("race_id", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE race_riders AS race_rider
            SET race_id = category.race_id
            FROM categories AS category
            WHERE category.id = race_rider.category_id
            """
        )
    )
    _require_clean(
        "SELECT id FROM race_riders WHERE race_id IS NULL",
        "Cannot scope race riders: one or more entries have no owning race.",
    )
    _require_clean(
        """
        SELECT race_id, rider_id
        FROM race_riders
        GROUP BY race_id, rider_id
        HAVING COUNT(*) > 1
        """,
        "Cannot enforce one rider entry per race: duplicate entries exist.",
    )
    _require_clean(
        """
        SELECT race_id, device_id
        FROM race_riders
        GROUP BY race_id, device_id
        HAVING COUNT(*) > 1
        """,
        "Cannot enforce one device per race: duplicate assignments exist.",
    )

    op.alter_column(
        "race_riders",
        "race_id",
        existing_type=sa.Integer(),
        nullable=False,
    )
    op.create_index(
        "ix_race_riders_race_id",
        "race_riders",
        ["race_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_race_riders_race_id_races",
        "race_riders",
        "races",
        ["race_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.drop_constraint(
        "race_riders_category_id_fkey",
        "race_riders",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_race_riders_category_race",
        "race_riders",
        "categories",
        ["category_id", "race_id"],
        ["id", "race_id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "ux_race_riders_race_rider",
        "race_riders",
        ["race_id", "rider_id"],
    )
    op.create_unique_constraint(
        "ux_race_riders_race_device",
        "race_riders",
        ["race_id", "device_id"],
    )


def downgrade() -> None:
    """
    Restore the previous indirect race scope and per-route category names.

    Existing Category and RaceRider rows keep their original route/category
    links. Only the repeated race_id fields and their stronger constraints are
    removed.
    """
    op.drop_constraint(
        "ux_race_riders_race_device",
        "race_riders",
        type_="unique",
    )
    op.drop_constraint(
        "ux_race_riders_race_rider",
        "race_riders",
        type_="unique",
    )
    op.drop_constraint(
        "fk_race_riders_category_race",
        "race_riders",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_race_riders_race_id_races",
        "race_riders",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "race_riders_category_id_fkey",
        "race_riders",
        "categories",
        ["category_id"],
        ["id"],
    )
    op.drop_index("ix_race_riders_race_id", table_name="race_riders")
    op.drop_column("race_riders", "race_id")

    op.drop_index("ux_categories_race_name_ci", table_name="categories")
    op.drop_constraint(
        "ck_categories_name_trimmed_nonempty",
        "categories",
        type_="check",
    )
    op.drop_constraint(
        "fk_categories_route_race",
        "categories",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_categories_race_id_races",
        "categories",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "categories_route_id_fkey",
        "categories",
        "route",
        ["route_id"],
        ["id"],
    )
    op.create_unique_constraint(
        "ux_route_category_name",
        "categories",
        ["route_id", "name"],
    )
    op.drop_constraint(
        "ux_categories_id_race_id",
        "categories",
        type_="unique",
    )
    op.drop_index("ix_categories_race_id", table_name="categories")
    op.drop_column("categories", "race_id")

    op.drop_constraint(
        "ux_route_id_race_id",
        "route",
        type_="unique",
    )
