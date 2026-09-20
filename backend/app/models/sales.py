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
    Computed,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from .base import Base, ReferenceMixin, TimestampMixin, enum_column, utcnow

if TYPE_CHECKING:  # relationship targets only -- importing them at
    # runtime would make core and sales import each other in a cycle.
    from .core import InventoryItem, Vendor
    from .reference import Currency, SalesVenueKind
    from .scaffold import User

__all__ = [
    "Address",
    "AddressKind",
    "ClaimState",
    "Customer",
    "Listing",
    "ListingFormat",
    "ListingStatus",
    "OfferClaim",
    "SalesFeeKind",
    "SalesOrder",
    "SalesOrderChange",
    "SalesOrderChangeKind",
    "SalesOrderFee",
    "SalesOrderItem",
    "SalesOrderItemShare",
    "SalesVenue",
    "Shipment",
]


# SQLAlchemy cascade: delete the children with the parent, and delete
# any child removed from the collection.
_CASCADE_ALL_DELETE_ORPHAN = "all, delete-orphan"


class AddressKind(enum.StrEnum):
    """What an address is for. A customer may have one of each."""

    shipping = "shipping"
    billing = "billing"


class SalesOrderChangeKind(enum.StrEnum):
    """What one row of an order's history records."""

    placed = "placed"
    line_added = "line_added"
    line_removed = "line_removed"
    quantity = "quantity"
    unit_price = "unit_price"
    customer = "customer"
    notes = "notes"
    status = "status"
    total = "total"


class ListingFormat(enum.StrEnum):
    """How a listing sells: at a fixed price, or to the highest bidder."""

    fixed_price = "fixed_price"
    auction = "auction"


class ListingStatus(enum.StrEnum):
    """Whether a listing is on offer now.

    `paused` is a store listing set aside while its item is offered
    elsewhere; it resumes when that offer ends unsold (selling design).
    """

    active = "active"
    paused = "paused"
    ended = "ended"


class ClaimState(enum.StrEnum):
    """Whether a claim currently holds its item off every other platform.

    `paused` is a store listing set aside while the item is offered
    elsewhere: it keeps the listing and its price, and resumes when that
    offer ends unsold. Only an `active` claim reserves the item, which is
    what the partial unique index below enforces.
    """

    active = "active"
    paused = "paused"
    released = "released"


class SalesVenue(TimestampMixin, Base):
    """A platform the business sells through: the web store, eBay, an auction house.

    The owner's own accounts, not shipped reference data -- another
    installation sells elsewhere. `vendor_id` links the platform to the
    purchase source of the same name, so eBay is one partner whether buying
    or selling. The default fees are for estimates only; a sale records what
    was actually charged.
    """

    __tablename__ = "sales_venue"

    #: Optimistic concurrency -- see the note on InventoryItem.version.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        """Optimistic concurrency: every UPDATE checks the version it read."""
        return {"version_id_col": cls.version}

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Machine-facing and immutable, like a reference code.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sales_venue_kind_id: Mapped[int] = mapped_column(
        ForeignKey("sales_venue_kind.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    #: True on exactly one row, the web store; a partial unique index says so.
    is_own_store: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    vendor_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendor.id", ondelete="RESTRICT"), nullable=True
    )
    #: The owner's username or seller id on the platform.
    account_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: e.g. https://www.ebay.com/itm/{external_id}
    listing_url_template: Mapped[str | None] = mapped_column(String(500), nullable=True)
    #: Fractions: 0.1325 is 13.25%.
    commission_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )
    processing_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )
    processing_fixed: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    listing_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: When the default fees were read from the platform.
    terms_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    kind: Mapped[SalesVenueKind] = relationship()
    vendor: Mapped[Vendor | None] = relationship()

    __table_args__ = (
        UniqueConstraint("code", name="uq_sales_venue_code"),
        UniqueConstraint("vendor_id", name="uq_sales_venue_vendor"),
        Index(
            "uq_sales_venue_own_store",
            "is_own_store",
            unique=True,
            postgresql_where=text("is_own_store"),
        ),
        CheckConstraint(
            "commission_rate >= 0 AND commission_rate <= 1",
            name="ck_sales_venue_commission_rate",
        ),
        CheckConstraint(
            "processing_rate >= 0 AND processing_rate <= 1",
            name="ck_sales_venue_processing_rate",
        ),
        CheckConstraint(
            "processing_fixed >= 0", name="ck_sales_venue_processing_fixed"
        ),
        CheckConstraint("listing_fee >= 0", name="ck_sales_venue_listing_fee"),
    )


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
    sales_venue_id: Mapped[int] = mapped_column(
        ForeignKey("sales_venue.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    format: Mapped[ListingFormat] = mapped_column(
        enum_column(ListingFormat, "listing_format"),
        default=ListingFormat.fixed_price,
        nullable=False,
    )
    #: Written only through `status`; see ListingStatus.
    status: Mapped[ListingStatus] = mapped_column(
        enum_column(ListingStatus, "listing_status"),
        default=ListingStatus.active,
        nullable=False,
    )
    #: Generated from `status`, so every reader written before statuses
    #: existed -- checkout, the public catalogue, the for-sale warning -- keeps
    #: its meaning. It cannot be written; set `status`.
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        Computed("status = 'active'::listing_status", persisted=True),
        nullable=False,
    )
    #: The platform's own listing number, and its page.
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    #: The offer this store listing was set aside for, so settling that offer
    #: knows which listings to resume. Null unless `status` is `paused`.
    paused_by_listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=True
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
    sales_venue: Mapped[SalesVenue] = relationship()
    order_items: Mapped[list[SalesOrderItem]] = relationship(back_populates="listing")
    paused_by: Mapped[Listing | None] = relationship(
        remote_side=lambda: [Listing.id],
        foreign_keys=lambda: [Listing.paused_by_listing_id],
    )

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


class OfferClaim(TimestampMixin, Base):
    """One item's hold on one listing, and the "offered once" guarantee.

    An item listing has one claim; a lot listing (phase 3) will have one per
    member, which is why the rule lives here rather than on `listing`.
    Written only by `app.offering_writes`, in the same transaction as the
    listing it mirrors: a claim's state always follows its listing's status.
    """

    __tablename__ = "offer_claim"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    state: Mapped[ClaimState] = mapped_column(
        enum_column(ClaimState, "offer_claim_state"),
        default=ClaimState.active,
        nullable=False,
    )

    listing: Mapped[Listing] = relationship()
    item: Mapped[InventoryItem] = relationship()

    __table_args__ = (
        UniqueConstraint("listing_id", "inventory_item_id", name="uq_offer_claim_pair"),
        # The guarantee: at most one active claim per item. A paused or
        # released claim does not reserve anything, so it is excluded.
        Index(
            "uq_offer_claim_active",
            "inventory_item_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
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
    #: The platform this buyer is known on; null for a store customer.
    sales_venue_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_venue.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    #: Their username there ("coinfan88"). Null on the platform's single
    #: undisclosed buyer, used by auction houses that do not name buyers.
    venue_username: Mapped[str | None] = mapped_column(String(128), nullable=True)

    addresses: Mapped[list[Address]] = relationship(
        back_populates="customer", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )
    orders: Mapped[list[SalesOrder]] = relationship(back_populates="customer")

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_customer_user"),
        # Indexed on the lowered username, not the plain column: platforms
        # display one account's name inconsistently ("CoinFan88" one order,
        # "coinfan88" the next), and two rows would split one buyer's history
        # in two. Written exactly as PostgreSQL stores it -- see the note on
        # ix_inventory_item_fts in core.py -- or the drift test reports this
        # index as changed on every run.
        Index(
            "uq_customer_venue_username",
            "sales_venue_id",
            text("lower(venue_username::text)"),
            unique=True,
            postgresql_where=text("venue_username IS NOT NULL"),
        ),
        Index(
            "uq_customer_venue_undisclosed",
            "sales_venue_id",
            unique=True,
            postgresql_where=text(
                "(venue_username IS NULL) AND (sales_venue_id IS NOT NULL)"
            ),
        ),
    )


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
    sales_venue_id: Mapped[int] = mapped_column(
        ForeignKey("sales_venue.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    #: The platform's order number, for a sale made elsewhere.
    external_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
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
    #: The account that entered the order: the buyer, or an administrator
    #: acting for them. Null for orders placed before this was recorded.
    placed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Optimistic concurrency, as on Listing and InventoryItem.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    customer: Mapped[Customer] = relationship(back_populates="orders")
    items: Mapped[list[SalesOrderItem]] = relationship(
        back_populates="order", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )
    fees: Mapped[list[SalesOrderFee]] = relationship(
        back_populates="order", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )
    shipments: Mapped[list[Shipment]] = relationship(
        back_populates="order", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )
    placed_by: Mapped[User | None] = relationship()
    changes: Mapped[list[SalesOrderChange]] = relationship(
        back_populates="order",
        cascade=_CASCADE_ALL_DELETE_ORPHAN,
        order_by="SalesOrderChange.id",
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        """Optimistic concurrency: every UPDATE checks the version it read."""
        return {"version_id_col": cls.version}

    __table_args__ = (
        CheckConstraint("total_amount >= 0", name="ck_sales_order_total_non_negative"),
    )


class SalesOrderItem(Base):
    """One line of an order.

    ``unit_price`` is captured at purchase time, so a later price change never
    rewrites order history. ``item_snapshot`` does the same for the item: what
    it was called, graded and described as when it was sold (app.sale_snapshot).
    An item returned, corrected and sold again gets a new line with its own
    snapshot; this one keeps the first sale as it was.
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
    #: The item and its listing when the line was made. None only for lines
    #: made before snapshots were kept.
    item_snapshot: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    snapshot_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    order: Mapped[SalesOrder] = relationship(back_populates="items")
    listing: Mapped[Listing] = relationship(back_populates="order_items")
    shares: Mapped[list[SalesOrderItemShare]] = relationship(
        back_populates="line", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )

    __table_args__ = (
        CheckConstraint("quantity > 0", name="ck_sales_order_item_quantity_positive"),
        CheckConstraint(
            "unit_price >= 0", name="ck_sales_order_item_price_non_negative"
        ),
    )


class SalesFeeKind(ReferenceMixin, Base):
    """A kind of fee a platform charges on a sale.

    Seeded with the schema rather than from `backend/data/reference/`: this is
    a closed vocabulary the product defines, not numismatic reference data
    with an outside source to cite.
    """

    __tablename__ = "sales_fee_kind"


class SalesOrderFee(Base):
    """One fee line on an order, as the platform's statement shows it.

    Actual money, not an estimate: the platform's own figures are what a tax
    return needs. The estimates shown while offering come from
    `sales_venue`'s default rates and are never stored.

    Net payout is `sales_order.total_amount - sum(amount)`, computed when
    asked. Storing it would give two places to disagree.
    """

    __tablename__ = "sales_order_fee"

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_order_id: Mapped[int] = mapped_column(
        ForeignKey("sales_order.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sales_fee_kind_id: Mapped[int] = mapped_column(
        ForeignKey("sales_fee_kind.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    order: Mapped[SalesOrder] = relationship(back_populates="fees")
    fee_kind: Mapped[SalesFeeKind] = relationship()

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_sales_order_fee_non_negative"),
    )


class SalesOrderItemShare(Base):
    """One item's share of an order line's money.

    Every sold line has shares: one row for a single-item listing, one per
    member for a lot. A share of one looks redundant and is deliberate --
    it makes this table the single permanent answer to "which items did this
    order carry", with one query shape instead of two. `app.sale_state`
    depends on that, and so will realised-gain reporting.

    Shares sum to their line exactly (`app.allocation`), so a cent is never
    lost between the order total and the items that made it up.
    """

    __tablename__ = "sales_order_item_share"

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_order_item_id: Mapped[int] = mapped_column(
        ForeignKey("sales_order_item.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    #: This item's share of `sales_order_item.unit_price * quantity`.
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: This item's share of the order's fees.
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )

    line: Mapped[SalesOrderItem] = relationship(back_populates="shares")

    __table_args__ = (
        UniqueConstraint(
            "sales_order_item_id",
            "inventory_item_id",
            name="uq_share_line_item",
        ),
    )


class SalesOrderChange(Base):
    """One change to an order: who, when, and what it was before and after.

    One save writes several rows sharing `changed_at` and `changed_by_id`.
    Values are text because they are of mixed kinds; `change` is closed.
    """

    __tablename__ = "sales_order_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_order_id: Mapped[int] = mapped_column(
        ForeignKey("sales_order.id", ondelete="CASCADE"), index=True, nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    changed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    change: Mapped[SalesOrderChangeKind] = mapped_column(
        enum_column(SalesOrderChangeKind, "sales_order_change_kind"), nullable=False
    )
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=True
    )
    from_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped[SalesOrder] = relationship(back_populates="changes")
    changed_by: Mapped[User | None] = relationship()
    listing: Mapped[Listing | None] = relationship()


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
