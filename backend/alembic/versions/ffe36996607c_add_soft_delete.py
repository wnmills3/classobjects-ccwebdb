"""add soft delete

`deleted_at` on `inventory_item`, and `AND i.deleted_at IS NULL` in all four
views. A view stores its source columns by reference, so the views are dropped
first and recreated from the current definitions afterwards.

Revision ID: ffe36996607c
Revises: 3f3559d49a9f

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.models.views import CREATE_VIEWS, DROP_VIEWS, create_views

revision: str = "ffe36996607c"
down_revision: str | None = "3f3559d49a9f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in DROP_VIEWS:
        op.execute(statement)

    op.add_column(
        "inventory_item",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_inventory_item_deleted_at", "inventory_item", ["deleted_at"])

    for statement in CREATE_VIEWS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DROP_VIEWS:
        op.execute(statement)

    op.drop_index("ix_inventory_item_deleted_at", table_name="inventory_item")
    op.drop_column("inventory_item", "deleted_at")

    # The pre-soft-delete view text, so the downgrade leaves a consistent
    # database rather than four views naming a dropped column.
    for statement in create_views(soft_delete=False):
        op.execute(statement)
