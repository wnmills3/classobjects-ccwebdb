"""Reference (classifier) tables.

Every table here follows `ReferenceMixin`: id, code, label, sort_order,
is_active, source. A handful carry extra columns, and those are the interesting
ones -- each extra column exists because some question could not be answered
without it.

Foreign keys pointing at these tables are ``ON DELETE RESTRICT``: a classifier
that is in use must not be able to vanish and orphan the rows that reference
it.

**Two exceptions, both deliberate.** ``series_alias.series_id`` and
``series_year_range.series_id`` cascade, because those rows belong to their
series rather than referring to it -- they are `reference_merge._OWNED`, and
deleting a series is meant to take them with it. Everything else restricts.
"""

from __future__ import annotations

import enum
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    Computed,
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

from .base import Base, ProvenanceSource, ReferenceMixin, enum_column, utcnow

__all__ = [
    "AppliesTo",
    "AttributeGroup",
    "Authenticity",
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
    "ItemAttribute",
    "ItemKind",
    "ItemStatus",
    "Metal",
    "Mint",
    "NoteIssue",
    "NoteType",
    "ReferenceAlias",
    "ReferenceMerge",
    "SalesOrderStatus",
    "SalesVenueKind",
    "SealColor",
    "SetForm",
    "ShipmentStatus",
    "SignatureCombination",
    "StorageForm",
    "StorageLocationKind",
    "StrikeType",
    "ValuationBasis",
    "VendorKind",
]


class AppliesTo(enum.StrEnum):
    """Which kind of item an error type, attribute or designation is relevant to."""

    coin = "coin"
    currency = "currency"
    any = "any"


class DenominationKind(enum.StrEnum):
    """Whether a face value is a coin or a note. The same value exists as both."""

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
    #: default for data entry; `inventory_item.piece_count` is the truth.
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
    """Issuing country."""

    __tablename__ = "country"

    iso_alpha2: Mapped[str | None] = mapped_column(String(2), nullable=True)


class Denomination(ReferenceMixin, Base):
    """A face value in a currency.

    ``kind`` separates coin denominations from note denominations because the
    same face value exists as both: a US dollar is a coin and a bill, and they
    are different objects with different catalogs.
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


class Series(ReferenceMixin, Base):
    """A design series: Morgan Dollar, Peace Dollar, Winged Liberty Head Dime.

    "Series" is the industry's word, not one invented here -- PCGS organizes
    its price guide, population report and CoinFacts by series, so using the
    same term is what lets a value be looked up against a published guide.

    Notes carry one too where a design has a distinct identity, though the
    canonical identifier for US paper money is the Friedberg number rather
    than a series name.

    ``label`` is the formal name and ``aliases`` carry what people actually
    say. That distinction is load-bearing: "Winged Liberty Head Dime" appears
    nowhere in this collection's own descriptions and "Mercury" appears 104
    times, so a search that knew only the formal name would find nothing.
    """

    __tablename__ = "series"

    #: First and last year the design was struck. Nullable because a series
    #: still in production has no end, and some are not precisely bounded.
    year_start: Mapped[int | None] = mapped_column(Integer, nullable=True)
    year_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Which inventory this series belongs to, so the coin picker does not
    #: offer Silver Certificate and the currency picker does not offer Morgan.
    applies_to: Mapped[str] = mapped_column(
        String(16), default="coin", server_default=text("'coin'"), nullable=False
    )
    denomination_id: Mapped[int | None] = mapped_column(
        ForeignKey("denomination.id", ondelete="RESTRICT"), nullable=True
    )
    #: The design shares its denomination and series with ordinary notes, so
    #: those facts alone do not decide it -- a Hawaii note is a 1934 or 1935A
    #: note like any other, apart from its seal and overprint. Such a design is
    #: assigned only on evidence: a matching seal color, or text naming it.
    needs_evidence: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    #: The seal color that is evidence for this design: brown for Hawaii,
    #: yellow for North Africa.
    seal_color_id: Mapped[int | None] = mapped_column(
        ForeignKey("seal_color.id", ondelete="RESTRICT"), nullable=True
    )
    #: The note class every note of this design is. A design that names one
    #: is never assigned to a note recorded as another class, and a note
    #: recorded as this class is evidence for it: the Series 1929 National
    #: Bank Notes share their series and brown seal with the Federal Reserve
    #: Bank Notes, and only the class tells them apart.
    note_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("note_type.id", ondelete="RESTRICT"), nullable=True
    )

    aliases: Mapped[list[SeriesAlias]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )
    year_ranges: Mapped[list[SeriesYearRange]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )

    __table_args__ = (UniqueConstraint("code", name="uq_series_code"),)


class SeriesAlias(Base):
    """A colloquial name people search by.

    Kept separate from ``series.label`` rather than stuffed into it because
    the relationship is many-to-one in both directions: a series has several
    nicknames ("Mercury", "Merc"), and one nickname spans several series
    ("Cartwheel" is any large silver dollar).

    Aliases are never typed onto an item. An item inherits them through its
    series, so a nickname added later applies retrospectively to everything
    already classified.
    """

    __tablename__ = "series_alias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True, nullable=False
    )
    alias: Mapped[str] = mapped_column(String(64), nullable=False)
    #: False once someone removed a shipped alias: seed loads only add, so
    #: the row is kept to remember the removal (app.aliases).
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    source: Mapped[ProvenanceSource] = mapped_column(
        enum_column(ProvenanceSource, "provenance_source"),
        default=ProvenanceSource.seeded,
        server_default=text("'seeded'"),
        nullable=False,
    )

    series: Mapped[Series] = relationship(back_populates="aliases")

    __table_args__ = (UniqueConstraint("series_id", "alias", name="uq_series_alias"),)


class SeriesYearRange(Base):
    """One run of years, at one denomination, in which a design was issued.

    A design can need several: the Morgan dollar is 1878-1904, 1921 and 2021
    on, and a single span would make every dollar coin since 1878 a Morgan.
    The denomination is here, not only on the design, because some designs
    pair different denominations with different series -- the Hawaii $1 is
    Series 1935A, its $5 Series 1934 and 1934A. Null falls back to the
    design's own.

    ``letters`` narrows a note's series by its letter. Null means any letter,
    or none; otherwise it lists the allowed letters, with ``*`` for no letter,
    so ``*A`` is plain 1934 and 1934A and ``B`` is 1963B alone.

    A design with no rows is matched on its own span and denomination.
    """

    __tablename__ = "series_year_range"

    #: Stands for "no series letter" in `letters`.
    NO_LETTER = "*"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    series_id: Mapped[int] = mapped_column(
        ForeignKey("series.id", ondelete="CASCADE"), index=True, nullable=False
    )
    denomination_id: Mapped[int | None] = mapped_column(
        ForeignKey("denomination.id", ondelete="RESTRICT"), nullable=True
    )
    year_start: Mapped[int] = mapped_column(Integer, nullable=False)
    #: Null while the design is still being issued.
    year_end: Mapped[int | None] = mapped_column(Integer, nullable=True)
    letters: Mapped[str | None] = mapped_column(String(27), nullable=True)

    series: Mapped[Series] = relationship(back_populates="year_ranges")

    __table_args__ = (
        # NULLS NOT DISTINCT: most ranges have no denomination of their own,
        # and an ordinary unique constraint treats every null as different, so
        # a re-load could add the same coin range twice.
        UniqueConstraint(
            "series_id",
            "denomination_id",
            "year_start",
            name="uq_series_year_range",
            postgresql_nulls_not_distinct=True,
        ),
    )

    @classmethod
    def allows_letter(cls, letters: str | None, letter: str | None) -> bool:
        """Whether a note's series letter is one a range's `letters` allows.

        None allows any letter, so a coin (which has none) is never excluded.
        """
        if letters is None:
            return True
        wanted = (letter or "").strip().upper() or cls.NO_LETTER
        return len(wanted) == 1 and wanted in letters.upper()


class Mint(ReferenceMixin, Base):
    """Coin mint.

    ``mark`` is the letter struck on the coin, and may be blank: Philadelphia
    struck no mark on most issues.
    """

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


class StrikeType(ReferenceMixin, Base):
    """How a coin was struck: business strike, proof, specimen, reverse proof.

    Split from the grade (docs/specs/item-attributes-design.md): PR69+ is a
    proof with grade 69+, SP68 a specimen with 68. A strike is a way of
    making the coin, not a rank, and the services pair any of them with the
    same Sheldon numbers.

    ``prefix`` and ``suffix`` compose the familiar display -- "PR69+",
    "PR70 Reverse Proof". A strike with no prefix (the business strike) takes
    its prefix from the number: MS from 60, AU from 50, down to PO 1.
    """

    __tablename__ = "strike_type"

    prefix: Mapped[str | None] = mapped_column(String(16), nullable=True)
    suffix: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Grade(ReferenceMixin, Base):
    """A condition grade: a Sheldon number, a note grade, or an adjectival one.

    Coin grades are numbers alone -- ``65``, ``64+`` -- and the strike type
    says whether that is MS65 or PR65. ``numeric_value`` is what makes "all
    my 65-and-better Morgans" an answerable question; ``is_plus`` ranks a plus
    grade above its number, so 64+ falls between 64 and 65. The few grades
    with no number (Circulated, Ungraded) stay null rather than being given a
    fictional one.
    """

    __tablename__ = "grade"

    grade_scale_id: Mapped[int | None] = mapped_column(
        ForeignKey("grade_scale.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    numeric_value: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_plus: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    #: The grade's place in order: the number, and half a point for a plus.
    grade_rank: Mapped[Decimal | None] = mapped_column(
        Numeric(4, 1),
        Computed(
            "numeric_value + CASE WHEN is_plus THEN 0.5 ELSE 0 END", persisted=True
        ),
        nullable=True,
    )

    grade_scale: Mapped[GradeScale | None] = relationship()


class GradeDesignation(ReferenceMixin, Base):
    """Grade suffix: DCAM, CAM, RD, RB, BN, FS, FB on a coin; EPQ, PPQ on a note.

    One per grade, and part of it -- PMG writes "Choice Uncirculated 64 EPQ"
    -- which is why it is a column on the item rather than an attribute.
    `applies_to` says which side of the coin/note split each belongs to, so
    a note is offered only paper qualities and a coin only strike ones.
    """

    __tablename__ = "grade_designation"

    applies_to: Mapped[AppliesTo] = mapped_column(
        enum_column(AppliesTo, "applies_to"),
        default=AppliesTo.coin,
        nullable=False,
    )


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


class NoteIssue(Base):
    """One small-size issue: a denomination and series, and what it was.

    A $1 Series 1957 is a Silver Certificate with a blue seal, signed Priest
    and Anderson. Those are facts of the issue, not observations of a note, so
    they are recorded once here and looked up by `app.classifier_defaults`
    rather than typed on every note (docs/specs/classifier-defaults-design.md).

    One row per class and seal a series was issued in: $5 Series 1934A was
    both a green-seal Federal Reserve Note and a yellow-seal Silver
    Certificate, and those are two rows. When a note's facts match more than
    one row, its class is for a person to decide.

    Owned by the seed file: a load makes the rows match it exactly.
    """

    __tablename__ = "note_issue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    denomination_id: Mapped[int] = mapped_column(
        ForeignKey("denomination.id", ondelete="RESTRICT"), nullable=False
    )
    series_year: Mapped[int] = mapped_column(Integer, nullable=False)
    #: The series letter, or null for a plain series (1935, not 1935A).
    series_letter: Mapped[str | None] = mapped_column(String(1), nullable=True)
    note_type_id: Mapped[int] = mapped_column(
        ForeignKey("note_type.id", ondelete="RESTRICT"), nullable=False
    )
    seal_color_id: Mapped[int] = mapped_column(
        ForeignKey("seal_color.id", ondelete="RESTRICT"), nullable=False
    )
    #: Null where the issue was not signed by a Treasurer and Secretary pair
    #: (the Series 1929 bank notes carry bank officers' signatures).
    signature_combination_id: Mapped[int | None] = mapped_column(
        ForeignKey("signature_combination.id", ondelete="RESTRICT"), nullable=True
    )
    #: What sets this row apart within its series, when something does:
    #: "Hawaii", "North Africa".
    variant: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: The first serial letter a Federal Reserve Note of this issue carries,
    #: for $5 and higher from Series 1996 (BEP: A = 1996, B = 1999, ...).
    #: Null where the serial carries no series letter.
    serial_prefix: Mapped[str | None] = mapped_column(String(1), nullable=True)

    __table_args__ = (
        UniqueConstraint(
            "denomination_id",
            "series_year",
            "series_letter",
            "note_type_id",
            "seal_color_id",
            name="uq_note_issue",
            postgresql_nulls_not_distinct=True,
        ),
        Index("ix_note_issue_lookup", "denomination_id", "series_year"),
    )


class ReferenceAlias(Base):
    """Another name for a row of any classifier table.

    "Legal Tender Note" is a United States Note; "National Currency" is a
    National Bank Note. Text uses the other names, so search and the pickers
    must recognize them. One table serves every vocabulary (note types now, grade
    designations next: Ultra Cameo for UCAM) rather than one alias table per
    classifier. `series_alias` predates it and stays.

    The row is named by table and id, not by a foreign key -- a key cannot
    point at "any table". Seed files name it by code, as everywhere else.
    """

    __tablename__ = "reference_alias"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    row_id: Mapped[int] = mapped_column(Integer, nullable=False)
    alias: Mapped[str] = mapped_column(String(255), nullable=False)
    #: False once someone removed a shipped alias: seed loads only add, so
    #: the row is kept to remember the removal (app.aliases).
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
    source: Mapped[ProvenanceSource] = mapped_column(
        enum_column(ProvenanceSource, "provenance_source"),
        default=ProvenanceSource.seeded,
        server_default=text("'seeded'"),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("table_name", "row_id", "alias", name="uq_reference_alias"),
        Index("ix_reference_alias_table_row", "table_name", "row_id"),
    )


class ReferenceMerge(Base):
    """A value merged into another, and so removed (app.reference_merge).

    Kept so the removal lasts: a seed load skips a merged code, and reads a
    seed row that names it as naming the value it was merged into. Named by
    table and code, as seed files name values; the merged row is gone.
    """

    __tablename__ = "reference_merge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    table_name: Mapped[str] = mapped_column(String(64), nullable=False)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    merged_into: Mapped[str] = mapped_column(String(64), nullable=False)
    merged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    #: SET NULL: removing a member of staff must not undo a merge.
    merged_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("table_name", "code", name="uq_reference_merge"),
    )


class AttributeGroup(enum.StrEnum):
    """What kind of fact an item attribute records.

    docs/specs/item-attributes-design.md, section 2. The editor groups them
    by this.
    """

    #: Star Note, Fancy Serial, Radar: read from a note's serial.
    serial = "serial"
    #: No Motto, Wide / Narrow, Mule: a variety of the design or printing.
    variety = "variety"
    #: First Strike, Early Releases, First Day of Issue: a release pedigree,
    #: each service's own, since each defines its own window.
    release = "release"
    #: CAC: someone other than the grader verified the grade.
    verification = "verification"
    #: Details, Genuine, NET: what the holder says instead of, or beside, a
    #: straight grade.
    qualifier = "qualifier"


class ItemAttribute(ReferenceMixin, Base):
    """Star Note, No Motto, First Strike, CAC ... -- many-to-many with an item.

    These are attributes, not grades, which is why they are a link table rather
    than another column on the item: a coin can be DCAM *and* First Strike
    *and* CAC-approved, a note a star note *and* a fancy serial *and* No
    Motto. Was `note_attribute`, which held serial features only.
    """

    __tablename__ = "item_attribute"

    applies_to: Mapped[AppliesTo] = mapped_column(
        enum_column(AppliesTo, "applies_to"),
        default=AppliesTo.any,
        nullable=False,
    )
    attribute_group: Mapped[AttributeGroup] = mapped_column(
        enum_column(AttributeGroup, "attribute_group"),
        nullable=False,
        index=True,
    )


class SealColor(ReferenceMixin, Base):
    """Treasury seal color: blue, red, brown, green, gold."""

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


class SalesVenueKind(ReferenceMixin, Base):
    """How a sales platform sells: own store, marketplace, live show, auction house."""

    __tablename__ = "sales_venue_kind"


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
