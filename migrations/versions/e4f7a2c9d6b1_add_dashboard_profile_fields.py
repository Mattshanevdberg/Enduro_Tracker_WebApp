"""add dashboard race lifecycle and profile media reference fields

Revision ID: e4f7a2c9d6b1
Revises: c9e6a4b13d8f
Create Date: 2026-07-22 00:00:00.000000

This migration replaces the ambiguous races.active Boolean with an explicit
dashboard lifecycle and adds the descriptive/media-reference values required by
the public race and rider cards. Image values are filenames/keys only; race art
remains developer-managed, while normalized Rider uploads live in persistent
media storage rather than PostgreSQL.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "e4f7a2c9d6b1"
down_revision: Union[str, Sequence[str], None] = "c9e6a4b13d8f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """
    Add dashboard/profile fields and convert active races to explicit statuses.

    Existing active races become upcoming so they remain public and open for
    entry. Existing inactive races become draft because the former Boolean did
    not contain enough information to safely infer that they were completed.
    Administrators can deliberately mark historical rows completed afterward.
    """
    op.add_column("races", sa.Column("location", sa.String(length=256), nullable=True))
    op.add_column(
        "races",
        sa.Column("logo_image_filename", sa.String(length=255), nullable=True),
    )
    op.add_column("races", sa.Column("status", sa.String(length=16), nullable=True))
    op.execute(
        sa.text(
            "UPDATE races SET status = CASE WHEN active THEN 'upcoming' ELSE 'draft' END"
        )
    )
    op.alter_column(
        "races",
        "status",
        existing_type=sa.String(length=16),
        server_default="draft",
        nullable=False,
    )
    op.create_check_constraint(
        "ck_races_status",
        "races",
        "status IN ('draft', 'upcoming', 'live', 'completed')",
    )
    op.drop_column("races", "active")

    op.add_column(
        "riders",
        sa.Column("profile_image_filename", sa.String(length=255), nullable=True),
    )


def downgrade() -> None:
    """
    Restore the former active Boolean and remove dashboard/profile metadata.

    Upcoming and live races map back to active. Draft and completed races map
    to inactive because the Boolean cannot preserve the four lifecycle values.
    """
    op.drop_column("riders", "profile_image_filename")

    op.add_column("races", sa.Column("active", sa.Boolean(), nullable=True))
    op.execute(
        sa.text(
            "UPDATE races SET active = CASE "
            "WHEN status IN ('upcoming', 'live') THEN true ELSE false END"
        )
    )
    op.alter_column(
        "races",
        "active",
        existing_type=sa.Boolean(),
        server_default=sa.true(),
        nullable=False,
    )
    op.drop_constraint("ck_races_status", "races", type_="check")
    op.drop_column("races", "status")
    op.drop_column("races", "logo_image_filename")
    op.drop_column("races", "location")
