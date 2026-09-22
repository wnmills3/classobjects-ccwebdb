"""Auctions: consigning lots to a sale, and settling it.

Adds `auction` (`draft` -> `scheduled` -> [`consigned`] -> `closed` ->
`settled`, or `cancelled`) and `auction_lot`, one row per auction listing
(`listing_id` unique -- an auction lot *is* the detail of one listing, the
same relationship `sales_lot` has). `lot_number` is unique only within its
auction, since lot numbers restart every sale.

Also seeds the `consigned` storage-location kind, the same way phase 1
seeded `sales_venue_kind`: `storage_location_kind` itself already has a
JSON-backed vocabulary (`data/reference/operations.json`, loaded by
`app.seeding.seed_all`) that this row has also been added to for a fresh
install, but a database that has already run past this migration is not
re-seeded automatically, so the row is inserted here too. `_upsert` is
idempotent on `code`, so running `seed_all` afterwards leaves this row alone.

Revision ID: e267ec3aedc1
Revises: 0b323ea7ef7c
Create Date: 2026-09-22 09:31:11.892659

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e267ec3aedc1"
down_revision: str | None = "0b323ea7ef7c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATUS = postgresql.ENUM(
    "draft",
    "scheduled",
    "consigned",
    "closed",
    "settled",
    "cancelled",
    name="auction_status",
)
_RESULT = postgresql.ENUM("sold", "unsold", "withdrawn", name="auction_lot_result")

#: The next sort_order after `in_transit` (40) and before `sold` (50) in
#: operations.json's storage_location_kind list.
_CONSIGNED_SORT_ORDER = 45


def upgrade() -> None:
    """Create `auction` and `auction_lot`, and seed the `consigned` location kind."""
    bind = op.get_bind()
    _STATUS.create(bind)
    _RESULT.create(bind)

    op.create_table(
        "auction",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sales_venue_id", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("external_id", sa.String(length=128), nullable=True),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            postgresql.ENUM(name="auction_status", create_type=False),
            nullable=False,
        ),
        sa.Column("consigned_on", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["sales_venue_id"],
            ["sales_venue.id"],
            name="fk_auction_sales_venue_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_auction_sales_venue_id", "auction", ["sales_venue_id"])

    op.create_table(
        "auction_lot",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("auction_id", sa.Integer(), nullable=False),
        sa.Column("listing_id", sa.Integer(), nullable=False),
        sa.Column("lot_number", sa.String(length=32), nullable=False),
        sa.Column("reserve", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column(
            "result",
            postgresql.ENUM(name="auction_lot_result", create_type=False),
            nullable=True,
        ),
        sa.Column("hammer_price", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("buyer_customer_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["auction_id"],
            ["auction.id"],
            name="fk_auction_lot_auction_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["buyer_customer_id"],
            ["customer.id"],
            name="fk_auction_lot_buyer_customer_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["listing_id"],
            ["listing.id"],
            name="fk_auction_lot_listing_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "auction_id", "lot_number", name="uq_auction_lot_auction_lot_number"
        ),
        sa.UniqueConstraint("listing_id", name="uq_auction_lot_listing_id"),
    )
    op.create_index("ix_auction_lot_auction_id", "auction_lot", ["auction_id"])

    bind.execute(
        sa.text(
            "INSERT INTO storage_location_kind "
            "(code, label, sort_order, is_active, source) "
            "VALUES ('consigned', 'Consigned to auction house', :o, true, 'seeded')"
        ),
        {"o": _CONSIGNED_SORT_ORDER},
    )


def downgrade() -> None:
    """Drop `auction_lot` before `auction`, and remove the seeded location kind."""
    bind = op.get_bind()
    bind.execute(
        sa.text("DELETE FROM storage_location_kind WHERE code = 'consigned'")
    )

    op.drop_index("ix_auction_lot_auction_id", table_name="auction_lot")
    op.drop_table("auction_lot")

    op.drop_index("ix_auction_sales_venue_id", table_name="auction")
    op.drop_table("auction")

    op.execute("DROP TYPE IF EXISTS auction_lot_result")
    op.execute("DROP TYPE IF EXISTS auction_status")
