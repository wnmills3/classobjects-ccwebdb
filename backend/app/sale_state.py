"""Whether an item is up for sale, and so should not change unnoticed.

An item is **for sale** while a listing offers it (active or paused, with
stock) or an order that has not shipped holds it (pending, paid or packed).
A **paused** store listing counts: the item is not in the shop this minute
only because it is being offered somewhere else, and both the offer and the
listing waiting behind it are things a buyer is looking at. Changing it
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

from . import offering_writes
from .models import (
    Listing,
    ListingStatus,
    SalesOrder,
    SalesOrderItem,
    SalesOrderStatus,
    SalesVenue,
)

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


def _offering(db: Session, item_ids: Collection[int]) -> dict[int, set[int]]:
    """Which listings offer each item: the claims, and the listings themselves.

    Two sources because there are two ways an item is on a listing. A claim
    (`app.offering_writes`) is the general one, and the only one that will
    work for a lot, whose listing names the lot rather than its members. A
    listing written directly against the item is the other: `app.seed`
    creates one that way for its demo catalogue, and a listing made before
    claims existed has none. Reading only claims would quietly stop warning
    about those.
    """
    wanted: dict[int, set[int]] = {}
    for item_id, claims in offering_writes.claims_for(db, item_ids).items():
        wanted.setdefault(item_id, set()).update(claim.listing_id for claim in claims)
    direct = db.execute(
        select(Listing.inventory_item_id, Listing.id).where(
            Listing.inventory_item_id.in_(list(item_ids)),
            Listing.status.in_(offering_writes.ON_OFFER),
        )
    ).tuples()
    for item_id, listing_id in direct:
        wanted.setdefault(item_id, set()).add(listing_id)
    return wanted


def for_sale(db: Session, item_ids: Collection[int]) -> dict[int, list[SaleUse]]:
    """The items among `item_ids` that are for sale, and why."""
    ids = list(item_ids)
    found: dict[int, list[SaleUse]] = {}
    if not ids:
        return found
    wanted = _offering(db, ids)
    all_listing_ids = {listing_id for ids_ in wanted.values() for listing_id in ids_}
    described: dict[int, str] = {}
    if all_listing_ids:
        rows = db.execute(
            select(
                Listing.id,
                Listing.price,
                Listing.status,
                SalesVenue.name,
                SalesVenue.is_own_store,
            )
            .join(SalesVenue, SalesVenue.id == Listing.sales_venue_id)
            .where(
                Listing.id.in_(all_listing_ids),
                Listing.status.in_(offering_writes.ON_OFFER),
                Listing.quantity_available > 0,
            )
        ).tuples()
        for listing_id, price, listing_status, venue_name, own_store in rows:
            text = f"listing #{listing_id} at {price}"
            if not own_store:
                text += f" on {venue_name}"
            if listing_status is ListingStatus.paused:
                text += " (paused)"
            described[listing_id] = text
    for item_id, listing_ids in wanted.items():
        for listing_id in sorted(listing_ids):
            if listing_id in described:
                found.setdefault(item_id, []).append(
                    SaleUse("listing", listing_id, described[listing_id])
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
