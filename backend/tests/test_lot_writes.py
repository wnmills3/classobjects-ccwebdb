"""Assembling a sales lot, and what cannot be in one."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app import lot_writes
from app.lot_writes import LotRefused, add_member, create_lot, remove_member
from app.models import InventoryItem, Listing, SalesLot, SalesLotStatus, utcnow
from sqlalchemy import select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]


def _lot(db: Session, title: str = "Three Morgan Dollars") -> SalesLot:
    """An empty assembling lot, through the writer rather than by hand."""
    return create_lot(db, title=title, description="")


def test_a_new_lot_is_assembling_and_empty(db: Session) -> None:
    """Nothing is grouped until someone adds it."""
    lot = _lot(db)
    assert lot.status is SalesLotStatus.assembling
    assert lot_writes.open_members(db, lot) == []


def test_membership_is_frozen_once_offered(
    db: Session, received_item: InventoryItem
) -> None:
    """The buyer is looking at that exact group."""
    lot = _lot(db)
    lot.status = SalesLotStatus.offered
    db.flush()
    with pytest.raises(LotRefused, match="offered"):
        add_member(db, lot, received_item)


def test_an_item_with_stock_above_one_cannot_join(
    db: Session, listing: Listing
) -> None:
    """A claim covers a whole item; a partly-sold item is not a whole item.

    The `listing` fixture's `quantity_available` is 5 (conftest's
    `build_listing` default), which is exactly the shape this refuses.
    """
    item = listing.inventory_item
    assert item is not None
    with pytest.raises(LotRefused, match="quantity"):
        add_member(db, _lot(db), item)


def test_a_split_item_cannot_join(db: Session, received_item: InventoryItem) -> None:
    """Offer the pieces, the same refusal `offering_writes` gives."""
    received_item.split_at = utcnow()
    db.flush()
    with pytest.raises(LotRefused, match="split"):
        add_member(db, _lot(db), received_item)


def test_an_item_already_in_an_open_lot_cannot_join_another(
    db: Session, received_item: InventoryItem
) -> None:
    """Refused with a message, not an IntegrityError in the user's face."""
    first, second = _lot(db, "First"), _lot(db, "Second")
    add_member(db, first, received_item)
    with pytest.raises(LotRefused, match="already in lot"):
        add_member(db, second, received_item)


def test_removing_a_member_releases_it(
    db: Session, received_item: InventoryItem
) -> None:
    """Removal deletes the membership row; the item can then join another lot.

    While a lot is `assembling` nothing has been offered or sold, so a
    removal is an edit, not history -- `released_at` is the record of a lot
    that sold or was dissolved, and only `offering_writes` (a later task)
    writes it. This is a deliberate rewrite of the brief's version of this
    test, which asserted `released_at is not None`; see task-2-report.md for
    why that would have been wrong.

    Both lots are taken as locals here. The previous draft of this test named
    `other_lot` in its body without taking it as a parameter -- a `NameError`
    that would have been discovered only at run time.
    """
    first, second = _lot(db, "First"), _lot(db, "Second")
    add_member(db, first, received_item)
    remove_member(db, first, received_item)
    membership = db.scalars(
        select(lot_writes.SalesLotItem).where(
            lot_writes.SalesLotItem.sales_lot_id == first.id,
            lot_writes.SalesLotItem.inventory_item_id == received_item.id,
        )
    ).one_or_none()
    assert membership is None
    add_member(db, second, received_item)  # must not raise
    assert [row.inventory_item_id for row in lot_writes.open_members(db, second)] == [
        received_item.id
    ]


def test_a_refused_add_writes_nothing(
    db: Session, listing: Listing, make_item: ItemFactory
) -> None:
    """A refused change leaves the lot exactly as it was.

    The same discipline `offering_writes.offer` follows: every condition is
    checked before anything is written, so a batch can be all or nothing.
    """
    lot = _lot(db)
    good = make_item(title="Joins fine", item_cost=Decimal("10.00"))
    add_member(db, lot, good)
    busy = listing.inventory_item
    assert busy is not None
    with pytest.raises(LotRefused):
        add_member(db, lot, busy)
    assert [row.inventory_item_id for row in lot_writes.open_members(db, lot)] == [
        good.id
    ]
