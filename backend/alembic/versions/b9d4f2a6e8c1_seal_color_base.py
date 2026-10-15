"""Seal colors: a shade names the color it is read as.

A light green seal is a green Federal Reserve seal of a lighter printing,
but the issue facts know only green, so every light green note was
reported as disagreeing with its series. `seal_color.base_seal_color_id`
names the color a shade is read as, set on the Vocabularies page as
"Read as".

Light Green was added on the live database by hand, not seeded; the
upgrade reads it as green where both exist, by code, and touches no other
row. A downgrade drops the column.

Revision ID: b9d4f2a6e8c1
Revises: e7b3d9a1c5f2
Create Date: 2026-10-15 12:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b9d4f2a6e8c1"
down_revision: str | None = "e7b3d9a1c5f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the base color, and read light green as green where both exist."""
    op.add_column(
        "seal_color", sa.Column("base_seal_color_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "seal_color_base_seal_color_id_fkey",
        "seal_color",
        "seal_color",
        ["base_seal_color_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index(
        "ix_seal_color_base_seal_color_id", "seal_color", ["base_seal_color_id"]
    )
    op.create_check_constraint(
        "ck_seal_color_base_is_another",
        "seal_color",
        "base_seal_color_id IS NULL OR base_seal_color_id <> id",
    )
    op.get_bind().execute(
        sa.text(
            "UPDATE seal_color SET base_seal_color_id ="
            " (SELECT id FROM seal_color WHERE code = 'green')"
            " WHERE code = 'light_green'"
        )
    )


def downgrade() -> None:
    """Drop the base color."""
    op.drop_constraint("ck_seal_color_base_is_another", "seal_color", type_="check")
    op.drop_index("ix_seal_color_base_seal_color_id", table_name="seal_color")
    op.drop_constraint(
        "seal_color_base_seal_color_id_fkey", "seal_color", type_="foreignkey"
    )
    op.drop_column("seal_color", "base_seal_color_id")
