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

**One implementation, two doors onto it.** `record_sale_lines` takes a
buyer's whole purchase -- several listings on one order -- and `record_sale`
is that function called with a list of one. The Listings page's Record sale
(`app.routers.offers`) still calls `record_sale`; shop checkout writes its
orders through `order_writes.place_order` directly, without fees, and never
calls either. The second caller this single entry point was built for
(spec, *Where record-a-sale lives*) is phase-4 auction settlement, and it
arrived in Task 3: an auction settling four lots to two buyers is **two**
calls, one per buyer, not four and not a second implementation of fees and
shares that can drift from this one.

`record_sale_lines` rather than a loop over `record_sale` because an auction
house bills per buyer *order*, not per lot: two lots to one buyer are one
order with two lines, and the order's fee divides across every coin on both
lines. That widened denominator is the only thing settlement needed that
recording a single sale did not.
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
    AuctionLot,
    InventoryItem,
    Listing,
    ListingFormat,
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
    "SaleLine",
    "SaleRefused",
    "ShareMissing",
    "money_problem",
    "record_sale",
    "record_sale_lines",
]

#: What a sale's order status is, by the kind of platform it happened on.
#: A marketplace or live auction has collected the money and the owner still
#: has to ship; an auction house has already shipped for us.
#:
#: Subscripted, never `.get(..., "paid")`: a venue kind this dictionary does
#: not name must be refused, not silently treated as "paid" -- the one
#: default that would misreport a sale as money already collected.
#:
#: `own_store` is deliberately absent (ruling S4, 2026-09-22): a shop item
#: sells through checkout, and an in-person sale of one is an order placed
#: on the customer's behalf -- `_refuse_store_sale` says so in words, and
#: this absence keeps `record_sale_lines` failing closed behind it.
_STATUS_BY_VENUE_KIND = {
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


@dataclass(frozen=True)
class SaleLine:
    """One listing on one order, and what it sold for.

    The unit `record_sale_lines` takes several of, because an auction house
    bills a buyer once for every lot they took rather than once per lot. It
    carries no quantity: an outside sale is always the whole offer -- a lot
    listing is quantity 1 by `ck_listing_lot_quantity_one`, and the single
    item case has always passed 1 -- so a quantity here would be a second
    way to say something no caller can vary.
    """

    listing_id: int
    price: Decimal


def money_problem(amount: Decimal, what: str) -> str | None:
    """Why `amount` cannot be money on this branch, or None if it can.

    The predicate half of `_refuse_money`, public because
    `auctions.settle` has to collect **every** bad figure in a settlement
    grid before refusing -- the console shows a grid, and fixing one problem
    at a time is miserable -- while every other caller wants to stop at the
    first. Two answers to "is this a real amount of money" is how a hammer
    price the settlement accepted becomes one `record_sale_lines` refuses
    half way through a settlement that has already written orders.

    Sub-cent is refused, not rounded, because PostgreSQL's rounding of
    `Numeric(12, 2)` and `allocate`'s `ROUND_HALF_EVEN` do not agree on a
    half cent: a row and the shares split from it would differ by a cent.
    """
    if amount < 0:
        return f"{what} cannot be negative, not {amount}"
    if amount != amount.quantize(_CENT):
        return f"{what} must be given to the cent, not {amount}"
    return None


def _refuse_money(amount: Decimal, what: str) -> None:
    """Raise `SaleInputInvalid` if `amount` is not money to the cent."""
    problem = money_problem(amount, what)
    if problem is not None:
        raise SaleInputInvalid(problem)


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
    order `record_sale_lines` below already runs in -- and it asks once per
    listing, up front, so a two-line order's second listing is read while it
    is still whole rather than after the first one's ending has run.

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


def _refuse_store_sale(listing: Listing) -> None:
    """Refuse Record sale on a web-store listing: it sells through checkout.

    Ruling S4 (2026-09-22), settling the spec's open decision *Record-a-sale
    on a store listing*. The Listings page already hid the button on store
    rows; the API still took the request, which made it a second way to sell
    a shop item -- past the cart and past checkout, minting an "Undisclosed
    buyer (store)" when the username was blank. An in-person sale of a shop
    item is entered as an order on the customer's behalf (the Sales page),
    which goes through checkout's own rules.
    """
    if listing.sales_venue.is_own_store:
        raise SaleRefused(
            f"listing #{listing.id} is in the web store, which sells through "
            "checkout. For an in-person sale, place an order on the customer's "
            "behalf from the Sales page."
        )


def _refuse_manual_auction_sale(db: Session, listing: Listing) -> None:
    """Refuse to record a manual sale against an auction-format listing.

    Ruling R25 (Task 5 fix round 1): an auction lot sells through
    **settlement** (`app.auctions.settle`), never through Record sale --
    settlement is the only thing that produces the fees, the shares and the
    lot's dissolution or `sold` result together, in one transaction, and
    ends every member's store listing the way a sale should. Record sale
    calling `offering_writes.end_offer(sold=True)` on an auction listing
    would end it and write an order outside settlement entirely, leaving the
    `auction_lot` row live and pointing at a listing no longer offered --
    the same failure `routers.offers._refuse_auction_lot` closes for
    `POST /api/listings/{id}/end` (defect 1) -- and additionally strands the
    auction for good: `settle`'s own call to `record_sale_lines` would then
    refuse that lot's listing as "not on offer", so the auction could never
    be settled after.

    **Keyed on the `auction_lot` row, not on `format` alone.** An
    auction-format listing need not belong to an auction: the Offer dialog
    offers a coin directly on eBay by auction, and that sale is recorded
    through Record sale like any other. A listing whose lot was removed
    (ruling R11 deletes the row) is ended, so the ordinary "not on offer"
    refusal already answers it (review of the final fix wave, Important #1).

    **Placed in `record_sale`, not in `record_sale_lines`.** The single-sale
    convenience wrapper is what `routers.offers.record_listing_sale` --
    "Record sale", the button beside "End" on the Listings page -- calls;
    `app.auctions.settle` calls `record_sale_lines` directly, several
    listings at a time, for exactly the auction-format listings this guard
    exists to protect. Moving the check into `record_sale_lines` would
    refuse settlement's own legitimate calls; every test in
    `test_auction_settlement.py` that settles a sold lot exercises that path
    unchanged, which is the proof the guard does not reach it.
    """
    if listing.format is ListingFormat.auction:
        auction_id = db.scalar(
            select(AuctionLot.auction_id).where(AuctionLot.listing_id == listing.id)
        )
        if auction_id is None:
            return
        raise SaleRefused(
            f"listing #{listing.id} is a lot of auction #{auction_id}; it sells "
            "through settlement, not Record sale"
        )


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

    The one-listing case of `record_sale_lines`, and **only** that: this
    function keeps the name and the signature its production caller
    (`POST /api/listings/{id}/sale`) already uses and adds no second
    implementation of fees, shares or endings below it. Everything this used
    to do, and every reason it did it in that order, now lives in
    `record_sale_lines`; read that docstring for the behavior, including
    which refusals are `SaleInputInvalid` and which stay a plain
    `SaleRefused`.

    Widened rather than duplicated because auction settlement needs several
    listings on one order -- an auction house bills per buyer, not per lot --
    and a second order creator beside this one is exactly the drift the
    spec's "one entry point on purpose" was written to prevent.

    Refuses first, before `record_sale_lines` ever runs, if `listing` is an
    auction-format listing -- see `_refuse_manual_auction_sale`. That check
    lives here rather than in `record_sale_lines` precisely so
    `app.auctions.settle`'s own calls into the wider function are untouched.
    """
    _refuse_store_sale(listing)
    _refuse_manual_auction_sale(db, listing)
    return record_sale_lines(
        db,
        [SaleLine(listing_id=listing.id, price=price)],
        buyer_username=buyer_username,
        external_order_id=external_order_id,
        fees=fees,
        recorded_by=recorded_by,
        equal_shares=equal_shares,
        status_code=status_code,
    )


def record_sale_lines(
    db: Session,
    lines: Sequence[SaleLine],
    *,
    buyer_username: str | None,
    external_order_id: str | None,
    fees: Sequence[FeeLine],
    recorded_by: User,
    equal_shares: bool = False,
    status_code: str | None = None,
) -> SalesOrder:
    """Record one buyer's whole purchase as a single order, and end its listings.

    One order, one buyer, one platform, any number of listings. The
    single-listing `record_sale` is this function with a list of one; there
    is no other path.

    **The fee spans the lines, and the price does not.** `place_order`
    already writes one zero-fee share per offered item -- one per member for
    a lot listing, cost-weighted -- so each line's own money is divided
    among that line's own coins before this function sees it. A fee is the
    *order's* money: an auction house bills a buyer once for everything they
    took, so it is divided once, across every item of every line, by the
    same `_weights`/`allocate` pair. That widened denominator is the only
    genuinely new arithmetic here, and it is where a cent can go missing.

    Takes every listing's row lock and re-reads it before deciding anything,
    so the "is it still on offer?" question is answered from the current row
    rather than from whatever the caller loaded. A second operator racing any
    one of them is refused with "not on offer". One
    `offering_writes.lock_for_sale` call covers the whole order: that
    function is the single owner of the acquisition order -- lot rows, then
    items, then listings -- and a caller that takes them itself, or takes
    them a listing at a time, is what reproduces the deadlock
    `docs/specs/lock-order-design.md` records.

    Raises before writing anything, as one of two kinds -- the split matters
    to a caller mapping this to HTTP, so it is a subclass, not just a
    message. `SaleInputInvalid` (itself a `SaleRefused`): no lines at all, the
    same listing named twice, a `price` or a fee that is negative or given to
    less than the cent (see `money_problem` for why sub-cent cannot be
    rounded), or a listing with nothing left to divide money among
    (`_shared_items`). Plain `SaleRefused`, a genuine conflict rather than bad
    input: a listing is not on offer, two listings are on different
    platforms, or the venue's kind has no default order status and no
    `status_code` was given explicitly. An unknown fee kind code, or an
    explicit `status_code` that is not itself a real status, is resolved
    before the order is created too, for the same reason -- each fails as
    `HTTPException`, the way every other classifier lookup in this codebase
    does, rather than reaching `place_order` after a write has already
    happened.

    The order of the checks below is the order the single-listing version
    always ran in -- locked status, then price, then fees, then the items,
    the fee kinds, the order status and only then the buyer, which is the
    first thing that writes. Each check now runs over every line before the
    next begins, so a two-line order refuses on the same grounds and in the
    same sequence a one-line order does.
    """
    if not lines:
        raise SaleInputInvalid("a sale needs at least one listing")
    listing_ids = [line.listing_id for line in lines]
    if len(set(listing_ids)) != len(listing_ids):
        raise SaleInputInvalid("a listing cannot appear twice on one order")

    # Locked and re-read *before* the status check, not after it. The
    # listings the caller named are whatever it had in hand, and the check
    # below is the one that decides whether this sale may happen at all, so
    # deciding it on an unlocked read is deciding it on a value another
    # operator may already have changed. The spec's *Concurrency* paragraph
    # requires the lock before the read for exactly this reason.
    #
    # Nothing downstream re-reads `status`: `place_order` takes the same
    # locks a moment later, but `order_writes` skips both `sellable_in_shop`
    # and `is_active` when a venue is given, because an outside listing is
    # neither. Its stock check is all that remains -- an accidental backstop
    # at quantity 1, with a message about stock rather than about the sale,
    # and no backstop at all above 1 (`app/seed.py`'s demo listings are 5 and
    # 20). Without this re-read two operators can both record a sale against
    # an already-ended listing and call `end_offer(sold=True)` twice.
    #
    # `offering_writes.end_offer` re-reads the same way, for the same reason;
    # this follows it. Taken through `offering_writes.lock_for_sale`, which
    # is the single owner of the acquisition order -- the listings' lots,
    # then their items, then the listings -- rather than a
    # `FOR UPDATE OF listing` of its own. This used to lock the listing alone
    # and call the order unchanged, which was true of `place_order` as it
    # then was and false of `offer`: that pair of orders was the deadlock
    # `docs/specs/lock-order-design.md` records. `place_order` and
    # `end_offer` below both take the same rows again, and find them held.
    #
    # One call for the whole order, never one per line: the canonical order
    # is only canonical across a whole acquisition, and two settlements
    # taking their lots one at a time in different orders is the deadlock
    # this door exists to prevent.
    #
    # `.get` and an explicit refusal rather than a subscript: this replaced a
    # `.scalar_one()`, whose `NoResultFound` named the row that was missing,
    # and a bare `KeyError` on an integer reads like a bug in this function
    # instead. A listing cannot actually vanish here -- `listing.sales_lot_id`
    # and `sales_order_item.listing_id` are both `RESTRICT` and nothing in
    # this codebase deletes a listing -- so this is the same 409-shaped
    # conflict as the status check below rather than a case a caller is
    # expected to meet.
    locked = offering_writes.lock_for_sale(db, listing_ids=listing_ids)
    ordered = sorted(lines, key=lambda line: line.listing_id)
    listings: list[Listing] = []
    for line in ordered:
        row = locked.listings.get(line.listing_id)
        if row is None:
            raise SaleRefused(f"Listing {line.listing_id} no longer exists")
        if row.status is not ListingStatus.active:
            # Names the platform as well as the listing: the spec's *Errors*
            # section asks for both, and an owner with the same item offered
            # in two places needs to know which offer this was about.
            raise SaleRefused(
                f"Listing {row.id} on {row.sales_venue.name} is not on offer "
                f"({row.status.value})"
            )
        listings.append(row)

    for line in ordered:
        _refuse_money(line.price, "Price")
    for fee in fees:
        _refuse_money(fee.amount, "A fee")

    # One order belongs to one platform -- `sales_order.sales_venue_id` is a
    # single column, and the fee kinds, the buyer and the default status are
    # all read off that one venue below. A caller that mixed two would get an
    # order filed under whichever listing happened to sort first, which is
    # a conflict worth naming rather than a silent choice.
    venue_ids = {row.sales_venue_id for row in listings}
    if len(venue_ids) > 1:
        raise SaleRefused(
            "one order cannot span two platforms: listings "
            + ", ".join(str(row.id) for row in listings)
        )
    venue = listings[0].sales_venue
    items_by_listing = {row.id: _shared_items(db, row) for row in listings}
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
        [
            order_writes.Line(
                listing_id=line.listing_id, quantity=1, unit_price=line.price
            )
            for line in ordered
        ],
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

    # `place_order` already created one share per offered item on every line
    # -- one for an item listing carrying the whole line's `amount`, one per
    # member for a lot listing carrying its cost-weighted part -- each with a
    # zero `fee_amount` (`order_writes._sync_shares`). Only the fee half is
    # this module's to fill in; it never constructs a share.
    #
    # `line.shares` is safe to read here: `place_order` passes `new_line=True`,
    # so `_sync_shares` never consults that collection and so never leaves it
    # cached empty from before the row existed. This was a `select()` while it
    # did.
    covered = [(row.id, item) for row in listings for item in items_by_listing[row.id]]
    fee_amounts = allocate(
        total_fees, _weights([item for _, item in covered], equal=equal_shares)
    )
    shares_by_item = {
        (order_line.listing_id, share.inventory_item_id): share
        for order_line in order.items
        for share in order_line.shares
    }
    for (listing_id, item), fee_amount in zip(covered, fee_amounts, strict=True):
        share = shares_by_item.get((listing_id, item.id))
        if share is None:
            raise ShareMissing(
                f"{item.item_code} has no share on the line for listing "
                f"{listing_id}; order_writes wrote one per offered item"
            )
        share.fee_amount = fee_amount

    for row in listings:
        offering_writes.end_offer(db, row, sold=True)
    db.flush()
    return order
