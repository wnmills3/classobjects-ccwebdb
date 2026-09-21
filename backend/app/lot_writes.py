"""Assembling a sales lot: who is in it, and what cannot be.

Membership changes only while a lot is `assembling`; everything about a
lot's *offer* -- freezing it, selling it, dissolving it -- belongs to
`offering_writes`. Keeping assembly separate here is what stops this module
needing the claim rules.

The two modules import each other, so the cycle is broken the way this
codebase already breaks the `offering_writes`/`sale_state` one: this module
imports `offering_writes` at the top, and `offering_writes` imports this one
*inside* the two functions that need it. `tests/conftest.py` imports
`app.lot_writes` first, and a module-level import back from `offering_writes`
raises `ImportError` in exactly that order.

Functions flush and never commit; the caller's request owns the transaction,
as everywhere else in this codebase.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import offering_writes, sale_state
from .models import (
    InventoryItem,
    Listing,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
)

__all__ = [
    "EmptyLot",
    "LotRefused",
    "add_member",
    "create_lot",
    "open_members",
    "remove_member",
]


class LotRefused(Exception):
    """The lot cannot be changed as asked, with the reason for a person."""


class EmptyLot(LotRefused):
    """The lot has no open members, and the action asked needs at least one.

    A subclass, not a field on `LotRefused`, mirroring `SaleInputInvalid` on
    `SaleRefused` (`app.sales_writes`): an empty lot is bad input the router
    maps to 422, by `except`-clause order, while a bare `LotRefused` is a
    conflict mapped to 409 -- and every existing `except LotRefused` keeps
    catching this too. Nothing in this module raises it: assembling a lot
    never requires it to be non-empty, only offering one does, and that is
    `offering_writes._lot_members`' decision, which raises this.
    """


def create_lot(db: Session, *, title: str, description: str = "") -> SalesLot:
    """Start a new lot, assembling and with no members yet."""
    lot = SalesLot(title=title, description=description)
    db.add(lot)
    db.flush()
    return lot


def open_members(db: Session, lot: SalesLot) -> list[SalesLotItem]:
    """The lot's current members, in inventory-item-id order.

    That order is decided once, here: every downstream reader -- locking
    order, share order, snapshot order -- depends on the whole system
    agreeing on one deterministic sequence rather than each picking its own.
    """
    return list(
        db.scalars(
            select(SalesLotItem)
            .where(
                SalesLotItem.sales_lot_id == lot.id,
                SalesLotItem.released_at.is_(None),
            )
            .order_by(SalesLotItem.inventory_item_id)
        ).all()
    )


def _refuse_unless_assembling(db: Session, lot: SalesLot) -> None:
    """Refuse any membership change once a lot is no longer assembling.

    Once offered, the buyer is looking at that exact group -- both `offer`
    and a removal or addition after that point would silently change what
    was already shown, so both are refused here rather than just one.

    The row is taken FOR UPDATE and re-read before `status` is asked, not
    asked as the caller loaded it. `SalesLot.version` gives this table an
    optimistic lock, but a membership change writes `sales_lot_item` and
    never updates `sales_lot`, so the version never moves and cannot see
    this race at all: without the lock, an add that read `assembling` before
    `offering_writes.offer` froze the lot would insert a member into a lot
    already on sale -- a coin in a group a buyer is looking at, with no
    claim on it and nothing to stop it being offered again elsewhere.
    `offering_writes._lot_members` takes the same lock before it reads the
    membership, so whichever of the two arrives second waits, re-reads, and
    finds what the first one wrote. It is the lot's row throughout, so the
    pair can never each hold what the other needs.

    Flushed first, because `Session.refresh` expires the instance *before* it
    autoflushes: a pending change to this lot would be discarded rather than
    written. No caller can do that today, and this is the line that keeps it
    that way once Task 5 gives lots a router.
    """
    db.flush()
    db.refresh(lot, with_for_update=True)
    if lot.status is not SalesLotStatus.assembling:
        raise LotRefused(
            f"lot #{lot.id} is {lot.status.value}, so its membership is frozen"
        )


def _refuse_unofferable(item: InventoryItem) -> None:
    """Refuse an item that is not, right now, an item this module can claim.

    The same asks `offering_writes._refuse_unofferable` makes, in the same
    order and the same words, minus its venue check -- a lot has no venue.

    The already-sold check is **not** among the things left out, and an
    earlier version of this docstring was wrong to say `_refuse_partial` was
    "this module's own version of 'already spoken for'". It is not: its order
    half asks `sale_state.for_sale`, which filters to `OPEN_ORDER_STATUSES`
    and stops seeing the item the moment its order ships. Without the
    disposition check below, a coin sold and shipped could be grouped into a
    fresh lot and offered again. A lot must never be *stricter* than an
    offer, so the list is `offering_writes.SOLD_AWAY` exactly, which
    deliberately excludes `returned_by_buyer`: that coin came back and
    offering it again -- alone or in a group -- is what happens next.
    """
    if item.deleted_at is not None:
        raise LotRefused(f"{item.item_code}: has been deleted")
    if item.split_at is not None:
        raise LotRefused(
            f"{item.item_code}: has been split into pieces; offer the pieces"
        )
    status = item.status.code
    if status != "received":
        raise LotRefused(f"{item.item_code}: is not received (it is {status})")
    disposition = item.disposition.code
    if disposition in offering_writes.SOLD_AWAY:
        raise LotRefused(
            f"{item.item_code}: has already been sold (it is {disposition})"
        )


def _refuse_partial(db: Session, item: InventoryItem) -> None:
    """Refuse an item that is not, right now, a whole item to claim.

    A claim covers a whole item; a partly-sold or partly-listed item is not
    one (spec, *`sales_lot` and `sales_lot_item`*). Above one unit still on
    offer, or any unit an open order still holds, are both that shape -- a
    broken-up lot is not a whole item either.

    The sold half is asked through `sale_state.for_sale` rather than a second
    query, so "spoken for" has one definition -- the one
    `offering_writes._refuse_sold` already defers to -- instead of two that
    can drift apart. A query restated here would have to rediscover
    `for_sale`'s own filter to `OPEN_ORDER_STATUSES`; the first version of
    this function did not, and so refused an item forever over an order that
    had since been cancelled and its stock returned.
    """
    over_listed = db.execute(
        select(Listing.id, Listing.quantity_available)
        .where(
            Listing.inventory_item_id == item.id,
            Listing.status.in_(offering_writes.ON_OFFER),
            Listing.quantity_available > 1,
        )
        .limit(1)
    ).first()
    if over_listed is not None:
        listing_id, quantity = over_listed
        raise LotRefused(
            f"{item.item_code}: has more than one unit (quantity {quantity}) "
            f"on listing #{listing_id}; a lot claims a whole item"
        )
    held_by_order = [
        use
        for use in sale_state.for_sale(db, [item.id]).get(item.id, [])
        if use.kind == "order"
    ]
    if held_by_order:
        raise LotRefused(
            f"{item.item_code}: has sold units ({held_by_order[0].text}); "
            "a lot claims a whole item"
        )


def _lot_holding(db: Session, item_id: int) -> SalesLot | None:
    """The lot with an open membership on this item, if one holds it now.

    At most one row can match -- `uq_sales_lot_item_open` guarantees it --
    so this is one definition of "already in a lot", asked both before the
    write below and again if the write races it.
    """
    row = db.scalars(
        select(SalesLotItem).where(
            SalesLotItem.inventory_item_id == item_id,
            SalesLotItem.released_at.is_(None),
        )
    ).one_or_none()
    return row.lot if row is not None else None


def add_member(db: Session, lot: SalesLot, item: InventoryItem) -> SalesLotItem:
    """Add one item to an assembling lot.

    Every condition is checked before anything is written, so a refused add
    leaves the lot exactly as it was and a batch of adds can be all or
    nothing -- the same discipline `offering_writes.offer` follows.
    """
    _refuse_unless_assembling(db, lot)
    _refuse_unofferable(item)
    _refuse_partial(db, item)
    other = _lot_holding(db, item.id)
    if other is not None:
        raise LotRefused(f"{item.item_code} is already in lot #{other.id}")

    row = SalesLotItem(sales_lot_id=lot.id, inventory_item_id=item.id)
    try:
        with db.begin_nested():
            db.add(row)
            db.flush()
    except IntegrityError as exc:
        # `uq_sales_lot_item_open` is the backstop, not the check above: a
        # concurrent add can still win the race between that read and this
        # flush. Inside a savepoint, not bare, so only this failed insert
        # rolls back -- the caller's transaction, and whatever it already
        # wrote, stays usable. By the time this flush can see the conflict,
        # the other session's row must already be committed (Postgres blocks
        # this insert on it rather than erroring immediately), so it can be
        # named the same way the sequential check above names it.
        #
        # `db.flush()` flushes the whole session, not just `row` -- in
        # production (autoflush=False, unlike this module's own test
        # fixture) a caller's own unrelated pending write can be what
        # actually violates a constraint inside this savepoint. Only this
        # specific constraint is a race this function knows how to explain;
        # anything else is a different bug and must not be misreported as
        # "already in lot".
        constraint = getattr(getattr(exc.orig, "diag", None), "constraint_name", None)
        if constraint != "uq_sales_lot_item_open":
            raise
        other = _lot_holding(db, item.id)
        if other is None:
            raise LotRefused(f"{item.item_code} is already in another lot") from exc
        raise LotRefused(f"{item.item_code} is already in lot #{other.id}") from exc
    return row


def remove_member(db: Session, lot: SalesLot, item: InventoryItem) -> None:
    """Take an item out of an assembling lot.

    Deletes the open membership row outright -- an explicit `db.delete`,
    never a mutation of `lot.members` -- rather than releasing it. While a
    lot is `assembling`, nothing has been offered or sold, so a removal here
    is an edit to the group, not history worth keeping: `released_at` is the
    record of a lot that sold or was dissolved, and `offering_writes._end` is
    its only writer. This module never assigns it.

    Because removal deletes rather than releasing, `uq_sales_lot_item_pair`
    -- the non-partial unique constraint on `(sales_lot_id,
    inventory_item_id)` -- never stops the same item rejoining the same lot
    later: a fresh row is a fresh pair.
    """
    _refuse_unless_assembling(db, lot)
    row = db.scalars(
        select(SalesLotItem).where(
            SalesLotItem.sales_lot_id == lot.id,
            SalesLotItem.inventory_item_id == item.id,
            SalesLotItem.released_at.is_(None),
        )
    ).one_or_none()
    if row is None:
        raise LotRefused(f"{item.item_code} is not in lot #{lot.id}")
    db.delete(row)
    db.flush()
