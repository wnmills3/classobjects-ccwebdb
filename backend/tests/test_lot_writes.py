"""Assembling a sales lot, and what cannot be in one."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app import lot_writes
from app.buyers import venue_buyer
from app.lot_writes import LotRefused, add_member, create_lot, remove_member
from app.models import (
    Disposition,
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
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]


def _lot(db: Session, title: str = "Three Morgan Dollars") -> SalesLot:
    """An empty assembling lot, through the writer rather than by hand."""
    return create_lot(db, title=title, description="")


def _set_disposition(db: Session, item: InventoryItem, code: str) -> None:
    """Move an item's disposition the way another write path would."""
    item.disposition_id = require_code(db, Disposition, code, "disposition")
    db.flush()


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


@pytest.mark.parametrize("disposition", ["sold", "shipped", "delivered"])
def test_a_sold_item_cannot_be_grouped(
    db: Session, received_item: InventoryItem, disposition: str
) -> None:
    """A coin a buyer has bought is not the business's to group and sell again.

    `_refuse_partial` does not cover this and an earlier docstring claimed it
    did: its order half asks `sale_state.for_sale`, which filters to
    `OPEN_ORDER_STATUSES` and stops seeing the item once the order ships --
    so after shipping, nothing refused it. All three `SOLD_AWAY`
    dispositions, because the shipped and delivered ones are exactly the two
    an open-order check cannot reach.

    The disposition is set directly rather than by selling a lot. A member
    sold inside a lot is never moved off `listed` today
    (`order_writes._after_stock_change` returns silently on a lot listing's
    NULL item id), so a lot-sale version of this test would go red for that
    reason instead of this one, and would keep passing if this guard were
    deleted. That route closes in Task 4 and the test for it belongs there.
    """
    lot = _lot(db)
    _set_disposition(db, received_item, disposition)

    with pytest.raises(LotRefused, match="has already been sold"):
        add_member(db, lot, received_item)


def test_a_returned_item_can_still_be_grouped(
    db: Session, received_item: InventoryItem
) -> None:
    """`returned_by_buyer` is deliberately not in `SOLD_AWAY`.

    That coin came back, and offering it again -- alone or in a group -- is
    exactly what happens next. A lot must never be stricter than an offer,
    and `offering_writes._refuse_sold` lets this one through.
    """
    lot = _lot(db)
    _set_disposition(db, received_item, "returned_by_buyer")

    row = add_member(db, lot, received_item)

    assert row.inventory_item_id == received_item.id


def test_the_lot_lock_re_reads_the_row_it_locked(
    db: Session, make_item: ItemFactory
) -> None:
    """A membership change reads `status` from the row, not from the session.

    The twin of `test_offering_writes.py`'s
    `test_the_item_lock_re_reads_the_row_it_locked`, and the reason
    `_refuse_unless_assembling` takes the lot FOR UPDATE: under READ
    COMMITTED a request that waited on that lock resumes holding the values
    it read *before* the wait, so a lock without a re-read would let an add
    that saw `assembling` put a member into a lot `offering_writes.offer`
    has since frozen -- a coin inside a group a buyer is looking at, with no
    claim on it.

    `synchronize_session=False` is what makes the session's copy stale on
    purpose: without it SQLAlchemy would helpfully update the identity map,
    and the test would pass whether or not anything re-read the row. There is
    no second connection here, so this measures the re-read, not the wait;
    the wait is Postgres's to keep, and nothing in this suite may open a
    second session against the shared test database.

    The item is built *before* the UPDATE, and that ordering is the test.
    `make_item` commits, and this session expires on commit, so building it
    afterwards would reload `lot.status` by itself -- the first version of
    this test did exactly that and passed with the lock deleted.
    """
    lot = _lot(db)
    joiner = make_item()
    db.execute(
        update(SalesLot)
        .where(SalesLot.id == lot.id)
        .values(status=SalesLotStatus.offered, version=SalesLot.version + 1)
        .execution_options(synchronize_session=False)
    )
    assert lot.status is SalesLotStatus.assembling  # stale, on purpose

    with pytest.raises(LotRefused, match="frozen"):
        add_member(db, lot, joiner)
