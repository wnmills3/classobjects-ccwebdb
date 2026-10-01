"""Items: a piece with no date at all.

A gold bar or an undated round carries no year, and an empty year said only
"not recorded" -- so every such piece sat in "No year recorded" beside coins
whose date was simply never typed (owner, 2026-10-01). `no_date` records that
the piece has none; a check constraint keeps it from holding a year as well.

Every existing item gets false, so the constraint holds on the rows already
there and nothing changes until the owner marks a piece.

Revision ID: f6c9e4a1b3d5
Revises: e5b8d3f0a2c4
Create Date: 2026-10-01 15:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "f6c9e4a1b3d5"
down_revision: str | None = "e5b8d3f0a2c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add `no_date`, false for every item, and its check constraint."""
    op.add_column(
        "inventory_item",
        sa.Column(
            "no_date", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
    )
    op.create_check_constraint(
        "ck_inventory_item_no_date_no_years",
        "inventory_item",
        "NOT no_date OR (year_start IS NULL AND year_end IS NULL)",
    )


def downgrade() -> None:
    """Drop the constraint and the column."""
    op.drop_constraint(
        "ck_inventory_item_no_date_no_years", "inventory_item", type_="check"
    )
    op.drop_column("inventory_item", "no_date")
