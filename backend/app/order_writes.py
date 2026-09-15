"""Writing orders: the one place an order's stock moves.

Checkout, an administrator placing an order for a customer, and an
administrator revising one all come through here, so the row locking that
stops an oversell exists once. Functions flush and never commit; the caller's
request owns the transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import NoReturn

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Customer,
    Disposition,
    InventoryItem,
    Listing,
    SalesOrder,
    SalesOrderChange,
    SalesOrderChangeKind,
    SalesOrderItem,
    SalesOrderStatus,
    User,
)
from .models.base import utcnow
from .references import require_code

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
    """Lock listings FOR UPDATE in id order, so contenders never deadlock."""
    rows = db.scalars(
        select(Listing)
        .where(Listing.id.in_(ids))
        .order_by(Listing.id)
        .with_for_update()
    ).all()
    found = {listing.id: listing for listing in rows}
    missing = sorted(ids - set(found))
    if missing:
        _refuse(db, status.HTTP_404_NOT_FOUND, f"Unknown listing id(s): {missing}")
    return found


def _after_stock_change(db: Session, listing: Listing, before: int) -> None:
    """Move the item's disposition when its listing's stock crosses zero."""
    after = listing.quantity_available
    item = db.get(InventoryItem, listing.inventory_item_id)
    if item is None:
        return
    if before > 0 and after == 0:
        item.disposition_id = require_code(db, Disposition, "sold", "disposition")
    elif before == 0 and after > 0 and listing.is_active:
        item.disposition_id = require_code(db, Disposition, "listed", "disposition")


def place_order(
    db: Session,
    customer: Customer,
    lines: Sequence[Line],
    placed_by: User,
    notes: str | None = None,
) -> SalesOrder:
    """Create an order, taking its stock under row locks."""
    listings = _lock_listings(db, {line.listing_id for line in lines})
    for line in sorted(lines, key=lambda line: line.listing_id):
        listing = listings[line.listing_id]
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

    order = SalesOrder(
        customer_id=customer.id,
        sales_order_status_id=require_code(db, SalesOrderStatus, "pending", "status"),
        placed_by_id=placed_by.id,
        notes=notes,
    )
    total = Decimal("0.00")
    for line in sorted(lines, key=lambda line: line.listing_id):
        listing = listings[line.listing_id]
        before = listing.quantity_available
        listing.quantity_available -= line.quantity
        price = listing.price if line.unit_price is None else line.unit_price
        total += price * line.quantity
        order.items.append(
            SalesOrderItem(
                listing_id=listing.id, quantity=line.quantity, unit_price=price
            )
        )
        _after_stock_change(db, listing, before)
    order.total_amount = total
    db.add(order)
    db.flush()
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
