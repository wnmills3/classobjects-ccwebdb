"""Purchase flow. Customers create and read their own orders; admins see all."""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from ..deps import AdminUser, CurrentUser, DbSession
from ..models import Coin, Order, OrderItem, OrderStatus, UserRole
from ..schemas import OrderCreate, OrderOut, OrderStatusUpdate

router = APIRouter(prefix="/orders", tags=["orders"])


@router.post("", response_model=OrderOut, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: DbSession, user: CurrentUser) -> Order:
    """Place an order, decrementing inventory atomically.

    Rows are locked with SELECT ... FOR UPDATE and taken in a stable id order,
    so two concurrent buyers cannot oversell the same item or deadlock against
    each other.
    """
    wanted = {line.coin_id: line.quantity for line in payload.items}

    coins = db.scalars(
        select(Coin)
        .where(Coin.id.in_(wanted))
        .order_by(Coin.id)
        .with_for_update()
    ).all()

    found = {coin.id for coin in coins}
    missing = sorted(set(wanted) - found)
    if missing:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown coin id(s): {missing}",
        )

    order = Order(user_id=user.id, status=OrderStatus.pending)
    total = Decimal("0.00")

    for coin in coins:
        quantity = wanted[coin.id]
        if not coin.is_active:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{coin.title!r} is not currently for sale",
            )
        if coin.quantity < quantity:
            db.rollback()
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Only {coin.quantity} of {coin.title!r} remain "
                    f"(requested {quantity})"
                ),
            )

        coin.quantity -= quantity
        total += coin.price * quantity
        order.items.append(
            OrderItem(coin_id=coin.id, quantity=quantity, unit_price=coin.price)
        )

    order.total_amount = total
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


@router.get("", response_model=list[OrderOut])
def list_orders(db: DbSession, user: CurrentUser) -> list[Order]:
    """Customers see their own orders; administrators see every order."""
    stmt = select(Order).order_by(Order.id.desc())
    if user.role is not UserRole.admin:
        stmt = stmt.where(Order.user_id == user.id)
    return list(db.scalars(stmt).unique().all())


@router.get("/{order_id}", response_model=OrderOut)
def get_order(order_id: int, db: DbSession, user: CurrentUser) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
    if user.role is not UserRole.admin and order.user_id != user.id:
        # 404 rather than 403 so ids of other customers' orders do not leak.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )
    return order


@router.patch("/{order_id}", response_model=OrderOut)
def update_order_status(
    order_id: int, payload: OrderStatusUpdate, db: DbSession, _admin: AdminUser
) -> Order:
    order = db.get(Order, order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Order not found"
        )

    previous = order.status
    order.status = payload.status

    # Cancelling an order that had not shipped returns stock to the catalogue.
    if payload.status is OrderStatus.cancelled and previous is not OrderStatus.cancelled:
        for item in order.items:
            coin = db.get(Coin, item.coin_id, with_for_update=True)
            if coin is not None:
                coin.quantity += item.quantity

    db.commit()
    db.refresh(order)
    return order
