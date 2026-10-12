"""A note and the star note of the same serial number, found for each other.

A replacement note is numbered in its own run, so a star note whose eight
digits are those of an ordinary note in the collection is a pair collectors
keep together -- and one easy to own without knowing it. Asked of a note, this
finds the star notes of its digits; asked of a star note, the notes without
the star.

Only the digits are compared. The letters round them say which Bank and
block, and a pair across two Banks or two denominations is still a pair to be
told of: each match comes with its denomination and series for the owner to
judge.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import CurrencyDetail, Denomination, InventoryItem
from .serial_patterns import SERIAL_DIGITS, digits_of

__all__ = ["SerialPair", "serial_pairs"]


@dataclass(frozen=True)
class SerialPair:
    """One note in the collection whose serial pairs with the one asked about."""

    id: int
    item_code: str
    serial_number: str
    denomination: str | None
    series: str | None


def serial_pairs(db: Session, serial: str | None, item_id: int) -> list[SerialPair]:
    """The notes that pair with `serial`, other than item `item_id` itself.

    Star notes of its digits when `serial` has no star; notes without one
    when it has. Nothing for a serial that is not eight digits long: one cut
    short would match every other cut short the same way, and says nothing of
    the note in hand. Deleted and split items are left out.
    """
    digits = digits_of(serial or "")
    if len(digits) != SERIAL_DIGITS:
        return []
    starred = CurrencyDetail.serial_number.contains("*", autoescape=True)
    rows = db.execute(
        select(
            InventoryItem.id,
            InventoryItem.item_code,
            CurrencyDetail.serial_number,
            Denomination.label,
            CurrencyDetail.series_designation,
        )
        .join(CurrencyDetail, CurrencyDetail.inventory_item_id == InventoryItem.id)
        .join(
            Denomination,
            Denomination.id == InventoryItem.denomination_id,
            isouter=True,
        )
        .where(
            InventoryItem.id != item_id,
            InventoryItem.split_at.is_(None),
            InventoryItem.deleted_at.is_(None),
            func.regexp_replace(CurrencyDetail.serial_number, r"\D", "", "g") == digits,
            ~starred if "*" in (serial or "") else starred,
        )
        .order_by(InventoryItem.item_code)
    ).tuples()
    # A row matched on its digits has a serial; `or ""` is for the type alone.
    return [
        SerialPair(ident, code, found or "", denomination, series)
        for ident, code, found, denomination, series in rows
    ]
