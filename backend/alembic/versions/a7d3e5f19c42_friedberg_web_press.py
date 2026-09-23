"""Friedberg catalogue: record web press, and make the identity index bite.

- `friedberg_number.web_press`: printed on a web press rather than sheet-fed,
  NULL when not known. The two printings of one series, district and
  denomination are different types with different numbers, so it joins the
  identifying tuple.
- `uq_friedberg_number_identity` is rebuilt with that column and with
  NULLS NOT DISTINCT. Postgres treats NULLs as distinct in a unique index by
  default, and most series have no letter, so the old index never fired for
  them: the same type could be recorded twice under two numbers (measured
  2026-09-23). A database that already holds such duplicates fails here,
  loudly -- merge them first; live held one row on 2026-09-23.

Revision ID: a7d3e5f19c42
Revises: cb1bb956f50b
Create Date: 2026-09-23 13:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3e5f19c42"
down_revision: str | None = "cb1bb956f50b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX = "uq_friedberg_number_identity"
_KNOWN = sa.text(
    "denomination_id IS NOT NULL AND series_year IS NOT NULL "
    "AND note_type_id IS NOT NULL"
)
_OLD_COLUMNS = [
    "denomination_id",
    "series_year",
    "series_letter",
    "note_type_id",
    "district_letter",
]


def upgrade() -> None:
    """Add the column, then rebuild the identity index around it."""
    op.add_column(
        "friedberg_number", sa.Column("web_press", sa.Boolean(), nullable=True)
    )
    op.drop_index(_INDEX, table_name="friedberg_number")
    op.create_index(
        _INDEX,
        "friedberg_number",
        [*_OLD_COLUMNS, "web_press"],
        unique=True,
        postgresql_nulls_not_distinct=True,
        postgresql_where=_KNOWN,
    )


def downgrade() -> None:
    """Restore the old index, then drop the column."""
    op.drop_index(_INDEX, table_name="friedberg_number")
    op.create_index(
        _INDEX,
        "friedberg_number",
        _OLD_COLUMNS,
        unique=True,
        postgresql_where=_KNOWN,
    )
    op.drop_column("friedberg_number", "web_press")
