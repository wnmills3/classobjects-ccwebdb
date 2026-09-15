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


EDITABLE_STATUSES = frozenset({"pending", "paid"})


def revise_order(
    db: Session,
    order: SalesOrder,
    *,
    status_code: str,
    customer: Customer,
    lines: Sequence[Line],
    notes: str | None,
    version: int,
    by: User,
) -> bool:
    """Make an order's contents match `lines`, moving stock by the difference.

    Every check runs before anything changes, so a refused save changes no
    line and no stock. Returns whether anything changed.
    """
    if status_code not in EDITABLE_STATUSES:
        _refuse(
            db,
            status.HTTP_409_CONFLICT,
            f"Order #{order.id} is {status_code}; only pending or paid orders "
            "can be changed.",
        )
    if version != order.version:
        _refuse(
            db,
            status.HTTP_409_CONFLICT,
            f"Order #{order.id} was changed by someone else (you have version "
            f"{version}, current is {order.version}). Reload and reapply your changes.",
        )

    current = {item.listing_id: item for item in order.items}
    desired = {line.listing_id: line for line in lines}
    ids = set(current) | set(desired)
    listings = _lock_listings(db, ids)

    deltas: dict[int, int] = {}
    for listing_id in sorted(ids):
        have = current[listing_id].quantity if listing_id in current else 0
        want = desired[listing_id].quantity if listing_id in desired else 0
        deltas[listing_id] = want - have
        listing = listings[listing_id]
        if deltas[listing_id] > 0:
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
                    f"Only {listing.quantity_available} more of listing {listing_id} "
                    f"are available (this change needs {deltas[listing_id]})",
                )

    stamp = utcnow()
    changes: list[SalesOrderChange] = []

    def record(
        kind: SalesOrderChangeKind,
        listing_id: int | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> None:
        """Queue one history row for this save."""
        changes.append(
            SalesOrderChange(
                sales_order_id=order.id,
                changed_at=stamp,
                changed_by_id=by.id,
                change=kind,
                listing_id=listing_id,
                from_value=before,
                to_value=after,
            )
        )

    old_total = order.total_amount
    for listing_id in sorted(ids):
        listing = listings[listing_id]
        delta = deltas[listing_id]
        if delta:
            before = listing.quantity_available
            listing.quantity_available -= delta
            _after_stock_change(db, listing, before)
        existing = current.get(listing_id)
        line = desired.get(listing_id)
        if existing is None and line is not None:
            price = listing.price if line.unit_price is None else line.unit_price
            order.items.append(
                SalesOrderItem(
                    listing_id=listing_id, quantity=line.quantity, unit_price=price
                )
            )
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
            if delta:
                record(
                    SalesOrderChangeKind.quantity,
                    listing_id,
                    str(existing.quantity),
                    str(line.quantity),
                )
                existing.quantity = line.quantity
            if line.unit_price is not None and line.unit_price != existing.unit_price:
                record(
                    SalesOrderChangeKind.unit_price,
                    listing_id,
                    money(existing.unit_price),
                    money(line.unit_price),
                )
                existing.unit_price = line.unit_price

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
        db.flush()
    return bool(changes)


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
