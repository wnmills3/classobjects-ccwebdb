"""Identification: four concepts that are routinely conflated.

  grading agency     who certified it        -> inventory_item.grading_service_id
  certificate serial unique to one holder    -> item_certification.cert_number
  note serial        printed on the banknote -> currency_detail.serial_number
  type number        shared by all of a type -> friedberg_number / pcgs_type

The last is the subtle one: a Friedberg or PCGS number identifies a *type*, not
an individual object. Two identical notes share a Friedberg number and have
different serial numbers.

Both catalogs are commercial, with no bulk license available, so both are
curated tables that grow with use rather than seeded datasets. Resolution
against them is a **proposal**, never a derivation -- see the resolver in
`app.identification`.
"""

from __future__ import annotations

from datetime import datetime

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
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, ProvenanceSource, TimestampMixin, enum_column, utcnow

__all__ = [
    "FriedbergNumber",
    "ItemAttributeLink",
    "ItemCertification",
    "ItemError",
    "PcgsType",
]


class ItemCertification(TimestampMixin, Base):
    """A grading certificate.

    One to many, not one to one: a lot may contain several certified pieces,
    and each certificate number is its own row.
    """

    __tablename__ = "item_certification"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    grading_service_id: Mapped[int | None] = mapped_column(
        ForeignKey("grading_service.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    #: Text, always -- certificate serials carry leading zeros and letters.
    cert_number: Mapped[str] = mapped_column(String(64), nullable=False, index=True)


class ItemError(Base):
    """One mint or printing error recorded on an item.

    Many rows per item, not one FK on `inventory_item`: miscut and overprint
    errors commonly appear on the same bill, and a single `error_type_id`
    column cannot express that. Placed here rather than on the item itself
    because it is an identification of a feature of the object, the same
    role `ItemAttributeLink` and `ItemCertification` play.

    The unique constraint on `(inventory_item_id, error_type_id)` means the
    same error recorded twice on one item updates one row rather than
    creating a second -- it is the same fact, not two.
    """

    __tablename__ = "item_error"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: An error is meaningless without its item, so it is deleted with it.
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: RESTRICT, matching every other classifier FK here: a type in use
    #: cannot be deleted out from under the rows that reference it.
    error_type_id: Mapped[int] = mapped_column(
        ForeignKey("error_type.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    #: Free text, per error -- so miscut and overprint on the same bill each
    #: get their own note rather than sharing one field. Never inferred from
    #: free text: see the note on `ErrorType`.
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    #: A machine guess must never be indistinguishable from a curated fact.
    source: Mapped[ProvenanceSource] = mapped_column(
        enum_column(ProvenanceSource, "provenance_source"),
        default=ProvenanceSource.manual,
        nullable=False,
    )
    #: SET NULL rather than CASCADE: deactivating a member of staff must not
    #: erase the record that the error was noted.
    noted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    noted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "inventory_item_id", "error_type_id", name="uq_item_error_item_type"
        ),
    )


class ItemAttributeLink(Base):
    """Many-to-many: what an item is, beyond its grade.

    Star Note and No Motto describe the object; they do not describe its
    condition, and putting them in the grade column is what makes condition
    unqueryable.

    **A removed attribute stays removed.** A rule that reads the serial or
    the facts would put a deleted link straight back, so a person removing
    one sets `removed_at` and the row stays; every reader skips it and every
    rule leaves it alone (app.item_attributes).
    docs/specs/item-attributes-design.md, section 2.
    """

    __tablename__ = "item_attribute_link"

    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="CASCADE"), primary_key=True
    )
    item_attribute_id: Mapped[int] = mapped_column(
        ForeignKey("item_attribute.id", ondelete="RESTRICT"),
        primary_key=True,
        index=True,
    )
    #: `derived` for a rule's reading, `manual` for a person's.
    source: Mapped[ProvenanceSource] = mapped_column(
        enum_column(ProvenanceSource, "provenance_source"),
        default=ProvenanceSource.manual,
        nullable=False,
    )
    #: Which rule derived it (`serial_pattern`, `attribute_rule`); None for a
    #: person's and for links older than the column.
    derived_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    noted_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    noted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    #: Set when a person removed a derived attribute; the row is kept so no
    #: rule adds it back.
    removed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class FriedbergNumber(TimestampMixin, Base):
    """US currency type catalog.

    ``fr_number`` is unconditionally unique so that a licensed dataset could
    later be merged in on the catalog number without creating duplicates.

    The identifying *tuple* is only partially unique: a plain unique index over
    it would reject two differently half-known types, which is a normal state
    for a catalog built by hand as notes arrive.
    """

    __tablename__ = "friedberg_number"

    id: Mapped[int] = mapped_column(primary_key=True)
    fr_number: Mapped[str] = mapped_column(String(32), nullable=False)
    base_number: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    district_letter: Mapped[str | None] = mapped_column(String(1), nullable=True)

    note_type_id: Mapped[int | None] = mapped_column(
        ForeignKey("note_type.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    denomination_id: Mapped[int | None] = mapped_column(
        ForeignKey("denomination.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    series_year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    series_letter: Mapped[str | None] = mapped_column(String(4), nullable=True)
    seal_color_id: Mapped[int | None] = mapped_column(
        ForeignKey("seal_color.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    signature_combination_id: Mapped[int | None] = mapped_column(
        ForeignKey("signature_combination.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    size_class: Mapped[str | None] = mapped_column(String(16), nullable=True)
    #: Printed on a web press rather than sheet-fed; None when not known. The
    #: two printings of one series, district and denomination are different
    #: types with different numbers, so this is part of the identity below.
    web_press: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    #: Where it was printed, `dc` or `fw`; None when not known. A 2017-A $1
    #: is 3005-A from Washington and 3006-A from Fort Worth, so this is part
    #: of the identity below, as `web_press` is.
    printing_facility: Mapped[str | None] = mapped_column(String(2), nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[ProvenanceSource] = mapped_column(
        enum_column(ProvenanceSource, "provenance_source"),
        default=ProvenanceSource.manual,
        nullable=False,
    )
    #: Set when a person confirms the row, which is what turns a proposal into
    #: a fact the next lookup can trust.
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("fr_number", name="uq_friedberg_number_fr_number"),
        # NULLS NOT DISTINCT: most series have no letter, so with NULLs
        # distinct this index never fired for them and the same type could be
        # recorded twice under two numbers (measured 2026-09-23). Which makes
        # every column that tells two types apart a member: the signatures
        # (many series differ by nothing else) and the seal (a wartime brown
        # or yellow seal beside the regular blue) were missing, and two real
        # types were refused as one (code review, 2026-09-23).
        Index(
            "uq_friedberg_number_identity",
            "denomination_id",
            "series_year",
            "series_letter",
            "note_type_id",
            "district_letter",
            "web_press",
            "signature_combination_id",
            "seal_color_id",
            "printing_facility",
            unique=True,
            postgresql_nulls_not_distinct=True,
            postgresql_where=text(
                "denomination_id IS NOT NULL AND series_year IS NOT NULL "
                "AND note_type_id IS NOT NULL"
            ),
        ),
        CheckConstraint(
            "size_class IS NULL OR size_class IN ('large', 'small', 'fractional')",
            name="ck_friedberg_number_size_class",
        ),
        CheckConstraint(
            "printing_facility IS NULL OR printing_facility IN ('dc', 'fw')",
            name="ck_friedberg_number_printing_facility",
        ),
    )


class PcgsType(TimestampMixin, Base):
    """Coin type catalog.

    The coin-side complement to a Friedberg number: it identifies the type, so
    every 1881-S Morgan shares one PCGS number regardless of grade.
    """

    __tablename__ = "pcgs_type"

    id: Mapped[int] = mapped_column(primary_key=True)
    pcgs_number: Mapped[int] = mapped_column(Integer, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)

    denomination_id: Mapped[int | None] = mapped_column(
        ForeignKey("denomination.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    series: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    variety: Mapped[str | None] = mapped_column(String(128), nullable=True)
    year: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    mint_id: Mapped[int | None] = mapped_column(
        ForeignKey("mint.id", ondelete="RESTRICT"), index=True, nullable=True
    )

    source: Mapped[ProvenanceSource] = mapped_column(
        enum_column(ProvenanceSource, "provenance_source"),
        default=ProvenanceSource.manual,
        nullable=False,
    )
    verified_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        UniqueConstraint("pcgs_number", name="uq_pcgs_type_number"),
        Index(
            "uq_pcgs_type_identity",
            "denomination_id",
            "year",
            "mint_id",
            "variety",
            unique=True,
            postgresql_where=text(
                "denomination_id IS NOT NULL AND year IS NOT NULL "
                "AND mint_id IS NOT NULL"
            ),
        ),
    )
