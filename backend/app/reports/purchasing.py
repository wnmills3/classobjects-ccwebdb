"""Purchasing and receiving: what has been bought and has not yet arrived.

One report so far, `pr_outstanding`: one row per purchase order carrying at
least one live item still `ordered` or `missing` -- the same outstanding
definition Receiving's own order list (`GET /api/purchase-orders`) uses
(`app.live.OUTSTANDING_STATUSES`), so this report and Receiving can never
disagree about what "not yet arrived" means.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, select
from sqlalchemy.orm import Session

from ..live import OUTSTANDING_STATUSES, live_item
from ..models import InventoryItem, ItemStatus, PurchaseOrder, Seller, Vendor
from .base import Column, Report, ReportResult
from .registry import register

__all__ = ["PR_OUTSTANDING", "OutstandingParams"]

_NO_NUMBER = "(no number)"

_COLUMNS = [
    Column("order", "Order", "text"),
    Column("vendor", "Vendor", "text"),
    Column("seller", "Seller", "text"),
    Column("ordered", "Ordered", "date"),
    Column("days_waiting", "Days waiting", "count"),
    Column("outstanding", "Outstanding", "count"),
    Column("items", "Items", "count"),
    Column("outstanding_cost", "Outstanding cost", "money"),
    Column("overdue", "Overdue", "text"),
]


class OutstandingParams(BaseModel):
    """How many days a purchase may wait before it is marked overdue.

    A purchase's own `ordered_on` date is otherwise the only thing this
    report reads: there is no status or vendor filter, since a purchase with
    nothing outstanding never produces a row at all.
    """

    overdue_days: int = Field(default=21, ge=1, title="Overdue after (days)")


def _pr_outstanding(db: Session, params: OutstandingParams) -> ReportResult:
    """One row per purchase with a live item still `ordered` or `missing`.

    `outstanding`, `items` and `outstanding_cost` are grouped aggregates
    computed in SQL -- one query, one row per purchase -- rather than
    fetched per-item and summed in Python, the same shape `list_purchase_orders`
    (`app.routers.acquisitions`) already uses for `outstanding`/`total`.
    `live_item()` sits in the join's `ON` clause, so a purchase whose only
    lines are non-live is not silently dropped by the aggregation, and the
    `HAVING` below then removes it because it has nothing outstanding.

    "Today" is computed once here, in Python, rather than as SQL
    `CURRENT_DATE` -- the database server's clock and timezone are not
    necessarily the application's, and a test builds its dates relative to
    `date.today()` too.
    """
    is_outstanding = ItemStatus.code.in_(OUTSTANDING_STATUSES)
    outstanding = func.count(case((is_outstanding, InventoryItem.id)))
    outstanding_cost = func.coalesce(
        func.sum(case((is_outstanding, InventoryItem.total_cost))), 0
    )
    items = func.count(InventoryItem.id)

    stmt = (
        select(
            PurchaseOrder.id,
            PurchaseOrder.order_number,
            PurchaseOrder.ordered_on,
            Vendor.name.label("vendor_name"),
            Seller.name.label("seller_name"),
            outstanding.label("outstanding"),
            items.label("items"),
            outstanding_cost.label("outstanding_cost"),
        )
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .outerjoin(Seller, Seller.id == PurchaseOrder.seller_id)
        .outerjoin(
            InventoryItem,
            and_(InventoryItem.purchase_order_id == PurchaseOrder.id, live_item()),
        )
        .outerjoin(ItemStatus, ItemStatus.id == InventoryItem.status_id)
        .group_by(
            PurchaseOrder.id,
            PurchaseOrder.order_number,
            PurchaseOrder.ordered_on,
            Vendor.name,
            Seller.name,
        )
        .having(outstanding > 0)
        .order_by(PurchaseOrder.ordered_on.asc().nulls_last(), PurchaseOrder.id.asc())
    )

    today = date.today()
    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    total_outstanding = 0
    total_items = 0
    total_cost = Decimal("0")
    for row in db.execute(stmt).mappings().all():
        days_waiting = (today - row["ordered_on"]).days if row["ordered_on"] else None
        overdue = (
            "Overdue"
            if days_waiting is not None and days_waiting > params.overdue_days
            else ""
        )
        rows.append(
            {
                "order": row["order_number"] or _NO_NUMBER,
                "vendor": row["vendor_name"],
                "seller": row["seller_name"] or "",
                "ordered": row["ordered_on"],
                "days_waiting": days_waiting,
                "outstanding": row["outstanding"],
                "items": row["items"],
                "outstanding_cost": row["outstanding_cost"],
                "overdue": overdue,
            }
        )
        drills.append(f"/receiving?order={row['id']}")
        # A running Python total of each row's own SQL-computed sum -- exact,
        # since Decimal addition (unlike float) never drifts, and simpler
        # than a second aggregate query grouped over nothing.
        total_outstanding += row["outstanding"]
        total_items += row["items"]
        total_cost += row["outstanding_cost"]

    if not rows:
        return ReportResult(
            columns=_COLUMNS,
            rows=[],
            totals=None,
            drills=[],
            notes=["Nothing is outstanding."],
        )

    totals: dict[str, object] = {
        "order": "All purchases",
        "vendor": None,
        "seller": None,
        "ordered": None,
        "days_waiting": None,
        "outstanding": total_outstanding,
        "items": total_items,
        "outstanding_cost": total_cost,
        "overdue": None,
    }

    return ReportResult(
        columns=_COLUMNS,
        rows=rows,
        totals=totals,
        drills=drills,
        notes=[f"Overdue: waiting more than {params.overdue_days} days."],
    )


PR_OUTSTANDING = register(
    Report(
        id="pr_outstanding",
        group="Purchasing and receiving",
        title="Not yet arrived",
        purpose="Purchases with items still ordered or missing: vendor, "
        "seller, order date, days waiting, items outstanding and their "
        "cost; oldest first.",
        params=OutstandingParams,
        run=_pr_outstanding,
    )
)
