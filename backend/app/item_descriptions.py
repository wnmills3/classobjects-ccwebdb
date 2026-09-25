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
service, which the holder shows. Errors are promoted beside the
attributes, right after the grade: they are what an error note sells on
(owner, 2026-09-24) -- "Error Note, Misaligned Print (Reverse) 1963A $1".

It is a suggestion only: nothing here writes, and the editor puts it in the
draft, where the owner's Save is what keeps it. The editor's reads the
*saved* item, so it asks for other edits to be saved first. The New item
form's reads an unsaved one (`routers.inventory._draft_item`): an item
built in memory from the form's fields, never added to the session, with
the attributes its serial earns (`app.serial_patterns`) and the errors the
form holds.

Facts, not a catalog's arrangement: every part is a label from this
database's own vocabularies or the item's own values (CLAUDE.md,
*Reference data*).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

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

__all__ = ["FANCY_SERIAL", "Features", "suggested_description"]

#: The attribute that says only "some digit pattern": left out beside one.
FANCY_SERIAL = "fancy_serial"


@dataclass(frozen=True)
class Features:
    """An unsaved item's attributes and errors, which no table holds yet.

    `attributes` are labels in vocabulary order; `errors` are (label,
    details) pairs in vocabulary order.
    """

    attributes: list[str] = field(default_factory=list)
    errors: list[tuple[str, str | None]] = field(default_factory=list)


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


def _features(db: Session, item: InventoryItem, given: Features | None) -> list[str]:
    """Attributes then errors, as one part: ``Radar, Misaligned Print (Reverse)``.

    Attributes short ("Radar", not "Radar Serial"); an error with its
    details in brackets. Comma-separated, so several do not run together.
    `given` stands in for the tables when the item is not saved.
    """
    if given is not None:
        attributes, errors = given.attributes, given.errors
    else:
        held = item_attributes.held_attributes(db, item.id)
        # "Fancy Serial" is the umbrella over the digit patterns: beside the
        # pattern itself -- Trinary, Radar -- it says nothing more.
        if len(held) > 1:
            held = [h for h in held if h.code != FANCY_SERIAL]
        attributes = [h.label for h in held]
        errors = list(
            db.execute(
                select(ErrorType.label, ItemError.details)
                .join(ErrorType, ErrorType.id == ItemError.error_type_id)
                .where(ItemError.inventory_item_id == item.id)
                .order_by(ErrorType.sort_order, ErrorType.label)
            ).tuples()
        )
    labels = [_SERIAL_SUFFIX.sub("", label) for label in attributes]
    labels += [
        f"{label} ({details})" if details else label for label, details in errors
    ]
    return [", ".join(labels)] if labels else []


def _note(db: Session, item: InventoryItem, given: Features | None) -> list[str]:
    """``[grade attributes year face S/N serial, note type seal]``."""
    detail = item.currency_detail
    assert detail is not None
    first: list[str] = []
    if grade := _grade(item):
        first.append(grade)
    first.extend(_features(db, item, given))
    # `series_designation` is computed by the database; an unsaved note has
    # none yet, so it is written the way the column writes it: 1935A.
    designation = detail.series_designation or (
        f"{detail.series_year}{detail.series_letter or ''}"
        if detail.series_year is not None
        else None
    )
    year = designation or (
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


def _coin(db: Session, item: InventoryItem, given: Features | None) -> list[str]:
    """``[grade attributes name, metal and fine weight, pieces]``."""
    first: list[str] = []
    if grade := _grade(item):
        first.append(grade)
    first.extend(_features(db, item, given))
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


def suggested_description(
    db: Session, item: InventoryItem, features: Features | None = None
) -> str:
    """A description of the item from its record, in sentences.

    `features` gives an unsaved item's attributes and errors; a saved item's
    are read from their tables. Empty when the record holds nothing to say --
    the form then says so rather than replacing the description with nothing.
    """
    note = item.currency_detail is not None
    sentences = _note(db, item, features) if note else _coin(db, item, features)
    return " ".join(f"{s}." for s in sentences)
