"""What counts as "a purchase" -- shared by `pr_spend`, `pr_sources` and `mn_tax`.

A purchase counts only when it carries at least one live item
(docs/specs/reporting-design.md, Decisions) -- an inner join to
`InventoryItem` through `live_item()`, never `pr_outstanding`'s outer one --
so no two reports built on top of this can silently disagree about the same
word. `pr_spend` and `mn_tax` read this one join, the same date-range filter
on `ordered_on`, and the same note explaining an excluded, undated purchase,
rather than each restating its own; `pr_sources` shares the rule and its
note. `pr_spend` and `mn_tax` are also one table with different measures,
and `period_by_vendor` builds it for both.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from sqlalchemy import ColumnElement, RowMapping, Select, and_, func, select
from sqlalchemy.orm import Session

from ..live import live_item
from ..models import InventoryItem, PurchaseOrder, Vendor
from .base import (
    Column,
    DateRange,
    Period,
    ReportResult,
    period_label,
    period_start,
)

__all__ = [
    "ALL_PERIODS",
    "ALL_VENDORS",
    "LIVE_ITEM_PURCHASE_NOTE",
    "dated_purchase_where",
    "join_live_purchase_items",
    "period_by_vendor",
    "undated_purchase_note",
]

#: What a subtotal row says in place of a vendor, and the totals row in
#: place of a period.
ALL_VENDORS = "All vendors"
ALL_PERIODS = "All periods"

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


def period_by_vendor(
    db: Session,
    params: DateRange,
    period: Period,
    columns: list[Column],
    aggregates: Sequence[ColumnElement[Any]],
) -> ReportResult:
    """Period x vendor over purchases with a live item, one measure per aggregate.

    `columns` are the report's own: a `period` and a `vendor` column, then
    one per aggregate, each keyed by its aggregate's label. The first
    aggregate must be the count of purchases, labelled `purchases` -- it
    decides the empty case.

    Three queries at three grouping levels: the overall total (which also
    decides the empty case), each period's own subtotal, and the period x
    vendor rows themselves -- so a subtotal can never drift from the rows it
    summarizes, nor the grand total from the subtotals, by so much as a cent.
    A period's rows are followed by its subtotal, vendor `ALL_VENDORS`.
    """
    measures = [column.key for column in columns[2:]]
    period_col = period_start(period, PurchaseOrder.ordered_on)
    where = dated_purchase_where(params)

    overall = (
        db.execute(join_live_purchase_items(select(*aggregates)).where(*where))
        .mappings()
        .one()
    )
    undated = db.execute(
        join_live_purchase_items(
            select(func.count(func.distinct(PurchaseOrder.id)))
        ).where(PurchaseOrder.ordered_on.is_(None))
    ).scalar_one()

    if not overall["purchases"]:
        notes = [undated_purchase_note(undated)] if undated else []
        return ReportResult(
            columns=columns, rows=[], totals=None, drills=[], notes=notes
        )

    period_subtotals = {
        row["period_start"]: row
        for row in db.execute(
            join_live_purchase_items(
                select(period_col.label("period_start"), *aggregates)
            )
            .where(*where)
            .group_by(period_col)
        )
        .mappings()
        .all()
    }

    rows_data = (
        db.execute(
            join_live_purchase_items(
                select(
                    period_col.label("period_start"),
                    Vendor.name.label("vendor_name"),
                    *aggregates,
                )
            )
            .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
            .where(*where)
            .group_by(period_col, Vendor.id, Vendor.name)
            .order_by(period_col.asc(), Vendor.name.asc())
        )
        .mappings()
        .all()
    )

    def row_for(
        period_value: date, vendor: str, source: RowMapping
    ) -> dict[str, object]:
        """One table row: the period's label, the vendor, then each measure."""
        return {
            "period": period_label(period, period_value),
            "vendor": vendor,
            **{key: source[key] for key in measures},
        }

    rows: list[dict[str, object]] = []
    current_period: date | None = None
    for row in rows_data:
        period_value = row["period_start"]
        if current_period is not None and period_value != current_period:
            rows.append(
                row_for(current_period, ALL_VENDORS, period_subtotals[current_period])
            )
        current_period = period_value
        rows.append(row_for(period_value, row["vendor_name"], row))
    if current_period is not None:
        rows.append(
            row_for(current_period, ALL_VENDORS, period_subtotals[current_period])
        )

    totals: dict[str, object] = {
        "period": ALL_PERIODS,
        "vendor": None,
        **{key: overall[key] for key in measures},
    }

    notes = [LIVE_ITEM_PURCHASE_NOTE]
    if undated:
        notes.append(undated_purchase_note(undated))

    return ReportResult(
        columns=columns,
        rows=rows,
        totals=totals,
        drills=[None] * len(rows),
        notes=notes,
    )
