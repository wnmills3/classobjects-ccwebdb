"""Identification: four concepts that are routinely conflated.

  grading agency     who certified it        -> inventory_item.grading_service_id
  certificate serial unique to one holder    -> item_certification.cert_number
  note serial        printed on the banknote -> currency_detail.serial_number
  type number        shared by all of a type -> friedberg_number / pcgs_type

The last is the subtle one: a Friedberg or PCGS number identifies a *type*, not
an individual object. Two identical notes share a Friedberg number and have
different serial numbers.

Both catalogues are commercial, with no bulk licence available, so both are
curated tables that grow with use rather than seeded datasets. Resolution
against them is a **proposal**, never a derivation -- see the resolver in
`app.identification`.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
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

from .base import Base, ProvenanceSource, TimestampMixin, enum_column

__all__ = [
    "FriedbergNumber",
    "ItemCertification",
    "ItemNoteAttribute",
    "PcgsType",
]


class ItemCertification(TimestampMixin, Base):
    """A grading certificate.

    One to many, not one to one: a lot may contain several certified pieces,
    and a comma-separated list of certificate numbers in a source becomes
    several rows here rather than one unparsed string.
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
    #: The certification string exactly as supplied, before it was split.
    raw: Mapped[str | None] = mapped_column(Text, nullable=True)


class ItemNoteAttribute(Base):
    """Many-to-many: banknote features that are attributes, not grades.

    Star Note and Fancy Serial describe the note; they do not describe its
    condition, and putting them in the grade column is what makes condition
    unqueryable.
    """

    __tablename__ = "item_note_attribute"

    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="CASCADE"), primary_key=True
    )
    note_attribute_id: Mapped[int] = mapped_column(
        ForeignKey("note_attribute.id", ondelete="RESTRICT"), primary_key=True
    )


class FriedbergNumber(TimestampMixin, Base):
    """US currency type catalogue.

    ``fr_number`` is unconditionally unique so that a licensed dataset could
    later be merged in on the catalogue number without creating duplicates.

    The identifying *tuple* is only partially unique: a plain unique index over
    it would reject two differently half-known types, which is a normal state
    for a catalogue built by hand as notes arrive.
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
        Index(
            "uq_friedberg_number_identity",
            "denomination_id",
            "series_year",
            "series_letter",
            "note_type_id",
            "district_letter",
            unique=True,
            postgresql_where=text(
                "denomination_id IS NOT NULL AND series_year IS NOT NULL "
                "AND note_type_id IS NOT NULL"
            ),
        ),
        CheckConstraint(
            "size_class IS NULL OR size_class IN ('large', 'small', 'fractional')",
            name="ck_friedberg_number_size_class",
        ),
    )


class PcgsType(TimestampMixin, Base):
    """Coin type catalogue.

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
