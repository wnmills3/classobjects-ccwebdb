"""What counts as "a purchase" -- shared by `pr_spend`, `pr_sources` and `mn_tax`.

A purchase counts only when it carries at least one live item
(docs/specs/reporting-design.md, Decisions) -- an inner join to
`InventoryItem` through `live_item()`, never `pr_outstanding`'s outer one --
so no two reports built on top of this can silently disagree about the same
word. `pr_spend` and `mn_tax` read this one join, the same date-range filter
on `ordered_on`, and the same note explaining an excluded, undated purchase,
rather than each restating its own; `pr_sources` shares the rule and its
note.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ColumnElement, Select, and_

from ..live import live_item
from ..models import InventoryItem, PurchaseOrder
from .base import DateRange

__all__ = [
    "LIVE_ITEM_PURCHASE_NOTE",
    "dated_purchase_where",
    "join_live_purchase_items",
    "undated_purchase_note",
]

#: The one rule every report built on `join_live_purchase_items` shares:
#: only a purchase with a live item counts as a purchase at all.
LIVE_ITEM_PURCHASE_NOTE = (
    "Purchases are counted only when they carry at least one live item."
)


def join_live_purchase_items(stmt: Select[Any]) -> Select[Any]:
    """`stmt`, inner-joined to the live items of the purchases it selects over.

    An inner join: a purchase with no live item of its own contributes
    nothing here -- not to the money columns, and not even to a
    `count(distinct ...)` of purchases, since that never sees a purchase
    this join dropped. The caller's own note should say so
    (`LIVE_ITEM_PURCHASE_NOTE`).
    """
    return stmt.select_from(PurchaseOrder).join(
        InventoryItem,
        and_(InventoryItem.purchase_order_id == PurchaseOrder.id, live_item()),
    )


def dated_purchase_where(params: DateRange) -> list[ColumnElement[bool]]:
    """A known order date, in range, inclusive on both ends when given."""
    where: list[ColumnElement[bool]] = [PurchaseOrder.ordered_on.is_not(None)]
    if params.date_from is not None:
        where.append(PurchaseOrder.ordered_on >= params.date_from)
    if params.date_to is not None:
        where.append(PurchaseOrder.ordered_on <= params.date_to)
    return where


def undated_purchase_note(count: int) -> str:
    """How many purchases with no order date were left out of a report."""
    plural = "s" if count != 1 else ""
    return f"{count} purchase{plural} with no order date excluded."
