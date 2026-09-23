"""Item field change log: who changed which field of an item, and when.

`item_field_change` gets one row per field an edit (`PATCH /inventory/{id}`
or the bulk edit) actually changes, with the old and new values as the item
editor sees them, the user and the time. The editor names who made a change
when it warns about a field changed elsewhere. New and empty: edits before
this migration are not recorded.

Revision ID: c5a9d3e8f174
Revises: b8e4f2a61d37
Create Date: 2026-09-23 18:40:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c5a9d3e8f174"
down_revision: str | None = "b8e4f2a61d37"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the change log and its lookup index."""
    op.create_table(
        "item_field_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("field_name", sa.String(length=64), nullable=False),
        sa.Column("old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("changed_by_id", sa.Integer(), nullable=True),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_item.id"],
            name="item_field_change_inventory_item_id_fkey",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_id"],
            ["users.id"],
            name="item_field_change_changed_by_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="item_field_change_pkey"),
    )
    op.create_index(
        "ix_item_field_change_item_field_at",
        "item_field_change",
        ["inventory_item_id", "field_name", "changed_at"],
    )


def downgrade() -> None:
    """Drop the change log."""
    op.drop_index("ix_item_field_change_item_field_at", table_name="item_field_change")
    op.drop_table("item_field_change")
