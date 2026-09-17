"""Whether an item is up for sale, and so should not change unnoticed.

An item is **for sale** while a listing offers it (active, with stock) or an
order that has not shipped holds it (pending, paid or packed). Changing it
then changes what a buyer is looking at, or what they have already agreed
to buy, so the console warns and a save must say it knows
(`acknowledge_for_sale`). Once an order ships, its lines keep a snapshot of
the item as sold (app.sale_snapshot), and editing the item is ordinary
again.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Listing, SalesOrder, SalesOrderItem, SalesOrderStatus

__all__ = ["OPEN_ORDER_STATUSES", "SaleUse", "for_sale", "refusal"]

#: Orders that hold an item but have not shipped it.
OPEN_ORDER_STATUSES = frozenset({"pending", "paid", "packed"})


@dataclass(frozen=True)
class SaleUse:
    """One reason an item is for sale."""

    #: "listing" or "order".
    kind: str
    id: int
    #: What a person reads: "listing #3 at 120.00", "order #7 (paid)".
    text: str


def for_sale(db: Session, item_ids: Collection[int]) -> dict[int, list[SaleUse]]:
    """The items among `item_ids` that are for sale, and why."""
    ids = list(item_ids)
    found: dict[int, list[SaleUse]] = {}
    if not ids:
        return found
    listings = db.execute(
        select(Listing.inventory_item_id, Listing.id, Listing.price).where(
            Listing.inventory_item_id.in_(ids),
            Listing.is_active.is_(True),
            Listing.quantity_available > 0,
        )
    ).tuples()
    for item_id, listing_id, price in listings:
        found.setdefault(item_id, []).append(
            SaleUse("listing", listing_id, f"listing #{listing_id} at {price}")
        )
    orders = db.execute(
        select(Listing.inventory_item_id, SalesOrder.id, SalesOrderStatus.code)
        .join(SalesOrderItem, SalesOrderItem.listing_id == Listing.id)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.sales_order_id)
        .join(SalesOrderStatus, SalesOrderStatus.id == SalesOrder.sales_order_status_id)
        .where(
            Listing.inventory_item_id.in_(ids),
            SalesOrderStatus.code.in_(OPEN_ORDER_STATUSES),
        )
        .distinct()
    ).tuples()
    for item_id, order_id, status in orders:
        found.setdefault(item_id, []).append(
            SaleUse("order", order_id, f"order #{order_id} ({status})")
        )
    for uses in found.values():
        uses.sort(key=lambda use: (use.kind, use.id))
    return found


def refusal(codes_and_uses: dict[str, list[SaleUse]]) -> str:
    """The message a save gets when it has not said it knows."""
    described = "; ".join(
        f"{code}: {', '.join(use.text for use in uses)}"
        for code, uses in sorted(codes_and_uses.items())
    )
    return (
        f"For sale -- {described}. A change shows to buyers at once. "
        "Save again with acknowledge_for_sale to make it."
    )
