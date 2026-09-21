"""Assembling a sales lot, and what cannot be in one."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app import lot_writes
from app.buyers import venue_buyer
from app.lot_writes import LotRefused, add_member, create_lot, remove_member
from app.models import (
    InventoryItem,
    Listing,
    SalesLot,
    SalesLotStatus,
    SalesOrderStatus,
    SalesVenue,
    User,
    utcnow,
)
from app.order_writes import Line, place_order, return_stock
from app.references import require_code
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
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


def test_a_cancelled_orders_stock_does_not_bar_a_lot_join(
    db: Session,
    ebay_listing: Listing,
    admin_user: User,
    ebay_venue: SalesVenue,
) -> None:
    """A cancelled order must not permanently bar an item from every lot.

    Fix for I1: the first version of `_refuse_partial` restated "sold units"
    as a bare join with no filter on order status, so a `sales_order_item`
    row -- permanent even after the order that made it is cancelled and its
    stock returned -- barred the item forever. `sale_state.for_sale`
    already excludes a cancelled order via `OPEN_ORDER_STATUSES`; this
    reaches that same, single definition instead of a second one that
    forgets to.

    Built the way `routers.orders.update_order_status` builds it for a real
    cancellation: `order_writes.place_order` then
    `order_writes.return_stock`, not a hand-rolled row.
    """
    buyer = venue_buyer(db, ebay_venue, "coinfan88")
    order = place_order(
        db,
        buyer,
        [Line(listing_id=ebay_listing.id, quantity=1, unit_price=Decimal("120.00"))],
        admin_user,
        venue=ebay_venue,
        status_code="paid",
    )
    order.sales_order_status_id = require_code(
        db, SalesOrderStatus, "cancelled", "status"
    )
    return_stock(db, order)
    db.flush()
    item = ebay_listing.inventory_item
    assert item is not None
    add_member(db, _lot(db), item)  # must not raise


def test_removing_an_item_not_in_the_lot_is_refused(
    db: Session, received_item: InventoryItem
) -> None:
    """Fix for I2: a double-clicked remove refuses, not a raw `NoResultFound`.

    `remove_member` used `.one()`, so nothing here would have raised
    `LotRefused` before the fix -- a bare `NoResultFound` reached the
    caller as a 500 instead of the 409 every other refusal in this module
    produces.
    """
    lot = _lot(db)
    with pytest.raises(LotRefused, match="not in lot"):
        remove_member(db, lot, received_item)


def test_an_unrelated_integrity_error_still_propagates(
    db: Session, received_item: InventoryItem
) -> None:
    """Fix for I3: only `uq_sales_lot_item_open` is reported as "already in lot".

    A stale *released* row for this exact (lot, item) pair -- not a shape
    this module's own `remove_member` can leave behind, since it deletes
    rather than releases, but exactly what a pre-ruling row or a future bulk
    import could -- collides with `uq_sales_lot_item_pair` instead, a
    different constraint, when `add_member` tries to insert a fresh open row
    for the same pair. That must propagate as an `IntegrityError`, not get
    misreported as "already in lot": before the fix, the `except
    IntegrityError` here was unscoped and would have swallowed it.
    """
    lot = _lot(db)
    db.execute(
        text(
            "INSERT INTO sales_lot_item "
            "(sales_lot_id, inventory_item_id, released_at, created_at, updated_at) "
            "VALUES (:lot_id, :item_id, now(), now(), now())"
        ),
        {"lot_id": lot.id, "item_id": received_item.id},
    )
    with pytest.raises(IntegrityError):
        add_member(db, lot, received_item)


def test_open_members_are_ordered_by_inventory_item_id(
    db: Session, make_item: ItemFactory
) -> None:
    """Fix for I4: `open_members`'s order is id-ascending, not insertion order.

    Items are added to the lot in descending id order -- the exact reverse
    of the expected result -- so this only passes if the query truly orders
    by `inventory_item_id`. A naive version of this test that added items in
    ascending order would pass even with the `ORDER BY` deleted, because
    insertion order and id order would coincide on a fresh database (the
    project's own "fresh-DB ids coincide" trap).
    """
    lot = _lot(db)
    items = [make_item(title=f"Member {n}") for n in range(3)]
    for item in reversed(items):
        add_member(db, lot, item)
    assert [row.inventory_item_id for row in lot_writes.open_members(db, lot)] == [
        item.id for item in items
    ]


def test_removing_a_member_is_frozen_once_offered(
    db: Session, received_item: InventoryItem
) -> None:
    """Fix for I5: `remove_member` refuses once a lot is no longer assembling.

    A buyer looking at the group must not have it silently shrink.
    """
    lot = _lot(db)
    add_member(db, lot, received_item)
    lot.status = SalesLotStatus.offered
    db.flush()
    with pytest.raises(LotRefused, match="frozen"):
        remove_member(db, lot, received_item)
