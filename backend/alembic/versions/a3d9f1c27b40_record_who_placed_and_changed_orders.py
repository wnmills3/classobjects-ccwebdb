"""record who placed and changed orders

An administrator may now place and edit orders for a customer, so an order
records the account that entered it, carries an optimistic-concurrency
version, and keeps a row per change in `sales_order_change`.

Existing orders get `version = 1` and no placer: nobody recorded who placed
them.

Revision ID: a3d9f1c27b40
Revises: e4b7a1c95d20
Create Date: 2026-09-15 12:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a3d9f1c27b40"
down_revision: str | None = "e4b7a1c95d20"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = (
    "placed",
    "line_added",
    "line_removed",
    "quantity",
    "unit_price",
    "customer",
    "notes",
    "status",
    "total",
)


def upgrade() -> None:
    """Add the placer and version to orders, and the change history table."""
    postgresql.ENUM(*_KINDS, name="sales_order_change_kind").create(op.get_bind())

    op.add_column(
        "sales_order", sa.Column("placed_by_id", sa.Integer(), nullable=True)
    )
    # Named: autogenerate emits None, and drop_constraint(None) cannot run.
    op.create_foreign_key(
        "fk_sales_order_placed_by_id",
        "sales_order",
        "users",
        ["placed_by_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.add_column(
        "sales_order",
        sa.Column(
            "version", sa.Integer(), server_default=sa.text("1"), nullable=False
        ),
    )

    op.create_table(
        "sales_order_change",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), nullable=False),
        sa.Column("changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("changed_by_id", sa.Integer(), nullable=True),
        sa.Column(
            "change",
            postgresql.ENUM(
                *_KINDS, name="sales_order_change_kind", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("listing_id", sa.Integer(), nullable=True),
        sa.Column("from_value", sa.Text(), nullable=True),
        sa.Column("to_value", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["sales_order_id"],
            ["sales_order.id"],
            name="fk_sales_order_change_sales_order_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["changed_by_id"],
            ["users.id"],
            name="fk_sales_order_change_changed_by_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_sales_order_change_listing_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_sales_order_change_sales_order_id"),
        "sales_order_change",
        ["sales_order_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the history before the type it uses, then the order columns."""
    op.drop_index(
        op.f("ix_sales_order_change_sales_order_id"), table_name="sales_order_change"
    )
    op.drop_table("sales_order_change")
    op.execute("DROP TYPE IF EXISTS sales_order_change_kind")
    op.drop_column("sales_order", "version")
    op.drop_constraint(
        "fk_sales_order_placed_by_id", "sales_order", type_="foreignkey"
    )
    op.drop_column("sales_order", "placed_by_id")
