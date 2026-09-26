"""An item's years: one year, or a range of them.

A single year is stored as ``year_start == year_end``. That is nearly the
whole collection's shape. A range is for a multi-year set, or a
coin whose date is only known to an era; a handful of items have one.

Callers set ``year_start`` alone -- the edit form's single Year box, bulk edit,
the Manage page -- and every write path used to apply it with a bare
``setattr``. On a single year that pulled the two ends apart, or, when the new
year was later, reached ``ck_inventory_item_year_range`` as an unhandled
IntegrityError. This module is the one place the rule lives.
"""

from __future__ import annotations

from collections.abc import Mapping

from fastapi import HTTPException, status

YEAR_FIELDS: frozenset[str] = frozenset({"year_start", "year_end"})

Years = tuple[int | None, int | None]


def resolve_years(current: Years, changes: Mapping[str, object]) -> Years | None:
    """The years an item should hold after `changes`, or None if untouched.

    A start sent alone moves the end with it when the item is a single year
    -- including one stored with no end at all -- and leaves a range's end
    where it is. Both sent are taken as given.
    """
    if not YEAR_FIELDS & changes.keys():
        return None
    start, end = current
    single = end is None or end == start

    new_start = changes.get("year_start", start)
    if "year_end" in changes:
        new_end = changes["year_end"]
    elif "year_start" in changes and single:
        new_end = new_start
    else:
        new_end = end
    assert new_start is None or isinstance(new_start, int)
    assert new_end is None or isinstance(new_end, int)
    return new_start, new_end


def backwards(years: Years) -> bool:
    """Whether a pair of years ends before it starts."""
    start, end = years
    return start is not None and end is not None and end < start


def refuse_backwards(years: Years, label: str) -> None:
    """Raise a 422 naming `label` if the years end before they start."""
    if backwards(years):
        start, end = years
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{label}: a range cannot end in {end}, before its start {start}.",
        )
