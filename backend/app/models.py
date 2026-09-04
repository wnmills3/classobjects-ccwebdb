"""Database models for the numismatic inventory and sales platform."""

from __future__ import annotations

import enum
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enum_column(py_enum: type[enum.Enum], name: str):
    """Store enum *values* (not member names) in a native PostgreSQL enum."""
    return Enum(
        py_enum,
        name=name,
        values_callable=lambda e: [member.value for member in e],
    )


class UserRole(str, enum.Enum):
    admin = "admin"
    customer = "customer"


class ItemKind(str, enum.Enum):
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
        _enum_column(UserRole, "user_role"),
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
    """A single inventory item: a coin or a banknote."""

    __tablename__ = "coins"

    id: Mapped[int] = mapped_column(primary_key=True)
    sku: Mapped[str] = mapped_column(
        String(64), unique=True, index=True, nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="", nullable=False)

    kind: Mapped[ItemKind] = mapped_column(
        _enum_column(ItemKind, "item_kind"), default=ItemKind.coin, nullable=False
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
        _enum_column(OrderStatus, "order_status"),
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
