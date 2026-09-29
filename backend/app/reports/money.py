"""Money: what the collection cost, what tax was paid on it, what it is worth.

Three reports. `mn_basis`: every live item, by acquisition status x sales
disposition -- the same two vocabularies `cb_holdings` filters by, shown
here whole rather than narrowed to one value of each. `mn_tax`: period x
vendor, over purchases with a live item (Ruling P2-8, `.purchases`), sales
tax paid. `mn_value`: per item kind, live items, the ones carrying the
owner's own recorded numismatic value, their cost and that value, and the
difference between the two -- never a price-guide figure, only what the
owner has entered by hand.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal, cast
from urllib.parse import urlencode

from pydantic import BaseModel, Field
from sqlalchemy import RowMapping, func, select
from sqlalchemy.orm import Session

from ..inventory_search import view_path
from ..models import Disposition, InventoryItem, ItemStatus, PurchaseOrder, Vendor
from .base import Column, DateRange, Report, ReportResult, period_label, period_start
from .purchases import (
    LIVE_ITEM_PURCHASE_NOTE,
    dated_purchase_where,
    join_live_purchase_items,
    undated_purchase_note,
)
from .registry import register
from .tables import ITEM as _I
from .tables import KIND as _K
from .tables import LIVE as _LIVE

__all__ = [
    "MN_BASIS",
    "MN_TAX",
    "MN_VALUE",
    "BasisParams",
    "TaxParams",
    "TaxPeriod",
    "ValueParams",
]

# ---------------------------------------------------------------------------
# mn_basis
# ---------------------------------------------------------------------------

_ST = ItemStatus.__table__.alias("st")
_DISP = Disposition.__table__.alias("disp")

_BASIS_COLUMNS = [
    Column("status", "Status", "text"),
    Column("disposition", "Disposition", "text"),
    Column("items", "Items", "count"),
    Column("total_cost", "Total cost", "money"),
]

_NO_LIVE_ITEMS = "There are no live items."


class BasisParams(BaseModel):
    """`mn_basis` takes no parameters: every live item, status x disposition."""


def _mn_basis(db: Session, params: BasisParams) -> ReportResult:
    """Status x disposition of every live item: items and total cost.

    One grouping level, one query -- unlike `cb_holdings`, there is no
    subtotal here to add up, only the grand total. Never drilled: the coin
    and currency inventories are two separate searches, and a status x
    disposition row spans both kinds at once, so no single search page
    could ever reproduce one row's own count.
    """
    rows_data = (
        db.execute(
            select(
                _ST.c.label.label("status_label"),
                _ST.c.sort_order.label("status_sort"),
                _DISP.c.label.label("disposition_label"),
                _DISP.c.sort_order.label("disposition_sort"),
                func.count().label("items"),
                func.sum(_I.c.total_cost).label("total_cost"),
            )
            .select_from(
                _I.join(_ST, _ST.c.id == _I.c.status_id).join(
                    _DISP, _DISP.c.id == _I.c.disposition_id
                )
            )
            .where(_LIVE)
            .group_by(
                _ST.c.id,
                _ST.c.label,
                _ST.c.sort_order,
                _DISP.c.id,
                _DISP.c.label,
                _DISP.c.sort_order,
            )
            .order_by(_ST.c.sort_order, _DISP.c.sort_order)
        )
        .mappings()
        .all()
    )
    if not rows_data:
        return ReportResult(
            columns=_BASIS_COLUMNS,
            rows=[],
            totals=None,
            drills=[],
            notes=[_NO_LIVE_ITEMS],
        )

    rows: list[dict[str, object]] = [
        {
            "status": row["status_label"],
            "disposition": row["disposition_label"],
            "items": row["items"],
            "total_cost": row["total_cost"],
        }
        for row in rows_data
    ]

    totals: dict[str, object] = {
        "status": "All statuses",
        "disposition": None,
        "items": sum(cast("int", r["items"]) for r in rows),
        "total_cost": sum(
            (cast("Decimal", r["total_cost"]) for r in rows), Decimal("0")
        ),
    }

    return ReportResult(
        columns=_BASIS_COLUMNS,
        rows=rows,
        totals=totals,
        drills=[None] * len(rows),
    )


MN_BASIS = register(
    Report(
        id="mn_basis",
        group="Money",
        title="Cost basis",
        purpose="Status x disposition of every live item: items and total cost.",
        params=BasisParams,
        run=_mn_basis,
    )
)


# ---------------------------------------------------------------------------
# mn_tax
# ---------------------------------------------------------------------------

#: `mn_tax` buckets by month or year only -- a narrower choice than
#: purchasing's three-way `Period`, but a subtype of it (every `TaxPeriod`
#: value is also a `Period` value), so `period_start`/`period_label`
#: (Ruling P2-2) take it unchanged.
TaxPeriod = Literal["month", "year"]

_TAX_COLUMNS = [
    Column("period", "Period", "text"),
    Column("vendor", "Vendor", "text"),
    Column("purchases", "Purchases", "count"),
    Column("sales_tax", "Sales tax", "money"),
]

#: `InventoryItem.sales_tax`, not `_I.c.sales_tax` -- `join_live_purchase_items`
#: joins the plain `InventoryItem` table onto `PurchaseOrder`, never the `.tables`
#: alias `mn_basis`/`mn_value` use; mixing the two would leave `_I` unjoined
#: and PostgreSQL would silently cross it in as a cartesian product.
_TAX_AGGREGATES = (
    func.count(func.distinct(PurchaseOrder.id)).label("purchases"),
    func.coalesce(func.sum(InventoryItem.sales_tax), 0).label("sales_tax"),
)


class TaxParams(DateRange):
    """Which purchases to total, and how to bucket them by time.

    A purchase is in range by its own `ordered_on`, inclusive of either
    bound; an absent bound is open on that side, as `DateRange` always
    means. `period` buckets the range into months (the default) or years --
    `mn_tax` never buckets by quarter.
    """

    period: TaxPeriod = Field(default="month", title="Period")


def _mn_tax(db: Session, params: TaxParams) -> ReportResult:
    """Period x vendor: purchases and sales tax paid, over purchases with a live item.

    Reuses `pr_spend`'s own building blocks (`.purchases`) for what counts
    as "a purchase", how it is dated, and how an undated one is reported --
    rather than restating any of the three -- so the two reports can never
    drift apart on what a purchase is. Three queries at three grouping
    levels, exactly as `pr_spend` uses: the overall total (which also
    decides the empty case), each period's own subtotal, and the period x
    vendor rows themselves.
    """
    period_col = period_start(params.period, PurchaseOrder.ordered_on)
    where = dated_purchase_where(params)

    overall = (
        db.execute(join_live_purchase_items(select(*_TAX_AGGREGATES)).where(*where))
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
            columns=_TAX_COLUMNS, rows=[], totals=None, drills=[], notes=notes
        )

    period_subtotals = {
        row["period_start"]: row
        for row in db.execute(
            join_live_purchase_items(
                select(period_col.label("period_start"), *_TAX_AGGREGATES)
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
                    *_TAX_AGGREGATES,
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

    def _row(period_value: date, vendor: str, source: RowMapping) -> dict[str, object]:
        return {
            "period": period_label(params.period, period_value),
            "vendor": vendor,
            "purchases": source["purchases"],
            "sales_tax": source["sales_tax"],
        }

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    current_period: date | None = None

    def _append_subtotal(period_value: date) -> None:
        rows.append(_row(period_value, "All vendors", period_subtotals[period_value]))
        drills.append(None)

    for row in rows_data:
        period_value = row["period_start"]
        if current_period is not None and period_value != current_period:
            _append_subtotal(current_period)
        current_period = period_value

        rows.append(_row(period_value, row["vendor_name"], row))
        drills.append(None)

    if current_period is not None:
        _append_subtotal(current_period)

    totals: dict[str, object] = {
        "period": "All periods",
        "vendor": None,
        "purchases": overall["purchases"],
        "sales_tax": overall["sales_tax"],
    }

    notes = [LIVE_ITEM_PURCHASE_NOTE]
    if undated:
        notes.append(undated_purchase_note(undated))

    return ReportResult(
        columns=_TAX_COLUMNS, rows=rows, totals=totals, drills=drills, notes=notes
    )


MN_TAX = register(
    Report(
        id="mn_tax",
        group="Money",
        title="Sales tax paid",
        purpose="Period x vendor: purchases and sales tax paid, over "
        "purchases with a live item.",
        params=TaxParams,
        run=_mn_tax,
    )
)


# ---------------------------------------------------------------------------
# mn_value
# ---------------------------------------------------------------------------

_VALUE_COLUMNS = [
    Column("kind", "Kind", "text"),
    Column("items", "Items", "count"),
    Column("valued_items", "Items with a recorded value", "count"),
    Column("cost", "Cost", "money"),
    Column("value", "Recorded value", "money"),
    Column("difference", "Difference", "money"),
    Column("unvalued_items", "Items without a recorded value", "count"),
]

#: Never a price-guide or vendor figure -- only what the owner has typed
#: into `InventoryItem.numismatic_value` by hand.
_VALUE_NOTE = (
    "Recorded value is the owner's own entry (numismatic_value); no "
    "price-guide or vendor value is derived, seeded or shown."
)


class ValueParams(BaseModel):
    """`mn_value` takes no parameters: every live item, by kind."""


def _value_query_string(kind_code: str) -> str:
    """This kind's own live-item search: every status and disposition.

    `view_path(kind_code)` alone, with no `status=`/`disposition=`, already
    matches the search's own default reach for a live row (no soft-deleted
    row, no split parent) -- the same items `mn_value` counts as `items` --
    so `kind=` is the only filter this drill ever needs, and currency
    (which has no `kind=` filter at all) needs none.
    """
    path = view_path(kind_code)
    if kind_code == "currency":
        return path
    return f"{path}?{urlencode({'kind': kind_code})}"


def _mn_value(db: Session, params: ValueParams) -> ReportResult:
    """Per kind: live items, the ones with a recorded value, and their totals.

    `cost` and `value` sum only the items carrying the owner's own
    `numismatic_value` -- an item with none contributes to `items` and
    `unvalued_items` alone, never to the money columns, so a total of zero
    there never reads as "worth nothing" when it truly means "never
    entered". `difference` is `value - cost` over that same subset.
    """
    has_value = _I.c.numismatic_value.is_not(None)
    rows_data = (
        db.execute(
            select(
                _K.c.code.label("kind_code"),
                _K.c.label.label("kind_label"),
                _K.c.sort_order.label("kind_sort"),
                func.count().label("items"),
                func.count().filter(has_value).label("valued_items"),
                func.coalesce(func.sum(_I.c.total_cost).filter(has_value), 0).label(
                    "cost"
                ),
                func.coalesce(
                    func.sum(_I.c.numismatic_value).filter(has_value), 0
                ).label("value"),
            )
            .select_from(_I.join(_K, _K.c.id == _I.c.item_kind_id))
            .where(_LIVE)
            .group_by(_K.c.id, _K.c.code, _K.c.label, _K.c.sort_order)
            .order_by(_K.c.sort_order, _K.c.id)
        )
        .mappings()
        .all()
    )
    if not rows_data:
        return ReportResult(
            columns=_VALUE_COLUMNS,
            rows=[],
            totals=None,
            drills=[],
            notes=[_NO_LIVE_ITEMS],
        )

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for row in rows_data:
        items = cast("int", row["items"])
        valued_items = cast("int", row["valued_items"])
        cost = cast("Decimal", row["cost"])
        value = cast("Decimal", row["value"])
        rows.append(
            {
                "kind": row["kind_label"],
                "items": items,
                "valued_items": valued_items,
                "cost": cost,
                "value": value,
                "difference": value - cost,
                "unvalued_items": items - valued_items,
            }
        )
        drills.append(_value_query_string(row["kind_code"]))

    totals: dict[str, object] = {
        "kind": "All kinds",
        "items": sum(cast("int", r["items"]) for r in rows),
        "valued_items": sum(cast("int", r["valued_items"]) for r in rows),
        "cost": sum((cast("Decimal", r["cost"]) for r in rows), Decimal("0")),
        "value": sum((cast("Decimal", r["value"]) for r in rows), Decimal("0")),
        "difference": sum(
            (cast("Decimal", r["difference"]) for r in rows), Decimal("0")
        ),
        "unvalued_items": sum(cast("int", r["unvalued_items"]) for r in rows),
    }

    return ReportResult(
        columns=_VALUE_COLUMNS,
        rows=rows,
        totals=totals,
        drills=drills,
        notes=[_VALUE_NOTE],
    )


MN_VALUE = register(
    Report(
        id="mn_value",
        group="Money",
        title="Recorded value",
        purpose="Per item kind: live items, the ones with a recorded "
        "numismatic value, their cost and value, the difference, and "
        "items without one.",
        params=ValueParams,
        run=_mn_value,
    )
)
