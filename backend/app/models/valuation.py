"""Valuation: what an item is worth, as opposed to what it cost.

Cost basis is fixed at purchase and stored on `inventory_item`. Value moves
daily and is therefore *computed*, never stored. The one table shaped to
hold a stored figure, `valuation_snapshot`, pairs it with the spot price that
produced it so the number would stay reproducible; it is unused scaffolding,
and nothing writes a row.

A common-date, low-grade silver coin is worth its metal. A key date in high
grade carries a collector premium unrelated to spot. One stored number cannot
represent both.
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, ProvenanceSource, TimestampMixin, enum_column, utcnow
from .reference import Metal

__all__ = ["Composition", "MetalPrice", "ValuationSnapshot"]


class Composition(TimestampMixin, Base):
    """What a coin is made of, by denomination, country and year range.

    Coinage composition is a matter of legislation and mint specification, so
    it is a *public fact* that resolves from what the item already is, rather
    than something recorded per item. A US dime struck in 1963 is 90% silver
    because the law said so, not because anyone typed it in.

    Overridable per item: `inventory_item` carries its own metal, fineness and
    weights for the cases this table cannot know about.
    """

    __tablename__ = "composition"

    id: Mapped[int] = mapped_column(primary_key=True)
    denomination_id: Mapped[int] = mapped_column(
        ForeignKey("denomination.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    country_id: Mapped[int] = mapped_column(
        ForeignKey("country.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    #: Inclusive year range over which this composition was in force. Null
    #: year_to means "still current".
    year_from: Mapped[int] = mapped_column(Integer, nullable=False)
    year_to: Mapped[int | None] = mapped_column(Integer, nullable=True)

    metal_id: Mapped[int] = mapped_column(
        ForeignKey("metal.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    fineness: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    #: Total weight of one piece.
    gross_weight_ozt: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    #: Precious metal content of one piece -- the melt input.
    fine_weight_ozt: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    note: Mapped[str | None] = mapped_column(String(500), nullable=True)

    source: Mapped[ProvenanceSource] = mapped_column(
        enum_column(ProvenanceSource, "provenance_source"),
        default=ProvenanceSource.seeded,
        nullable=False,
    )

    metal: Mapped[Metal] = relationship()

    __table_args__ = (
        CheckConstraint(
            "year_to IS NULL OR year_to >= year_from",
            name="ck_composition_year_range",
        ),
        CheckConstraint(
            "fineness > 0 AND fineness <= 1", name="ck_composition_fineness_fraction"
        ),
        CheckConstraint(
            "fine_weight_ozt >= 0", name="ck_composition_fine_weight_non_negative"
        ),
        UniqueConstraint(
            "denomination_id",
            "country_id",
            "year_from",
            "metal_id",
            name="uq_composition_span",
        ),
        Index("ix_composition_lookup", "denomination_id", "country_id", "year_from"),
    )


class MetalPrice(Base):
    """Spot price time series, one row per quote.

    A time series rather than a current-price column: revaluing a portfolio
    "as of" a date is a normal question, and a single mutable column cannot
    answer it.
    """

    __tablename__ = "metal_price"

    id: Mapped[int] = mapped_column(primary_key=True)
    metal_id: Mapped[int] = mapped_column(
        ForeignKey("metal.id", ondelete="RESTRICT"), nullable=False
    )
    quoted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    price_per_ozt: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    source: Mapped[str] = mapped_column(
        String(64), default="manual", server_default=text("'manual'"), nullable=False
    )

    metal: Mapped[Metal] = relationship()

    __table_args__ = (
        CheckConstraint("price_per_ozt >= 0", name="ck_metal_price_non_negative"),
        UniqueConstraint("metal_id", "quoted_at", name="uq_metal_price_quote"),
        # The hot path is "latest quote for this metal", which this index
        # answers with a single backwards scan.
        Index(
            "ix_metal_price_latest",
            "metal_id",
            text("quoted_at DESC"),
        ),
    )


def melt_value(fine_ounces: Decimal, price_per_ozt: Decimal) -> Decimal:
    """What fine troy ounces melt for at a spot price, to the cent.

    A half cent rounds up, as the `item_valuation` view's `round()` does, so
    every place that shows a melt value agrees on a tie.
    """
    return (fine_ounces * price_per_ozt).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP
    )


class ValuationSnapshot(Base):
    """A point-in-time valuation, recorded with the inputs that produced it.

    Storing ``spot_price_used`` is what makes a snapshot reproducible. A bare
    historical value column would leave every past figure unexplainable.

    Unused scaffolding: nothing writes a row or reads one.
    """

    __tablename__ = "valuation_snapshot"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    valuation_basis_id: Mapped[int] = mapped_column(
        ForeignKey("valuation_basis.id", ondelete="RESTRICT"), nullable=False
    )

    spot_price_used: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 4), nullable=True
    )
    fine_weight_ozt: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    #: How many pieces the item held when the snapshot was taken. Copied
    #: rather than joined, so a later correction to the item does not silently
    #: restate a past valuation. Named to match `inventory_item.piece_count`.
    piece_count: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )

    melt_value: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    numismatic_value: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    reported_value: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )

    __table_args__ = (
        Index(
            "ix_valuation_snapshot_item_time",
            "inventory_item_id",
            text("captured_at DESC"),
        ),
    )
