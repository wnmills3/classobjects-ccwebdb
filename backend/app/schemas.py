"""Pydantic request/response models."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .models import CoinKind, OrderStatus, UserRole

# --------------------------------------------------------------------------
# Auth / users
# --------------------------------------------------------------------------


class UserCreate(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    full_name: str = Field(default="", max_length=255)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    created_at: datetime


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


# --------------------------------------------------------------------------
# Catalogue
# --------------------------------------------------------------------------


class CoinBase(BaseModel):
    sku: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=255)
    description: str = ""
    kind: CoinKind = CoinKind.coin
    country: str = Field(default="", max_length=100)
    year: int | None = Field(default=None, ge=-3000, le=2200)
    denomination: str = Field(default="", max_length=100)
    composition: str = Field(default="", max_length=100)
    grade: str = Field(default="", max_length=50)
    certification: str = Field(default="", max_length=100)
    mint_mark: str = Field(default="", max_length=20)
    price: Decimal = Field(ge=Decimal("0"), max_digits=12, decimal_places=2)
    quantity: int = Field(default=0, ge=0)
    image_url: str = Field(default="", max_length=500)
    is_active: bool = True


class CoinCreate(CoinBase):
    pass


class CoinUpdate(BaseModel):
    """All fields optional -- only what is supplied gets changed."""

    title: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = None
    kind: CoinKind | None = None
    country: str | None = Field(default=None, max_length=100)
    year: int | None = Field(default=None, ge=-3000, le=2200)
    denomination: str | None = Field(default=None, max_length=100)
    composition: str | None = Field(default=None, max_length=100)
    grade: str | None = Field(default=None, max_length=50)
    certification: str | None = Field(default=None, max_length=100)
    mint_mark: str | None = Field(default=None, max_length=20)
    price: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    quantity: int | None = Field(default=None, ge=0)
    image_url: str | None = Field(default=None, max_length=500)
    is_active: bool | None = None


class CoinOut(CoinBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    created_at: datetime
    updated_at: datetime


class CoinPage(BaseModel):
    """A page of catalogue results plus the total matching count."""

    items: list[CoinOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------


class OrderLineIn(BaseModel):
    coin_id: int
    quantity: int = Field(ge=1)


class OrderCreate(BaseModel):
    items: list[OrderLineIn] = Field(min_length=1)

    @field_validator("items")
    @classmethod
    def no_duplicate_coins(cls, items: list[OrderLineIn]) -> list[OrderLineIn]:
        seen = {item.coin_id for item in items}
        if len(seen) != len(items):
            raise ValueError("each coin_id may appear at most once per order")
        return items


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    coin_id: int
    quantity: int
    unit_price: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    status: OrderStatus
    total_amount: Decimal
    created_at: datetime
    items: list[OrderItemOut]


class OrderStatusUpdate(BaseModel):
    status: OrderStatus
