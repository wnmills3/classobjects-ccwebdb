"""Friedberg numbers: a star note and a mule are types of their own.

A mule differs from its plain type only by its plates, and a star note only
by its serial -- neither is a catalog fact -- so `uq_friedberg_number_identity`
held `3007-E`, `3007-Em` and `3007-E*` to be one type, and whichever was
recorded first refused the others. Two columns generated
from `fr_number` (`is_star`, `is_mule`, the rule `app.fr_format.fr_traits`
reads) join the index.

Only loosens the index, so the upgrade cannot fail on existing rows. A
downgrade can, once a type and its mule or star are both recorded: that is
the state the old index refused.

Revision ID: d4a7c2e9f1b3
Revises: c3e8a1f05b72
Create Date: 2026-09-30 23:55:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7c2e9f1b3"
down_revision: str | None = "c3e8a1f05b72"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_friedberg_number_identity"
_COLUMNS = [
    "denomination_id",
    "series_year",
    "series_letter",
    "note_type_id",
    "district_letter",
    "web_press",
    "signature_combination_id",
    "seal_color_id",
    "printing_facility",
]
_WHERE = sa.text(
    "denomination_id IS NOT NULL AND series_year IS NOT NULL "
    "AND note_type_id IS NOT NULL"
)


def _create_index(columns: list[str]) -> None:
    op.create_index(
        _INDEX,
        "friedberg_number",
        columns,
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=_WHERE,
    )


def upgrade() -> None:
    """Add the generated columns and widen the identity index with them."""
    op.add_column(
        "friedberg_number",
        sa.Column(
            "is_star",
            sa.Boolean(),
            sa.Computed("fr_number LIKE '%*'", persisted=True),
            nullable=False,
        ),
    )
    op.add_column(
        "friedberg_number",
        sa.Column(
            "is_mule",
            sa.Boolean(),
            sa.Computed("fr_number ~ 'm[*]?$'", persisted=True),
            nullable=False,
        ),
    )
    op.drop_index(_INDEX, table_name="friedberg_number")
    _create_index([*_COLUMNS, "is_star", "is_mule"])


def downgrade() -> None:
    """Narrow the index back and drop the columns."""
    op.drop_index(_INDEX, table_name="friedberg_number")
    _create_index(_COLUMNS)
    op.drop_column("friedberg_number", "is_mule")
    op.drop_column("friedberg_number", "is_star")
