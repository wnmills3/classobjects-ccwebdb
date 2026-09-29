"""`pr_outstanding`, `pr_spend`, `pr_sources` and `pr_received`."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import cast

import pytest
from app.models import (
    InventoryItem,
    ItemStatus,
    ItemStatusHistory,
    PurchaseOrder,
    Seller,
    Vendor,
)
from app.reports.base import period_label
from app.reports.purchasing import (
    PR_OUTSTANDING,
    PR_RECEIVED,
    PR_SOURCES,
    PR_SPEND,
    OutstandingParams,
    ReceivedParams,
    SourcesParams,
    SpendParams,
)
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, build_purchase_order, code_id


def _item(
    db: Session, order: PurchaseOrder, status: str, cost: Decimal
) -> InventoryItem:
    """A live item on `order`, in `status`, with an exact cost basis."""
    item = build_bare_item(
        db,
        status_id=code_id(db, ItemStatus, status),
        item_cost=cost,
        tax_rate=Decimal("0"),
        shipping_cost=Decimal("0"),
    )
    item.purchase_order_id = order.id
    db.commit()
    return item


def _order(
    db: Session,
    vendor_name: str,
    *,
    order_number: str | None = "ORD-1",
    ordered_on: date | None = None,
    seller_name: str | None = None,
) -> PurchaseOrder:
    """A purchase order, optionally with a seller, for one vendor."""
    seller_id = None
    if seller_name is not None:
        seller = Seller(name=seller_name)
        db.add(seller)
        db.flush()
        seller_id = seller.id
    return build_purchase_order(
        db,
        vendor_name=vendor_name,
        order_number=order_number,
        ordered_on=ordered_on,
        seller_id=seller_id,
        commit=False,
    )


# ---------------------------------------------------------------------------
# Shared helpers for pr_spend / pr_sources / pr_received: several purchase
# orders under the *same* vendor (or seller), which `_order` above cannot
# build -- `build_purchase_order` always creates a fresh `Vendor`, and both
# `vendor.name` and `seller.name` are unique.
# ---------------------------------------------------------------------------


def _vendor(db: Session, name: str) -> Vendor:
    """A vendor by this name, committed so later orders can reference it."""
    vendor = Vendor(name=name)
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return vendor


def _order_for(
    db: Session,
    vendor: Vendor,
    *,
    order_number: str,
    ordered_on: date | None,
    seller_name: str | None = None,
) -> PurchaseOrder:
    """A purchase order for an existing `vendor`, optionally naming a seller."""
    seller_id = None
    if seller_name is not None:
        seller = Seller(name=seller_name)
        db.add(seller)
        db.flush()
        seller_id = seller.id
    order = PurchaseOrder(
        vendor_id=vendor.id,
        order_number=order_number,
        ordered_on=ordered_on,
        seller_id=seller_id,
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _spend_item(
    db: Session,
    order: PurchaseOrder,
    *,
    item_cost: Decimal,
    shipping_cost: Decimal = Decimal("0"),
    tax_rate: Decimal = Decimal("0"),
    status: str = "received",
) -> InventoryItem:
    """A live item on `order`, with its cost, shipping and tax independently set."""
    item = build_bare_item(
        db,
        status_id=code_id(db, ItemStatus, status),
        item_cost=item_cost,
        shipping_cost=shipping_cost,
        tax_rate=tax_rate,
    )
    item.purchase_order_id = order.id
    db.commit()
    return item


def _receive(
    db: Session,
    item: InventoryItem,
    *,
    arrived_on: date | None = None,
    changed_at: datetime | None = None,
) -> ItemStatusHistory:
    """A `received` transition on `item`, dated exactly as given.

    Built directly, not through `app.lifecycle_writes.set_status` (which
    always timestamps `changed_at` as "now") -- this report's own tests need
    to control both `arrived_on` and `changed_at` independently.
    """
    history = ItemStatusHistory(
        inventory_item_id=item.id,
        from_status_id=item.status_id,
        to_status_id=code_id(db, ItemStatus, "received"),
        changed_at=changed_at or datetime.now(UTC),
        arrived_on=arrived_on,
    )
    db.add(history)
    item.status_id = history.to_status_id
    db.commit()
    return history


def _local_noon(days_ago: int) -> datetime:
    """Local noon `days_ago` days before today -- never near a DST boundary.

    The same anchor `test_reports_selling.py`'s own `_local_noon` uses, for
    the same reason: a day-count fixture anchored to "now" can land inside
    the hour a DST transition adds or removes, and noon never does.
    """
    local = (datetime.now() - timedelta(days=days_ago)).date()
    return datetime.combine(local, time(12, 0)).astimezone()


# ---------------------------------------------------------------------------
# Parity with GET /api/purchase-orders
# ---------------------------------------------------------------------------


def test_outstanding_counts_match_the_purchase_orders_endpoint(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order_a = _order(db, "Vendor A", order_number="A-1")
    _item(db, order_a, "ordered", Decimal("10.00"))
    _item(db, order_a, "missing", Decimal("20.00"))
    _item(db, order_a, "received", Decimal("30.00"))

    order_b = _order(db, "Vendor B", order_number="B-1")
    _item(db, order_b, "received", Decimal("5.00"))  # fully received: no row

    db.commit()

    api_rows = {
        row["id"]: row
        for row in client.get("/api/purchase-orders", headers=admin_headers).json()
    }

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    report_orders = {row["order"] for row in result.rows}

    assert api_rows[order_a.id]["outstanding"] == 2
    assert api_rows[order_a.id]["total"] == 3
    assert api_rows[order_b.id]["outstanding"] == 0
    assert "A-1" in report_orders
    assert "B-1" not in report_orders  # outstanding == 0 at the endpoint too

    a_row = next(r for r in result.rows if r["order"] == "A-1")
    assert a_row["outstanding"] == api_rows[order_a.id]["outstanding"] == 2
    assert a_row["items"] == api_rows[order_a.id]["total"] == 3


def test_a_deleted_item_and_a_split_parent_are_not_counted(db: Session) -> None:
    order = _order(db, "Vendor D", order_number="D-1")
    _item(db, order, "ordered", Decimal("10.00"))
    deleted = _item(db, order, "ordered", Decimal("99.00"))
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    parent = _item(db, order, "ordered", Decimal("88.00"))
    parent.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    row = next(r for r in result.rows if r["order"] == "D-1")
    assert row["outstanding"] == 1
    assert row["items"] == 1
    assert row["outstanding_cost"] == Decimal("10.00")


def test_a_received_only_purchase_is_absent(db: Session) -> None:
    order = _order(db, "Vendor R", order_number="R-1")
    _item(db, order, "received", Decimal("10.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    assert "R-1" not in {row["order"] for row in result.rows}


# ---------------------------------------------------------------------------
# Days waiting and the overdue mark
# ---------------------------------------------------------------------------


def test_days_waiting_is_today_minus_ordered_on(db: Session) -> None:
    order = _order(
        db, "Vendor W", order_number="W-1", ordered_on=date.today() - timedelta(days=10)
    )
    _item(db, order, "ordered", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    row = next(r for r in result.rows if r["order"] == "W-1")
    assert row["days_waiting"] == 10


def test_overdue_mark_exactly_at_the_threshold_is_not_overdue(db: Session) -> None:
    order = _order(
        db, "Vendor T", order_number="T-1", ordered_on=date.today() - timedelta(days=21)
    )
    _item(db, order, "ordered", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams(overdue_days=21))
    row = next(r for r in result.rows if r["order"] == "T-1")
    assert row["days_waiting"] == 21
    assert row["overdue"] == ""


def test_overdue_mark_just_over_the_threshold_is_overdue(db: Session) -> None:
    order = _order(
        db, "Vendor T", order_number="T-2", ordered_on=date.today() - timedelta(days=22)
    )
    _item(db, order, "ordered", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams(overdue_days=21))
    row = next(r for r in result.rows if r["order"] == "T-2")
    assert row["days_waiting"] == 22
    assert row["overdue"] == "Overdue"


def test_undated_purchase_has_no_day_count_and_no_overdue_mark(db: Session) -> None:
    order = _order(db, "Vendor U", order_number="U-1", ordered_on=None)
    _item(db, order, "ordered", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    row = next(r for r in result.rows if r["order"] == "U-1")
    assert row["days_waiting"] is None
    assert row["overdue"] == ""
    assert row["ordered"] is None


def test_overdue_days_must_be_at_least_one() -> None:
    with pytest.raises(ValidationError):
        OutstandingParams(overdue_days=0)


# ---------------------------------------------------------------------------
# Columns: order text, vendor, seller, cost
# ---------------------------------------------------------------------------


def test_order_with_no_number_shows_a_placeholder(db: Session) -> None:
    order = _order(db, "Vendor N", order_number=None)
    _item(db, order, "ordered", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor N")
    assert row["order"] == "(no number)"


def test_seller_is_shown_when_present_and_empty_when_absent(db: Session) -> None:
    with_seller = _order(db, "Vendor S1", order_number="S-1", seller_name="Some Seller")
    _item(db, with_seller, "ordered", Decimal("1.00"))
    without_seller = _order(db, "Vendor S2", order_number="S-2")
    _item(db, without_seller, "ordered", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    assert (
        next(r for r in result.rows if r["order"] == "S-1")["seller"] == "Some Seller"
    )
    assert next(r for r in result.rows if r["order"] == "S-2")["seller"] == ""


def test_outstanding_cost_sums_only_the_outstanding_items(db: Session) -> None:
    order = _order(db, "Vendor C", order_number="C-1")
    _item(db, order, "ordered", Decimal("10.00"))
    _item(db, order, "missing", Decimal("20.00"))
    _item(db, order, "received", Decimal("999.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    row = next(r for r in result.rows if r["order"] == "C-1")
    assert row["outstanding"] == 2
    assert row["items"] == 3
    assert row["outstanding_cost"] == Decimal("30.00")


def test_drill_is_the_receiving_page_for_that_order(db: Session) -> None:
    order = _order(db, "Vendor V", order_number="V-1")
    _item(db, order, "ordered", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    idx = next(i for i, r in enumerate(result.rows) if r["order"] == "V-1")
    assert result.drills[idx] == f"/receiving?order={order.id}"


# ---------------------------------------------------------------------------
# Order: oldest first, undated last
# ---------------------------------------------------------------------------


def test_rows_are_ordered_oldest_first_with_undated_last(db: Session) -> None:
    older = _order(
        db,
        "Vendor O1",
        order_number="O-OLD",
        ordered_on=date.today() - timedelta(days=30),
    )
    _item(db, older, "ordered", Decimal("1.00"))
    newer = _order(
        db,
        "Vendor O2",
        order_number="O-NEW",
        ordered_on=date.today() - timedelta(days=5),
    )
    _item(db, newer, "ordered", Decimal("1.00"))
    undated = _order(db, "Vendor O3", order_number="O-UNDATED", ordered_on=None)
    _item(db, undated, "ordered", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    orders = [row["order"] for row in result.rows]
    assert orders == ["O-OLD", "O-NEW", "O-UNDATED"]


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


def test_totals_row_sums_outstanding_items_and_cost_leaves_others_empty(
    db: Session,
) -> None:
    order_a = _order(db, "Vendor TA", order_number="TA-1")
    _item(db, order_a, "ordered", Decimal("10.00"))
    _item(db, order_a, "missing", Decimal("5.00"))
    order_b = _order(db, "Vendor TB", order_number="TB-1")
    _item(db, order_b, "ordered", Decimal("7.50"))
    _item(db, order_b, "received", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    assert result.totals is not None
    assert result.totals["order"] == "All purchases"
    assert result.totals["outstanding"] == sum(
        cast("int", r["outstanding"]) for r in result.rows
    )
    assert result.totals["items"] == sum(cast("int", r["items"]) for r in result.rows)
    assert result.totals["outstanding_cost"] == sum(
        (cast("Decimal", r["outstanding_cost"]) for r in result.rows), Decimal("0")
    )
    for key in ("vendor", "seller", "ordered", "days_waiting", "overdue"):
        assert not result.totals.get(key)


def test_no_outstanding_purchases_returns_no_totals(db: Session) -> None:
    order = _order(db, "Vendor E", order_number="E-1")
    _item(db, order, "received", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams())
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []


# ---------------------------------------------------------------------------
# Notes
# ---------------------------------------------------------------------------


def test_notes_state_the_overdue_threshold(db: Session) -> None:
    order = _order(db, "Vendor N1", order_number="N-1")
    _item(db, order, "ordered", Decimal("1.00"))
    db.commit()

    result = PR_OUTSTANDING.run(db, OutstandingParams(overdue_days=30))
    assert any("30" in note for note in result.notes)


# ---------------------------------------------------------------------------
# period_label (app.reports.base), shared with mn_tax (Ruling P2-2)
# ---------------------------------------------------------------------------


def test_period_label_month_is_year_dash_month() -> None:
    assert period_label("month", date(2026, 9, 15)) == "2026-09"
    assert period_label("month", date(2026, 1, 1)) == "2026-01"


def test_period_label_quarter_names_the_quarter() -> None:
    assert period_label("quarter", date(2026, 1, 1)) == "2026 Q1"
    assert period_label("quarter", date(2026, 4, 1)) == "2026 Q2"
    assert period_label("quarter", date(2026, 7, 1)) == "2026 Q3"
    assert period_label("quarter", date(2026, 10, 31)) == "2026 Q4"


def test_period_label_year_is_just_the_year() -> None:
    assert period_label("year", date(2026, 6, 15)) == "2026"


# ---------------------------------------------------------------------------
# pr_spend
# ---------------------------------------------------------------------------


def test_spend_buckets_by_month_by_default(db: Session) -> None:
    order = _order(db, "Vendor SP1", order_number="SP-1", ordered_on=date(2026, 3, 10))
    _spend_item(db, order, item_cost=Decimal("50.00"), shipping_cost=Decimal("5.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor SP1")
    assert row["period"] == "2026-03"
    assert row["purchases"] == 1
    assert row["items"] == 1
    assert row["item_cost"] == Decimal("50.00")
    assert row["shipping"] == Decimal("5.00")
    assert row["total_cost"] == Decimal("55.00")


def test_spend_sums_item_cost_shipping_and_sales_tax_to_the_total(
    db: Session,
) -> None:
    order = _order(db, "Vendor SP2", order_number="SP-2", ordered_on=date(2026, 3, 10))
    _spend_item(
        db,
        order,
        item_cost=Decimal("100.00"),
        shipping_cost=Decimal("10.00"),
        tax_rate=Decimal("0.05"),
    )
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor SP2")
    assert row["item_cost"] == Decimal("100.00")
    assert row["shipping"] == Decimal("10.00")
    assert cast("Decimal", row["sales_tax"]) > Decimal("0")
    assert row["total_cost"] == (
        cast("Decimal", row["item_cost"])
        + cast("Decimal", row["shipping"])
        + cast("Decimal", row["sales_tax"])
    )


def test_spend_period_subtotal_sums_its_vendors(db: Session) -> None:
    order_a = _order(
        db, "Vendor SPA", order_number="SPA-1", ordered_on=date(2026, 4, 1)
    )
    _spend_item(db, order_a, item_cost=Decimal("30.00"))
    order_b = _order(
        db, "Vendor SPB", order_number="SPB-1", ordered_on=date(2026, 4, 15)
    )
    _spend_item(db, order_b, item_cost=Decimal("20.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    period_rows = [r for r in result.rows if r["period"] == "2026-04"]
    assert [r["vendor"] for r in period_rows] == [
        "Vendor SPA",
        "Vendor SPB",
        "All vendors",
    ]
    subtotal = period_rows[-1]
    assert subtotal["purchases"] == 2
    assert subtotal["items"] == 2
    assert subtotal["total_cost"] == Decimal("50.00")


def test_spend_rows_are_period_ascending_then_vendor_name(db: Session) -> None:
    order_feb = _order(
        db, "Vendor SPZ", order_number="SPZ-1", ordered_on=date(2026, 2, 5)
    )
    _spend_item(db, order_feb, item_cost=Decimal("10.00"))
    order_jan_b = _order(
        db, "Vendor SPB2", order_number="SPB2-1", ordered_on=date(2026, 1, 20)
    )
    _spend_item(db, order_jan_b, item_cost=Decimal("10.00"))
    order_jan_a = _order(
        db, "Vendor SPA2", order_number="SPA2-1", ordered_on=date(2026, 1, 5)
    )
    _spend_item(db, order_jan_a, item_cost=Decimal("10.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    labels = [(r["period"], r["vendor"]) for r in result.rows]
    assert labels == [
        ("2026-01", "Vendor SPA2"),
        ("2026-01", "Vendor SPB2"),
        ("2026-01", "All vendors"),
        ("2026-02", "Vendor SPZ"),
        ("2026-02", "All vendors"),
    ]


def test_spend_quarter_bucketing_across_a_year_boundary(db: Session) -> None:
    order_dec = _order(
        db, "Vendor SPQ1", order_number="SPQ-1", ordered_on=date(2025, 12, 20)
    )
    _spend_item(db, order_dec, item_cost=Decimal("10.00"))
    order_jan = _order(
        db, "Vendor SPQ2", order_number="SPQ-2", ordered_on=date(2026, 1, 5)
    )
    _spend_item(db, order_jan, item_cost=Decimal("10.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams(period="quarter"))
    periods = sorted(
        {cast("str", r["period"]) for r in result.rows if r["vendor"] != "All vendors"}
    )
    assert periods == ["2025 Q4", "2026 Q1"]


def test_spend_year_bucketing(db: Session) -> None:
    order = _order(db, "Vendor SPY", order_number="SPY-1", ordered_on=date(2026, 6, 1))
    _spend_item(db, order, item_cost=Decimal("10.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams(period="year"))
    row = next(r for r in result.rows if r["vendor"] == "Vendor SPY")
    assert row["period"] == "2026"


def test_spend_date_bounds_are_inclusive(db: Session) -> None:
    order_in = _order(
        db, "Vendor SPD1", order_number="SPD-1", ordered_on=date(2026, 5, 10)
    )
    _spend_item(db, order_in, item_cost=Decimal("10.00"))
    order_edge_from = _order(
        db, "Vendor SPD2", order_number="SPD-2", ordered_on=date(2026, 5, 1)
    )
    _spend_item(db, order_edge_from, item_cost=Decimal("10.00"))
    order_edge_to = _order(
        db, "Vendor SPD3", order_number="SPD-3", ordered_on=date(2026, 5, 31)
    )
    _spend_item(db, order_edge_to, item_cost=Decimal("10.00"))
    order_before = _order(
        db, "Vendor SPD4", order_number="SPD-4", ordered_on=date(2026, 4, 30)
    )
    _spend_item(db, order_before, item_cost=Decimal("10.00"))
    order_after = _order(
        db, "Vendor SPD5", order_number="SPD-5", ordered_on=date(2026, 6, 1)
    )
    _spend_item(db, order_after, item_cost=Decimal("10.00"))
    db.commit()

    result = PR_SPEND.run(
        db, SpendParams(date_from=date(2026, 5, 1), date_to=date(2026, 5, 31))
    )
    vendors = {r["vendor"] for r in result.rows}
    assert vendors == {"Vendor SPD1", "Vendor SPD2", "Vendor SPD3", "All vendors"}


def test_spend_absent_bound_is_open(db: Session) -> None:
    order = _order(db, "Vendor SPO1", order_number="SPO-1", ordered_on=date(2020, 1, 1))
    _spend_item(db, order, item_cost=Decimal("10.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams(date_to=date(2026, 1, 1)))
    assert "Vendor SPO1" in {r["vendor"] for r in result.rows}


def test_spend_undated_purchase_excluded_with_a_note(db: Session) -> None:
    dated = _order(db, "Vendor SPU1", order_number="SPU-1", ordered_on=date(2026, 5, 5))
    _spend_item(db, dated, item_cost=Decimal("10.00"))
    undated = _order(db, "Vendor SPU2", order_number="SPU-2", ordered_on=None)
    _spend_item(db, undated, item_cost=Decimal("10.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    assert "Vendor SPU2" not in {r["vendor"] for r in result.rows}
    assert any("1 purchase" in note and "excluded" in note for note in result.notes)


def test_spend_no_undated_purchase_has_no_undated_note(db: Session) -> None:
    order = _order(db, "Vendor SPN", order_number="SPN-1", ordered_on=date(2026, 5, 5))
    _spend_item(db, order, item_cost=Decimal("10.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    assert not any("excluded" in note for note in result.notes)


def test_spend_a_deleted_item_leaves_its_purchase_out(db: Session) -> None:
    order = _order(db, "Vendor SPX", order_number="SPX-1", ordered_on=date(2026, 5, 5))
    item = _spend_item(db, order, item_cost=Decimal("10.00"))
    item.deleted_at = datetime(2026, 5, 6, tzinfo=UTC)
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    assert "Vendor SPX" not in {r["vendor"] for r in result.rows}


def test_spend_a_split_parent_item_leaves_its_purchase_out(db: Session) -> None:
    order = _order(db, "Vendor SPS", order_number="SPS-1", ordered_on=date(2026, 5, 5))
    item = _spend_item(db, order, item_cost=Decimal("10.00"))
    item.split_at = datetime(2026, 5, 6, tzinfo=UTC)
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    assert "Vendor SPS" not in {r["vendor"] for r in result.rows}


def test_spend_totals_row_sums_the_data_rows(db: Session) -> None:
    order_a = _order(
        db, "Vendor SPT1", order_number="SPT-1", ordered_on=date(2026, 7, 1)
    )
    _spend_item(db, order_a, item_cost=Decimal("40.00"), shipping_cost=Decimal("4.00"))
    order_b = _order(
        db, "Vendor SPT2", order_number="SPT-2", ordered_on=date(2026, 8, 1)
    )
    _spend_item(db, order_b, item_cost=Decimal("60.00"), shipping_cost=Decimal("6.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    assert result.totals is not None
    data_rows = [r for r in result.rows if r["vendor"] != "All vendors"]
    assert result.totals["purchases"] == sum(
        cast("int", r["purchases"]) for r in data_rows
    )
    assert result.totals["items"] == sum(cast("int", r["items"]) for r in data_rows)
    assert result.totals["item_cost"] == sum(
        (cast("Decimal", r["item_cost"]) for r in data_rows), Decimal("0")
    )
    assert result.totals["total_cost"] == sum(
        (cast("Decimal", r["total_cost"]) for r in data_rows), Decimal("0")
    )


def test_spend_nothing_in_range_returns_no_rows(db: Session) -> None:
    order = _order(db, "Vendor SPE", order_number="SPE-1", ordered_on=date(2020, 1, 1))
    _spend_item(db, order, item_cost=Decimal("10.00"))
    db.commit()

    result = PR_SPEND.run(
        db, SpendParams(date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    )
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []


def test_spend_drills_are_all_none(db: Session) -> None:
    order = _order(
        db, "Vendor SPDR", order_number="SPDR-1", ordered_on=date(2026, 5, 5)
    )
    _spend_item(db, order, item_cost=Decimal("10.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    assert all(drill is None for drill in result.drills)


def test_spend_a_purchase_with_one_deleted_and_one_live_item_counts_the_live_one(
    db: Session,
) -> None:
    order = _order(
        db, "Vendor SPDL", order_number="SPDL-1", ordered_on=date(2026, 5, 5)
    )
    _spend_item(db, order, item_cost=Decimal("10.00"))
    deleted = _spend_item(db, order, item_cost=Decimal("999.00"))
    deleted.deleted_at = datetime(2026, 5, 6, tzinfo=UTC)
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor SPDL")
    assert row["purchases"] == 1
    assert row["items"] == 1
    assert row["item_cost"] == Decimal("10.00")


def test_spend_quarter_merges_two_months_into_one_bucket(db: Session) -> None:
    order_a = _order(
        db, "Vendor SPQM1", order_number="SPQM-1", ordered_on=date(2026, 1, 10)
    )
    _spend_item(db, order_a, item_cost=Decimal("10.00"))
    order_b = _order(
        db, "Vendor SPQM2", order_number="SPQM-2", ordered_on=date(2026, 2, 20)
    )
    _spend_item(db, order_b, item_cost=Decimal("20.00"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams(period="quarter"))
    period_rows = [r for r in result.rows if r["period"] == "2026 Q1"]
    assert {r["vendor"] for r in period_rows} == {
        "Vendor SPQM1",
        "Vendor SPQM2",
        "All vendors",
    }
    subtotal = next(r for r in period_rows if r["vendor"] == "All vendors")
    assert subtotal["purchases"] == 2
    assert subtotal["total_cost"] == Decimal("30.00")


def test_spend_money_sums_are_exact_with_odd_cents(db: Session) -> None:
    order_a = _order(
        db, "Vendor SPC1", order_number="SPC-1", ordered_on=date(2026, 5, 5)
    )
    _spend_item(db, order_a, item_cost=Decimal("10.33"), shipping_cost=Decimal("2.21"))
    order_b = _order(
        db, "Vendor SPC2", order_number="SPC-2", ordered_on=date(2026, 5, 6)
    )
    _spend_item(db, order_b, item_cost=Decimal("7.77"), shipping_cost=Decimal("1.19"))
    db.commit()

    result = PR_SPEND.run(db, SpendParams())
    data_rows = [r for r in result.rows if r["vendor"] != "All vendors"]
    assert sum(
        (cast("Decimal", r["total_cost"]) for r in data_rows), Decimal("0")
    ) == Decimal("21.50")
    assert result.totals is not None
    assert result.totals["total_cost"] == Decimal("21.50")


# ---------------------------------------------------------------------------
# pr_sources
# ---------------------------------------------------------------------------


def test_sources_vendor_row_aggregates_across_its_purchases(db: Session) -> None:
    vendor = _vendor(db, "Vendor SO1")
    order1 = _order_for(db, vendor, order_number="SO-1", ordered_on=date(2026, 1, 10))
    _item(db, order1, "received", Decimal("10.00"))
    order2 = _order_for(db, vendor, order_number="SO-2", ordered_on=date(2026, 3, 5))
    _item(db, order2, "received", Decimal("20.00"))
    db.commit()

    result = PR_SOURCES.run(db, SourcesParams())
    row = next(
        r for r in result.rows if r["vendor"] == "Vendor SO1" and r["seller"] == ""
    )
    assert row["purchases"] == 2
    assert row["items"] == 2
    assert row["total_spent"] == Decimal("30.00")
    assert row["first_order"] == date(2026, 1, 10)
    assert row["last_order"] == date(2026, 3, 5)


def test_sources_seller_rows_appear_only_for_purchases_naming_one(
    db: Session,
) -> None:
    vendor = _vendor(db, "Vendor SO2")
    with_seller_z = _order_for(
        db,
        vendor,
        order_number="SO2-1",
        ordered_on=date(2026, 2, 1),
        seller_name="zeta_seller",
    )
    _item(db, with_seller_z, "received", Decimal("15.00"))
    with_seller_a = _order_for(
        db,
        vendor,
        order_number="SO2-2",
        ordered_on=date(2026, 2, 5),
        seller_name="alpha_seller",
    )
    _item(db, with_seller_a, "received", Decimal("5.00"))
    no_seller = _order_for(
        db, vendor, order_number="SO2-3", ordered_on=date(2026, 2, 10)
    )
    _item(db, no_seller, "received", Decimal("1.00"))
    db.commit()

    result = PR_SOURCES.run(db, SourcesParams())
    vendor_idx = next(
        i
        for i, r in enumerate(result.rows)
        if r["vendor"] == "Vendor SO2" and r["seller"] == ""
    )
    sellers = [
        r["seller"]
        for r in result.rows[vendor_idx + 1 :]
        if r["vendor"] == "Vendor SO2" and r["seller"] != ""
    ]
    assert sellers == ["alpha_seller", "zeta_seller"]


def test_sources_seller_row_is_its_own_subset_not_the_vendors(db: Session) -> None:
    vendor = _vendor(db, "Vendor SO3")
    with_seller = _order_for(
        db,
        vendor,
        order_number="SO3-1",
        ordered_on=date(2026, 4, 1),
        seller_name="the_seller",
    )
    _item(db, with_seller, "received", Decimal("15.00"))
    no_seller = _order_for(
        db, vendor, order_number="SO3-2", ordered_on=date(2026, 4, 2)
    )
    _item(db, no_seller, "received", Decimal("85.00"))
    db.commit()

    result = PR_SOURCES.run(db, SourcesParams())
    vendor_row = next(
        r for r in result.rows if r["vendor"] == "Vendor SO3" and r["seller"] == ""
    )
    seller_row = next(r for r in result.rows if r["seller"] == "the_seller")
    assert vendor_row["purchases"] == 2
    assert vendor_row["total_spent"] == Decimal("100.00")
    assert seller_row["purchases"] == 1
    assert seller_row["total_spent"] == Decimal("15.00")


def test_sources_vendors_ordered_by_name(db: Session) -> None:
    vendor_z = _vendor(db, "Vendor SOZ")
    _item(
        db,
        _order_for(db, vendor_z, order_number="SOZ-1", ordered_on=date(2026, 1, 1)),
        "received",
        Decimal("1.00"),
    )
    vendor_a = _vendor(db, "Vendor SOA")
    _item(
        db,
        _order_for(db, vendor_a, order_number="SOA-1", ordered_on=date(2026, 1, 1)),
        "received",
        Decimal("1.00"),
    )
    db.commit()

    result = PR_SOURCES.run(db, SourcesParams())
    vendor_rows = [r["vendor"] for r in result.rows if r["seller"] == ""]
    assert vendor_rows.index("Vendor SOA") < vendor_rows.index("Vendor SOZ")


def test_sources_counts_live_items_only(db: Session) -> None:
    vendor = _vendor(db, "Vendor SOL")
    order = _order_for(db, vendor, order_number="SOL-1", ordered_on=date(2026, 1, 1))
    _item(db, order, "received", Decimal("10.00"))
    deleted = _item(db, order, "received", Decimal("99.00"))
    deleted.deleted_at = datetime(2026, 1, 2, tzinfo=UTC)
    parent = _item(db, order, "received", Decimal("88.00"))
    parent.split_at = datetime(2026, 1, 2, tzinfo=UTC)
    db.commit()

    result = PR_SOURCES.run(db, SourcesParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor SOL")
    assert row["items"] == 1
    assert row["total_spent"] == Decimal("10.00")
    assert row["purchases"] == 1


def test_sources_purchase_with_no_live_item_does_not_count(db: Session) -> None:
    """The same rule `pr_spend` uses: no live item, no counted purchase.

    This is the vendor's only purchase, so once it stops counting the
    vendor has nothing left to show and does not appear at all.
    """
    vendor = _vendor(db, "Vendor SON")
    order = _order_for(db, vendor, order_number="SON-1", ordered_on=date(2026, 1, 1))
    only_item = _item(db, order, "received", Decimal("50.00"))
    only_item.deleted_at = datetime(2026, 1, 2, tzinfo=UTC)
    db.commit()

    result = PR_SOURCES.run(db, SourcesParams())
    assert "Vendor SON" not in {r["vendor"] for r in result.rows}


def test_sources_notes_state_the_live_item_rule(db: Session) -> None:
    vendor = _vendor(db, "Vendor SOTE")
    order = _order_for(db, vendor, order_number="SOTE-1", ordered_on=date(2026, 1, 1))
    _item(db, order, "received", Decimal("1.00"))
    db.commit()

    result = PR_SOURCES.run(db, SourcesParams())
    assert any("live item" in note for note in result.notes)


def test_sources_totals_fill_first_and_last_order_with_the_overall_span(
    db: Session,
) -> None:
    vendor_a = _vendor(db, "Vendor SOF1")
    _item(
        db,
        _order_for(db, vendor_a, order_number="SOF1-1", ordered_on=date(2026, 1, 5)),
        "received",
        Decimal("10.00"),
    )
    vendor_b = _vendor(db, "Vendor SOF2")
    _item(
        db,
        _order_for(db, vendor_b, order_number="SOF2-1", ordered_on=date(2026, 6, 20)),
        "received",
        Decimal("10.00"),
    )
    db.commit()

    result = PR_SOURCES.run(db, SourcesParams())
    assert result.totals is not None
    assert result.totals["first_order"] == date(2026, 1, 5)
    assert result.totals["last_order"] == date(2026, 6, 20)


def test_sources_totals_are_over_vendor_rows_only(db: Session) -> None:
    vendor = _vendor(db, "Vendor SOT")
    order = _order_for(
        db,
        vendor,
        order_number="SOT-1",
        ordered_on=date(2026, 5, 1),
        seller_name="sot_seller",
    )
    _item(db, order, "received", Decimal("40.00"))
    db.commit()

    result = PR_SOURCES.run(db, SourcesParams())
    assert result.totals is not None
    assert result.totals["purchases"] == 1
    assert result.totals["items"] == 1
    assert result.totals["total_spent"] == Decimal("40.00")


def test_sources_with_no_purchases_returns_no_rows(db: Session) -> None:
    result = PR_SOURCES.run(db, SourcesParams())
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []


def test_sources_drills_are_all_none(db: Session) -> None:
    vendor = _vendor(db, "Vendor SOD")
    order = _order_for(
        db,
        vendor,
        order_number="SOD-1",
        ordered_on=date(2026, 1, 1),
        seller_name="sod_seller",
    )
    _item(db, order, "received", Decimal("1.00"))
    db.commit()

    result = PR_SOURCES.run(db, SourcesParams())
    assert all(d is None for d in result.drills)


# ---------------------------------------------------------------------------
# pr_received
# ---------------------------------------------------------------------------


def test_received_uses_arrived_on_when_recorded(db: Session) -> None:
    vendor = _vendor(db, "Vendor RE1")
    order = _order_for(db, vendor, order_number="RE-1", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("25.00"))
    _receive(db, item, arrived_on=date(2026, 3, 5))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor RE1")
    assert row["day"] == date(2026, 3, 5)
    assert row["items"] == 1
    assert row["total_cost"] == Decimal("25.00")


def test_received_falls_back_to_changed_at_local_date(db: Session) -> None:
    vendor = _vendor(db, "Vendor RE2")
    order = _order_for(db, vendor, order_number="RE-2", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("10.00"))
    _receive(db, item, arrived_on=None, changed_at=_local_noon(3))
    db.commit()

    expected_day = (datetime.now() - timedelta(days=3)).date()
    result = PR_RECEIVED.run(db, ReceivedParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor RE2")
    assert row["day"] == expected_day


def test_received_more_than_once_counts_once_per_receipt(db: Session) -> None:
    vendor = _vendor(db, "Vendor RE3")
    order = _order_for(db, vendor, order_number="RE-3", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("12.00"))
    _receive(db, item, arrived_on=date(2026, 3, 5))
    item.status_id = code_id(db, ItemStatus, "returned")
    db.commit()
    _receive(db, item, arrived_on=date(2026, 3, 20))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    rows = [r for r in result.rows if r["vendor"] == "Vendor RE3"]
    assert len(rows) == 2
    assert sum(cast("int", r["items"]) for r in rows) == 2


def test_received_counts_an_item_with_no_purchase_under_no_purchase(
    db: Session,
) -> None:
    """A received item recorded without a purchase is counted, not dropped."""
    item = build_bare_item(
        db,
        status_id=code_id(db, ItemStatus, "ordered"),
        item_cost=Decimal("7.25"),
        tax_rate=Decimal("0"),
        shipping_cost=Decimal("0"),
    )
    assert item.purchase_order_id is None
    db.commit()
    _receive(db, item, arrived_on=date(2026, 3, 7))

    result = PR_RECEIVED.run(db, ReceivedParams())
    row = next(r for r in result.rows if r["day"] == date(2026, 3, 7))
    assert row["vendor"] == "No purchase"
    assert row["items"] == 1
    assert row["total_cost"] == Decimal("7.25")


def test_received_excludes_a_deleted_item(db: Session) -> None:
    vendor = _vendor(db, "Vendor RE4")
    order = _order_for(db, vendor, order_number="RE-4", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("12.00"))
    _receive(db, item, arrived_on=date(2026, 3, 5))
    item.deleted_at = datetime(2026, 3, 6, tzinfo=UTC)
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    assert "Vendor RE4" not in {r["vendor"] for r in result.rows}


def test_received_excludes_a_split_parent_item(db: Session) -> None:
    vendor = _vendor(db, "Vendor RE5")
    order = _order_for(db, vendor, order_number="RE-5", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("12.00"))
    _receive(db, item, arrived_on=date(2026, 3, 5))
    item.split_at = datetime(2026, 3, 6, tzinfo=UTC)
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    assert "Vendor RE5" not in {r["vendor"] for r in result.rows}


def test_received_date_bounds_are_inclusive(db: Session) -> None:
    vendor = _vendor(db, "Vendor RE6")
    order = _order_for(db, vendor, order_number="RE-6", ordered_on=date(2026, 3, 1))
    in_range = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, in_range, arrived_on=date(2026, 3, 10))
    edge_from = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, edge_from, arrived_on=date(2026, 3, 1))
    edge_to = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, edge_to, arrived_on=date(2026, 3, 31))
    before = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, before, arrived_on=date(2026, 2, 28))
    after = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, after, arrived_on=date(2026, 4, 1))
    db.commit()

    result = PR_RECEIVED.run(
        db, ReceivedParams(date_from=date(2026, 3, 1), date_to=date(2026, 3, 31))
    )
    days = {r["day"] for r in result.rows if r["vendor"] == "Vendor RE6"}
    assert days == {date(2026, 3, 10), date(2026, 3, 1), date(2026, 3, 31)}


def test_received_absent_bound_is_open(db: Session) -> None:
    vendor = _vendor(db, "Vendor RE7")
    order = _order_for(db, vendor, order_number="RE-7", ordered_on=date(2020, 1, 1))
    item = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, item, arrived_on=date(2020, 1, 5))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams(date_to=date(2026, 1, 1)))
    assert "Vendor RE7" in {r["vendor"] for r in result.rows}


def test_received_totals_row_sums_the_rows(db: Session) -> None:
    vendor_a = _vendor(db, "Vendor RE8")
    order_a = _order_for(
        db, vendor_a, order_number="RE8-1", ordered_on=date(2026, 3, 1)
    )
    item_a = _item(db, order_a, "ordered", Decimal("10.00"))
    _receive(db, item_a, arrived_on=date(2026, 3, 1))
    vendor_b = _vendor(db, "Vendor RE9")
    order_b = _order_for(
        db, vendor_b, order_number="RE9-1", ordered_on=date(2026, 3, 2)
    )
    item_b = _item(db, order_b, "ordered", Decimal("20.00"))
    _receive(db, item_b, arrived_on=date(2026, 3, 2))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    assert result.totals is not None
    assert result.totals["items"] == sum(cast("int", r["items"]) for r in result.rows)
    assert result.totals["total_cost"] == sum(
        (cast("Decimal", r["total_cost"]) for r in result.rows), Decimal("0")
    )


def test_received_rows_are_day_ascending_then_vendor(db: Session) -> None:
    vendor_z = _vendor(db, "Vendor REZ")
    order_z = _order_for(
        db, vendor_z, order_number="REZ-1", ordered_on=date(2026, 3, 1)
    )
    item_z = _item(db, order_z, "ordered", Decimal("1.00"))
    _receive(db, item_z, arrived_on=date(2026, 3, 1))
    vendor_a = _vendor(db, "Vendor REA")
    order_a = _order_for(
        db, vendor_a, order_number="REA-1", ordered_on=date(2026, 3, 1)
    )
    item_a = _item(db, order_a, "ordered", Decimal("1.00"))
    _receive(db, item_a, arrived_on=date(2026, 3, 1))
    earlier_vendor = _vendor(db, "Vendor REE")
    earlier_order = _order_for(
        db, earlier_vendor, order_number="REE-1", ordered_on=date(2026, 2, 1)
    )
    earlier_item = _item(db, earlier_order, "ordered", Decimal("1.00"))
    _receive(db, earlier_item, arrived_on=date(2026, 2, 15))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    keys = [(r["day"], r["vendor"]) for r in result.rows]
    assert keys == [
        (date(2026, 2, 15), "Vendor REE"),
        (date(2026, 3, 1), "Vendor REA"),
        (date(2026, 3, 1), "Vendor REZ"),
    ]


def test_received_with_nothing_received_returns_no_rows(db: Session) -> None:
    result = PR_RECEIVED.run(db, ReceivedParams())
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []


def test_received_note_explains_repeated_receipts(db: Session) -> None:
    vendor = _vendor(db, "Vendor REN")
    order = _order_for(db, vendor, order_number="REN-1", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, item, arrived_on=date(2026, 3, 1))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    assert any("counts once" in note for note in result.notes)


def test_received_drills_are_all_none(db: Session) -> None:
    vendor = _vendor(db, "Vendor RED")
    order = _order_for(db, vendor, order_number="RED-1", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, item, arrived_on=date(2026, 3, 1))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    assert all(d is None for d in result.drills)


def test_received_totals_row_labels_all_days_on_the_vendor_column(
    db: Session,
) -> None:
    """`day` (a date-kind column) stays empty; the label is text, in `vendor`."""
    vendor = _vendor(db, "Vendor RET")
    order = _order_for(db, vendor, order_number="RET-1", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, item, arrived_on=date(2026, 3, 1))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    assert result.totals is not None
    assert result.totals["day"] is None
    assert result.totals["vendor"] == "All days"


# ---------------------------------------------------------------------------
# pr_received: only a transition counts, never an opening row
# ---------------------------------------------------------------------------


def test_received_excludes_an_opening_row(db: Session) -> None:
    """An item entered already `received` gets only an opening row: no receipt."""
    vendor = _vendor(db, "Vendor REO1")
    order = _order_for(db, vendor, order_number="REO-1", ordered_on=date(2026, 3, 1))
    item = build_bare_item(
        db,
        status_id=code_id(db, ItemStatus, "received"),
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
        shipping_cost=Decimal("0"),
    )
    item.purchase_order_id = order.id
    db.flush()
    db.add(
        ItemStatusHistory(
            inventory_item_id=item.id,
            from_status_id=None,
            to_status_id=code_id(db, ItemStatus, "received"),
            changed_at=datetime.now(UTC),
            arrived_on=date(2026, 3, 1),
        )
    )
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    assert "Vendor REO1" not in {r["vendor"] for r in result.rows}


def test_received_note_explains_opening_rows_are_excluded(db: Session) -> None:
    vendor = _vendor(db, "Vendor REON")
    order = _order_for(db, vendor, order_number="REON-1", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, item, arrived_on=date(2026, 3, 1))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    assert any("already received" in note for note in result.notes)


def test_received_a_genuine_transition_still_counts(db: Session) -> None:
    """The case the opening-row rule must not also throw out."""
    vendor = _vendor(db, "Vendor RET2")
    order = _order_for(db, vendor, order_number="RET2-1", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("15.00"))
    _receive(db, item, arrived_on=date(2026, 3, 8))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor RET2")
    assert row["day"] == date(2026, 3, 8)
    assert row["items"] == 1
    assert row["total_cost"] == Decimal("15.00")


def test_received_split_parent_counted_through_its_live_children(db: Session) -> None:
    """The pieces arrived with the parent -- attributed to them, on its day.

    Also exercises exact money with odd cents through the children-total
    aggregate (`12.34 + 56.78 = 69.12`), not just the direct-item path.
    """
    vendor = _vendor(db, "Vendor RESP")
    order = _order_for(db, vendor, order_number="RESP-1", ordered_on=date(2026, 3, 1))
    parent = _item(db, order, "ordered", Decimal("100.00"))
    _receive(db, parent, arrived_on=date(2026, 3, 5))

    child_a = build_bare_item(
        db,
        parent_item_id=parent.id,
        status_id=parent.status_id,
        item_cost=Decimal("12.34"),
        tax_rate=Decimal("0"),
        shipping_cost=Decimal("0"),
    )
    child_b = build_bare_item(
        db,
        parent_item_id=parent.id,
        status_id=parent.status_id,
        item_cost=Decimal("56.78"),
        tax_rate=Decimal("0"),
        shipping_cost=Decimal("0"),
    )
    for child in (child_a, child_b):
        db.add(
            ItemStatusHistory(
                inventory_item_id=child.id,
                from_status_id=None,
                to_status_id=parent.status_id,
                changed_at=datetime.now(UTC),
            )
        )
    parent.split_at = datetime(2026, 3, 6, tzinfo=UTC)
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    matching = [r for r in result.rows if r["vendor"] == "Vendor RESP"]
    assert len(matching) == 1
    row = matching[0]
    assert row["day"] == date(2026, 3, 5)
    assert row["items"] == 2
    assert row["total_cost"] == Decimal("69.12")


# ---------------------------------------------------------------------------
# pr_received: date range vs. arrived_on / changed_at
# ---------------------------------------------------------------------------


def test_received_date_range_includes_a_fallback_dated_row_in_range(
    db: Session,
) -> None:
    vendor = _vendor(db, "Vendor REF1")
    order = _order_for(db, vendor, order_number="REF-1", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, item, arrived_on=None, changed_at=_local_noon(3))
    db.commit()

    expected_day = (datetime.now() - timedelta(days=3)).date()
    result = PR_RECEIVED.run(
        db, ReceivedParams(date_from=expected_day, date_to=expected_day)
    )
    assert "Vendor REF1" in {r["vendor"] for r in result.rows}


def test_received_date_range_excludes_a_fallback_dated_row_out_of_range(
    db: Session,
) -> None:
    vendor = _vendor(db, "Vendor REF2")
    order = _order_for(db, vendor, order_number="REF-2", ordered_on=date(2020, 1, 1))
    item = _item(db, order, "ordered", Decimal("1.00"))
    _receive(db, item, arrived_on=None, changed_at=_local_noon(30))
    db.commit()

    bound_day = (datetime.now() - timedelta(days=3)).date()
    result = PR_RECEIVED.run(db, ReceivedParams(date_from=bound_day, date_to=bound_day))
    assert "Vendor REF2" not in {r["vendor"] for r in result.rows}


def test_received_uses_arrived_on_not_changed_at_when_both_present_in_range(
    db: Session,
) -> None:
    """`arrived_on` is inside the range; a much later `changed_at` must not matter."""
    vendor = _vendor(db, "Vendor RES1")
    order = _order_for(db, vendor, order_number="RES-1", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("1.00"))
    _receive(
        db,
        item,
        arrived_on=date(2026, 3, 10),
        changed_at=datetime(2026, 4, 15, 12, 0, tzinfo=UTC),
    )
    db.commit()

    result = PR_RECEIVED.run(
        db, ReceivedParams(date_from=date(2026, 3, 1), date_to=date(2026, 3, 31))
    )
    row = next(r for r in result.rows if r["vendor"] == "Vendor RES1")
    assert row["day"] == date(2026, 3, 10)


def test_received_arrived_on_outside_range_excludes_even_if_changed_at_inside(
    db: Session,
) -> None:
    """`arrived_on` outside the range; `changed_at` alone must not pull it in."""
    vendor = _vendor(db, "Vendor RES2")
    order = _order_for(db, vendor, order_number="RES-2", ordered_on=date(2026, 3, 1))
    item = _item(db, order, "ordered", Decimal("1.00"))
    _receive(
        db,
        item,
        arrived_on=date(2026, 2, 1),
        changed_at=datetime(2026, 3, 15, 12, 0, tzinfo=UTC),
    )
    db.commit()

    result = PR_RECEIVED.run(
        db, ReceivedParams(date_from=date(2026, 3, 1), date_to=date(2026, 3, 31))
    )
    assert "Vendor RES2" not in {r["vendor"] for r in result.rows}


def test_received_money_sums_are_exact_with_odd_cents(db: Session) -> None:
    vendor = _vendor(db, "Vendor REC1")
    order = _order_for(db, vendor, order_number="REC-1", ordered_on=date(2026, 3, 1))
    item_a = _item(db, order, "ordered", Decimal("10.33"))
    _receive(db, item_a, arrived_on=date(2026, 3, 5))
    item_b = _item(db, order, "ordered", Decimal("7.77"))
    _receive(db, item_b, arrived_on=date(2026, 3, 5))
    db.commit()

    result = PR_RECEIVED.run(db, ReceivedParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor REC1")
    assert row["total_cost"] == Decimal("18.10")
