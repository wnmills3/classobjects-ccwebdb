"""Friedberg identity: signatures and seal tell two types apart too.

`uq_friedberg_number_identity` became NULLS NOT DISTINCT in `a7d3e5f19c42`,
so a missing letter or district counts as a match. It did not include
`signature_combination_id` or `seal_color_id`, and many series differ by
nothing but who signed them -- or by a wartime brown or yellow seal beside
the regular blue -- so a second, genuinely different type was refused as a
duplicate. Both columns join the index. Widening a unique index can only
admit rows the old one refused, so no existing data can fail this.

Revision ID: b8e4f2a61d37
Revises: a7d3e5f19c42
Create Date: 2026-09-23 16:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4f2a61d37"
down_revision: str | None = "a7d3e5f19c42"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_friedberg_number_identity"
_KNOWN = sa.text(
    "denomination_id IS NOT NULL AND series_year IS NOT NULL "
    "AND note_type_id IS NOT NULL"
)
_BEFORE = [
    "denomination_id",
    "series_year",
    "series_letter",
    "note_type_id",
    "district_letter",
    "web_press",
]
_AFTER = [*_BEFORE, "signature_combination_id", "seal_color_id"]


def _rebuild(columns: list[str]) -> None:
    op.drop_index(_INDEX, table_name="friedberg_number")
    op.create_index(
        _INDEX,
        "friedberg_number",
        columns,
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=_KNOWN,
    )


def upgrade() -> None:
    """Add the signatures and the seal to the identifying tuple."""
    _rebuild(_AFTER)


def downgrade() -> None:
    """Back to the tuple without them (fails if rows now differ only by them)."""
    _rebuild(_BEFORE)
