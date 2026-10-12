"""Compositions: a row may describe one design series.

A composition was found by denomination and year alone, which cannot tell a
Morgan dollar of 2021 from a Sacagawea dollar of the same year and has
nothing to go by for a Silver Eagle, whose series carries no denomination.
`composition.series_id` names the design a row describes, `denomination_id`
becomes optional beside it, and a row must name one or the other.

The span's uniqueness takes the series in, with nulls not distinct, so two
rows for one denomination and year can differ by design and a re-load still
cannot add a span twice.

Only loosens what a row may hold, so the upgrade cannot fail on existing
rows. A downgrade refuses while any row names a series: those rows have no
place in the older table.

Revision ID: c9f5b1e3a7d4
Revises: b8e4a2d0f6c3
Create Date: 2026-10-12 12:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c9f5b1e3a7d4"
down_revision: str | None = "b8e4a2d0f6c3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UNIQUE = "uq_composition_span"
_NAMES_A_COIN = "ck_composition_names_a_coin"


def upgrade() -> None:
    """Add the series, free the denomination, and widen the span's uniqueness."""
    op.add_column("composition", sa.Column("series_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "composition_series_id_fkey",
        "composition",
        "series",
        ["series_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_composition_series_id", "composition", ["series_id"])
    op.alter_column("composition", "denomination_id", nullable=True)
    op.create_check_constraint(
        _NAMES_A_COIN,
        "composition",
        "denomination_id IS NOT NULL OR series_id IS NOT NULL",
    )
    op.drop_constraint(_UNIQUE, "composition", type_="unique")
    op.create_unique_constraint(
        _UNIQUE,
        "composition",
        ["denomination_id", "series_id", "country_id", "year_from", "metal_id"],
        postgresql_nulls_not_distinct=True,
    )


def downgrade() -> None:
    """The table as it was, refused while a row names a series."""
    named = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM composition WHERE series_id IS NOT NULL"))
        .scalar_one()
    )
    if named:
        raise RuntimeError(
            f"{named} composition row(s) name a series; delete them before downgrading"
        )
    op.drop_constraint(_UNIQUE, "composition", type_="unique")
    op.create_unique_constraint(
        _UNIQUE,
        "composition",
        ["denomination_id", "country_id", "year_from", "metal_id"],
    )
    op.drop_constraint(_NAMES_A_COIN, "composition", type_="check")
    op.alter_column("composition", "denomination_id", nullable=False)
    op.drop_index("ix_composition_series_id", table_name="composition")
    op.drop_constraint("composition_series_id_fkey", "composition", type_="foreignkey")
    op.drop_column("composition", "series_id")
