"""Purchasing and receiving: what has been bought and has not yet arrived.

Four reports. `pr_outstanding`: one row per purchase order carrying at least
one live item still `ordered` or `missing` -- the same outstanding
definition Receiving's own order list (`GET /api/purchase-orders`) uses
(`app.live.OUTSTANDING_STATUSES`), so this report and Receiving can never
disagree about what "not yet arrived" means. `pr_spend`: period x vendor
spending, over purchases with a live item. `pr_sources`: every vendor with
such a purchase, and every seller one has named, with what was bought from
them.
`pr_received`: arrival day x vendor, from the acquisition-status history
itself.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import cast

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, RowMapping, and_, case, func, or_, select
from sqlalchemy.orm import Session

from ..live import OUTSTANDING_STATUSES, live_item
from ..models import (
    InventoryItem,
    ItemStatus,
    ItemStatusHistory,
    PurchaseOrder,
    Seller,
    Vendor,
)
from .base import (
    Column,
    DateRange,
    Period,
    Report,
    ReportResult,
)
from .live_purchases import (
    ALL_VENDORS,
    LIVE_ITEM_PURCHASE_NOTE,
    period_by_vendor,
)
from .receipts import receipt_day, received_transitions
from .registry import register

__all__ = [
    "PR_OUTSTANDING",
    "PR_RECEIVED",
    "PR_SOURCES",
    "PR_SPEND",
    "OutstandingParams",
    "ReceivedParams",
    "SourcesParams",
    "SpendParams",
]

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


# ---------------------------------------------------------------------------
# pr_spend
# ---------------------------------------------------------------------------


class SpendParams(DateRange):
    """Which purchases to total, and how to bucket them by time.

    A purchase is in range by its own `ordered_on`, inclusive of either
    bound; an absent bound is open on that side, as `DateRange` always
    means. `period` buckets the range into months (the default), quarters
    or years.
    """

    period: Period = Field(default="month", title="Period")


_SPEND_COLUMNS = [
    Column("period", "Period", "text"),
    Column("vendor", "Vendor", "text"),
    Column("purchases", "Purchases", "count"),
    Column("items", "Items", "count"),
    Column("item_cost", "Item cost", "money"),
    Column("shipping", "Shipping", "money"),
    Column("sales_tax", "Sales tax", "money"),
    Column("total_cost", "Total", "money"),
]

_SPEND_AGGREGATES = (
    func.count(func.distinct(PurchaseOrder.id)).label("purchases"),
    func.count(InventoryItem.id).label("items"),
    func.coalesce(func.sum(InventoryItem.item_cost), 0).label("item_cost"),
    func.coalesce(func.sum(InventoryItem.shipping_cost), 0).label("shipping"),
    func.coalesce(func.sum(InventoryItem.sales_tax), 0).label("sales_tax"),
    func.coalesce(func.sum(InventoryItem.total_cost), 0).label("total_cost"),
)


def _pr_spend(db: Session, params: SpendParams) -> ReportResult:
    """Period x vendor: purchases, items and cost, over purchases with a live item.

    The rows, each period's subtotal and the grand total are
    `.live_purchases.period_by_vendor`'s, which `mn_tax` builds its table
    from too.
    """
    return period_by_vendor(
        db, params, params.period, _SPEND_COLUMNS, _SPEND_AGGREGATES
    )


PR_SPEND = register(
    Report(
        id="pr_spend",
        group="Purchasing and receiving",
        title="Spending",
        purpose="Period x vendor: purchases, items, item cost, shipping, "
        "sales tax and total, over purchases with a live item.",
        params=SpendParams,
        run=_pr_spend,
    )
)


# ---------------------------------------------------------------------------
# pr_sources
# ---------------------------------------------------------------------------


class SourcesParams(BaseModel):
    """`pr_sources` takes no parameters: every vendor and seller bought from."""


_SOURCES_COLUMNS = [
    Column("vendor", "Vendor", "text"),
    Column("seller", "Seller", "text"),
    Column("purchases", "Purchases", "count"),
    Column("items", "Items", "count"),
    Column("total_spent", "Total spent", "money"),
    Column("first_order", "First order", "date"),
    Column("last_order", "Last order", "date"),
]

_SOURCES_AGGREGATES = (
    func.count(func.distinct(PurchaseOrder.id)).label("purchases"),
    func.count(InventoryItem.id).label("items"),
    func.coalesce(func.sum(InventoryItem.total_cost), 0).label("total_spent"),
    func.min(PurchaseOrder.ordered_on).label("first_order"),
    func.max(PurchaseOrder.ordered_on).label("last_order"),
)


def _sources_row(name: str, source: RowMapping) -> dict[str, object]:
    """One vendor's or seller's own figures, shaped for `_SOURCES_COLUMNS`."""
    return {
        "vendor": name,
        "purchases": source["purchases"],
        "items": source["items"],
        "total_spent": source["total_spent"],
        "first_order": source["first_order"],
        "last_order": source["last_order"],
    }


def _pr_sources(db: Session, params: SourcesParams) -> ReportResult:
    """One row per vendor with a counted purchase, then per seller one named.

    A purchase counts here under the same rule `pr_spend` uses -- an inner
    join to a live item, not `pr_outstanding`'s outer one -- so the two
    reports can never silently disagree about what "a purchase" means; see
    `LIVE_ITEM_PURCHASE_NOTE` (`.live_purchases`). A vendor with no counted
    purchase does not appear at all. `items` and `total_spent` are simply
    the sums of the live items that same join already selected. Vendor
    rows carry the vendor's full figures; a seller row beneath one is that
    seller's own subset, only for the sellers a counted purchase has
    actually named -- a purchase naming no seller contributes to the vendor
    row alone. Totals are over vendor rows only: a seller row is a further
    breakdown of purchases the vendor row already counts, not more
    purchases; its own first/last order is the overall earliest/latest
    across those same vendor rows.
    """
    vendor_rows = (
        db.execute(
            select(
                Vendor.id.label("vendor_id"),
                Vendor.name.label("vendor_name"),
                *_SOURCES_AGGREGATES,
            )
            .select_from(Vendor)
            .join(PurchaseOrder, PurchaseOrder.vendor_id == Vendor.id)
            .join(
                InventoryItem,
                and_(InventoryItem.purchase_order_id == PurchaseOrder.id, live_item()),
            )
            .group_by(Vendor.id, Vendor.name)
            .order_by(Vendor.name.asc())
        )
        .mappings()
        .all()
    )

    if not vendor_rows:
        return ReportResult(
            columns=_SOURCES_COLUMNS,
            rows=[],
            totals=None,
            drills=[],
            notes=["No purchases recorded."],
        )

    seller_rows = (
        db.execute(
            select(
                PurchaseOrder.vendor_id.label("vendor_id"),
                Seller.name.label("seller_name"),
                *_SOURCES_AGGREGATES,
            )
            .select_from(PurchaseOrder)
            .join(Seller, Seller.id == PurchaseOrder.seller_id)
            .join(
                InventoryItem,
                and_(InventoryItem.purchase_order_id == PurchaseOrder.id, live_item()),
            )
            .group_by(PurchaseOrder.vendor_id, Seller.id, Seller.name)
            .order_by(Seller.name.asc())
        )
        .mappings()
        .all()
    )
    sellers_by_vendor: dict[int, list[RowMapping]] = defaultdict(list)
    for row in seller_rows:
        sellers_by_vendor[row["vendor_id"]].append(row)

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    total_purchases = 0
    total_items = 0
    total_spent = Decimal("0")
    first_orders: list[date] = []
    last_orders: list[date] = []
    for vrow in vendor_rows:
        entry = _sources_row(vrow["vendor_name"], vrow)
        entry["seller"] = ""
        rows.append(entry)
        drills.append(None)
        total_purchases += vrow["purchases"]
        total_items += vrow["items"]
        total_spent += vrow["total_spent"]
        if vrow["first_order"] is not None:
            first_orders.append(cast("date", vrow["first_order"]))
        if vrow["last_order"] is not None:
            last_orders.append(cast("date", vrow["last_order"]))

        for srow in sellers_by_vendor.get(vrow["vendor_id"], []):
            seller_entry = _sources_row(vrow["vendor_name"], srow)
            seller_entry["seller"] = srow["seller_name"]
            rows.append(seller_entry)
            drills.append(None)

    totals: dict[str, object] = {
        "vendor": ALL_VENDORS,
        "seller": None,
        "purchases": total_purchases,
        "items": total_items,
        "total_spent": total_spent,
        "first_order": min(first_orders) if first_orders else None,
        "last_order": max(last_orders) if last_orders else None,
    }

    return ReportResult(
        columns=_SOURCES_COLUMNS,
        rows=rows,
        totals=totals,
        drills=drills,
        notes=[LIVE_ITEM_PURCHASE_NOTE],
    )


PR_SOURCES = register(
    Report(
        id="pr_sources",
        group="Purchasing and receiving",
        title="Vendors and sellers",
        purpose="One row per vendor, and per seller a purchase has named: "
        "purchases, live items, total spent, and first/last order date.",
        params=SourcesParams,
        run=_pr_sources,
    )
)


# ---------------------------------------------------------------------------
# pr_received
# ---------------------------------------------------------------------------


class ReceivedParams(DateRange):
    """`pr_received` takes no parameters beyond the date range."""


_RECEIVED_COLUMNS = [
    Column("day", "Day", "date"),
    Column("vendor", "Vendor", "text"),
    Column("items", "Items", "count"),
    Column("total_cost", "Total cost", "money"),
]

_RECEIVED_NOTE = (
    "An item received more than once (for example, returned and later "
    "received again) counts once for each receipt."
)

#: `lifecycle_writes.record_initial_status` writes this same `to_status`
#: for a brand-new item's *opening* row (`from_status_id IS NULL`) -- a
#: console entry, a split child, or a seed -- and an item whose history
#: starts at its current status has only such a row. Neither is an arrival,
#: so both are excluded by the same
#: `from_status_id IS NOT NULL` test, and this note says so in plain words.
_OPENING_ROW_NOTE = (
    "Items entered already received, and history recorded before status "
    "changes were tracked, are not counted as arrivals."
)


#: The vendor column's text for a received item recorded with no purchase.
_NO_PURCHASE = "No purchase"


def _received_prefilter(params: ReceivedParams) -> list[ColumnElement[bool]]:
    """A coarse, SQL-side narrowing of `pr_received`'s rows by date range.

    Never the final word -- `_pr_received` still runs the exact test
    (`local_date`, per row) in Python -- only a way to avoid fetching every
    `received` transition the collection has ever recorded when a narrow
    range is asked for. Widened a day past each bound on the `changed_at`
    side, since a receipt's local calendar day can fall a day either side
    of the UTC instant `changed_at` stores, depending on the server's own
    time zone; a row with `arrived_on` set is bounded by it exactly, since
    that column already reads as a calendar date with no zone to widen.
    """
    conditions: list[ColumnElement[bool]] = []
    if params.date_from is not None:
        widened = datetime.combine(
            params.date_from - timedelta(days=1), time.min, tzinfo=UTC
        )
        conditions.append(
            or_(
                ItemStatusHistory.arrived_on >= params.date_from,
                and_(
                    ItemStatusHistory.arrived_on.is_(None),
                    ItemStatusHistory.changed_at >= widened,
                ),
            )
        )
    if params.date_to is not None:
        widened = datetime.combine(
            params.date_to + timedelta(days=1), time.max, tzinfo=UTC
        )
        conditions.append(
            or_(
                ItemStatusHistory.arrived_on <= params.date_to,
                and_(
                    ItemStatusHistory.arrived_on.is_(None),
                    ItemStatusHistory.changed_at <= widened,
                ),
            )
        )
    return conditions


def _received_children_totals(
    db: Session, parent_ids: set[int]
) -> dict[int, tuple[int, Decimal]]:
    """Each split parent's own live children, as `(items, total_cost)`.

    One grouped query over every parent this report's own transitions named,
    not one query per parent: `InventoryItem.parent_item_id` is indexed, and
    the `IN` list is bounded by how many split parents were ever received,
    not by the collection's size.
    """
    if not parent_ids:
        return {}
    return {
        row["parent_id"]: (row["items"], row["total_cost"])
        for row in db.execute(
            select(
                InventoryItem.parent_item_id.label("parent_id"),
                func.count().label("items"),
                func.coalesce(func.sum(InventoryItem.total_cost), 0).label(
                    "total_cost"
                ),
            )
            .where(InventoryItem.parent_item_id.in_(parent_ids), live_item())
            .group_by(InventoryItem.parent_item_id)
        )
        .mappings()
        .all()
    }


def _pr_received(db: Session, params: ReceivedParams) -> ReportResult:
    """Arrival day x vendor, from `item_status_history` transitions to `received`.

    Only a transition counts -- `from_status_id IS NOT NULL` -- never the
    opening row every new item gets (`_OPENING_ROW_NOTE`): that row means
    "this item started out already received," not "it arrived."

    A split parent (`InventoryItem.split_at IS NOT NULL`) keeps its own
    transition even though it is no longer live itself, so its receipt is
    attributed to its own live children instead of dropped: `items` and
    `total_cost` come from `_received_children_totals`, on the parent's own
    receipt day and vendor, since the pieces arrived with the parent, not
    on some later day their own (opening-row-only) history would otherwise
    never surface at all. A deleted item is excluded outright: a deleted
    row should never have existed, so neither did its receipt.

    Fetched one transition at a time, not grouped in SQL: a row's day is
    `receipts.receipt_day` -- its own `arrived_on` when recorded, else
    `changed_at`'s local calendar date, a conversion SQL cannot do without
    knowing the application server's own time zone -- so both the
    bucketing and the exact date-range filter happen in Python, over the
    rows `_received_prefilter` has already narrowed. The transitions
    themselves come from `receipts.received_transitions`, the one query
    `sl_aging` also reads, extended here with the item, purchase order and
    vendor this report needs beyond it. The purchase and vendor are outer
    joins: an item recorded with no purchase still arrived, so its receipt
    is counted under `_NO_PURCHASE` rather than silently dropped.
    """
    stmt = (
        received_transitions()
        .add_columns(
            InventoryItem.split_at,
            InventoryItem.total_cost,
            func.coalesce(Vendor.name, _NO_PURCHASE).label("vendor_name"),
        )
        .join(InventoryItem, InventoryItem.id == ItemStatusHistory.inventory_item_id)
        .outerjoin(PurchaseOrder, PurchaseOrder.id == InventoryItem.purchase_order_id)
        .outerjoin(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(InventoryItem.deleted_at.is_(None), *_received_prefilter(params))
    )
    transitions = db.execute(stmt).mappings().all()

    children_totals = _received_children_totals(
        db,
        {
            row["inventory_item_id"]
            for row in transitions
            if row["split_at"] is not None
        },
    )

    bucket_items: dict[tuple[date, str], int] = defaultdict(int)
    bucket_cost: dict[tuple[date, str], Decimal] = defaultdict(lambda: Decimal("0"))
    for row in transitions:
        day = receipt_day(row["arrived_on"], row["changed_at"])
        if params.date_from is not None and day < params.date_from:
            continue
        if params.date_to is not None and day > params.date_to:
            continue
        key = (day, row["vendor_name"])
        if row["split_at"] is None:
            bucket_items[key] += 1
            bucket_cost[key] += row["total_cost"]
        else:
            items, cost = children_totals.get(
                row["inventory_item_id"], (0, Decimal("0"))
            )
            if items:
                bucket_items[key] += items
                bucket_cost[key] += cost

    if not bucket_items:
        return ReportResult(
            columns=_RECEIVED_COLUMNS,
            rows=[],
            totals=None,
            drills=[],
            notes=["Nothing was received in this range."],
        )

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    total_items = 0
    total_cost = Decimal("0")
    for key in sorted(bucket_items):
        day, vendor_name = key
        items = bucket_items[key]
        cost_sum = bucket_cost[key]
        rows.append(
            {"day": day, "vendor": vendor_name, "items": items, "total_cost": cost_sum}
        )
        drills.append(None)
        total_items += items
        total_cost += cost_sum

    # "All days" labels the text `vendor` column, not the date-kind `day`
    # one -- a date column holding the string "All days" would be a date
    # column that is not a date, on the wire and in the workbook alike.
    totals: dict[str, object] = {
        "day": None,
        "vendor": "All days",
        "items": total_items,
        "total_cost": total_cost,
    }

    return ReportResult(
        columns=_RECEIVED_COLUMNS,
        rows=rows,
        totals=totals,
        drills=drills,
        notes=[_RECEIVED_NOTE, _OPENING_ROW_NOTE],
    )


PR_RECEIVED = register(
    Report(
        id="pr_received",
        group="Purchasing and receiving",
        title="Received",
        purpose="Arrival day x vendor, from acquisition-status history: "
        "items and total cost.",
        params=ReceivedParams,
        run=_pr_received,
    )
)
