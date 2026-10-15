"""Metals: a plated or layered metal names the metal it is read as.

A gold-plated silver coin was a metal no composition names, so the
classifier took it for a coin its composition rules out and gave it no
weights. `metal.base_metal_id` names the metal a composition reads it as:
gold-plated silver and silver-copper clad are silver, 24K gold-plated clad
is clad.

Those three were added on the live database by hand, not seeded; the
upgrade gives each its base where it exists, by code, and touches no other
row. A downgrade drops the column.

Revision ID: c4e9a2f7b1d8
Revises: d2b6e8f3a5c7
Create Date: 2026-10-15 11:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4e9a2f7b1d8"
down_revision: str | None = "d2b6e8f3a5c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The hand-added metals and what each is read as, by code.
_BASES = {
    "gold_plated_silver": "silver",
    "silver_copper_clad": "silver",
    "24k_gold_plated_clad": "clad",
}


def upgrade() -> None:
    """Add the base metal, and set it on the plated metals that exist."""
    op.add_column("metal", sa.Column("base_metal_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "metal_base_metal_id_fkey",
        "metal",
        "metal",
        ["base_metal_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_metal_base_metal_id", "metal", ["base_metal_id"])
    op.create_check_constraint(
        "ck_metal_base_is_another",
        "metal",
        "base_metal_id IS NULL OR base_metal_id <> id",
    )
    for code, base in _BASES.items():
        op.get_bind().execute(
            sa.text(
                "UPDATE metal SET base_metal_id = "
                "(SELECT id FROM metal WHERE code = :base) WHERE code = :code"
            ),
            {"code": code, "base": base},
        )


def downgrade() -> None:
    """Drop the base metal."""
    op.drop_constraint("ck_metal_base_is_another", "metal", type_="check")
    op.drop_index("ix_metal_base_metal_id", table_name="metal")
    op.drop_constraint("metal_base_metal_id_fkey", "metal", type_="foreignkey")
    op.drop_column("metal", "base_metal_id")
