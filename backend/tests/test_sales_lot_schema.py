"""The database guarantees behind a sales lot.

Each of these is a constraint rather than a rule in Python, because each one
is a rule two concurrent requests could otherwise both believe they satisfy.

Every `Listing(...)` here passes `currency_id`. It is `nullable=False` with no
default, so omitting it fires `IntegrityError` on NOT NULL *before* PostgreSQL
evaluates any CHECK -- which would leave both constraint tests green while
testing nothing at all. Each raises-test asserts on the constraint's *name*
for the same reason.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.models import (
    Currency,
    InventoryItem,
    Listing,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesVenue,
    utcnow,
)
from app.references import require_code
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _usd(db: Session) -> int:
    """The currency id every listing below needs. See the module docstring."""
    return require_code(db, Currency, "USD", "currency")


def test_an_item_is_in_at_most_one_open_lot(
    db: Session, received_item: InventoryItem
) -> None:
    """Two open lots holding one coin would offer it twice."""
    first, second = SalesLot(title="A"), SalesLot(title="B")
    db.add_all([first, second])
    db.flush()
    db.add(SalesLotItem(sales_lot_id=first.id, inventory_item_id=received_item.id))
    db.flush()
    db.add(SalesLotItem(sales_lot_id=second.id, inventory_item_id=received_item.id))
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "uq_sales_lot_item_open" in str(excinfo.value)


def test_a_released_membership_frees_the_item(
    db: Session, received_item: InventoryItem
) -> None:
    """A dissolved lot's members can be grouped again; history is kept."""
    first, second = SalesLot(title="A"), SalesLot(title="B")
    db.add_all([first, second])
    db.flush()
    db.add(
        SalesLotItem(
            sales_lot_id=first.id,
            inventory_item_id=received_item.id,
            released_at=utcnow(),
        )
    )
    db.flush()
    db.add(SalesLotItem(sales_lot_id=second.id, inventory_item_id=received_item.id))
    db.flush()  # must not raise: the partial index counts only open rows


def test_a_released_item_cannot_rejoin_the_same_lot(
    db: Session, received_item: InventoryItem
) -> None:
    """`uq_sales_lot_item_pair` is not partial: a pair exists at most once, ever.

    The previous test proves the *partial* index only counts open rows, using
    two different lots so it never touches `uq_sales_lot_item_pair`. This is
    the case that constraint alone is responsible for: the same item rejoining
    the very same lot it already (now released) belonged to.
    """
    lot = SalesLot(title="A")
    db.add(lot)
    db.flush()
    db.add(
        SalesLotItem(
            sales_lot_id=lot.id,
            inventory_item_id=received_item.id,
            released_at=utcnow(),
        )
    )
    db.flush()
    db.add(SalesLotItem(sales_lot_id=lot.id, inventory_item_id=received_item.id))
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "uq_sales_lot_item_pair" in str(excinfo.value)


def test_a_new_lot_defaults_to_assembling_and_version_one(db: Session) -> None:
    """Pins the status enum's first member and the optimistic-concurrency start."""
    lot = SalesLot(title="A")
    db.add(lot)
    db.flush()
    assert lot.status == SalesLotStatus.assembling
    assert lot.version == 1


def test_a_listing_names_an_item_or_a_lot_but_not_both(
    db: Session, received_item: InventoryItem, ebay_venue: SalesVenue
) -> None:
    """Both would make "which items did this offer?" ambiguous."""
    lot = SalesLot(title="A")
    db.add(lot)
    db.flush()
    db.add(
        Listing(
            inventory_item_id=received_item.id,
            sales_lot_id=lot.id,
            sales_venue_id=ebay_venue.id,
            currency_id=_usd(db),
            price=Decimal("10.00"),
            quantity_available=1,
        )
    )
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "ck_listing_item_xor_lot" in str(excinfo.value)


def test_a_listing_names_at_least_one_of_them(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """Neither would be an offer of nothing."""
    db.add(
        Listing(
            sales_venue_id=ebay_venue.id,
            currency_id=_usd(db),
            price=Decimal("10.00"),
            quantity_available=1,
        )
    )
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "ck_listing_item_xor_lot" in str(excinfo.value)


def test_a_lot_listing_offers_exactly_one(db: Session, ebay_venue: SalesVenue) -> None:
    """A lot is a specific group of specific coins; there is only one of it."""
    lot = SalesLot(title="A")
    db.add(lot)
    db.flush()
    db.add(
        Listing(
            sales_lot_id=lot.id,
            sales_venue_id=ebay_venue.id,
            currency_id=_usd(db),
            price=Decimal("10.00"),
            quantity_available=2,
        )
    )
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "ck_listing_lot_quantity_one" in str(excinfo.value)
