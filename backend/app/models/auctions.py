"""Auctions: consigning lots to a sale, and settling it.

`auction` and `auction_lot` are the schema half of the selling design's
auction flow (`docs/specs/selling-design.md`, the `auction` and `auction_lot`
section). An auction-format listing always belongs to an auction, and a timed
eBay auction is modeled the same way -- an auction with one lot. Nothing in
this module writes these tables: `app/auctions.py` is their sole writer, the
same pattern `offering_writes.py` and `lifecycle_writes.py` follow for their
own tables.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from .base import Base, TimestampMixin, enum_column

if TYPE_CHECKING:  # relationship targets only -- see the note in sales.py.
    from .sales import Customer, Listing, SalesVenue

__all__ = [
    "Auction",
    "AuctionLot",
    "AuctionLotResult",
    "AuctionStatus",
]


class AuctionStatus(enum.StrEnum):
    """An auction's life. `consigned` applies only to auction houses.

    A house takes physical custody before the sale, which the owner has to be
    able to see; an eBay or Whatnot auction never leaves the premises, so it
    goes straight from `scheduled` to `closed`.
    """

    draft = "draft"
    scheduled = "scheduled"
    consigned = "consigned"
    closed = "closed"
    settled = "settled"
    cancelled = "cancelled"


class AuctionLotResult(enum.StrEnum):
    """What one lot did, entered after the sale closes."""

    sold = "sold"
    unsold = "unsold"
    withdrawn = "withdrawn"


class Auction(TimestampMixin, Base):
    """One sale event at a platform: a live auction show, or an auction house's sale.

    `sales_venue_id` points at the platform running the sale (kind
    `live_auction`, `auction_house`, or `marketplace` for a single timed eBay
    auction). `consigned_on` and the `consigned` status apply only to
    `auction_house` platforms, which take physical custody of the items
    before the sale; nothing enforces that restriction at the schema level,
    since it is a rule about *when* a status may be set, not about the shape
    of a row -- see `app.auctions.consign`, its sole writer, which is also
    where custody is tracked by `consigned_on is not None` rather than by the
    status column (ruling R13).
    """

    __tablename__ = "auction"

    #: Optimistic concurrency -- see the note on InventoryItem.version.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        """Optimistic concurrency: every UPDATE checks the version it read."""
        return {"version_id_col": cls.version}

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_venue_id: Mapped[int] = mapped_column(
        ForeignKey("sales_venue.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    #: The sale number, as the platform names it.
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    starts_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ends_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[AuctionStatus] = mapped_column(
        enum_column(AuctionStatus, "auction_status"),
        default=AuctionStatus.draft,
        nullable=False,
    )
    #: When physical custody moved to the auction house. Null until
    #: `status` becomes `consigned`.
    consigned_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    sales_venue: Mapped[SalesVenue] = relationship()
    lots: Mapped[list[AuctionLot]] = relationship(back_populates="auction")


class AuctionLot(Base):
    """One auction listing's detail: its lot number, reserve and outcome.

    One row per auction listing (`listing_id` **unique**) -- an auction lot
    *is* the detail of one listing, the same relationship `sales_lot` has to
    the listing that offers it. `lot_number` restarts every sale, so it is
    unique only within its auction, not globally. `result` stays null until
    the auction is settled.
    """

    __tablename__ = "auction_lot"

    id: Mapped[int] = mapped_column(primary_key=True)
    auction_id: Mapped[int] = mapped_column(
        ForeignKey("auction.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=False
    )
    #: Text, not an integer: houses print lots like "14A" or "1014".
    lot_number: Mapped[str] = mapped_column(String(32), nullable=False)
    reserve: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: Null until the auction is settled.
    result: Mapped[AuctionLotResult | None] = mapped_column(
        enum_column(AuctionLotResult, "auction_lot_result"), nullable=True
    )
    hammer_price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    buyer_customer_id: Mapped[int | None] = mapped_column(
        ForeignKey("customer.id", ondelete="RESTRICT"), index=True, nullable=True
    )

    auction: Mapped[Auction] = relationship(back_populates="lots")
    listing: Mapped[Listing] = relationship()
    buyer: Mapped[Customer | None] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "auction_id", "lot_number", name="uq_auction_lot_auction_lot_number"
        ),
        UniqueConstraint("listing_id", name="uq_auction_lot_listing_id"),
    )
