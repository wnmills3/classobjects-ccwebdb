"""Field confirmations go: the table nobody fills.

`item_field_review` held one row per field a person had marked as checked
against the piece itself. The console no longer offers the mark and nothing
reads it, so the table is dropped with whatever it holds.

Revision ID: b8e4a2d0f6c3
Revises: a7d3f1c9e5b2
Create Date: 2026-10-07 22:10:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e4a2d0f6c3"
down_revision: str | None = "a7d3f1c9e5b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The table goes, its index and sequence with it."""
    op.drop_table("item_field_review")


def downgrade() -> None:
    """The table as the baseline made it, empty: its rows are not brought back."""
    op.create_table(
        "item_field_review",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "inventory_item_id",
            sa.Integer(),
            sa.ForeignKey(
                "inventory_item.id",
                name="item_field_review_inventory_item_id_fkey",
                ondelete="CASCADE",
            ),
            nullable=False,
        ),
        sa.Column("field_name", sa.String(64), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "reviewed_by_id",
            sa.Integer(),
            sa.ForeignKey(
                "users.id",
                name="item_field_review_reviewed_by_id_fkey",
                ondelete="SET NULL",
            ),
            nullable=True,
        ),
        sa.UniqueConstraint(
            "inventory_item_id", "field_name", name="uq_item_field_review"
        ),
    )
    op.create_index(
        "ix_item_field_review_inventory_item_id",
        "item_field_review",
        ["inventory_item_id"],
    )
