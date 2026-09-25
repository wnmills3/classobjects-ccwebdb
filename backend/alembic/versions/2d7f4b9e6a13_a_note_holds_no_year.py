"""A note holds no year of its own: its series year is its year.

Owner, 2026-09-25: the item's years exist for coins and for lots of mixed
years. A banknote's year is its series year, and storing it a second time
only let the two disagree (three did). This empties the years of every note;
what shows or searches a note's year reads `currency_detail.series_year`.
Any review or derivation mark on those years goes with them.

The downgrade puts the series year back as the note's year: the old
arrangement, less the three values that disagreed.

Revision ID: 2d7f4b9e6a13
Revises: 8c1e5d2a9f40
Create Date: 2026-09-25 00:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "2d7f4b9e6a13"
down_revision: str | None = "8c1e5d2a9f40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOTES = "SELECT inventory_item_id FROM currency_detail"


def upgrade() -> None:
    """Empty every note's years, and any mark on them."""
    for table in ("item_field_review", "item_field_source"):
        op.execute(
            f"DELETE FROM {table} WHERE field_name IN ('year_start', 'year_end') "
            f"AND inventory_item_id IN ({_NOTES})"
        )
    op.execute(
        "UPDATE inventory_item SET year_start = NULL, year_end = NULL "
        f"WHERE id IN ({_NOTES}) AND (year_start IS NOT NULL OR year_end IS NOT NULL)"
    )


def downgrade() -> None:
    """Give every note its series year as its year again."""
    op.execute(
        "UPDATE inventory_item i SET year_start = cd.series_year, "
        "year_end = cd.series_year FROM currency_detail cd "
        "WHERE cd.inventory_item_id = i.id"
    )
