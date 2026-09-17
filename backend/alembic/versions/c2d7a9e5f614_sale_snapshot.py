"""Order lines keep a snapshot of the item as sold.

An item can be corrected, returned and sold again; the snapshot keeps each
sale as it was (app.sale_snapshot). Lines made before this have none.

Revision ID: c2d7a9e5f614
Revises: b8e1f4a2c975
Create Date: 2026-09-17 10:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c2d7a9e5f614"
down_revision: str | None = "b8e1f4a2c975"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add item_snapshot and snapshot_at to sales_order_item."""
    op.add_column(
        "sales_order_item",
        sa.Column(
            "item_snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
    )
    op.add_column(
        "sales_order_item",
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    """Drop them; the snapshots are lost."""
    op.drop_column("sales_order_item", "snapshot_at")
    op.drop_column("sales_order_item", "item_snapshot")
