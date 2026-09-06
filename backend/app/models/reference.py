"""Reference (classifier) tables.

Every table here follows `ReferenceMixin`: id, code, label, sort_order,
is_active, source. A handful carry extra columns, and those are the interesting
ones -- each extra column exists because some question could not be answered
without it.

Foreign keys pointing at these tables are ``ON DELETE RESTRICT`` throughout: a
classifier that is in use must not be able to vanish and orphan the rows that
reference it.
"""

from __future__ import annotations

import enum
from decimal import Decimal

from sqlalchemy import ForeignKey, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, ReferenceMixin, enum_column

__all__ = [
    "Authenticity",
    "AppliesTo",
    "BullionForm",
    "Carrier",
    "Country",
    "Currency",
    "Denomination",
    "DenominationKind",
    "Disposition",
    "ErrorType",
    "FedDistrict",
    "Grade",
    "GradeDesignation",
    "GradeScale",
    "GradingService",
    "ImageRole",
    "ItemKind",
    "ItemStatus",
    "Metal",
    "Mint",
    "NoteAttribute",
    "NoteType",
    "SalesOrderStatus",
    "SealColor",
    "SetForm",
    "ShipmentStatus",
    "SignatureCombination",
    "StorageForm",
    "StorageLocationKind",
    "ValuationBasis",
    "VendorKind",
]


class AppliesTo(str, enum.Enum):
    """Which kind of item an error vocabulary term is relevant to."""

    coin = "coin"
    currency = "currency"
    any = "any"


class DenominationKind(str, enum.Enum):
    coin = "coin"
    note = "note"


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


class ItemKind(ReferenceMixin, Base):
    """Top level: coin, currency, bullion, set, medal, token, other, unknown."""

    __tablename__ = "item_kind"


class BullionForm(ReferenceMixin, Base):
    """Silver Eagle, Copper Round, Silver Bar, Krugerrand, ..."""

    __tablename__ = "bullion_form"

    metal_id: Mapped[int | None] = mapped_column(
        ForeignKey("metal.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    #: Typical fine weight for the product, when the form implies one. A
    #: "Silver Eagle" is always one troy ounce of .999; a "Silver Bar" is not.
    typical_fine_weight_ozt: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 6), nullable=True
    )
    typical_fineness: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )

    metal: Mapped[Metal | None] = relationship()


class SetForm(ReferenceMixin, Base):
    """Mint Set, Proof Set, Silver Proof Set, Prestige Set, Coin Set."""

    __tablename__ = "set_form"


class StorageForm(ReferenceMixin, Base):
    """Physical packaging: single, roll, tube, box, bag, album, ..."""

    __tablename__ = "storage_form"

    #: Pieces the packaging conventionally holds -- a cent roll is 50. Only a
    #: default for data entry; `inventory_item.storage_quantity` is the truth.
    default_quantity: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# Issuer and face value
# ---------------------------------------------------------------------------


class Currency(ReferenceMixin, Base):
    """Issuing currency: USD, CAD, GBP, ..."""

    __tablename__ = "currency"

    symbol: Mapped[str] = mapped_column(String(8), default="", nullable=False)
    minor_units: Mapped[int] = mapped_column(Integer, default=2, nullable=False)


class Country(ReferenceMixin, Base):
    __tablename__ = "country"

    iso_alpha2: Mapped[str | None] = mapped_column(String(2), nullable=True)


class Denomination(ReferenceMixin, Base):
    """A face value in a currency.

    ``kind`` separates coin denominations from note denominations because the
    same face value exists as both: a US dollar is a coin and a bill, and they
    are different objects with different catalogues.
    """

    __tablename__ = "denomination"

    currency_id: Mapped[int] = mapped_column(
        ForeignKey("currency.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    face_value: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False)
    kind: Mapped[DenominationKind] = mapped_column(
        enum_column(DenominationKind, "denomination_kind"), nullable=False
    )

    currency: Mapped[Currency] = relationship()

    __table_args__ = (
        UniqueConstraint("code", name="uq_denomination_code"),
        UniqueConstraint(
            "currency_id", "face_value", "kind", name="uq_denomination_value"
        ),
    )


class Mint(ReferenceMixin, Base):
    """Coin mint. ``mark`` is the letter struck on the coin, and may be blank:
    Philadelphia struck no mark on most issues."""

    __tablename__ = "mint"

    mark: Mapped[str] = mapped_column(String(4), default="", nullable=False)
    country_id: Mapped[int | None] = mapped_column(
        ForeignKey("country.id", ondelete="RESTRICT"), index=True, nullable=True
    )


# ---------------------------------------------------------------------------
# Condition
# ---------------------------------------------------------------------------


class GradeScale(ReferenceMixin, Base):
    """Sheldon numeric (1-70), adjectival, or a note scale."""

    __tablename__ = "grade_scale"


class Grade(ReferenceMixin, Base):
    """A condition grade.

    ``numeric_value`` is what makes "all my MS65-and-better Morgans" an
    answerable question. Adjectival grades such as BU carry no number and stay
    null rather than being assigned a fictional one.
    """

    __tablename__ = "grade"

    grade_scale_id: Mapped[int | None] = mapped_column(
        ForeignKey("grade_scale.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    numeric_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: True for proof grades (PR/PF), which are a striking method, not a rank.
    is_proof: Mapped[bool] = mapped_column(default=False, nullable=False)

    grade_scale: Mapped[GradeScale | None] = relationship()


class GradeDesignation(ReferenceMixin, Base):
    """Grade suffix: DCAM, CAM, RD, RB, BN, FS, FB."""

    __tablename__ = "grade_designation"


class GradingService(ReferenceMixin, Base):
    """PCGS, NGC, ANACS, ICG, PMG, SEGS."""

    __tablename__ = "grading_service"


class Authenticity(ReferenceMixin, Base):
    """unverified, genuine, counterfeit, questionable."""

    __tablename__ = "authenticity"


# ---------------------------------------------------------------------------
# Banknote vocabulary
# ---------------------------------------------------------------------------


class NoteType(ReferenceMixin, Base):
    """Federal Reserve Note, Silver Certificate, United States Note, ..."""

    __tablename__ = "note_type"


class NoteAttribute(ReferenceMixin, Base):
    """Star Note, Fancy Serial, Consecutive, ... -- many-to-many with an item.

    These are attributes, not grades, which is why they are a link table rather
    than another column on the item.
    """

    __tablename__ = "note_attribute"


class SealColor(ReferenceMixin, Base):
    """Treasury seal colour: blue, red, brown, green, gold."""

    __tablename__ = "seal_color"


class FedDistrict(ReferenceMixin, Base):
    """Federal Reserve district, A Boston through L San Francisco."""

    __tablename__ = "fed_district"

    letter: Mapped[str] = mapped_column(String(1), nullable=False)
    city: Mapped[str] = mapped_column(String(64), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)


class SignatureCombination(ReferenceMixin, Base):
    """Treasurer and Secretary of the Treasury, with the term they served."""

    __tablename__ = "signature_combination"

    treasurer: Mapped[str] = mapped_column(String(128), nullable=False)
    secretary: Mapped[str] = mapped_column(String(128), nullable=False)
    term_from: Mapped[int | None] = mapped_column(Integer, nullable=True)
    term_to: Mapped[int | None] = mapped_column(Integer, nullable=True)


# ---------------------------------------------------------------------------
# Metal and valuation
# ---------------------------------------------------------------------------


class Metal(ReferenceMixin, Base):
    """silver, gold, copper, platinum, palladium."""

    __tablename__ = "metal"

    symbol: Mapped[str] = mapped_column(String(4), default="", nullable=False)
    is_precious: Mapped[bool] = mapped_column(default=True, nullable=False)


class ValuationBasis(ReferenceMixin, Base):
    """Which value applies to an item: melt, numismatic, manual."""

    __tablename__ = "valuation_basis"


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class ErrorType(ReferenceMixin, Base):
    """A manufacturing defect, which usually makes an item more desirable.

    ``applies_to`` exists because the vocabularies barely overlap -- a coin is
    *struck*, a note is *printed* -- and the entry form should offer only what
    is relevant to the item in hand.
    """

    __tablename__ = "error_type"

    applies_to: Mapped[AppliesTo] = mapped_column(
        enum_column(AppliesTo, "applies_to"),
        default=AppliesTo.any,
        nullable=False,
        index=True,
    )


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class ItemStatus(ReferenceMixin, Base):
    """Acquisition lifecycle: ordered, received, canceled, returned, missing."""

    __tablename__ = "item_status"


class Disposition(ReferenceMixin, Base):
    """Sales lifecycle: held, listed, sold, shipped, delivered, ..."""

    __tablename__ = "disposition"


class StorageLocationKind(ReferenceMixin, Base):
    """safe_deposit_box, safe, home, in_transit, sold, unknown."""

    __tablename__ = "storage_location_kind"


# ---------------------------------------------------------------------------
# Images, sales and shipping
# ---------------------------------------------------------------------------


class ImageRole(ReferenceMixin, Base):
    """What a photograph shows: obverse, reverse, edge, slab, ..."""

    __tablename__ = "image_role"


class VendorKind(ReferenceMixin, Base):
    """Acquisition channel: marketplace, auction, mint, dealer."""

    __tablename__ = "vendor_kind"


class Carrier(ReferenceMixin, Base):
    """USPS, UPS, FedEx, DHL."""

    __tablename__ = "carrier"

    tracking_url_template: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )


class SalesOrderStatus(ReferenceMixin, Base):
    """Customer order lifecycle: pending, paid, shipped, delivered, ..."""

    __tablename__ = "sales_order_status"


class ShipmentStatus(ReferenceMixin, Base):
    """label_created, in_transit, delivered, lost, returned."""

    __tablename__ = "shipment_status"
