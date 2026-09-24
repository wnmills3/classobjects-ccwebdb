"""A description for an item, composed from what its record says.

The item editor's **Suggest** button (owner's request, 2026-09-23). Most
descriptions are the seller's words -- "Item As Seen On Our Ebay Live
Stream", "#7" -- and rewriting several thousand by hand starts from a blank
box. `suggested_description` writes one from the item's classified facts,
in the owner's own style: the grade and what makes the piece special first,
then what it is, with no field labels (owner, 2026-09-24):

    Superb Gem Unc 67 EPQ Radar 1999 $1 S/N F06566560R. Federal Reserve
    Note Green Seal.

    MS64 First Strike 1921-S Morgan Dollar. Silver, 0.7734 ozt fine.

The district and signatures are left out -- they follow from the serial and
series, and a buyer does not search by them -- and so is the grading
service, which the holder shows. Errors come last, as a selling point.

It is a suggestion only: nothing here writes, and the editor puts it in the
draft, where the owner's Save is what keeps it. It reads the *saved* item,
so the editor asks for other edits to be saved first.

Facts, not a catalogue's arrangement: every part is a label from this
database's own vocabularies or the item's own values (CLAUDE.md,
*Reference data*).
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import grades, item_attributes
from .models import (
    ErrorType,
    InventoryItem,
    ItemError,
    NoteType,
    SealColor,
)
from .offer_titles import name_of

__all__ = ["suggested_description"]

#: Words a note grade is written shorter in: "Superb Gem Unc 67".
_SHORT_GRADE_WORDS = ((re.compile(r"\bUncirculated\b"), "Unc"),)

#: An attribute's label less the noun every serial feature shares: a
#: "Radar Serial" is written "Radar" beside the serial number itself.
_SERIAL_SUFFIX = re.compile(r"\s+Serial$")


def _grade(item: InventoryItem) -> str | None:
    """``Superb Gem Unc 67 EPQ``, ``MS64 DCAM``: the grade and designation."""
    grade = grades.display_item(item)
    if grade is None:
        return None
    for pattern, short in _SHORT_GRADE_WORDS:
        grade = pattern.sub(short, grade)
    if item.grade_designation is not None:
        grade = f"{grade} {item.grade_designation.code}"
    return grade


def _attributes(db: Session, item: InventoryItem) -> list[str]:
    """The item's attributes as one part, short: ``Radar``, ``Star Note, Binary``.

    Comma-separated when there are several, so "Low Serial Number, Binary,
    Double Quad" does not run together into one phrase.
    """
    labels = [
        _SERIAL_SUFFIX.sub("", held.label)
        for held in item_attributes.held_attributes(db, item.id)
    ]
    return [", ".join(labels)] if labels else []


def _errors(db: Session, item: InventoryItem) -> str | None:
    """``Error: Ink Smear (left margin)``, or several after ``Errors:``."""
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


def _note(db: Session, item: InventoryItem) -> list[str]:
    """``[grade attributes year face S/N serial, note type seal]``."""
    detail = item.currency_detail
    assert detail is not None
    first: list[str] = []
    if grade := _grade(item):
        first.append(grade)
    first.extend(_attributes(db, item))
    year = detail.series_designation or (
        str(item.year_start) if item.year_start is not None else None
    )
    if year:
        first.append(year)
    if item.denomination is not None:
        first.append(item.denomination.label.removesuffix(" Bill"))
    if detail.serial_number:
        first.append(f"S/N {detail.serial_number}")

    second: list[str] = []
    if detail.note_type_id is not None:
        note_type = db.get(NoteType, detail.note_type_id)
        if note_type is not None:
            second.append(note_type.label)
    if detail.seal_color_id is not None:
        seal = db.get(SealColor, detail.seal_color_id)
        if seal is not None:
            second.append(seal.label)
    return [" ".join(part) for part in (first, second) if part]


def _coin(db: Session, item: InventoryItem) -> list[str]:
    """``[grade attributes name, metal and fine weight, pieces]``."""
    first: list[str] = []
    if grade := _grade(item):
        first.append(grade)
    first.extend(_attributes(db, item))
    if (name := name_of(db, item)) is not None:
        first.append(name)
    sentences = [" ".join(first)] if first else []
    if item.metal is not None:
        metal = item.metal.label
        fine = item.fine_weight_ozt
        if fine is not None and fine > 0:
            metal += f", {fine.normalize():f} ozt fine"
        sentences.append(metal)
    if item.piece_count and item.piece_count > 1:
        sentences.append(f"{item.piece_count} pieces")
    return sentences


def suggested_description(db: Session, item: InventoryItem) -> str:
    """A description of the item from its saved record, in sentences.

    Empty when the record holds nothing to say -- the editor then says so
    rather than replacing the description with nothing.
    """
    note = item.currency_detail is not None
    sentences = _note(db, item) if note else _coin(db, item)
    if errors := _errors(db, item):
        sentences.append(errors)
    return " ".join(f"{s}." for s in sentences)
