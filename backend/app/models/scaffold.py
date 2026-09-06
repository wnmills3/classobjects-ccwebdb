"""The original storefront scaffold: users, a flat catalogue, and orders.

This predates the target schema in `docs/database-design.md` and is superseded
by it -- `coins` is a strictly less capable `inventory_item`. It is kept for
now because the storefront routers, the auth flow and the oversell concurrency
test all run against it, and moving those is a separate piece of work from
building the schema.

`users` is **not** scaffold: it is the login table the whole application uses,
and the target schema references it from `customer`, `item_status_history`,
`location_history` and both type catalogues.

One rename was forced. The scaffold's PostgreSQL enum type was called
``item_kind``, and the target schema has a reference *table* of that name. In
PostgreSQL a table implicitly creates a composite type, so tables and types
share one namespace and the two genuinely collide. The enum is ``coin_kind``.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, enum_column, utcnow

__all__ = [
    "Coin",
    "CoinKind",
    "Order",
    "OrderItem",
    "OrderStatus",
    "User",
    "UserRole",
]


class UserRole(str, enum.Enum):
    admin = "admin"
    customer = "customer"


class CoinKind(str, enum.Enum):
    """Scaffold catalogue kind. Superseded by the `item_kind` reference table,
    which carries eight values rather than two."""

    coin = "coin"
    banknote = "banknote"


class OrderStatus(str, enum.Enum):
    pending = "pending"
    paid = "paid"
    shipped = "shipped"
    cancelled = "cancelled"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole, "user_role"),
        default=UserRole.customer,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    orders: Mapped[list[Order]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Coin(Base):
    """A single scaffold catalogue item: a coin or a banknote."""

    __tablename__ = "coins"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    kind: Mapped[CoinKind] = mapped_column(
        enum_column(CoinKind, "coin_kind"), default=CoinKind.coin, nullable=False
    )
    country: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    denomination: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    composition: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    grade: Mapped[str] = mapped_column(String(50), default="", nullable=False)
    certification: Mapped[str] = mapped_column(String(100), default="", nullable=False)
    mint_mark: Mapped[str] = mapped_column(String(20), default="", nullable=False)

    # Money is NUMERIC(12, 2) and maps to Decimal -- never float.
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    image_url: Mapped[str] = mapped_column(String(500), default="", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )

    order_items: Mapped[list[OrderItem]] = relationship(back_populates="coin")


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=False
    )
    status: Mapped[OrderStatus] = mapped_column(
        enum_column(OrderStatus, "order_status"),
        default=OrderStatus.pending,
        nullable=False,
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    user: Mapped[User] = relationship(back_populates="orders")
    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(
        ForeignKey("orders.id", ondelete="CASCADE"), index=True, nullable=False
    )
    coin_id: Mapped[int] = mapped_column(
        ForeignKey("coins.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    # Price captured at purchase time, so later catalogue edits do not
    # rewrite historical orders.
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[Order] = relationship(back_populates="items")
    coin: Mapped[Coin] = relationship(back_populates="order_items")
