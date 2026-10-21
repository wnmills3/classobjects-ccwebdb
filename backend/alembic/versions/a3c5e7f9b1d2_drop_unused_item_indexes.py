"""Drop the item's full-text and `attributes` indexes: nothing queries them.

The inventory search matches text with `ILIKE`, which a `to_tsvector` index
cannot serve, and nothing filters on `attributes` with a JSONB operator. Both
cost a write on every item save for no read.

Revision ID: a3c5e7f9b1d2
Revises: e5a9c3d7f1b2
Create Date: 2026-10-21 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "a3c5e7f9b1d2"
down_revision: str | None = "e5a9c3d7f1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Both indexes go."""
    op.drop_index("ix_inventory_item_fts", table_name="inventory_item")
    op.drop_index("ix_inventory_item_attributes", table_name="inventory_item")


def downgrade() -> None:
    """Both back, exactly as the baseline defines them."""
    op.execute(
        "CREATE INDEX ix_inventory_item_attributes ON public.inventory_item "
        "USING gin (attributes)"
    )
    op.execute(
        "CREATE INDEX ix_inventory_item_fts ON public.inventory_item USING gin "
        "(to_tsvector('english'::regconfig, "
        "(((source_title)::text || ' '::text) || description)))"
    )
