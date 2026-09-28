"""The one rule for which inventory rows are live.

A report, a search view, and a receiving list must never disagree about
what counts as "in the collection". This is that rule, in one place, so a
later report reuses it rather than growing its own copy that could drift.
"""

from __future__ import annotations

from sqlalchemy import ColumnElement, and_

from .models import InventoryItem


def live_item() -> ColumnElement[bool]:
    """The predicate for a live inventory row.

    A line that should never count towards what is outstanding, what a
    vendor sent, or what a receiving clerk sees: a soft-deleted row, which
    never should have existed, and a split parent, which has been replaced
    by its own children and would otherwise be received twice alongside
    them.
    """
    return and_(InventoryItem.deleted_at.is_(None), InventoryItem.split_at.is_(None))
