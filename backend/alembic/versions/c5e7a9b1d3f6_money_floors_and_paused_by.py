"""Floors on the money columns that had none, and `paused_by` only when paused.

A share's fee, an auction lot's reserve and hammer price, and an item's
numismatic value are never negative. A listing names the offer that paused it
only while it is paused; `offering_writes` clears the pointer as it resumes or
ends one, and any pointer left on a listing that is no longer paused names
nothing, so it is cleared before the constraint is added. A negative amount is
not corrected: the upgrade fails on it, for a person to look at.

Revision ID: c5e7a9b1d3f6
Revises: b4d6f8a0c2e4
Create Date: 2026-10-21 12:20:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "c5e7a9b1d3f6"
down_revision: str | None = "b4d6f8a0c2e4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: (name, table, condition), in the models' words.
_CHECKS = (
    (
        "ck_sales_order_item_share_fee_non_negative",
        "sales_order_item_share",
        "fee_amount >= 0",
    ),
    (
        "ck_auction_lot_reserve_non_negative",
        "auction_lot",
        "reserve IS NULL OR reserve >= 0",
    ),
    (
        "ck_auction_lot_hammer_price_non_negative",
        "auction_lot",
        "hammer_price IS NULL OR hammer_price >= 0",
    ),
    (
        "ck_inventory_item_numismatic_value_non_negative",
        "inventory_item",
        "numismatic_value IS NULL OR numismatic_value >= 0",
    ),
    (
        "ck_listing_paused_by_only_when_paused",
        "listing",
        "status = 'paused'::listing_status OR paused_by_listing_id IS NULL",
    ),
)


def upgrade() -> None:
    """Clear stale pause pointers, then add every check."""
    op.execute(
        "UPDATE listing SET paused_by_listing_id = NULL "
        "WHERE status <> 'paused'::listing_status "
        "AND paused_by_listing_id IS NOT NULL"
    )
    for name, table, condition in _CHECKS:
        op.create_check_constraint(name, table, condition)


def downgrade() -> None:
    """The checks go; the cleared pointers stay cleared."""
    for name, table, _condition in reversed(_CHECKS):
        op.drop_constraint(name, table, type_="check")
