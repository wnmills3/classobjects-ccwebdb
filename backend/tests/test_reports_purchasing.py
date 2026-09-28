"""`pr_outstanding`: purchases with items still `ordered` or `missing`."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import cast

import pytest
from app.models import InventoryItem, ItemStatus, PurchaseOrder, Seller
from app.reports.purchasing import PR_OUTSTANDING, OutstandingParams
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
