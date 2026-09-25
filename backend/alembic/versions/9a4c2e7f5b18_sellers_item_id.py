"""The seller's own id for the listing an item was bought from.

The owner, 2026-09-25: eBay's item number, kept on each item, so a lot's
pieces stay traceable to their listing and a purchase can be matched to the
seller's order records. Added empty; `app.ebay_orders` fills it from each
item's listing link.

Revision ID: 9a4c2e7f5b18
Revises: 6e0a3c8d1b57
Create Date: 2026-09-25 01:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9a4c2e7f5b18"
down_revision: str | None = "6e0a3c8d1b57"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The column, and an index to find an item by it."""
    op.add_column(
        "inventory_item", sa.Column("sellers_item_id", sa.String(64), nullable=True)
    )
    op.create_index(
        "ix_inventory_item_sellers_item_id", "inventory_item", ["sellers_item_id"]
    )


def downgrade() -> None:
    """Both go."""
    op.drop_index("ix_inventory_item_sellers_item_id", table_name="inventory_item")
    op.drop_column("inventory_item", "sellers_item_id")
