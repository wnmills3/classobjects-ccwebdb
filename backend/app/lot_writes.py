"""Assembling a sales lot: who is in it, and what cannot be.

Membership changes only while a lot is `assembling`; everything about a
lot's *offer* -- freezing it, selling it, dissolving it -- belongs to
`offering_writes`, which a later task widens to cover lots. Keeping assembly
separate here is what stops this module needing the claim rules, and it is
why `offering_writes` can import from here without a cycle.

Functions flush and never commit; the caller's request owns the transaction,
as everywhere else in this codebase.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from . import offering_writes
from .models import (
    InventoryItem,
    Listing,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesOrderItem,
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
    never requires it to be non-empty, only offering one does, which is
    `offering_writes`'s decision to make in a later task.
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


def _refuse_unless_assembling(lot: SalesLot) -> None:
    """Refuse any membership change once a lot is no longer assembling.

    Once offered, the buyer is looking at that exact group -- both `offer`
    and a removal or addition after that point would silently change what
    was already shown, so both are refused here rather than just one.
    """
    if lot.status is not SalesLotStatus.assembling:
        raise LotRefused(
            f"lot #{lot.id} is {lot.status.value}, so its membership is frozen"
        )


def _refuse_unofferable(item: InventoryItem) -> None:
    """Refuse an item that is not, right now, an item this module can claim.

    The same three asks `offering_writes._refuse_unofferable` makes, in the
    same order and the same words -- minus its venue and already-sold-away
    checks, which do not apply here: a lot has no venue, and `_refuse_partial`
    below is this module's own version of "already spoken for".
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


def _refuse_partial(db: Session, item: InventoryItem) -> None:
    """Refuse an item that is not, right now, a whole item to claim.

    A claim covers a whole item; a partly-sold or partly-listed item is not
    one (spec, *`sales_lot` and `sales_lot_item`*). Above one unit still on
    offer, or any unit already sold through any listing of it, are both that
    shape -- a broken-up lot is not a whole item either.
    """
    over_listed = db.scalar(
        select(Listing.id)
        .where(
            Listing.inventory_item_id == item.id,
            Listing.status.in_(offering_writes.ON_OFFER),
            Listing.quantity_available > 1,
        )
        .limit(1)
    )
    if over_listed is not None:
        raise LotRefused(
            f"{item.item_code}: quantity_available is above 1 on listing "
            f"#{over_listed}; a lot claims a whole item"
        )
    sold = db.scalar(
        select(SalesOrderItem.id)
        .join(Listing, Listing.id == SalesOrderItem.listing_id)
        .where(Listing.inventory_item_id == item.id)
        .limit(1)
    )
    if sold is not None:
        raise LotRefused(f"{item.item_code}: has sold units; a lot claims a whole item")


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
    _refuse_unless_assembling(lot)
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
        other = _lot_holding(db, item.id)
        if other is None:
            raise LotRefused(f"{item.item_code} is already in lot") from exc
        raise LotRefused(f"{item.item_code} is already in lot #{other.id}") from exc
    return row


def remove_member(db: Session, lot: SalesLot, item: InventoryItem) -> None:
    """Take an item out of an assembling lot.

    Deletes the open membership row outright -- an explicit `db.delete`,
    never a mutation of `lot.members` -- rather than releasing it. While a
    lot is `assembling`, nothing has been offered or sold, so a removal here
    is an edit to the group, not history worth keeping: `released_at` is the
    record of a lot that sold or was dissolved, and `offering_writes` (a
    later task) is its only writer. This module never assigns it.

    Because removal deletes rather than releasing, `uq_sales_lot_item_pair`
    -- the non-partial unique constraint on `(sales_lot_id,
    inventory_item_id)` -- never stops the same item rejoining the same lot
    later: a fresh row is a fresh pair.
    """
    _refuse_unless_assembling(lot)
    row = db.scalars(
        select(SalesLotItem).where(
            SalesLotItem.sales_lot_id == lot.id,
            SalesLotItem.inventory_item_id == item.id,
            SalesLotItem.released_at.is_(None),
        )
    ).one()
    db.delete(row)
    db.flush()
