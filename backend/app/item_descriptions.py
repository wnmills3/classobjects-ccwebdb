"""A description for an item, composed from what its record says.

The item editor's **Suggest** button (owner's request, 2026-09-23). Most
descriptions are the seller's words -- "Item As Seen On Our Ebay Live
Stream", "#7" -- and rewriting several thousand by hand starts from a blank
box. `suggested_description` writes one from the item's classified facts,
the way a collector would describe it, for the owner to edit and save:

    Series 1999 $1 Federal Reserve Note. District F - Atlanta, Green Seal,
    signatures Withrow / Summers. Serial number F06566560R. Graded PMG
    Choice Uncirculated 64 EPQ, certificate 8061234-005. Star Note.
    Errors: Ink Smear (left margin).

It is a suggestion only: nothing here writes, and the editor puts it in the
draft, where the owner's Save is what keeps it. It reads the *saved* item,
so the editor asks for other edits to be saved first.

Facts, not a catalogue's arrangement: every part is a label from this
database's own vocabularies or the item's own values (CLAUDE.md,
*Reference data*). The item's name is `offer_titles.name_of`, so a listing
title and a description never name the same coin two ways.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import grades, item_attributes
from .models import (
    ErrorType,
    FedDistrict,
    InventoryItem,
    ItemCertification,
    ItemError,
    SealColor,
    SignatureCombination,
)
from .offer_titles import name_of

__all__ = ["suggested_description"]

#: The grading service code meaning the owner graded it.
_SELF_GRADED = "SELF"


def _sentence(parts: list[str]) -> str | None:
    """Parts joined as one sentence, or None when there are none."""
    return ", ".join(parts) if parts else None


def _note_details(db: Session, item: InventoryItem) -> list[str]:
    """District, seal and signatures, then the serial, each its own part."""
    detail = item.currency_detail
    if detail is None:
        return []
    about: list[str] = []
    if detail.fed_district_id is not None:
        district = db.get(FedDistrict, detail.fed_district_id)
        if district is not None:
            about.append(f"District {district.label}")
    if detail.seal_color_id is not None:
        seal = db.get(SealColor, detail.seal_color_id)
        if seal is not None:
            about.append(seal.label)
    if detail.signature_combination_id is not None:
        signers = db.get(SignatureCombination, detail.signature_combination_id)
        if signers is not None:
            about.append(f"signatures {signers.label}")
    sentences = [s for s in [_sentence(about)] if s]
    if detail.serial_number:
        sentences.append(f"Serial number {detail.serial_number}")
    return sentences


def _coin_details(item: InventoryItem) -> list[str]:
    """Metal and its fine weight; a lot's piece count."""
    parts: list[str] = []
    if item.metal is not None:
        metal = item.metal.label
        fine = item.fine_weight_ozt
        if fine is not None and fine > 0:
            metal += f", {fine.normalize():f} ozt fine"
        parts.append(metal)
    sentences = [s for s in [_sentence(parts)] if s]
    if item.piece_count and item.piece_count > 1:
        sentences.append(f"{item.piece_count} pieces")
    return sentences


def _grading(db: Session, item: InventoryItem) -> str | None:
    """``Graded PMG Choice Uncirculated 64 EPQ, certificate 8061234-005``."""
    grade = grades.display_item(item)
    if grade is not None and item.grade_designation is not None:
        grade = f"{grade} {item.grade_designation.code}"
    service = item.grading_service
    certs = list(
        db.scalars(
            select(ItemCertification.cert_number)
            .where(ItemCertification.inventory_item_id == item.id)
            .order_by(ItemCertification.id)
        )
    )
    if service is not None and service.code == _SELF_GRADED:
        text = f"Owner's grade {grade}" if grade else None
    elif service is not None:
        text = f"Graded {service.label}" + (f" {grade}" if grade else "")
    elif grade is not None:
        # No service recorded is not the same as raw: most of these notes
        # are slabbed and only the grader was never typed in.
        text = f"Grade {grade}"
    else:
        text = None
    if certs:
        label = "certificate" if len(certs) == 1 else "certificates"
        cert = f"{label} {', '.join(certs)}"
        text = f"{text}, {cert}" if text else cert[0].upper() + cert[1:]
    return text


def _errors(db: Session, item: InventoryItem) -> str | None:
    """``Errors: Ink Smear (left margin), Misaligned Overprint``."""
    rows = db.execute(
        select(ErrorType.label, ItemError.details)
        .join(ErrorType, ErrorType.id == ItemError.error_type_id)
        .where(ItemError.inventory_item_id == item.id)
        .order_by(ErrorType.sort_order, ErrorType.label)
    ).tuples()
    named = [f"{label} ({details})" if details else label for label, details in rows]
    if not named:
        return None
    return ("Error: " if len(named) == 1 else "Errors: ") + ", ".join(named)


def suggested_description(db: Session, item: InventoryItem) -> str:
    """A description of the item from its saved record, in sentences.

    Empty when the record holds nothing to say -- the editor then says so
    rather than replacing the description with nothing.
    """
    sentences: list[str] = []
    name = name_of(db, item)
    if name is not None:
        sentences.append(name)
    if item.currency_detail is not None:
        sentences.extend(_note_details(db, item))
    else:
        sentences.extend(_coin_details(item))
    graded = _grading(db, item)
    if graded:
        sentences.append(graded)
    attributes = [held.label for held in item_attributes.held_attributes(db, item.id)]
    if attributes:
        sentences.append(", ".join(attributes))
    errors = _errors(db, item)
    if errors:
        sentences.append(errors)
    return " ".join(f"{s}." for s in sentences)
