"""An item's detail row follows its kind.

A banknote's own facts -- series, serial number, seal -- live on
`currency_detail`; everything else a coin-like item carries beyond the item
row -- mint, variety, PCGS type -- lives on `coin_detail`. Which of the two an
item has is decided by its kind, and every reader asks the row rather than the
kind: `_apply_note_changes` refuses a serial number for an item with no
`currency_detail`, the offer title reads whichever row is there.

So a change of kind must change the row with it. Left alone, a coin re-kinded
as a banknote would have no `currency_detail`, and every banknote field the
editor then offered would be refused as "not a banknote".
`match_detail_to_kind` is the one place that swaps them; the item edit and
the bulk edit both call it after setting the kind.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CoinDetail, CurrencyDetail, InventoryItem, ItemKind

__all__ = ["KindChangeRefused", "match_detail_to_kind"]

#: `currency_detail` columns that hold something worth keeping. The generated
#: `series_designation` is left out: it is derived from two of these.
_NOTE_COLUMNS: tuple[str, ...] = (
    "note_type_id",
    "series_year",
    "series_letter",
    "seal_color_id",
    "signature_combination_id",
    "fed_district_id",
    "serial_number",
    # Cleared through `DELETE /inventory/{id}/friedberg`, not a PATCH.
    "friedberg_id",
)


class KindChangeRefused(ValueError):
    """A kind change that would throw away a banknote's own facts."""


def match_detail_to_kind(db: Session, item: InventoryItem) -> None:
    """Give `item` the detail row its (possibly just changed) kind calls for.

    To a banknote: the coin row goes and an empty note row takes its place. A
    mint, variety or PCGS type cannot describe a banknote -- on a note filed
    as a coin, the "mint" is typically a series letter read as a mint mark
    (1935-D) -- so there is nothing on it to keep.

    From a banknote: refused while the note row still holds anything. A
    serial number is the one identifier a note has, and dropping it silently
    because the kind moved is the loss this module exists to prevent; the
    caller clears the note's fields first, deliberately.
    """
    currency_id = db.scalar(select(ItemKind.id).where(ItemKind.code == "currency"))
    # Through the relationships, whose delete-orphan cascade removes the row
    # let go of: no flush here, so an edit still reaches the database as one
    # UPDATE and moves the item's version once.
    if item.item_kind_id == currency_id:
        item.coin_detail = None
        if item.currency_detail is None:
            item.currency_detail = CurrencyDetail()
        return

    note = item.currency_detail
    if note is not None:
        held = [c for c in _NOTE_COLUMNS if getattr(note, c) is not None]
        if held:
            raise KindChangeRefused(
                f"{item.item_code} is a banknote holding "
                f"{', '.join(c.removesuffix('_id') for c in held)}; clear "
                "them before changing its kind. Nothing was changed."
            )
        item.currency_detail = None
    if item.coin_detail is None:
        item.coin_detail = CoinDetail()
