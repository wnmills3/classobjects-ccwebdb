"""Purchase flow. Customers create and read their own orders; admins see all.

Orders reference `listing`, which references `inventory_item` -- one foreign
key chain to either a coin or a banknote. That chain is what keeping a single
inventory table buys: with split coin/currency tables every order line would
need two nullable foreign keys and a constraint saying exactly one is set.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

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
from ..order_writes import (
    Line,
    customer_for_user,
    payment_adjustment_due,
    place_order,
    revise_order,
)
from ..references import require_code
from ..schemas import OrderCreate, OrderOut, OrderRevision, OrderStatusUpdate

router = APIRouter(prefix="/orders", tags=["orders"])

#: Statuses after which stock has left the building. Cancelling one of these
#: must not silently return stock that has already been posted.
SHIPPED_STATUSES = frozenset({"packed", "shipped", "delivered"})

# The same 404 whether the order does not exist or belongs to another
# customer, so order ids cannot be probed.
_ORDER_NOT_FOUND = "Order not found"


def _order_out(order: SalesOrder, status_code: str) -> OrderOut:
    return OrderOut(
        id=order.id,
        customer_id=order.customer_id,
        customer_name=order.customer.display_name,
        customer_email=order.customer.email,
        status=status_code,
        total_amount=order.total_amount,
        placed_at=order.placed_at,
        items=[
            {
                "id": line.id,
                "listing_id": line.listing_id,
                "title": line.listing.inventory_item.source_title,
                "quantity": line.quantity,
                "unit_price": line.unit_price,
            }
            for line in order.items
        ],
        version=order.version,
        notes=order.notes,
        placed_by_email=order.placed_by.email if order.placed_by else None,
        payment_adjustment_due=payment_adjustment_due(status_code, order.changes),
    )


#: What `_order_out` reads, loaded up front: a page of orders would otherwise
#: cost a query per order for its customer and two per line for its title.
_ORDER_DETAIL = (
    selectinload(SalesOrder.customer),
    selectinload(SalesOrder.items)
    .selectinload(SalesOrderItem.listing)
    .selectinload(Listing.inventory_item),
    selectinload(SalesOrder.placed_by),
    selectinload(SalesOrder.changes),
)


def _status_code(db: Session, order: SalesOrder) -> str:
    row = db.get(SalesOrderStatus, order.sales_order_status_id)
    return row.code if row else "pending"


def _load(db: Session, order_id: int) -> SalesOrder | None:
    return db.scalar(
        select(SalesOrder).where(SalesOrder.id == order_id).options(*_ORDER_DETAIL)
    )


def order_out(db: Session, order_id: int) -> OrderOut:
    """One order, freshly loaded, as the API returns it."""
    db.expire_all()
    order = _load(db, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND
        )
    return _order_out(order, _status_code(db, order))


@router.post("", status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: DbSession, user: CurrentUser) -> OrderOut:
    """Place an order, decrementing availability atomically.

    Routes through `order_writes.place_order`, which locks listings with
    SELECT ... FOR UPDATE and takes them in a stable id order, so two
    concurrent buyers can neither oversell the same listing nor deadlock
    against each other. This is verified by tests that drive the handler from
    real threads -- a test that serialises its requests would pass even with
    the lock removed.
    """
    customer = customer_for_user(db, user)
    order = place_order(
        db,
        customer,
        [Line(line.listing_id, line.quantity) for line in payload.items],
        placed_by=user,
    )
    db.commit()
    return order_out(db, order.id)


@router.get("")
def list_orders(db: DbSession, user: CurrentUser, mine: bool = False) -> list[OrderOut]:
    """Customers see their own orders; administrators see every order.

    `mine=true` is the caller's own orders whatever their role. The shop's
    "Your orders" page asks for it: an administrator browsing the shop is a
    customer there, and everyone's orders belong in the console.
    """
    stmt = select(SalesOrder).options(*_ORDER_DETAIL).order_by(SalesOrder.id.desc())
    if mine or user.role is not UserRole.admin:
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
    # Cancelling an unshipped order put its stock back on sale. Moving it on
    # again would leave an order standing on stock already offered to the next
    # buyer -- the same coins sold twice. Re-sending "cancelled" stays harmless.
    if previous == "cancelled" and payload.status != "cancelled":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order #{order.id} is cancelled and its stock has been "
            "returned. Place a new order instead.",
        )
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


@router.put("/{order_id}")
def revise(
    order_id: int, payload: OrderRevision, db: DbSession, admin: AdminUser
) -> OrderOut:
    """Replace an order's lines, prices, customer and notes, all or nothing."""
    order = _load(db, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND
        )
    customer = db.get(Customer, payload.customer_id)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such customer"
        )
    revise_order(
        db,
        order,
        status_code=_status_code(db, order),
        customer=customer,
        lines=[Line(i.listing_id, i.quantity, i.unit_price) for i in payload.items],
        notes=payload.notes,
        version=payload.version,
        by=admin,
    )
    try:
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order #{order_id} was changed while saving. Reload and retry.",
        ) from exc
    return order_out(db, order_id)
