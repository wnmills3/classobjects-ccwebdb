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

An order reaches its items through `sales_order_item_share`, not through
`listing.inventory_item_id`: a claim is released the moment its listing
sells and a lot listing names no item at all, so the share is the only link
that still finds a sold item's pieces.

`for_sale` answers the present tense -- is this item being offered or held
now. `ever_offered` below answers the past tense, which is a different
question with a different reader: a delete is refused by offer history that
`for_sale` has long since stopped reporting.
"""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import offering_writes
from .models import (
    InventoryItem,
    Listing,
    ListingStatus,
    SalesOrder,
    SalesOrderItem,
    SalesOrderItemShare,
    SalesOrderStatus,
    SalesVenue,
)

__all__ = [
    "OPEN_ORDER_STATUSES",
    "SaleUse",
    "ever_offered",
    "for_sale",
    "guard",
    "refusal",
]

#: Orders that hold an item but have not shipped it.
#:
#: `delivered` is deliberately excluded, an auction house included: an
#: auction house has already shipped for us by the time its sale is
#: recorded (`sales_writes._STATUS_BY_VENUE_KIND` starts that order
#: straight at `delivered`, past every status in between), and this
#: module's own docstring already treats "shipped" as the point past which
#: editing the item is ordinary again -- the snapshot on the line, not the
#: live record, is what a buyer was shown. `delivered` is strictly further
#: along than `shipped`, so leaving it out is that same rule reached one
#: step further, not a new exception. Widening this constant to include it
#: would also widen every other reader -- check for one before changing it.
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
    (`app.offering_writes`) is the general one, and the only one that works
    for a lot, whose listing names the lot rather than its members -- proved
    by `test_an_offered_lot_s_member_warns_like_a_listed_item` and
    `test_a_sold_lot_s_member_still_warns_while_the_order_is_open`
    (`tests/test_for_sale_guards.py`), which go red when this half is
    removed and when the order half below is joined through
    `listing.inventory_item_id` instead of through the share. A listing
    written directly against the item is the other: a listing made before
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
    # `inventory_item_id` is nullable -- a lot listing leaves it null -- so
    # the null is skipped rather than annotated away, the same way
    # `ever_offered` below does it. The `in_` already excludes the null rows
    # (SQL `IN` never matches NULL), so this drops nothing a correct query
    # would have kept; it keeps `wanted` honestly keyed by `int` instead of
    # letting a `None` key in beside the real ones, where the caller's
    # `found[item_id]` lookup would never find it again.
    #
    # A name of its own rather than reusing the claims loop's `item_id`: the
    # two loops carry different types now (a claim's item id cannot be null,
    # a listing's can), and one name for both hid that difference.
    for listed_item_id, listing_id in direct:
        if listed_item_id is not None:
            wanted.setdefault(listed_item_id, set()).add(listing_id)
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
    # Reached through `sales_order_item_share`, which names every item on
    # every line -- one share for an item listing, one per member for a lot.
    # The direct link (`listing.inventory_item_id`) cannot do this: a lot
    # listing names no item, and after a sale the claim is `released`, so
    # neither the claim nor the link finds the pieces that were sold. A share
    # is permanent, which is why every line has them, a single-item store
    # sale included (`order_writes._sync_shares`). Decided 2026-09-20; this
    # is the rule the module previously deferred to phase 3.
    orders = db.execute(
        select(
            SalesOrderItemShare.inventory_item_id,
            SalesOrder.id,
            SalesOrderStatus.code,
        )
        .join(
            SalesOrderItem,
            SalesOrderItem.id == SalesOrderItemShare.sales_order_item_id,
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.sales_order_id)
        .join(SalesOrderStatus, SalesOrderStatus.id == SalesOrder.sales_order_status_id)
        .where(
            SalesOrderItemShare.inventory_item_id.in_(ids),
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


def ever_offered(db: Session, item_ids: Collection[int]) -> set[int]:
    """Which of these items an offer has ever named -- the past-tense question.

    `for_sale` above asks the present tense, which is what an *edit* needs:
    it stops warning once the offer ends, because an ended offer is no longer
    what a buyer is looking at. A *delete* is the other case entirely. Once a
    coin has been offered, the offer is part of the sales history and the row
    it points at has to stay, so that refusal never clears -- it cannot be
    asked through a query filtered to `ON_OFFER` or `OPEN_ORDER_STATUSES`,
    and asking `for_sale` for it would answer no for every offer that has
    since ended.

    The same two halves as `_offering`, with the status and state filters
    dropped, and the claim half read through `offering_writes.ever_claimed`
    rather than a second query against `offer_claim` here -- that table has
    exactly one writer and, until this function, exactly one other reader
    (`claims_for`), both in `offering_writes`. Asking it directly from this
    module would leave "is this item spoken for" with two homes that could
    drift the moment the claim's shape changes.

    The claim half is what makes this lot-aware: `offering_writes.offer`
    writes one claim per member and a lot's listing names the lot, so an item
    that has only ever been offered inside a lot is reachable this way and no
    other. Here rather than in `routers.inventory` because "is this item
    spoken for" already has one home, and a second hand-written answer is
    what `lot_writes._refuse_partial` had to stop being.

    An item merely *assembling* into a lot is deliberately not here: it has
    not been offered, `remove_member` deletes its membership outright, and so
    the refusal it earns is a clearable one. `lot_writes.lot_holding` is the
    question for that.
    """
    ids = list(item_ids)
    if not ids:
        return set()
    named = offering_writes.ever_claimed(db, ids)
    # `inventory_item_id` is nullable on `listing` -- a lot listing leaves it
    # null -- so the null is skipped rather than annotated away. The `in_`
    # already excludes it; this keeps the set honestly `set[int]`.
    named.update(
        item_id
        for item_id in db.scalars(
            select(Listing.inventory_item_id).where(Listing.inventory_item_id.in_(ids))
        )
        if item_id is not None
    )
    return named


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


def guard(
    db: Session,
    items: Collection[InventoryItem],
    *,
    acknowledged: bool,
    kinds: Collection[str] | None = None,
) -> None:
    """409 unless the caller has said it knows these items are for sale.

    `kinds` narrows which reasons count, by `SaleUse.kind`. Split passes
    `{"listing"}` because `app.splitting` refuses an item in an order
    outright: an acknowledgement that does not let the caller through would
    teach an operator to tick past warnings that mean something.

    A 409 rather than a 422: the request is well formed and would be accepted
    at another moment. The status code matches the two call sites this
    replaces, and the console tells the case apart by the message's opening
    words ("For sale").
    """
    if acknowledged:
        return
    wanted = list(items)
    found = for_sale(db, [item.id for item in wanted])
    if kinds is not None:
        narrowed = set(kinds)
        found = {
            item_id: kept
            for item_id, uses in found.items()
            if (kept := [use for use in uses if use.kind in narrowed])
        }
    if not found:
        return
    codes = {item.id: item.item_code for item in wanted}
    # A bare 409 rather than `status.HTTP_409_CONFLICT`: `for_sale` above
    # binds a local named `status` in its order loop, and importing fastapi's
    # `status` into this module would put a shadowed name one function away
    # from a live one. Both spellings are already used in this codebase.
    raise HTTPException(
        status_code=409,
        detail=refusal({codes[item_id]: uses for item_id, uses in found.items()}),
    )
