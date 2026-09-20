"""Recording a sale: the money, the buyer, and which items it covered.

This module owns the one fact nothing else writes, `sales_order_fee`, and the
fee half of `sales_order_item_share` -- the amount half, and the row itself,
belong to `order_writes._sync_shares`, which creates a share for every line
`place_order` writes and leaves `fee_amount` at zero because neither checkout
nor a plain revision ever knows a fee. `record_sale` orchestrates rather than
duplicating: the order and its snapshot come from `order_writes.place_order`,
the buyer from `buyers.venue_buyer`, and the listing is ended by
`offering_writes.end_offer(sold=True)`. None of those grows a second path
here, and this module never constructs a `SalesOrderItemShare` itself -- only
fills in the `fee_amount` on rows `place_order` already made.

**One entry point on purpose.** `record_sale` has three callers -- the
Listings page's Record sale, shop checkout, and auction settlement -- and an
auction settling four lots to two buyers is two calls, not a second
implementation of fees and shares that can drift from this one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import offering_writes, order_writes
from .allocation import allocate
from .buyers import venue_buyer
from .models import (
    InventoryItem,
    Listing,
    ListingStatus,
    SalesFeeKind,
    SalesOrder,
    SalesOrderFee,
    SalesOrderItemShare,
    SalesOrderStatus,
    User,
)
from .references import require_code

__all__ = ["FeeLine", "SaleRefused", "record_sale"]

#: What a sale's order status is, by the kind of platform it happened on.
#: A marketplace or live auction has collected the money and the owner still
#: has to ship; an auction house has already shipped for us.
#:
#: Subscripted, never `.get(..., "paid")`: a venue kind this dictionary does
#: not name must be refused, not silently treated as "paid" -- the one
#: default that would misreport a sale as money already collected.
_STATUS_BY_VENUE_KIND = {
    "own_store": "paid",
    "marketplace": "paid",
    "live_auction": "paid",
    "auction_house": "delivered",
}

#: Money in this module is Decimal to the cent; nothing here rounds.
_CENT = Decimal("0.01")


class SaleRefused(Exception):
    """The sale cannot be recorded as asked, with the reason for a person."""


@dataclass(frozen=True)
class FeeLine:
    """One actual fee from the platform's statement."""

    kind_code: str
    amount: Decimal
    note: str | None = None


def _shared_items(listing: Listing) -> list[InventoryItem]:
    """The items a listing's money must be divided among.

    One for an item listing. For a lot listing (phase 3) this becomes the
    lot's members; until then a lot listing cannot exist.
    """
    item = listing.inventory_item
    if item is None:  # pragma: no cover - phase 3 widens this
        raise SaleRefused(f"Listing {listing.id} offers no item")
    return [item]


def _weights(items: Sequence[InventoryItem], *, equal: bool) -> list[Decimal]:
    """How to divide the fees between items: by cost, or equally.

    Cost basis is the default because it is the closest thing to what each
    piece was worth when the group was priced. Equal is chosen explicitly.
    Falling back to an equal split when every cost is zero is `allocate`'s
    own job -- it already treats an all-zero weight list that way -- so this
    function does not repeat that check.
    """
    if equal:
        return [Decimal(1)] * len(items)
    return [item.total_cost for item in items]


def record_sale(
    db: Session,
    listing: Listing,
    *,
    price: Decimal,
    buyer_username: str | None,
    external_order_id: str | None,
    fees: Sequence[FeeLine],
    recorded_by: User,
    equal_shares: bool = False,
    status_code: str | None = None,
) -> SalesOrder:
    """Record that `listing` sold, and end it. Caller commits.

    Raises `SaleRefused` before writing anything if: the listing is not on
    offer; `price` or a fee is given to less than the cent, or a fee is
    negative (this branch's money reconciles exactly, and a sub-cent amount
    cannot -- PostgreSQL's rounding of `Numeric(12, 2)` and `allocate`'s
    `ROUND_HALF_EVEN` do not agree on one, so a row and the shares split
    from it would disagree by a cent -- true of `price` the moment a lot
    listing divides it through `allocate` too, not only of a fee today); or
    `venue`'s kind has no default order status and no `status_code` was
    given explicitly. An unknown fee kind code, or an explicit `status_code`
    that is not itself a real status, is resolved before the order is
    created too, for the same reason -- each fails as `HTTPException`, the
    way every other classifier lookup in this codebase does, rather than
    reaching `place_order` after a write has already happened.
    """
    if listing.status is not ListingStatus.active:
        raise SaleRefused(
            f"Listing {listing.id} is not on offer ({listing.status.value})"
        )
    if price != price.quantize(_CENT):
        raise SaleRefused(f"Price must be given to the cent, not {price}")
    for fee in fees:
        if fee.amount < 0:
            raise SaleRefused("A fee cannot be negative")
        if fee.amount != fee.amount.quantize(_CENT):
            raise SaleRefused(f"A fee must be given to the cent, not {fee.amount}")

    venue = listing.sales_venue
    items = _shared_items(listing)
    # Resolved before the order exists, not inside the write loop below: an
    # unknown fee kind code must fail before anything is written, the same
    # discipline as the checks above.
    fee_kind_ids = [
        require_code(db, SalesFeeKind, fee.kind_code, "fee") for fee in fees
    ]

    # Resolved and validated here, above `venue_buyer` -- the first thing
    # below that writes -- rather than after `place_order` has already
    # flushed: an unmapped venue kind or an unknown `status_code` must both
    # fail before anything is written, not partway through.
    if status_code is not None:
        status = status_code
    else:
        try:
            status = _STATUS_BY_VENUE_KIND[venue.kind.code]
        except KeyError:
            raise SaleRefused(
                f"No default order status for sales venue kind {venue.kind.code!r}"
            ) from None
    require_code(db, SalesOrderStatus, status, "status")

    buyer = venue_buyer(db, venue, buyer_username)

    order = order_writes.place_order(
        db,
        buyer,
        [order_writes.Line(listing_id=listing.id, quantity=1, unit_price=price)],
        recorded_by,
        venue=venue,
        status_code=status,
        external_order_id=external_order_id,
    )
    # Captured once, right after the order exists: a flush below that fails
    # (an unlikely one, since everything refusable was checked above) leaves
    # every attribute read raising `PendingRollbackError` instead of the
    # error meant to surface.
    order_id = order.id

    total_fees = Decimal("0.00")
    for fee, kind_id in zip(fees, fee_kind_ids, strict=True):
        db.add(
            SalesOrderFee(
                sales_order_id=order_id,
                sales_fee_kind_id=kind_id,
                amount=fee.amount,
                note=fee.note,
            )
        )
        total_fees += fee.amount

    # `place_order` already created one share per item, carrying the whole
    # line's `amount` and a zero `fee_amount` (`order_writes._sync_shares`).
    # Only the fee half is this module's to fill in.
    #
    # Fetched with a fresh `select()`, not `line.shares`: `_sync_shares`
    # reads that same collection *before* the share exists, while deciding
    # whether to insert one, which leaves it cached empty on `line` for the
    # rest of this session -- inserting the row does not invalidate a
    # relationship collection some earlier read already populated. A plain
    # query has no such cache to be stale.
    line = order.items[0]
    weights = _weights(items, equal=equal_shares)
    fee_amounts = allocate(total_fees, weights)
    shares_by_item = {
        share.inventory_item_id: share
        for share in db.scalars(
            select(SalesOrderItemShare).where(
                SalesOrderItemShare.sales_order_item_id == line.id
            )
        )
    }
    for item, fee_amount in zip(items, fee_amounts, strict=True):
        shares_by_item[item.id].fee_amount = fee_amount

    offering_writes.end_offer(db, listing, sold=True)
    db.flush()
    return order
