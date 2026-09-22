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

**One entry point on purpose, even though today it has only one caller.**
`record_sale`'s only caller right now is the Listings page's Record sale
(`app.routers.offers`); shop checkout writes its orders through
`order_writes.place_order` directly, without fees, and never calls this
function. The design is for a caller that does not exist yet: phase-4
auction settlement, where an auction settling four lots to two buyers will
be two calls to `record_sale`, not a second implementation of fees and
shares that can drift from this one.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

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
    SalesOrderStatus,
    User,
)
from .references import require_code

__all__ = [
    "FeeLine",
    "SaleInputInvalid",
    "SaleRefused",
    "ShareMissing",
    "record_sale",
]

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


class SaleInputInvalid(SaleRefused):
    """The request itself is malformed, not merely in conflict with the state.

    A negative or sub-cent price or fee, and a listing with nothing left to
    divide money among (`_shared_items`, which answers empty for a lot whose
    members have already been released), are bad input -- 422 at the HTTP
    boundary, per the
    spec's own split between "bad input" and "conflicts with other work ... or
    a stale version". "Not on offer" and an unmapped venue kind stay the base
    `SaleRefused` -- 409 -- because both are true conflicts the caller could
    not have known about from the request alone.

    A subclass, not a field on `SaleRefused`, on purpose: every existing
    `except SaleRefused` and `pytest.raises(SaleRefused)` keeps catching this
    too, unchanged, and an HTTP layer dispatches on it by `except` clause
    order rather than an `if` on a message string that can default silently
    to the wrong status. **mypy does not check that ordering** -- a reversed
    `except SaleRefused` before `except SaleInputInvalid` still type-checks
    cleanly, since the narrower type is still assignable to the wider one.
    The only thing standing between that reversal and every 422 silently
    becoming a 409 is `test_sale_input_invalid_from_record_sale_is_a_422_not_a_409`
    in `test_record_sale_api.py`. A reader reordering those clauses will not
    be stopped by anything else.
    """


@dataclass(frozen=True)
class FeeLine:
    """One actual fee from the platform's statement."""

    kind_code: str
    amount: Decimal
    note: str | None = None


class ShareMissing(Exception):
    """A line is missing a share for one of the items its listing offered.

    Deliberately **not** a `SaleRefused`. `SaleRefused` maps to 409 in
    `routers/offers.py`, which tells a caller "something is in the way, try
    again" -- false here: `order_writes._sync_shares` writes one share per
    offered item in the same transaction, so a gap is an invariant violation
    inside this codebase that no retry can fix. Unmapped in the router on
    purpose, so it surfaces as a 500 with a message naming the item and the
    listing rather than as a bare `KeyError` naming an integer.
    """


def _shared_items(db: Session, listing: Listing) -> list[InventoryItem]:
    """The items a listing's money must be divided among.

    One for an item listing; a lot listing's open members, in item id order,
    for a lot. Asked through `offering_writes.offered_items` rather than
    decided here, so the fee split, the shares `order_writes` writes and the
    snapshot all divide the same group in the same order.

    Must be asked *before* `end_offer` releases the memberships, which is the
    order `record_sale` below already runs in.

    An empty answer stays `SaleInputInvalid` -- 422, the spec's empty-lot
    case. It is still reachable here even though `offering_writes.offer` now
    refuses to create such a listing: a lot whose members were released by an
    earlier ending would answer this way.
    """
    items = offering_writes.offered_items(db, listing)
    if not items:
        raise SaleInputInvalid(f"Listing {listing.id} offers no item")
    return items


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

    Takes `listing`'s row lock and re-reads it before deciding anything, so
    the "is it still on offer?" question is answered from the current row
    rather than from whatever the caller loaded. A second operator racing the
    same listing is refused with "not on offer".

    Raises before writing anything, as one of two kinds -- the split matters
    to a caller mapping this to HTTP, so it is a subclass, not just a
    message. `SaleInputInvalid` (itself a `SaleRefused`): `price` or a fee is
    negative, or either is given to less than the cent (this branch's money
    reconciles exactly, and a sub-cent amount cannot -- PostgreSQL's rounding
    of `Numeric(12, 2)` and `allocate`'s `ROUND_HALF_EVEN` do not agree on
    one, so a row and the shares split from it would disagree by a cent --
    true of `price` the moment a lot listing divides it through `allocate`
    too, not only of a fee today). Plain `SaleRefused`, a genuine conflict
    rather than bad input: the listing is not on offer, or `venue`'s kind has
    no default order status and no `status_code` was given explicitly. An
    unknown fee kind code, or an explicit `status_code` that is not itself a
    real status, is resolved before the order is created too, for the same
    reason -- each fails as `HTTPException`, the way every other classifier
    lookup in this codebase does, rather than reaching `place_order` after a
    write has already happened.
    """
    # Locked and re-read *before* the status check, not after it. `listing` is
    # whatever the caller had in hand -- `routers.offers` fetches it with a
    # plain `db.get` -- and the check below is the one that decides whether
    # this sale may happen at all, so deciding it on an unlocked read is
    # deciding it on a value another operator may already have changed. The
    # spec's *Concurrency* paragraph requires the lock before the read for
    # exactly this reason.
    #
    # Nothing downstream re-reads `status`: `place_order` takes the same lock
    # a moment later, but `order_writes` skips both `sellable_in_shop` and
    # `is_active` when a venue is given, because an outside listing is
    # neither. Its stock check is all that remains -- an accidental backstop
    # at quantity 1, with a message about stock rather than about the sale,
    # and no backstop at all above 1 (`app/seed.py`'s demo listings are 5 and
    # 20). Without this re-read two operators can both record a sale against
    # an already-ended listing and call `end_offer(sold=True)` twice.
    #
    # `offering_writes.end_offer` re-reads the same way, for the same reason;
    # this follows it. Taken through `offering_writes.lock_for_sale`, which is
    # the single owner of the acquisition order -- the listing's lot, then its
    # items, then the listings -- rather than a `FOR UPDATE OF listing` of its
    # own. This used to lock the listing alone and call the order unchanged,
    # which was true of `place_order` as it then was and false of `offer`:
    # that pair of orders was the deadlock
    # `docs/specs/lock-order-design.md` records. `place_order` and
    # `end_offer` below both take the same rows again, and find them held.
    listing = offering_writes.lock_for_sale(db, listing_ids=[listing.id]).listings[
        listing.id
    ]
    if listing.status is not ListingStatus.active:
        # Names the platform as well as the listing: the spec's *Errors*
        # section asks for both, and an owner with the same item offered in
        # two places needs to know which offer this was about.
        raise SaleRefused(
            f"Listing {listing.id} on {listing.sales_venue.name} is not on offer "
            f"({listing.status.value})"
        )
    if price < 0:
        raise SaleInputInvalid(f"Price cannot be negative, not {price}")
    if price != price.quantize(_CENT):
        raise SaleInputInvalid(f"Price must be given to the cent, not {price}")
    for fee in fees:
        if fee.amount < 0:
            raise SaleInputInvalid("A fee cannot be negative")
        if fee.amount != fee.amount.quantize(_CENT):
            raise SaleInputInvalid(f"A fee must be given to the cent, not {fee.amount}")

    venue = listing.sales_venue
    items = _shared_items(db, listing)
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

    # `place_order` already created one share per offered item -- one for an
    # item listing carrying the whole line's `amount`, one per member for a
    # lot listing carrying its cost-weighted part -- each with a zero
    # `fee_amount` (`order_writes._sync_shares`). Only the fee half is this
    # module's to fill in; it never constructs a share.
    #
    # `line.shares` is safe to read here: `place_order` passes `new_line=True`,
    # so `_sync_shares` never consults that collection and so never leaves it
    # cached empty from before the row existed. This was a `select()` while it
    # did.
    line = order.items[0]
    weights = _weights(items, equal=equal_shares)
    fee_amounts = allocate(total_fees, weights)
    shares_by_item = {share.inventory_item_id: share for share in line.shares}
    for item, fee_amount in zip(items, fee_amounts, strict=True):
        share = shares_by_item.get(item.id)
        if share is None:
            raise ShareMissing(
                f"{item.item_code} has no share on the line for listing "
                f"{listing.id}; order_writes wrote one per offered item"
            )
        share.fee_amount = fee_amount

    offering_writes.end_offer(db, listing, sold=True)
    db.flush()
    return order
