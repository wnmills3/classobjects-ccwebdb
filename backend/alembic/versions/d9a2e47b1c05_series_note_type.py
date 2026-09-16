"""A note design's class, for Series 1929 National Bank Notes.

The Series 1929 National Bank Notes and Federal Reserve Bank Notes share
their series year, denominations and brown seal; only the class tells them
apart. A design may now name its note class: it is never assigned to a note
recorded as another class, and a note recorded as that class is evidence for
it. The design itself is seeded, not added here.

Revision ID: d9a2e47b1c05
Revises: c3f8a61d7e24
Create Date: 2026-09-16 14:15:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "d9a2e47b1c05"
down_revision: str | None = "c3f8a61d7e24"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add `series.note_type_id`."""
    op.add_column("series", sa.Column("note_type_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_series_note_type_id",
        "series",
        "note_type",
        ["note_type_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Drop `series.note_type_id`."""
    op.drop_constraint("fk_series_note_type_id", "series", type_="foreignkey")
    op.drop_column("series", "note_type_id")
