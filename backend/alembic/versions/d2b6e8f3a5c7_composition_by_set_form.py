"""Compositions: a row may describe only the coins of one set.

A dime of 1965 or later was read as copper-nickel clad, whatever it was:
but the dimes, quarters and halves of a Silver Proof Set from 1992 are 90%
silver, and .999 from 2019, and the 1976 set's quarter and half 40%.
`composition.set_form_id` names the set a row describes; the span's
uniqueness takes it in, with nulls not distinct as before.

Only adds a column every existing row leaves empty, so the upgrade cannot
fail on existing rows. The rows themselves come from
`backend/data/reference/composition.json` through `app.seeding load`. A
downgrade refuses while any row names a set: those rows have no place in
the older table.

Revision ID: d2b6e8f3a5c7
Revises: a1f4c7e2b9d6
Create Date: 2026-10-14 21:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d2b6e8f3a5c7"
down_revision: str | None = "a1f4c7e2b9d6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNIQUE = "uq_composition_span"


def upgrade() -> None:
    """Add the set form and take it into the span's uniqueness."""
    op.add_column(
        "composition", sa.Column("set_form_id", sa.Integer(), nullable=True)
    )
    op.create_foreign_key(
        "composition_set_form_id_fkey",
        "composition",
        "set_form",
        ["set_form_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_composition_set_form_id", "composition", ["set_form_id"])
    op.drop_constraint(_UNIQUE, "composition", type_="unique")
    op.create_unique_constraint(
        _UNIQUE,
        "composition",
        [
            "denomination_id",
            "series_id",
            "set_form_id",
            "country_id",
            "year_from",
            "metal_id",
        ],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """The table as it was, refused while a row names a set."""
    named = (
        op.get_bind()
        .execute(
            sa.text("SELECT count(*) FROM composition WHERE set_form_id IS NOT NULL")
        )
        .scalar_one()
    )
    if named:
        raise RuntimeError(
            f"{named} composition row(s) name a set; delete them before downgrading"
        )
    op.drop_constraint(_UNIQUE, "composition", type_="unique")
    op.create_unique_constraint(
        _UNIQUE,
        "composition",
        ["denomination_id", "series_id", "country_id", "year_from", "metal_id"],
        postgresql_nulls_not_distinct=True,
    )
    op.drop_index("ix_composition_set_form_id", table_name="composition")
    op.drop_constraint(
        "composition_set_form_id_fkey", "composition", type_="foreignkey"
    )
    op.drop_column("composition", "set_form_id")
