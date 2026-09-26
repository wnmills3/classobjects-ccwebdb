"""A public title for an item, composed from what the record says it is.

`source_title` is the *seller's* wording from the purchase, which would go
onto eBay unedited if the operator did not stop to rewrite it. That wording
is often not a name at all: thousands of items are titled only by their
denomination -- "1", "0.25" or "$1 Bill" -- and a Whatnot purchase carries
lines such as "ITEM SEEN ON SCREEN ASK QUESTIONS NO CANCELLATIONS".

`suggested_title` builds the title from the item's own classified facts
instead, the way a collector would write it:

- a coin: ``1921-S Morgan Dollar PCGS MS64`` -- year (or span) and mint mark,
  then the set form, series or denomination, the variety, the grading
  service and the grade;
- a note: ``Series 1935A $1 Silver Certificate PMG 64`` -- series, face
  value and note type, then service and grade.

It is a **default**, shown in an editable field; the operator still owns the
wording. `source_title` remains the fallback only when the record holds no
fact worth naming, so an unclassified item is no worse off than before.

Facts, not a catalog's arrangement: every part is a label from this
database's own vocabularies (see CLAUDE.md, *Reference data*).
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from . import grades
from .models import (
    InventoryItem,
    Mint,
    NoteType,
    Series,
)

#: The grading service code meaning the owner graded it: not a service a
#: buyer would recognize, so it is left out of a public title.
_SELF_GRADED = "SELF"

#: The item kind whose title is written as a note rather than a coin.
_CURRENCY_KIND = "currency"


def _years(item: InventoryItem) -> str | None:
    """``1921``, or ``1878-1904`` for a span, or None when undated."""
    start, end = item.year_start, item.year_end
    if start is None:
        return None
    if end is not None and end != start:
        return f"{start}-{end}"
    return str(start)


def _graded(item: InventoryItem) -> list[str]:
    """``["PMG", "64", "EPQ"]``: service, grade, then designation.

    The designation is printed after the grade -- on a note's holder
    "64 EPQ" is the grade, and a title without the EPQ undersells it.
    """
    parts: list[str] = []
    service = item.grading_service
    if service is not None and service.code != _SELF_GRADED:
        parts.append(service.code)
    grade = grades.display_item(item)
    if grade:
        parts.append(grade)
        if item.grade_designation is not None:
            parts.append(item.grade_designation.code)
    return parts


def _series_label(db: Session, item: InventoryItem) -> str | None:
    """The series' label, e.g. ``Morgan Dollar``, or None."""
    if item.series_id is None:
        return None
    series = db.get(Series, item.series_id)
    return series.label if series is not None else None


def _coin_title(db: Session, item: InventoryItem) -> tuple[list[str], bool]:
    """A coin's title parts without the grade, and whether any part names it.

    A year and a mint mark ("1921-S") date a coin but do not say what it is;
    only a set form, series, denomination or variety does.
    """
    # Through the relationship, not a query by id: the New item form's
    # suggestion describes an item that is not saved and has no id.
    detail = item.coin_detail
    mint = (
        db.get(Mint, detail.mint_id)
        if detail is not None and detail.mint_id is not None
        else None
    )
    parts: list[str] = []
    years = _years(item)
    if years is not None:
        # A mint mark belongs to one year's coin; on a span it would claim
        # every year came from that mint.
        single_year = item.year_end in (None, item.year_start)
        if mint is not None and mint.mark and single_year:
            years = f"{years}-{mint.mark}"
        parts.append(years)
    named = False
    if item.set_form is not None:
        parts.append(item.set_form.label)
        named = True
    else:
        name = _series_label(db, item)
        if name is None and item.denomination is not None:
            name = item.denomination.label
        if name is not None:
            parts.append(name)
            named = True
    if detail is not None and detail.variety:
        parts.append(detail.variety)
        named = True
    return parts, named


def _note_title(db: Session, item: InventoryItem) -> tuple[list[str], bool]:
    """A note's title parts without the grade, and whether any part names it.

    A series or a year dates a note; its face value, type or series label
    say what it is.
    """
    detail = item.currency_detail
    note_type = (
        db.get(NoteType, detail.note_type_id)
        if detail is not None and detail.note_type_id is not None
        else None
    )
    parts: list[str] = []
    if detail is not None and detail.series_designation:
        parts.append(f"Series {detail.series_designation}")
    elif item.year_start is not None:
        parts.append(str(item.year_start))
    face = item.denomination.label if item.denomination is not None else None
    named = note_type is not None or face is not None
    if note_type is not None:
        # "$1 Bill" is the vocabulary's word for the face value alone; with
        # a note type beside it, "$1 Silver Certificate" is how it is said.
        if face is not None:
            parts.append(face.removesuffix(" Bill"))
        parts.append(note_type.label)
    elif face is not None:
        parts.append(face)
    else:
        name = _series_label(db, item)
        if name is not None:
            parts.append(name)
            named = True
    return parts, named


def name_of(db: Session, item: InventoryItem) -> str | None:
    """What the item is, without its grade; None when no fact names it.

    ``1921-S Morgan Dollar``, ``Series 1935A $1 Silver Certificate``.
    """
    if item.item_kind.code == _CURRENCY_KIND:
        described, named = _note_title(db, item)
    else:
        described, named = _coin_title(db, item)
    return " ".join(described) if named else None


def suggested_title(db: Session, item: InventoryItem) -> str:
    """The title a listing of this item should start from.

    Built from the item's classified facts; falls back to `source_title`
    when nothing in them names the item -- a date, a mint mark or a grade
    alone says nothing a buyer is looking for, and the seller's wording,
    however rough, is at least the operator's own record of what it is.
    Empty only when that is empty too, and the dialog then shows an empty
    title for the operator to write.
    """
    name = name_of(db, item)
    if name is None:
        return item.source_title
    return " ".join([name, *_graded(item)])


def suggested_titles(db: Session, item_ids: Sequence[int]) -> dict[int, str]:
    """`suggested_title` for each of these items that exists, keyed by id."""
    items = db.scalars(
        select(InventoryItem)
        .where(InventoryItem.id.in_(item_ids))
        # A title reads each item's own detail row: one query each for the
        # set rather than one per item.
        .options(
            selectinload(InventoryItem.coin_detail),
            selectinload(InventoryItem.currency_detail),
        )
    ).all()
    return {item.id: suggested_title(db, item) for item in items}
