"""rename valuation_snapshot.storage_quantity to piece_count

The rename in b78d71343405 covered `inventory_item` and stopped there, leaving
`valuation_snapshot` with the old name for the same idea. Two names for one
thing is how a join gets written against the wrong column, so this finishes
the job.

Separate revision rather than an edit to b78d71343405, which is already
applied: a change to an applied migration never runs.

`alter_column` rather than drop-and-add even though the table is empty today.
The table will not always be empty, and the migration is the record of intent
-- one that would discard data is the wrong record even when it happens to
discard none.

Revision ID: c1a4f0b27e93
Revises: b78d71343405
Create Date: 2026-09-08 11:20:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c1a4f0b27e93"
down_revision: str | None = "b78d71343405"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "valuation_snapshot", "storage_quantity", new_column_name="piece_count"
    )


def downgrade() -> None:
    op.alter_column(
        "valuation_snapshot", "piece_count", new_column_name="storage_quantity"
    )
