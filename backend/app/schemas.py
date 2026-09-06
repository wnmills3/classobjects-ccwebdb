"""Pydantic request/response models.

**Classifiers cross the API as codes, not ids.** A client sends
``"item_kind": "bullion"``, never ``"item_kind_id": 3``. Ids are internal and
may differ between installations; codes are the stable contract. The routers
resolve them, and an unknown code is a 422 rather than a silently null column.

**Money and weight are `Decimal`.** They are declared with explicit precision
so a client sending a float gets a validation error rather than a rounding
surprise several layers down.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from .models import UserRole

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
#
# A catalogue entry is a `listing` joined to the `inventory_item` behind it.
# The two are separate tables because an item may be listed, delisted and
# relisted at different prices, and because the public catalogue must be able
# to show a listing without exposing the item's cost basis or location. The
# API presents them as one resource, since that is how a shop is operated.
# --------------------------------------------------------------------------


class CatalogItemBase(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    description: str = ""

    # Classifiers, by code.
    item_kind: str = Field(default="coin", max_length=64)
    country: str | None = Field(default=None, max_length=64)
    denomination: str | None = Field(default=None, max_length=64)
    bullion_form: str | None = Field(default=None, max_length=64)
    grade: str | None = Field(default=None, max_length=64)
    grading_service: str | None = Field(default=None, max_length=64)
    metal: str | None = Field(default=None, max_length=64)

    year_start: int | None = Field(default=None, ge=-3000, le=2200)
    year_end: int | None = Field(default=None, ge=-3000, le=2200)

    fineness: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)
    gross_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)
    fine_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)

    #: Pieces in the lot itself -- a roll of 50 is one item with quantity 50.
    #: Distinct from `quantity_available`, which is how many are for sale.
    storage_quantity: int = Field(default=1, ge=1)

    price: Decimal = Field(ge=Decimal("0"), max_digits=12, decimal_places=2)
    currency: str = Field(default="USD", max_length=8)
    quantity_available: int = Field(default=1, ge=0)
    is_active: bool = True

    @field_validator("year_end")
    @classmethod
    def year_range_must_not_be_backwards(cls, value: int | None, info) -> int | None:
        start = info.data.get("year_start")
        if value is not None and start is not None and value < start:
            raise ValueError("year_end must not be earlier than year_start")
        return value


class CatalogItemCreate(CatalogItemBase):
    """Creates an inventory item and the listing that offers it, together."""


class CatalogItemUpdate(BaseModel):
    """All fields optional -- only what is supplied gets changed."""

    title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    item_kind: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, max_length=64)
    denomination: str | None = Field(default=None, max_length=64)
    bullion_form: str | None = Field(default=None, max_length=64)
    grade: str | None = Field(default=None, max_length=64)
    grading_service: str | None = Field(default=None, max_length=64)
    metal: str | None = Field(default=None, max_length=64)
    year_start: int | None = Field(default=None, ge=-3000, le=2200)
    year_end: int | None = Field(default=None, ge=-3000, le=2200)
    fineness: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)
    gross_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)
    fine_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)
    storage_quantity: int | None = Field(default=None, ge=1)
    price: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    quantity_available: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class CatalogItemOut(BaseModel):
    """What a buyer sees.

    Deliberately carries no cost basis, storage location or internal catalogue
    number -- see `public_catalog` in the database design. The admin views read
    the same shape, so a field cannot be added here for staff and leak to
    customers.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    inventory_item_id: int
    title: str
    description: str

    item_kind: str | None = None
    country: str | None = None
    denomination: str | None = None
    bullion_form: str | None = None
    grade: str | None = None
    grading_service: str | None = None
    metal: str | None = None

    year_start: int | None = None
    year_end: int | None = None
    fineness: Decimal | None = None
    gross_weight_ozt: Decimal | None = None
    fine_weight_ozt: Decimal | None = None
    storage_quantity: int = 1

    price: Decimal
    currency: str
    quantity_available: int
    is_active: bool
    created_at: datetime
    updated_at: datetime


class CatalogPage(BaseModel):
    """A page of catalogue results plus the total matching count."""

    items: list[CatalogItemOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------
# Orders
# --------------------------------------------------------------------------


class OrderLineIn(BaseModel):
    listing_id: int
    quantity: int = Field(ge=1)


class OrderCreate(BaseModel):
    items: list[OrderLineIn] = Field(min_length=1)

    @field_validator("items")
    @classmethod
    def no_duplicate_listings(cls, items: list[OrderLineIn]) -> list[OrderLineIn]:
        seen = {item.listing_id for item in items}
        if len(seen) != len(items):
            raise ValueError("each listing_id may appear at most once per order")
        return items


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    listing_id: int
    quantity: int
    unit_price: Decimal


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    customer_id: int
    status: str
    total_amount: Decimal
    placed_at: datetime
    items: list[OrderItemOut]


class OrderStatusUpdate(BaseModel):
    #: A `sales_order_status` code: pending, paid, packed, shipped,
    #: delivered, cancelled, refunded.
    status: str = Field(min_length=1, max_length=64)
