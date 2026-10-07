"""`live_purchases.period_by_vendor`: the table `pr_spend` and `mn_tax` share."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app.models import InventoryItem, PurchaseOrder, Vendor
from app.reports.base import Column, DateRange
from app.reports.live_purchases import (
    ALL_PERIODS,
    ALL_VENDORS,
    LIVE_ITEM_PURCHASE_NOTE,
    period_by_vendor,
    undated_purchase_note,
)
from app.reports.money import MN_TAX, TaxParams
from app.reports.purchasing import PR_SPEND, SpendParams
from sqlalchemy import func
from sqlalchemy.orm import Session

from tests.builders import build_bare_item

#: A report of the caller's own: the two fixed columns and two measures.
COLUMNS = [
    Column("period", "Period", "text"),
    Column("vendor", "Vendor", "text"),
    Column("purchases", "Purchases", "count"),
    Column("item_cost", "Item cost", "money"),
]
AGGREGATES = (
    func.count(func.distinct(PurchaseOrder.id)).label("purchases"),
    func.coalesce(func.sum(InventoryItem.item_cost), 0).label("item_cost"),
)


def _purchase(
    db: Session, vendor: Vendor, number: str, ordered_on: date | None, *costs: str
) -> PurchaseOrder:
    """A purchase from `vendor` with one live item per cost given."""
    order = PurchaseOrder(
        vendor_id=vendor.id, order_number=number, ordered_on=ordered_on
    )
    db.add(order)
    db.flush()
    for cost in costs:
        item = build_bare_item(
            db,
            item_cost=Decimal(cost),
            tax_rate=Decimal("0.05"),
            shipping_cost=Decimal("0"),
        )
        item.purchase_order_id = order.id
    db.commit()
    return order


def _vendor(db: Session, name: str) -> Vendor:
    """A vendor of this name."""
    vendor = Vendor(name=name)
    db.add(vendor)
    db.commit()
    return vendor


def _two_months(db: Session) -> None:
    """Zeta before Apmex by insertion, March before February: nothing pre-sorted.

    February: Apmex 10 + 15 on one purchase. March: Apmex 40, Zeta 100 and
    Zeta 200 on two purchases. One purchase has no item and one no date.
    """
    zeta = _vendor(db, "Zeta Coins")
    apmex = _vendor(db, "Apmex")
    _purchase(db, zeta, "Z-2", date(2026, 3, 20), "200.00")
    _purchase(db, zeta, "Z-1", date(2026, 3, 5), "100.00")
    _purchase(db, apmex, "A-2", date(2026, 3, 9), "40.00")
    _purchase(db, apmex, "A-1", date(2026, 2, 14), "10.00", "15.00")
    _purchase(db, apmex, "A-empty", date(2026, 3, 1))
    _purchase(db, zeta, "Z-undated", None, "999.00")


def test_rows_run_by_period_then_vendor_each_period_closed_by_its_subtotal(
    db: Session,
) -> None:
    _two_months(db)
    result = period_by_vendor(db, DateRange(), "month", COLUMNS, AGGREGATES)

    assert [(r["period"], r["vendor"]) for r in result.rows] == [
        ("2026-02", "Apmex"),
        ("2026-02", ALL_VENDORS),
        ("2026-03", "Apmex"),
        ("2026-03", "Zeta Coins"),
        ("2026-03", ALL_VENDORS),
    ]
    assert [(r["purchases"], r["item_cost"]) for r in result.rows] == [
        (1, Decimal("25.00")),
        (1, Decimal("25.00")),
        (1, Decimal("40.00")),
        (2, Decimal("300.00")),
        (3, Decimal("340.00")),
    ]


def test_a_row_carries_the_caller_s_measures_and_no_others(db: Session) -> None:
    _two_months(db)
    result = period_by_vendor(db, DateRange(), "month", COLUMNS, AGGREGATES)

    assert result.columns == COLUMNS
    for row in [*result.rows, result.totals]:
        assert row is not None
        assert list(row) == ["period", "vendor", "purchases", "item_cost"]


def test_the_total_counts_dated_purchases_with_a_live_item_only(db: Session) -> None:
    _two_months(db)
    result = period_by_vendor(db, DateRange(), "month", COLUMNS, AGGREGATES)

    # Four purchases: not the one with no item, not the one with no date.
    assert result.totals == {
        "period": ALL_PERIODS,
        "vendor": None,
        "purchases": 4,
        "item_cost": Decimal("365.00"),
    }
    assert result.notes == [LIVE_ITEM_PURCHASE_NOTE, undated_purchase_note(1)]
    assert result.drills == [None] * len(result.rows)


def test_the_period_asked_for_is_the_bucket(db: Session) -> None:
    _two_months(db)
    result = period_by_vendor(db, DateRange(), "year", COLUMNS, AGGREGATES)

    assert [(r["period"], r["vendor"], r["purchases"]) for r in result.rows] == [
        ("2026", "Apmex", 2),
        ("2026", "Zeta Coins", 2),
        ("2026", ALL_VENDORS, 4),
    ]


def test_a_date_range_narrows_rows_subtotals_and_total_alike(db: Session) -> None:
    _two_months(db)
    march = DateRange(date_from=date(2026, 3, 6), date_to=date(2026, 3, 31))
    result = period_by_vendor(db, march, "month", COLUMNS, AGGREGATES)

    assert [(r["vendor"], r["item_cost"]) for r in result.rows] == [
        ("Apmex", Decimal("40.00")),
        ("Zeta Coins", Decimal("200.00")),
        (ALL_VENDORS, Decimal("240.00")),
    ]
    assert result.totals is not None
    assert result.totals["purchases"] == 2


def test_nothing_in_range_is_an_empty_table_that_still_names_the_undated(
    db: Session,
) -> None:
    _two_months(db)
    never = DateRange(date_from=date(2030, 1, 1))
    result = period_by_vendor(db, never, "month", COLUMNS, AGGREGATES)

    assert (result.rows, result.totals, result.drills) == ([], None, [])
    assert result.notes == [undated_purchase_note(1)]


def test_the_two_reports_built_on_it_are_one_table_with_different_measures(
    db: Session,
) -> None:
    _two_months(db)
    spend = PR_SPEND.run(db, SpendParams())
    tax = MN_TAX.run(db, TaxParams())

    shared = ("period", "vendor", "purchases", "sales_tax")
    assert [tuple(r[k] for k in shared) for r in spend.rows] == [
        tuple(r[k] for k in shared) for r in tax.rows
    ]
    assert len(tax.rows) == 5
    assert tax.totals is not None and spend.totals is not None
    assert tax.totals["sales_tax"] == spend.totals["sales_tax"] == Decimal("18.25")
    assert list(tax.rows[0]) == list(shared)
