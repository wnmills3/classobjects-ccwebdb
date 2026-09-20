"""Sales fees and shares; platform buyers identified by their username.

Adds the fee vocabulary (`sales_fee_kind`) and `sales_order_fee`, one row per
fee line a platform charged on an order -- the actual amount taken, not the
estimate `sales_venue`'s default rates would produce. Adds
`sales_order_item_share`, one row per item's share of an order line's money,
which is the single permanent answer to "which items did this order carry".
Widens `customer` with `sales_venue_id` and `venue_username`, so a buyer met
on a marketplace is identified by their account there rather than by name
alone -- with a partial unique index on the *lowered* username, because a
platform shows one account's name inconsistently and two rows would split one
person's history, and a second partial unique index reserving each platform's
single undisclosed-buyer row (no username at all), used by auction houses
that do not name buyers.

**Backfills `sales_order_item_share`** for every order line that predates
this migration: one row per `sales_order_item`, `amount` = that line's
`unit_price * quantity`, `fee_amount` zero, `inventory_item_id` read from
the line's own listing. That is correct even for a line a later revision
changed, because `unit_price` and `quantity` are the line's current values
-- whatever `order_writes.revise_order` last set them to -- and a share
built from them now is exactly the share `_sync_shares` would have written
at that same revision had shares existed yet; nothing here needs the line's
history, only its present money. Lines whose listing names no item are
skipped rather than failing the migration; there are none today (every
listing's `inventory_item_id` is `NOT NULL`), and a lot listing that names
none is phase 3's case to handle, not this backfill's. Without this, a
database with order lines older than this migration would silently lose
`app.sale_state`'s for-sale warning for the items on them, since the order
half of that check now reaches an item only through this table.

Revision ID: c6908f789bf8
Revises: e7c3a5b19d84
Create Date: 2026-09-20 15:48:59.464652

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c6908f789bf8"
down_revision: str | None = "e7c3a5b19d84"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE = postgresql.ENUM(
    "seeded", "derived", "manual", name="provenance_source", create_type=False
)

#: As in app/models/sales.py's SalesFeeKind docstring: a closed vocabulary the
#: product defines, seeded here rather than from backend/data/reference/.
_FEE_KINDS = (
    ("commission", "Commission", 10),
    ("processing", "Payment processing", 20),
    ("listing", "Listing fee", 30),
    ("shipping_label", "Shipping label", 40),
    ("promotion", "Promotion", 50),
    ("other", "Other", 60),
)


def upgrade() -> None:
    """Create the fee, share and platform-buyer tables, and seed fee kinds."""
    bind = op.get_bind()

    op.create_table(
        "sales_fee_kind",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source", _SOURCE, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_sales_fee_kind_code"),
    )
    for code, label, order in _FEE_KINDS:
        bind.execute(
            sa.text(
                "INSERT INTO sales_fee_kind "
                "(code, label, sort_order, is_active, source) "
                "VALUES (:c, :l, :o, true, 'seeded')"
            ),
            {"c": code, "l": label, "o": order},
        )

    op.create_table(
        "sales_order_fee",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sales_order_id", sa.Integer(), nullable=False),
        sa.Column("sales_fee_kind_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column("note", sa.String(length=255), nullable=True),
        sa.CheckConstraint("amount >= 0", name="ck_sales_order_fee_non_negative"),
        sa.ForeignKeyConstraint(
            ["sales_order_id"],
            ["sales_order.id"],
            name="fk_sales_order_fee_sales_order_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["sales_fee_kind_id"],
            ["sales_fee_kind.id"],
            name="fk_sales_order_fee_sales_fee_kind_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sales_order_fee_sales_order_id", "sales_order_fee", ["sales_order_id"]
    )

    op.create_table(
        "sales_order_item_share",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sales_order_item_id", sa.Integer(), nullable=False),
        sa.Column("inventory_item_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(precision=12, scale=2), nullable=False),
        sa.Column(
            "fee_amount",
            sa.Numeric(precision=12, scale=2),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["sales_order_item_id"],
            ["sales_order_item.id"],
            name="fk_sales_order_item_share_sales_order_item_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_item.id"],
            name="fk_sales_order_item_share_inventory_item_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sales_order_item_id", "inventory_item_id", name="uq_share_line_item"
        ),
    )
    op.create_index(
        "ix_sales_order_item_share_sales_order_item_id",
        "sales_order_item_share",
        ["sales_order_item_id"],
    )
    op.create_index(
        "ix_sales_order_item_share_inventory_item_id",
        "sales_order_item_share",
        ["inventory_item_id"],
    )
    # Backfill: every line placed before this migration existed gets the
    # share `order_writes._sync_shares` would have given it -- see the
    # module docstring above for why the line's current unit_price and
    # quantity are the right source even for a since-revised line. Listings
    # with no item are excluded defensively; none exist today.
    bind.execute(
        sa.text(
            "INSERT INTO sales_order_item_share "
            "(sales_order_item_id, inventory_item_id, amount, fee_amount) "
            "SELECT soi.id, l.inventory_item_id, soi.unit_price * soi.quantity, 0 "
            "FROM sales_order_item soi "
            "JOIN listing l ON l.id = soi.listing_id "
            "WHERE l.inventory_item_id IS NOT NULL"
        )
    )

    op.add_column(
        "customer", sa.Column("sales_venue_id", sa.Integer(), nullable=True)
    )
    op.add_column(
        "customer", sa.Column("venue_username", sa.String(length=128), nullable=True)
    )
    op.create_index(
        "ix_customer_sales_venue_id", "customer", ["sales_venue_id"]
    )
    # Written exactly as PostgreSQL stores it -- confirmed with
    # `select indexdef from pg_indexes where indexname = ...` -- or the
    # models-versus-migrations drift test reports these as changed forever.
    op.create_index(
        "uq_customer_venue_username",
        "customer",
        ["sales_venue_id", sa.text("lower(venue_username::text)")],
        unique=True,
        postgresql_where=sa.text("venue_username IS NOT NULL"),
    )
    op.create_index(
        "uq_customer_venue_undisclosed",
        "customer",
        ["sales_venue_id"],
        unique=True,
        postgresql_where=sa.text(
            "(venue_username IS NULL) AND (sales_venue_id IS NOT NULL)"
        ),
    )
    op.create_foreign_key(
        "fk_customer_sales_venue_id",
        "customer",
        "sales_venue",
        ["sales_venue_id"],
        ["id"],
        ondelete="RESTRICT",
    )


def downgrade() -> None:
    """Drop the platform-buyer columns first, then the fee and share tables."""
    op.drop_constraint("fk_customer_sales_venue_id", "customer", type_="foreignkey")
    op.drop_index("uq_customer_venue_undisclosed", table_name="customer")
    op.drop_index("uq_customer_venue_username", table_name="customer")
    op.drop_index("ix_customer_sales_venue_id", table_name="customer")
    op.drop_column("customer", "venue_username")
    op.drop_column("customer", "sales_venue_id")

    op.drop_index(
        "ix_sales_order_item_share_inventory_item_id",
        table_name="sales_order_item_share",
    )
    op.drop_index(
        "ix_sales_order_item_share_sales_order_item_id",
        table_name="sales_order_item_share",
    )
    op.drop_table("sales_order_item_share")

    op.drop_index("ix_sales_order_fee_sales_order_id", table_name="sales_order_fee")
    op.drop_table("sales_order_fee")

    op.drop_table("sales_fee_kind")
