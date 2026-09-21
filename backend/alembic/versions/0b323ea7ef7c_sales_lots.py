"""Sales lots: a temporary grouping of items offered and sold as one thing.

Adds `sales_lot` (assembling/offered/sold/dissolved) and `sales_lot_item`, one
row per item's membership -- `released_at` rather than deletion, so which
coins were in a lot that sold stays part of the sale's record. The partial
unique index `uq_sales_lot_item_open` is the database guarantee that an item
is a member of at most one open lot at a time, the same shape as
`uq_offer_claim_active`.

Widens `listing`: `inventory_item_id` becomes nullable and a new
`sales_lot_id` is added, so a listing can name a lot instead of a single
item. `ck_listing_item_xor_lot` requires exactly one of the two, and
`ck_listing_lot_quantity_one` keeps a lot listing to a single unit -- a lot is
one specific group of specific coins, so there is only ever one of it.

Revision ID: 0b323ea7ef7c
Revises: c6908f789bf8
Create Date: 2026-09-21 09:16:38.613066

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0b323ea7ef7c"
down_revision: str | None = "c6908f789bf8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS = postgresql.ENUM(
    "assembling", "offered", "sold", "dissolved", name="sales_lot_status"
)


def upgrade() -> None:
    """Create `sales_lot` and `sales_lot_item`, and let a listing name a lot."""
    bind = op.get_bind()
    _STATUS.create(bind)

    op.create_table(
        "sales_lot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column(
            "description", sa.Text(), server_default=sa.text("''"), nullable=False
        ),
        sa.Column(
            "status",
            postgresql.ENUM(name="sales_lot_status", create_type=False),
            server_default="assembling",
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "sales_lot_item",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sales_lot_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("released_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_item.id"],
            name="fk_sales_lot_item_inventory_item_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["sales_lot_id"],
            ["sales_lot.id"],
            name="fk_sales_lot_item_sales_lot_id",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sales_lot_id", "inventory_item_id", name="uq_sales_lot_item_pair"
        ),
    )
    op.create_index(
        "ix_sales_lot_item_inventory_item_id",
        "sales_lot_item",
        ["inventory_item_id"],
    )
    op.create_index(
        "ix_sales_lot_item_sales_lot_id", "sales_lot_item", ["sales_lot_id"]
    )
    op.create_index(
        "uq_sales_lot_item_open",
        "sales_lot_item",
        ["inventory_item_id"],
        unique=True,
        postgresql_where=sa.text("released_at IS NULL"),
    )

    op.add_column("listing", sa.Column("sales_lot_id", sa.Integer(), nullable=True))
    op.alter_column(
        "listing", "inventory_item_id", existing_type=sa.Integer(), nullable=True
    )
    op.create_index("ix_listing_sales_lot_id", "listing", ["sales_lot_id"])
    op.create_foreign_key(
        "fk_listing_sales_lot_id",
        "listing",
        "sales_lot",
        ["sales_lot_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_listing_item_xor_lot",
        "listing",
        "(inventory_item_id IS NULL) <> (sales_lot_id IS NULL)",
    )
    op.create_check_constraint(
        "ck_listing_lot_quantity_one",
        "listing",
        "sales_lot_id IS NULL OR quantity_available <= 1",
    )


def downgrade() -> None:
    """Drop the lot constraints and column from listing, then the lot tables."""
    op.drop_constraint("ck_listing_lot_quantity_one", "listing", type_="check")
    op.drop_constraint("ck_listing_item_xor_lot", "listing", type_="check")
    # A lot listing has no item, so it cannot survive `inventory_item_id`
    # becoming NOT NULL again. This is enough for `test_migrations_round_trip`,
    # which runs this downgrade immediately after this same upgrade with no
    # lot listings yet in existence -- it is not a general-purpose cleanup.
    # `offer_claim.listing_id` and `sales_order_item.listing_id` are both
    # ON DELETE RESTRICT, so on a database where a lot listing was ever
    # claimed or sold, this DELETE raises instead of clearing the way, and a
    # real downgrade there needs a human decision, not a silent delete.
    op.execute("DELETE FROM listing WHERE sales_lot_id IS NOT NULL")
    op.alter_column(
        "listing", "inventory_item_id", existing_type=sa.Integer(), nullable=False
    )
    op.drop_constraint("fk_listing_sales_lot_id", "listing", type_="foreignkey")
    op.drop_index("ix_listing_sales_lot_id", table_name="listing")
    op.drop_column("listing", "sales_lot_id")

    op.drop_index("uq_sales_lot_item_open", table_name="sales_lot_item")
    op.drop_index("ix_sales_lot_item_sales_lot_id", table_name="sales_lot_item")
    op.drop_index("ix_sales_lot_item_inventory_item_id", table_name="sales_lot_item")
    op.drop_table("sales_lot_item")

    op.drop_table("sales_lot")
    op.execute("DROP TYPE IF EXISTS sales_lot_status")
