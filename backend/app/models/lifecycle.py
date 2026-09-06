"""Lifecycle and physical location.

Two independent axes. How an item **came in** (ordered, received, canceled)
and how it **goes out** (held, listed, sold, shipped) are different questions,
and collapsing them into one column makes "received and sold" unrepresentable.
Both live as foreign keys on `inventory_item`; the history of each lives here.

History is a table rather than overwritten columns, so that "when did this
actually arrive" survives a later correction to the status.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, utcnow
from .reference import StorageLocationKind

__all__ = ["ItemStatusHistory", "LocationHistory", "StorageLocation"]


class StorageLocation(TimestampMixin, Base):
    """Where items physically are.

    Never customer-visible. This is an authorisation boundary enforced by the
    `public_catalog` view and by tests, not a convention -- a public listing
    that leaked the safe-deposit box holding the item would be a security
    failure, not a cosmetic one.
    """

    __tablename__ = "storage_location"

    id: Mapped[int] = mapped_column(primary_key=True)
    storage_location_kind_id: Mapped[int] = mapped_column(
        ForeignKey("storage_location_kind.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    #: The bank, depository or building. Free text: institutions are not a
    #: classifier anyone searches across.
    institution: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: Box or container number.
    identifier: Mapped[str | None] = mapped_column(String(128), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    kind: Mapped[StorageLocationKind] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "storage_location_kind_id",
            "institution",
            "identifier",
            name="uq_storage_location_identity",
        ),
    )


class ItemStatusHistory(Base):
    """Every acquisition-status transition.

    Status is per item, not per order: an order may contain many items, split
    shipments are normal, and partial receipt must leave the remainder
    `ordered`.
    """

    __tablename__ = "item_status_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: Null on the first row -- an item has no status before it has one.
    from_status_id: Mapped[int | None] = mapped_column(
        ForeignKey("item_status.id", ondelete="RESTRICT"), nullable=True
    )
    to_status_id: Mapped[int] = mapped_column(
        ForeignKey("item_status.id", ondelete="RESTRICT"), nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    changed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_item_status_history_item_time",
            "inventory_item_id",
            text("changed_at DESC"),
        ),
    )


class LocationHistory(Base):
    """Every physical move.

    "Where was this in March" is a question worth being able to answer, and a
    sale writes two rows here -- in_transit, then sold.
    """

    __tablename__ = "location_history"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    storage_location_id: Mapped[int | None] = mapped_column(
        ForeignKey("storage_location.id", ondelete="RESTRICT"), nullable=True
    )
    moved_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    moved_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_location_history_item_time",
            "inventory_item_id",
            text("moved_at DESC"),
        ),
    )
