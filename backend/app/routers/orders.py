"""Purchase flow. Customers create and read their own orders; admins see all.

Orders reference `listing`, which references `inventory_item` -- one foreign
key chain to either a coin or a banknote. That chain is what keeping a single
inventory table buys: with split coin/currency tables every order line would
need two nullable foreign keys and a constraint saying exactly one is set.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from ..deps import AdminUser, CurrentUser, DbSession
from ..models import (
    Customer,
    Disposition,
    InventoryItem,
    Listing,
    SalesOrder,
    SalesOrderItem,
    SalesOrderStatus,
    User,
    UserRole,
)
from ..references import require_code
from ..schemas import OrderCreate, OrderOut, OrderStatusUpdate

router = APIRouter(prefix="/orders", tags=["orders"])

#: Statuses after which stock has left the building. Cancelling one of these
#: must not silently return stock that has already been posted.
SHIPPED_STATUSES = frozenset({"packed", "shipped", "delivered"})

# The same 404 whether the order does not exist or belongs to another
# customer, so order ids cannot be probed.
_ORDER_NOT_FOUND = "Order not found"


def _customer_for(db: Session, user: User) -> Customer:
    """Find or create the customer record behind a login.

    `customer` is separate from `users` because a buyer can exist without an
    account -- a walk-in or a phone order -- and because customer PII belongs
    on the sales side of the schema, not on the authentication table.
    """
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


def _order_out(order: SalesOrder, status_code: str) -> OrderOut:
    return OrderOut(
        id=order.id,
        customer_id=order.customer_id,
        status=status_code,
        total_amount=order.total_amount,
        placed_at=order.placed_at,
        items=[
            {
                "id": line.id,
                "listing_id": line.listing_id,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
            }
            for line in order.items
        ],
    )


def _status_code(db: Session, order: SalesOrder) -> str:
    row = db.get(SalesOrderStatus, order.sales_order_status_id)
    return row.code if row else "pending"


def _load(db: Session, order_id: int) -> SalesOrder | None:
    return db.scalar(
        select(SalesOrder)
        .where(SalesOrder.id == order_id)
        .options(selectinload(SalesOrder.items))
    )


@router.post("", status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: DbSession, user: CurrentUser) -> OrderOut:
    """Place an order, decrementing availability atomically.

    Listings are locked with SELECT ... FOR UPDATE and taken in a stable id
    order, so two concurrent buyers can neither oversell the same listing nor
    deadlock against each other. This is verified by tests that drive the
    handler from real threads -- a test that serialises its requests would
    pass even with the lock removed.
    """
    wanted = {line.listing_id: line.quantity for line in payload.items}

    listings = db.scalars(
        select(Listing)
        .where(Listing.id.in_(wanted))
        .order_by(Listing.id)
        .with_for_update()
    ).all()

    found = {listing.id for listing in listings}
    missing = sorted(set(wanted) - found)
    if missing:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown listing id(s): {missing}",
        )

    customer = _customer_for(db, user)
    order = SalesOrder(
        customer_id=customer.id,
        sales_order_status_id=require_code(db, SalesOrderStatus, "pending", "status"),
    )
    total = Decimal("0.00")

    for listing in listings:
        quantity = wanted[listing.id]
        if not listing.is_active:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Listing {listing.id} is not currently for sale",
            )
        if listing.quantity_available < quantity:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Only {listing.quantity_available} of listing "
                    f"{listing.id} remain (requested {quantity})"
                ),
            )

        listing.quantity_available -= quantity
        total += listing.price * quantity
        order.items.append(
            SalesOrderItem(
                listing_id=listing.id,
                quantity=quantity,
                unit_price=listing.price,
            )
        )

        # Selling the last unit moves the item along its disposition axis.
        # Status (how it came in) is untouched: the two are independent.
        if listing.quantity_available == 0:
            item = db.get(InventoryItem, listing.inventory_item_id)
            if item is not None:
                item.disposition_id = require_code(
                    db, Disposition, "sold", "disposition"
                )

    order.total_amount = total
    db.add(order)
    db.commit()
    db.refresh(order)
    return _order_out(order, "pending")


@router.get("")
def list_orders(db: DbSession, user: CurrentUser) -> list[OrderOut]:
    """Customers see their own orders; administrators see every order."""
    stmt = (
        select(SalesOrder)
        .options(selectinload(SalesOrder.items))
        .order_by(SalesOrder.id.desc())
    )
    if user.role is not UserRole.admin:
        customer = db.scalar(select(Customer).where(Customer.user_id == user.id))
        if customer is None:
            return []
        stmt = stmt.where(SalesOrder.customer_id == customer.id)

    orders = list(db.scalars(stmt).unique().all())
    return [_order_out(order, _status_code(db, order)) for order in orders]


def _visible_or_404(db: Session, order_id: int, user: User) -> SalesOrder:
    order = _load(db, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND
        )
    if user.role is not UserRole.admin:
        customer = db.scalar(select(Customer).where(Customer.user_id == user.id))
        # 404 rather than 403 so ids of other customers' orders do not leak.
        if customer is None or order.customer_id != customer.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND
            )
    return order


@router.get("/{order_id}")
def get_order(order_id: int, db: DbSession, user: CurrentUser) -> OrderOut:
    """One order. A customer sees only their own; an administrator sees any."""
    order = _visible_or_404(db, order_id, user)
    return _order_out(order, _status_code(db, order))


@router.patch("/{order_id}")
def update_order_status(
    order_id: int, payload: OrderStatusUpdate, db: DbSession, _admin: AdminUser
) -> OrderOut:
    """Advance an order. Cancelling an unshipped one returns its stock."""
    order = _load(db, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND
        )

    previous = _status_code(db, order)
    order.sales_order_status_id = require_code(
        db, SalesOrderStatus, payload.status, "status"
    )

    # Cancelling an order that had not shipped returns stock to the catalogue.
    if payload.status == "cancelled" and previous not in SHIPPED_STATUSES | {
        "cancelled"
    }:
        for line in order.items:
            listing = db.get(Listing, line.listing_id, with_for_update=True)
            if listing is None:
                continue
            listing.quantity_available += line.quantity
            item = db.get(InventoryItem, listing.inventory_item_id)
            if item is not None and listing.is_active:
                item.disposition_id = require_code(
                    db, Disposition, "listed", "disposition"
                )

    db.commit()
    db.refresh(order)
    return _order_out(order, payload.status)
