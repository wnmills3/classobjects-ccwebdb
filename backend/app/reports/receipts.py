"""The one definition of "an item's receipt" -- shared by `pr_received` and `sl_aging`.

Ruling P2-7: a receipt is a status transition *to* `received` --
`item_status_history.from_status_id IS NOT NULL` -- never the opening row a
brand-new item, a split child, or a seed gets, and never the live
database's one-time history reset (2026-09-25) to a single opening row per
item. Both reports read this one predicate through `received_transitions`,
and both read a transition's calendar day through `receipt_day`, so neither
can drift from what "received" means to the other.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import Select, select

from ..models import ItemStatus, ItemStatusHistory
from .base import local_date

__all__ = ["receipt_day", "received_transitions"]


def received_transitions() -> Select[Any]:
    """Every genuine arrival: `inventory_item_id`, `arrived_on`, `changed_at`.

    A base statement a caller may still `.join()`, `.add_columns()` and
    `.where()` -- `pr_received` adds the item, purchase order and vendor it
    also needs; `sl_aging` narrows it to its held items through a subquery.
    """
    return (
        select(
            ItemStatusHistory.inventory_item_id,
            ItemStatusHistory.arrived_on,
            ItemStatusHistory.changed_at,
        )
        .join(ItemStatus, ItemStatus.id == ItemStatusHistory.to_status_id)
        .where(
            ItemStatus.code == "received",
            ItemStatusHistory.from_status_id.is_not(None),
        )
    )


def receipt_day(arrived_on: date | None, changed_at: datetime) -> date:
    """A transition's own calendar day: `arrived_on` when recorded, else local.

    `changed_at`'s local calendar date (`local_date`) is a conversion SQL
    cannot do without knowing the application server's own time zone, so
    both callers resolve it here, in Python, rather than in the query.
    """
    return arrived_on or local_date(changed_at)
