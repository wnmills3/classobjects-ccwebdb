"""Writing orders: the one place an order's stock moves.

Checkout, an administrator placing an order for a customer, and an
administrator revising one all come through here, so the row locking that
stops an oversell exists once.

Functions flush and never commit; the caller's request owns the transaction.
The one thing that ends it here is `_refuse`, which rolls back before raising
so the row locks are released rather than held until the request unwinds --
reached from `place_order`, `revise_order` and `_lock_listings`. A caller
that catches one of those refusals is holding a rolled-back session, not a
live one.

**Known limit: `_refuse`'s rollback is whole-session, even inside a
savepoint.** `db.rollback()` unwinds the outermost transaction, not just the
nearest `db.begin_nested()`, so a `_refuse` reached from inside
`auctions.settle`'s savepoint (`place_order` is called there, through
`sales_writes.record_sale_lines`, always with a `venue`) would discard more
than that one buyer's order -- the whole settlement, not merely its
savepoint. It errs safe, never unsafe: more is rolled back, never less. It is
also unreached today, because `venue` being non-`None` already skips the two
checks (`sellable_in_shop`, `is_active`) that gate on a shop listing, and
settlement always requests exactly the quantity each lot listing offers, so
the one check that still applies with a venue -- `quantity_available <
line.quantity` -- has nothing to find. Recorded rather than fixed: narrowing
`_refuse` to roll back to the active savepoint would need it to know whether
one is open, which is a caller concern this module does not otherwise track.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import NoReturn

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from .allocation import allocate
from .models import (
    Customer,
    Disposition,
    InventoryItem,
    Listing,
    ListingStatus,
    SalesOrder,
    SalesOrderChange,
    SalesOrderChangeKind,
    SalesOrderItem,
    SalesOrderItemShare,
    SalesOrderStatus,
    SalesVenue,
    User,
)
from .models.base import utcnow
from .offering_writes import end_offer, lock_for_sale, offered_items, sellable_in_shop
from .references import require_code
from .sale_snapshot import take as take_snapshot
from .sales_venues import store_venue_id

_CENTS = Decimal("0.01")


@dataclass(frozen=True)
class Line:
    """One wanted line: a listing, how many, and optionally the price."""

    listing_id: int
    quantity: int
    unit_price: Decimal | None = None


def money(value: Decimal) -> str:
    """A money value as history stores it: two places, no symbol."""
    return str(value.quantize(_CENTS))


def _refuse(db: Session, code: int, detail: str) -> NoReturn:
    """Roll back, releasing any row locks, and raise."""
    db.rollback()
    raise HTTPException(status_code=code, detail=detail)


def customer_for_user(db: Session, user: User) -> Customer:
    """Find or create the customer record behind a login."""
    customer = db.scalar(select(Customer).where(Customer.user_id == user.id))
    if customer is None:
        customer = Customer(
            user_id=user.id,
            display_name=user.full_name or user.email,
            email=user.email,
        )
        db.add(customer)
        db.flush()
    return customer


def _lock_listings(db: Session, ids: set[int]) -> dict[int, Listing]:
    """Take every row these listings reach, and hand back the listings.

    **This function does not decide the lock order, and that is the point.**
    `place_order` is handed listing ids, but `offering_writes` must take
    items before listings -- its listing set is derived from claims, and the
    claim-uniqueness guarantee depends on holding the items before that
    derivation is read. Were the two orders different, a checkout of a lot
    and an `offer` or `end_offer` touching one of its coins could each hold
    what the other waited for, and Postgres would abort one with a 500
    instead of the clean refusal either path gives.

    So the acquisition is `offering_writes.lock_for_sale`: the lot rows these
    listings belong to, then their items, then the listings -- each kind in
    one ascending statement, with a confirming re-read of the member set. It
    is the same function `offer` and `end_offer` call, which is what makes the
    order a single definition rather than four call sites that agree by hand.

    What stays this function's own is everything after the lock.
    ``populate_existing`` matters as much as the lock itself -- without it, a
    listing already in the session's identity map (eagerly loaded by the
    caller before the lock was taken) is returned unchanged, locked but still
    holding pre-lock values for `quantity_available`, `is_active` and
    `version` -- and `sales_venue` is eagerly loaded rather than lazy, because
    `sellable_in_shop` reads it for every locked listing and a lazy load would
    emit that SELECT while these rows are held FOR UPDATE. `lock_for_sale`
    does both, for these reasons; see `_lock_listing_rows`.

    The result is narrowed to the ids asked for. `lock_for_sale` also returns
    the listings it reached through the items -- a member's own store listing
    that the lot's offer paused, say -- and those are rows this transaction
    holds but this order knows nothing about.
    """
    locked = lock_for_sale(db, listing_ids=ids).listings
    found = {
        listing_id: locked[listing_id] for listing_id in ids if listing_id in locked
    }
    missing = sorted(ids - set(found))
    if missing:
        _refuse(db, status.HTTP_404_NOT_FOUND, f"Unknown listing id(s): {missing}")
    return found


def _after_stock_change(db: Session, listing: Listing, before: int) -> None:
    """Move every offered item's disposition when the stock crosses zero.

    "Every offered item" is `offered_items`: one for an item listing, the
    lot's open members for a lot listing -- whose own `inventory_item_id` is
    NULL, so reading that column would leave every member of a sold lot at
    `listed`. A lot must be asked before `offering_writes.end_offer` releases
    its memberships, which is the order the sale path runs in.
    """
    after = listing.quantity_available
    if before > 0 and after == 0:
        code = "sold"
    elif before == 0 and after > 0 and listing.is_active:
        code = "listed"
    else:
        return
    disposition_id = require_code(db, Disposition, code, "disposition")
    for item in offered_items(db, listing):
        item.disposition_id = disposition_id


def _lot_sold_out(listing: Listing, before: int) -> bool:
    """Whether this stock change is a lot listing's only unit being bought.

    `ck_listing_lot_quantity_one` caps a lot listing at one unit, so a lot is
    either wholly sold or not sold at all -- there is no partly-sold lot to
    reason about, and this is a question with a yes/no answer rather than a
    proportion.
    """
    return (
        listing.sales_lot_id is not None
        and before > 0
        and listing.quantity_available == 0
    )


def _settle_sold_lots(db: Session, listings: Sequence[Listing]) -> None:
    """End each lot listing this order has just bought outright, as sold.

    **Why the sale path ends a lot listing when it leaves an item listing
    alone.** A sold-out item listing is left `active` at zero stock, which is
    odd but harmless: the item is `sold`, and nothing else in the database
    claims otherwise. A lot cannot be left that way. Its lifecycle is
    `assembling -> offered -> sold | dissolved` and `sales_lot_item.
    released_at` is set when the lot is sold or dissolved (spec, *`sales_lot`
    and `sales_lot_item`*), so a lot left `offered` after its coins have been
    bought is a false statement about the lot *and* leaves every membership
    row open -- each one blocking an already-sold coin under
    `uq_sales_lot_item_open`, so those coins could never join another lot.

    Ended through `offering_writes.end_offer(sold=True)` rather than by
    writing the lot here: that module is the only writer of
    `sales_lot.status`, `sales_lot_item.released_at` and `listing.status`,
    and it is what also ends the members' own store listings this lot's offer
    paused -- which must end, not resume, now that the coins are sold. Ending
    the listing is also what keeps the lot's `sold` from being overwritten:
    `_end` decides `sold` or `dissolved` from how the listing ends, so a lot
    marked sold under a listing still standing would be rewritten to
    `dissolved` the day anyone withdrew it.

    Called **after** the line's shares exist, never before: `_sync_shares`
    divides the money among `offered_items`, and `end_offer` releases exactly
    those memberships, so a lot ended first would leave the sale with no
    shares at all.

    **The lock order.** `_lock_listings` has already taken these rows
    through `offering_writes.lock_for_sale` -- lot, then items, then
    listings, the one canonical order -- so `end_offer` below re-locks rows
    this transaction already holds. A checkout that took them any other way
    could deadlock against a concurrent `offer` or `end_offer` of one of the
    lot's coins; `test_buying_a_lot_races_offering_one_of_its_coins`
    (`tests/test_offer_races.py`) is what stops that inversion.
    """
    for listing in listings:
        end_offer(db, listing, sold=True)


def _line(
    db: Session, listing: Listing, quantity: int, price: Decimal
) -> SalesOrderItem:
    """A new order line, with the item as it is being sold."""
    return SalesOrderItem(
        listing_id=listing.id,
        quantity=quantity,
        unit_price=price,
        item_snapshot=take_snapshot(db, listing),
        snapshot_at=utcnow(),
    )


def _items_by_id(db: Session, ids: Sequence[int]) -> list[InventoryItem]:
    """The named items, in id order -- the one order every lot reader uses.

    One statement rather than a `db.get` per id, and returning rows rather
    than `InventoryItem | None`, so a caller zipping this against a list of
    the same ids fails loudly (`strict=True`) if one has gone missing rather
    than quietly weighting it as nothing.
    """
    return list(
        db.scalars(
            select(InventoryItem)
            .where(InventoryItem.id.in_(ids))
            .order_by(InventoryItem.id)
        ).all()
    )


def _sync_shares(
    db: Session,
    line: SalesOrderItem,
    listing: Listing,
    amount: Decimal,
    *,
    new_line: bool = False,
) -> None:
    """Make a line's shares match its money, whether they are new or old.

    `place_order` calls this for a line it created in this same call, and
    `revise_order` for both that case and a line whose quantity or price just
    changed, where a share already exists and only `amount` need move --
    two writers, one place the rule "a share's amount equals its line's
    money" is enforced, so it cannot drift between them.

    `new_line=True` says the caller created this line in this call, so it
    provably has no shares yet and the lookup below must be skipped. Reading
    `line.shares` for such a line would emit a SELECT that can only come back
    empty -- one per line, inside the `FOR UPDATE` window `_lock_listings`'
    own docstring asks callers not to widen -- and, worse, would leave the
    collection **cached empty** for the rest of the session: adding the share
    with `db.add` does not invalidate a relationship collection an earlier
    read already populated. That stale empty collection is what made
    `sales_writes` and `routers.offers` read shares with their own `select()`
    instead of the relationship. With the flag, a checkout line's shares are
    never consulted and never poisoned.

    **Where the member list comes from differs by branch, and that is the
    whole of this function's lot handling.**

    The *insert* branch asks `offering_writes.offered_items` -- one item for
    an item listing, the lot's open members for a lot listing, in item id
    order -- and divides `amount` among them with `allocation.allocate`,
    weighted by `total_cost`, the same weighting `sales_writes._weights`
    gives the fees. It cannot ask `line.shares`: `new_line=True` says there
    are none, and reading the collection is what the flag exists to prevent.

    The *update* branch asks the **shares that already exist**, never
    `offered_items`. By the time a revision arrives, a sold lot's
    memberships have been released by `offering_writes._end`, so
    `offered_items` answers "none" for exactly the line a revision is most
    likely to touch. The shares are the line's own record of which items it
    covers; the new `amount` is redistributed across all of them, by the
    same cost weighting, rather than moved into one row.

    An update branch that finds no shares at all -- a line written before
    shares existed -- falls through to the insert branch, which is what the
    single-item version of this function did.

    Fees are not known at checkout or a plain revision -- neither prices
    them -- so a new share's `fee_amount` keeps its zero default, and an
    existing share's is left as `sales_writes` last set it. An outside sale
    fills fees in there, not here.
    """
    existing: dict[int, SalesOrderItemShare] = {}
    if not new_line:
        existing = {share.inventory_item_id: share for share in line.shares}

    if existing:
        held = _items_by_id(db, sorted(existing))
        for item, share_amount in zip(
            held, allocate(amount, [item.total_cost for item in held]), strict=True
        ):
            existing[item.id].amount = share_amount
        return

    offered = offered_items(db, listing)
    for item, share_amount in zip(
        offered, allocate(amount, [item.total_cost for item in offered]), strict=True
    ):
        db.add(
            SalesOrderItemShare(
                sales_order_item_id=line.id,
                inventory_item_id=item.id,
                amount=share_amount,
                fee_amount=Decimal("0.00"),
            )
        )


def place_order(
    db: Session,
    customer: Customer,
    lines: Sequence[Line],
    placed_by: User,
    notes: str | None = None,
    *,
    venue: SalesVenue | None = None,
    status_code: str = "pending",
    external_order_id: str | None = None,
) -> SalesOrder:
    """Create an order, taking its stock under row locks.

    `venue` is None for a shop checkout: the order is the store's and the
    shop's rules apply -- the listing must be this shop's, active, and hold
    the stock asked for. Passing a venue means the sale happened somewhere
    else and is being recorded after the fact, so those first two checks do
    not apply: an eBay listing is not meant to be sellable in the shop, and by
    the time a sale is recorded the offer is over. What still applies to both
    is the row lock and the stock check.

    `status_code` is `pending` for checkout, which the buyer has not paid
    yet. An outside platform has already collected the money (`paid`), and an
    auction house may already have shipped (`delivered`).

    A line that buys a **lot** listing outright ends that listing as sold and
    ends the lot with it, which a sold-out item listing does not get
    (`_settle_sold_lots` has the whole of why). Only for a shop order: a sale
    recorded from elsewhere passes a `venue`, and `sales_writes.record_sale`
    ends its own listing.

    `external_order_id` is the platform's own order number, for a sale
    recorded from elsewhere; None for a shop checkout. It is a constructor
    argument, not a later assignment, for the same reason `total_amount` is
    priced before the order exists: assigning it after the row has already
    been inserted would be a second statement against that row, bumping
    `version` to 2 on a brand-new order.

    Raises `HTTPException`: 404 for a listing that does not exist, and 409
    for one no longer sellable in the shop, one currently paused or ended, or
    one without the stock asked for. Every one goes through `_refuse`, which
    rolls the transaction back before raising -- so a caller that catches
    one holds a rolled-back session. There is no version check here: `place_order`
    takes no version from its caller, and the `FOR UPDATE` lock this function
    takes before any of the above makes a stale read impossible rather than
    merely detecting one after the fact -- that is `revise_order`'s job, on an
    order a caller already holds and may have read stale.
    """
    listings = _lock_listings(db, {line.listing_id for line in lines})
    for line in sorted(lines, key=lambda line: line.listing_id):
        listing = listings[line.listing_id]
        if venue is None:
            # `active_only=False`: whether the listing is this shop's at all is
            # one question, and whether it is on offer this minute is another
            # with its own message below. `offering_writes` owns both halves.
            if not sellable_in_shop(listing, active_only=False):
                _refuse(
                    db,
                    status.HTTP_409_CONFLICT,
                    f"Listing {listing.id} is not sold in this shop",
                )
            if not listing.is_active:
                _refuse(
                    db,
                    status.HTTP_409_CONFLICT,
                    f"Listing {listing.id} is not currently for sale",
                )
        if listing.quantity_available < line.quantity:
            _refuse(
                db,
                status.HTTP_409_CONFLICT,
                f"Only {listing.quantity_available} of listing {listing.id} remain "
                f"(requested {line.quantity})",
            )

    # Priced in its own pass, before the order exists: `SalesOrder` is
    # version-tracked (`version_id_col`), and a share needs a later flush to
    # get its line's id. If `total_amount` were assigned after that flush had
    # already inserted the order row, the assignment would be a second
    # statement against that row -- an UPDATE the version column counts,
    # bumping `version` to 2 on a brand-new order. Pricing everything first
    # means the single INSERT below already carries the right total.
    prices = {
        line.listing_id: (
            listings[line.listing_id].price
            if line.unit_price is None
            else line.unit_price
        )
        for line in lines
    }
    total = sum(
        (prices[line.listing_id] * line.quantity for line in lines), Decimal("0.00")
    )
    order = SalesOrder(
        customer_id=customer.id,
        sales_venue_id=store_venue_id(db) if venue is None else venue.id,
        sales_order_status_id=require_code(db, SalesOrderStatus, status_code, "status"),
        placed_by_id=placed_by.id,
        notes=notes,
        total_amount=total,
        external_order_id=external_order_id,
    )
    # Added now, not after the loop: a share needs its line's id, which does
    # not exist until both the order and the line have been flushed. Adding
    # the (still-empty) order here lets each `order.items.append` below
    # cascade the new line into the session, so the per-line flush actually
    # assigns it one.
    db.add(order)
    # Lines to give shares once this loop's inserts are flushed, exactly as
    # `revise_order` does it: a share needs its line's id, which does not
    # exist until the line has been flushed, so the sync is collected here and
    # run after one flush rather than a flush per line inside the loop.
    to_sync: list[tuple[SalesOrderItem, Listing, Decimal]] = []
    # Lot listings this order buys outright, to be ended as sold once the
    # shares below exist -- see `_settle_sold_lots` for both halves of why.
    # Only for a shop order: a sale recorded from somewhere else passes a
    # `venue`, and `sales_writes.record_sale` ends that listing itself.
    sold_lots: list[Listing] = []
    for line in sorted(lines, key=lambda line: line.listing_id):
        listing = listings[line.listing_id]
        before = listing.quantity_available
        listing.quantity_available -= line.quantity
        price = prices[line.listing_id]
        # The snapshot before the stock change: the item as it was offered.
        new_item = _line(db, listing, line.quantity, price)
        order.items.append(new_item)
        to_sync.append((new_item, listing, price * line.quantity))
        _after_stock_change(db, listing, before)
        if venue is None and _lot_sold_out(listing, before):
            sold_lots.append(listing)
    # Explicit -- production runs with autoflush disabled, so nothing here can
    # rely on an implicit one -- and after every `_after_stock_change` above,
    # which reads `InventoryItem` rows this flush would otherwise touch first.
    db.flush()
    # Every line gets shares, a single item included: `sale_state` and
    # realized gain both ask that table "which items did this order carry",
    # and a line with no shares would silently answer "none". A lot listing's
    # line is divided among its members; an item listing's line is
    # one share carrying the whole amount. `new_line=True` because every line
    # here was created just above: `_sync_shares` must not read a collection
    # it would only find empty and then leave cached that way.
    for synced_line, synced_listing, amount in to_sync:
        _sync_shares(db, synced_line, synced_listing, amount, new_line=True)
    db.flush()
    # After that flush, not before: `end_offer` re-reads the listing with
    # `populate_existing`, which would otherwise discard the stock change
    # made above rather than read it back.
    _settle_sold_lots(db, sold_lots)
    db.add(
        SalesOrderChange(
            sales_order_id=order.id,
            changed_at=utcnow(),
            changed_by_id=placed_by.id,
            change=SalesOrderChangeKind.placed,
            to_value=placed_by.email,
        )
    )
    db.flush()
    return order


EDITABLE_STATUSES = frozenset({"pending", "paid"})


def _order_status_code(db: Session, order: SalesOrder) -> str:
    """The order's status code, read fresh from its (possibly just-locked) row.

    `get_one`: a status id that names no row is a broken database, and
    reading it as `pending` would let an order in any state be revised.
    """
    return db.get_one(SalesOrderStatus, order.sales_order_status_id).code


def revise_order(
    db: Session,
    order: SalesOrder,
    *,
    customer: Customer,
    lines: Sequence[Line],
    notes: str | None,
    version: int,
    by: User,
) -> bool:
    """Make an order's contents match `lines`, moving stock by the difference.

    Locks the order row first -- always before `_lock_listings` takes any of
    the sale's own rows, so every writer that takes both takes them in the
    same order -- and
    re-reads it with `populate_existing`, including its items: a concurrent
    checkout or another revision may have changed the order or the stock a
    caller read before this call. The status actually checked is read from
    that freshly locked row, never from what a caller may have read earlier.

    Every check runs before anything changes, so a refused save changes no
    line and no stock. Returns whether anything changed.

    A line added here that buys a lot listing outright ends it as sold, the
    same as a checkout does (`_settle_sold_lots`). A line **removed** whose
    listing has already ended is refused instead, because its stock has
    nowhere to go back to -- the same refusal, for the same reason, that
    `routers.orders` makes when such an order is cancelled.
    """
    order = db.execute(
        select(SalesOrder)
        .where(SalesOrder.id == order.id)
        .options(selectinload(SalesOrder.items))
        .with_for_update()
        .execution_options(populate_existing=True)
    ).scalar_one()
    # Captured once, up front: after a flush fails below, every instance in
    # the session is expired, and reading an ORM attribute -- even a plain
    # id -- issues a SELECT that raises `PendingRollbackError` instead of
    # returning the cached value, because the transaction is awaiting
    # rollback. Every message built after mutations begin uses this plain
    # int, never `order.id`.
    order_id = order.id

    locked_status = _order_status_code(db, order)
    if locked_status not in EDITABLE_STATUSES:
        _refuse(
            db,
            status.HTTP_409_CONFLICT,
            f"Order #{order_id} is {locked_status}; only pending or paid orders "
            "can be changed.",
        )
    if version != order.version:
        _refuse(
            db,
            status.HTTP_409_CONFLICT,
            f"Order #{order_id} was changed by someone else (you have version "
            f"{version}, current is {order.version}). Reload and reapply your changes.",
        )

    current = {item.listing_id: item for item in order.items}
    desired = {line.listing_id: line for line in lines}
    ids = set(current) | set(desired)

    changes: list[SalesOrderChange] = []
    try:
        listings = _lock_listings(db, ids)

        deltas: dict[int, int] = {}
        for listing_id in sorted(ids):
            have = current[listing_id].quantity if listing_id in current else 0
            want = desired[listing_id].quantity if listing_id in desired else 0
            deltas[listing_id] = want - have
            listing = listings[listing_id]
            if deltas[listing_id] > 0:
                if not sellable_in_shop(listing, active_only=False):
                    _refuse(
                        db,
                        status.HTTP_409_CONFLICT,
                        f"Listing {listing_id} is not sold in this shop",
                    )
                if not listing.is_active:
                    _refuse(
                        db,
                        status.HTTP_409_CONFLICT,
                        f"Listing {listing_id} is not currently for sale",
                    )
                if listing.quantity_available < deltas[listing_id]:
                    _refuse(
                        db,
                        status.HTTP_409_CONFLICT,
                        f"Only {listing.quantity_available} more of listing "
                        f"{listing_id} are available (this change needs "
                        f"{deltas[listing_id]})",
                    )
            # Giving stock back to an ended listing strands what it sold, and
            # this is the same refusal `routers.orders._no_stock_to_return`
            # makes for a cancellation -- the other way to hand a line's stock
            # back. `_after_stock_change` moves items off `sold` only while
            # the listing is active, so the quantity would return to a listing
            # nobody can see while the coins stayed `sold` and un-offerable.
            # Reached by removing a **lot** line, whose listing this module
            # ended when the lot was bought, and by removing or shrinking a
            # line of a sale recorded from an outside platform.
            elif deltas[listing_id] < 0 and listing.status is ListingStatus.ended:
                _refuse(
                    db,
                    status.HTTP_409_CONFLICT,
                    f"Listing {listing_id} has ended, so the stock this order "
                    "holds cannot be put back on sale. A lot is sold as one "
                    "group and its listing ends with the sale.",
                )

        stamp = utcnow()

        def record(
            kind: SalesOrderChangeKind,
            listing_id: int | None = None,
            before: str | None = None,
            after: str | None = None,
        ) -> None:
            """Queue one history row for this save."""
            changes.append(
                SalesOrderChange(
                    sales_order_id=order_id,
                    changed_at=stamp,
                    changed_by_id=by.id,
                    change=kind,
                    listing_id=listing_id,
                    from_value=before,
                    to_value=after,
                )
            )

        old_total = order.total_amount
        # Lines to bring into money-agreement with their shares, once this
        # loop's mutations are flushed: a new line has no share yet, and a
        # modified one's existing share now disagrees with its new
        # quantity or price. Deferred rather than synced inline, because a
        # new line's id -- which its share needs -- does not exist until it
        # is flushed, and this loop still has row locks to take for the
        # other listings first.
        # The fourth element is `_sync_shares`' `new_line`: true for a line
        # this save created, which provably has no share yet, false for one
        # whose existing share has to be found and moved.
        to_sync: list[tuple[SalesOrderItem, Listing, Decimal, bool]] = []
        # As in `place_order`: a revision that adds a lot line buys that lot
        # outright, and the lot has to end sold rather than stay offered
        # (`_settle_sold_lots`). No venue test here, because the checks above
        # already refuse to add stock from a listing that is not the shop's.
        sold_lots: list[Listing] = []
        for listing_id in sorted(ids):
            listing = listings[listing_id]
            delta = deltas[listing_id]
            existing = current.get(listing_id)
            line = desired.get(listing_id)
            added: tuple[SalesOrderItem, Decimal] | None = None
            if existing is None and line is not None:
                price = listing.price if line.unit_price is None else line.unit_price
                # The snapshot before the stock change, as in `place_order`:
                # the item as it was offered, not as this line's sale left it.
                added = (_line(db, listing, line.quantity, price), price)
            if delta:
                before = listing.quantity_available
                listing.quantity_available -= delta
                _after_stock_change(db, listing, before)
                if _lot_sold_out(listing, before):
                    sold_lots.append(listing)
            if added is not None and line is not None:
                new_item, price = added
                order.items.append(new_item)
                to_sync.append((new_item, listing, price * line.quantity, True))
                record(
                    SalesOrderChangeKind.line_added,
                    listing_id,
                    after=f"{line.quantity} @ {money(price)}",
                )
            elif existing is not None and line is None:
                order.items.remove(existing)
                record(
                    SalesOrderChangeKind.line_removed,
                    listing_id,
                    before=f"{existing.quantity} @ {money(existing.unit_price)}",
                )
            elif existing is not None and line is not None:
                money_changed = False
                if delta:
                    record(
                        SalesOrderChangeKind.quantity,
                        listing_id,
                        str(existing.quantity),
                        str(line.quantity),
                    )
                    existing.quantity = line.quantity
                    money_changed = True
                if (
                    line.unit_price is not None
                    and line.unit_price != existing.unit_price
                ):
                    record(
                        SalesOrderChangeKind.unit_price,
                        listing_id,
                        money(existing.unit_price),
                        money(line.unit_price),
                    )
                    existing.unit_price = line.unit_price
                    money_changed = True
                if money_changed:
                    to_sync.append(
                        (
                            existing,
                            listing,
                            existing.unit_price * existing.quantity,
                            False,
                        )
                    )

        if customer.id != order.customer_id:
            record(
                SalesOrderChangeKind.customer,
                None,
                order.customer.display_name,
                customer.display_name,
            )
            order.customer = customer
        if (notes or None) != (order.notes or None):
            record(SalesOrderChangeKind.notes, None, order.notes, notes)
            order.notes = notes

        new_total = sum(
            (item.unit_price * item.quantity for item in order.items), Decimal("0.00")
        )
        if new_total != old_total:
            record(SalesOrderChangeKind.total, None, money(old_total), money(new_total))
            order.total_amount = new_total

        if changes:
            # A line-only edit leaves the order row untouched, and the version
            # only moves when that row is updated -- so touch it.
            order.updated_at = stamp
            db.add_all(changes)
            # Flushed before `_sync_shares`: a new line in `to_sync` has no
            # id until this INSERT runs, and `SalesOrderItemShare.
            # sales_order_item_id` is NOT NULL. Explicit, matching
            # `place_order` -- production runs with autoflush disabled.
            db.flush()
            for synced_line, synced_listing, amount, is_new in to_sync:
                _sync_shares(db, synced_line, synced_listing, amount, new_line=is_new)
            # After the shares, and after a flush, for the two reasons
            # `place_order` gives at the same point.
            db.flush()
            _settle_sold_lots(db, sold_lots)
    except StaleDataError:
        # **Defense in depth, not a live path.** Every version-tracked row
        # this function writes -- `sales_order`, `listing`, `inventory_item`,
        # `sales_lot` -- was locked and re-read first: the order row above,
        # and the rest through `_lock_listings` and so
        # `offering_writes.lock_for_sale`, which locks and re-reads every
        # item `_after_stock_change` writes. So a concurrent edit to a coin
        # does not refuse a revision
        # (`test_a_concurrently_edited_item_no_longer_refuses_a_revision`,
        # `tests/test_order_revision_race.py`).
        # `test_a_stale_data_error_inside_revise_order_is_a_409_not_a_500`,
        # in the same file, covers the clause itself by forcing the failure
        # from a patched flush -- so "defense in depth" does not mean
        # "unexercised": make this clause re-raise and that test goes red.
        #
        # If it is ever reached, it must surface the way the version check
        # above does rather than as an unhandled 500 -- using `order_id`, not
        # `order.id`: every instance in the session is expired once the flush
        # has failed, and reading an attribute off one issues a SELECT that
        # raises `PendingRollbackError` instead of the value.
        _refuse(
            db,
            status.HTTP_409_CONFLICT,
            f"Order #{order_id} or one of its items was changed while saving. "
            "Reload and retry.",
        )
    return bool(changes)


def record_status_change(
    db: Session, order: SalesOrder, before: str, after: str, by: User
) -> None:
    """Write a `status` history row when the status actually changed."""
    if before == after:
        return
    db.add(
        SalesOrderChange(
            sales_order_id=order.id,
            changed_at=utcnow(),
            changed_by_id=by.id,
            change=SalesOrderChangeKind.status,
            from_value=before,
            to_value=after,
        )
    )


def return_stock(db: Session, order: SalesOrder) -> None:
    """Add each line's quantity back to its listing.

    Takes the order's rows the same way `place_order` and `revise_order` do,
    through `_lock_listings` and so through `offering_writes.lock_for_sale`
    -- lot rows, then items, then listings, each FOR UPDATE in id order and
    re-read -- so this cannot deadlock against a concurrent checkout, a
    revision, or an offer touching the same coins. The caller locks and
    re-reads `order` itself first, before calling this, the same order-first
    sequence `revise_order` uses.
    """
    listings = _lock_listings(db, {item.listing_id for item in order.items})
    for item in sorted(order.items, key=lambda item: item.listing_id):
        listing = listings[item.listing_id]
        before = listing.quantity_available
        listing.quantity_available += item.quantity
        _after_stock_change(db, listing, before)


def payment_adjustment_due(
    status_code: str, changes: Sequence[SalesOrderChange]
) -> bool:
    """Whether a paid order's total changed after it was marked paid."""
    if status_code != "paid":
        return False
    paid = [
        c.id
        for c in changes
        if c.change is SalesOrderChangeKind.status and c.to_value == "paid"
    ]
    if not paid:
        return False
    return any(
        c.change is SalesOrderChangeKind.total and c.id > max(paid) for c in changes
    )
