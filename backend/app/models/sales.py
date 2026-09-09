"""Sales: listings, customers, orders and shipments.

`sales_order_item` references `listing`, which references `inventory_item` -- a
single foreign key chain to either a coin or a banknote. That chain is what
keeping one inventory table buys; with split coin/currency tables every order
line would need two nullable foreign keys and a constraint saying exactly one
is set.

**Naming note.** `docs/database-design.md` calls these `order` and
`order_item`. `order` is a reserved word in SQL, so every reference to it in a
view or a hand-written query would need quoting -- and the name is already
taken by the scaffold storefront. They are `sales_order` and
`sales_order_item` here, which is unambiguous in both directions.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from .base import Base, TimestampMixin, enum_column, utcnow

if TYPE_CHECKING:  # relationship targets only -- importing them at
    # runtime would make core and sales import each other in a cycle.
    from .core import InventoryItem
    from .reference import Currency

__all__ = [
    "Address",
    "AddressKind",
    "Customer",
    "Listing",
    "SalesOrder",
    "SalesOrderItem",
    "Shipment",
]


# SQLAlchemy cascade: delete the children with the parent, and delete
# any child removed from the collection.
_CASCADE_ALL_DELETE_ORPHAN = "all, delete-orphan"


class AddressKind(enum.StrEnum):
    """What an address is for. A customer may have one of each."""

    shipping = "shipping"
    billing = "billing"


class Listing(TimestampMixin, Base):
    """What is offered for sale, and at what price.

    Separate from `inventory_item` because an item may be listed, delisted and
    relisted at different prices, and because the public catalogue must be able
    to expose a listing without exposing the item behind it.
    """

    __tablename__ = "listing"

    #: Optimistic concurrency -- see the note on InventoryItem.version.
    #: `quantity_available` is deliberately NOT protected this way: it is a
    #: counter decremented under a row lock by the order path, where a version
    #: conflict would mean telling a buyer to try again for no reason.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    # A directive rather than a dict literal: DeclarativeBase declares
    # __mapper_args__ as an instance variable, so ClassVar is an override
    # error and Final a Liskov violation, while a bare literal trips RUF012.
    # See the note on __tablename__ in base.py.
    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        """Optimistic concurrency: every UPDATE checks the version it read."""
        return {"version_id_col": cls.version}

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency_id: Mapped[int] = mapped_column(
        ForeignKey("currency.id", ondelete="RESTRICT"), nullable=False
    )
    #: Units available. Normally 1, but a lot may be broken up for sale.
    quantity_available: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    title: Mapped[str] = mapped_column(
        String(500), default="", server_default=text("''"), nullable=False
    )
    description: Mapped[str] = mapped_column(
        Text, default="", server_default=text("''"), nullable=False
    )
    listed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    inventory_item: Mapped[InventoryItem] = relationship(back_populates="listings")
    currency: Mapped[Currency] = relationship()
    order_items: Mapped[list[SalesOrderItem]] = relationship(back_populates="listing")

    __table_args__ = (
        CheckConstraint("price >= 0", name="ck_listing_price_non_negative"),
        CheckConstraint(
            "quantity_available >= 0", name="ck_listing_quantity_non_negative"
        ),
        # The public catalogue reads only active listings, so the index that
        # serves it excludes everything else.
        Index(
            "ix_listing_active",
            "inventory_item_id",
            postgresql_where=text("is_active"),
        ),
    )


class Customer(TimestampMixin, Base):
    """A buyer.

    Optionally linked to a login: a customer record can exist for someone who
    bought without registering, and a registered user gets exactly one.
    """

    __tablename__ = "customer"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True, index=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    addresses: Mapped[list[Address]] = relationship(
        back_populates="customer", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )
    orders: Mapped[list[SalesOrder]] = relationship(back_populates="customer")

    __table_args__ = (UniqueConstraint("user_id", name="uq_customer_user"),)


class Address(TimestampMixin, Base):
    """A postal address, with validity dates.

    Customers move, and a historical order must still show where it was
    actually sent -- so an address is superseded rather than edited.
    """

    __tablename__ = "address"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.id", ondelete="CASCADE"), index=True, nullable=False
    )
    address_kind: Mapped[AddressKind] = mapped_column(
        enum_column(AddressKind, "address_kind"), nullable=False
    )
    line1: Mapped[str] = mapped_column(String(255), nullable=False)
    line2: Mapped[str | None] = mapped_column(String(255), nullable=True)
    city: Mapped[str] = mapped_column(String(128), nullable=False)
    region: Mapped[str | None] = mapped_column(String(128), nullable=True)
    postal_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    country_id: Mapped[int | None] = mapped_column(
        ForeignKey("country.id", ondelete="RESTRICT"), nullable=True
    )
    is_default: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="addresses")

    __table_args__ = (
        CheckConstraint(
            "valid_to IS NULL OR valid_from IS NULL OR valid_to >= valid_from",
            name="ck_address_validity_range",
        ),
        Index(
            "uq_address_default",
            "customer_id",
            "address_kind",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )


class SalesOrder(TimestampMixin, Base):
    """A customer order. See the naming note at the top of this module."""

    __tablename__ = "sales_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(
        ForeignKey("customer.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    sales_order_status_id: Mapped[int] = mapped_column(
        ForeignKey("sales_order_status.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    shipping_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("address.id", ondelete="RESTRICT"), nullable=True
    )
    billing_address_id: Mapped[int | None] = mapped_column(
        ForeignKey("address.id", ondelete="RESTRICT"), nullable=True
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        default=Decimal("0.00"),
        server_default=text("0.00"),
        nullable=False,
    )
    placed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False, index=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[SalesOrderItem]] = relationship(
        back_populates="order", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )
    shipments: Mapped[list[Shipment]] = relationship(
        back_populates="order", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )

    __table_args__ = (
        CheckConstraint("total_amount >= 0", name="ck_sales_order_total_non_negative"),
    )


class SalesOrderItem(Base):
    """One line of an order.

    ``unit_price`` is captured at purchase time, so a later price change never
    rewrites order history.
    """

    __tablename__ = "sales_order_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_order_id: Mapped[int] = mapped_column(
        ForeignKey("sales_order.id", ondelete="CASCADE"), index=True, nullable=False
    )
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped[SalesOrder] = relationship(back_populates="items")
    listing: Mapped[Listing] = relationship(back_populates="order_items")

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sales_order_item_quantity_positive"),
        CheckConstraint(
            "unit_price >= 0", name="ck_sales_order_item_price_non_negative"
        ),
    )


class Shipment(TimestampMixin, Base):
    """An outgoing parcel.

    Sits on the order rather than the item, because one parcel carries many
    lines. One-to-many, so partial shipment works the way partial receipt does
    on the buying side.
    """

    __tablename__ = "shipment"

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_order_id: Mapped[int] = mapped_column(
        ForeignKey("sales_order.id", ondelete="CASCADE"), index=True, nullable=False
    )
    carrier_id: Mapped[int | None] = mapped_column(
        ForeignKey("carrier.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    shipment_status_id: Mapped[int] = mapped_column(
        ForeignKey("shipment_status.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    tracking_number: Mapped[str | None] = mapped_column(
        String(128), nullable=True, index=True
    )
    service_level: Mapped[str | None] = mapped_column(String(64), nullable=True)
    shipped_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cost: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    insured_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: Parcel weight. Avoirdupois ounces -- this is a postal weight, not a
    #: metal weight, and must not be confused with the troy ounces used
    #: everywhere else in the schema.
    weight_oz: Mapped[Decimal | None] = mapped_column(Numeric(10, 3), nullable=True)

    order: Mapped[SalesOrder] = relationship(back_populates="shipments")

    __table_args__ = (
        CheckConstraint("cost IS NULL OR cost >= 0", name="ck_shipment_cost"),
        CheckConstraint(
            "insured_value IS NULL OR insured_value >= 0",
            name="ck_shipment_insured_value",
        ),
    )
