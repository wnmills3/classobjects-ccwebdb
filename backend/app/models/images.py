"""Images: the file and its use are separate things.

One photograph may serve several purposes with different visibility -- an
inventory shot that is never public, a listing shot that is. So `image` is the
file, stored once and content-addressed, and the link tables record use.

Three link tables rather than a polymorphic subject_type/subject_id pair: real
foreign keys, each independently constrained, each able to carry the columns
its own relationship needs.

**Bytes never live in the database.** A collection's photographs run to
gigabytes. These tables hold metadata and a storage key; a StorageBackend
abstracts local filesystem from S3-compatible object storage.
"""

from __future__ import annotations

import enum
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin, enum_column

if TYPE_CHECKING:  # relationship target only; importing core at
    # runtime would make core and images import each other.
    from .core import InventoryItem

__all__ = [
    "DerivativeKind",
    "Image",
    "ImageDerivative",
    "ItemImage",
    "ListingImage",
    "ShipmentImage",
    "ShipmentImageKind",
]


# Foreign-key target.
_FK_IMAGE = "image.id"


class DerivativeKind(enum.StrEnum):
    """The renditions generated at ingest. Originals are never served."""

    thumb = "thumb"
    web = "web"


class ShipmentImageKind(enum.StrEnum):
    """What a photograph of an outgoing parcel shows."""

    packed = "packed"
    label = "label"
    handover = "handover"
    damage = "damage"


class Image(TimestampMixin, Base):
    """A stored image file, identified by the hash of its cleansed bytes.

    EXIF is stripped at ingest, not at publish time: photographs of valuables
    routinely carry the GPS coordinates of where they were taken. The hash is
    taken **after** cleansing, so identity is the identity of what is actually
    stored.
    """

    __tablename__ = "image"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: sha256 of the cleansed file. Content-addressed, so re-importing the same
    #: photograph is a no-op rather than a duplicate.
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    media_type: Mapped[str] = mapped_column(String(64), nullable=False)
    byte_size: Mapped[int] = mapped_column(Integer, nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Read out of EXIF before it was stripped -- the one piece of metadata
    #: worth keeping, because capture order helps link photographs to items.
    captured_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    #: The original filename or path, for tracing an import back to its source.
    source_ref: Mapped[str | None] = mapped_column(String(1000), nullable=True)

    derivatives: Mapped[list[ImageDerivative]] = relationship(
        back_populates="image", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("sha256", name="uq_image_sha256"),
        CheckConstraint("byte_size >= 0", name="ck_image_byte_size_non_negative"),
    )


class ImageDerivative(Base):
    """A generated, public-safe rendition.

    Originals are never served. Public requests are answered only from these
    rows, which is the second half of the EXIF guarantee: even if an original
    somehow retained metadata, it is not reachable.
    """

    __tablename__ = "image_derivative"

    id: Mapped[int] = mapped_column(primary_key=True)
    image_id: Mapped[int] = mapped_column(
        ForeignKey(_FK_IMAGE, ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[DerivativeKind] = mapped_column(
        enum_column(DerivativeKind, "derivative_kind"), nullable=False
    )
    storage_key: Mapped[str] = mapped_column(String(500), nullable=False)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    image: Mapped[Image] = relationship(back_populates="derivatives")

    __table_args__ = (
        UniqueConstraint("image_id", "kind", name="uq_image_derivative_kind"),
    )


class ItemImage(Base):
    """Links a photograph to an inventory item.

    ``inventory_item_id`` is **nullable** on purpose. Photographs exist before
    anyone has decided what they depict, and must be storable, browsable and
    searchable in that state -- camera filenames carry only a timestamp, so
    linking is a manual, UI-assisted task rather than an import step.
    """

    __tablename__ = "item_image"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="CASCADE"),
        index=True,
        nullable=True,
    )
    image_id: Mapped[int] = mapped_column(
        ForeignKey(_FK_IMAGE, ondelete="CASCADE"), index=True, nullable=False
    )
    image_role_id: Mapped[int | None] = mapped_column(
        ForeignKey("image_role.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    is_primary: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    image: Mapped[Image] = relationship()
    item: Mapped[InventoryItem | None] = relationship(back_populates="images")

    __table_args__ = (
        UniqueConstraint("inventory_item_id", "image_id", name="uq_item_image_pair"),
        # At most one primary photograph per item. Partial, so the many
        # non-primary rows do not collide with each other.
        Index(
            "uq_item_image_primary",
            "inventory_item_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )


class ListingImage(Base):
    """Links a photograph to a public listing."""

    __tablename__ = "listing_image"

    id: Mapped[int] = mapped_column(primary_key=True)
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listing.id", ondelete="CASCADE"), index=True, nullable=False
    )
    image_id: Mapped[int] = mapped_column(
        ForeignKey(_FK_IMAGE, ondelete="CASCADE"), index=True, nullable=False
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0"), nullable=False
    )

    image: Mapped[Image] = relationship()

    __table_args__ = (
        UniqueConstraint("listing_id", "image_id", name="uq_listing_image_pair"),
    )


class ShipmentImage(Base):
    """Photographs of an outgoing parcel -- packed contents, label, damage."""

    __tablename__ = "shipment_image"

    id: Mapped[int] = mapped_column(primary_key=True)
    shipment_id: Mapped[int] = mapped_column(
        ForeignKey("shipment.id", ondelete="CASCADE"), index=True, nullable=False
    )
    image_id: Mapped[int] = mapped_column(
        ForeignKey(_FK_IMAGE, ondelete="CASCADE"), index=True, nullable=False
    )
    kind: Mapped[ShipmentImageKind] = mapped_column(
        enum_column(ShipmentImageKind, "shipment_image_kind"), nullable=False
    )

    image: Mapped[Image] = relationship()

    __table_args__ = (
        UniqueConstraint(
            "shipment_id", "image_id", "kind", name="uq_shipment_image_pair"
        ),
    )
