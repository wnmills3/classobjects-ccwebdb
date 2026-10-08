"""`mn_basis`, `mn_tax` and `mn_value`."""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from typing import cast
from urllib.parse import parse_qs, urlsplit

from app.inventory_search import COIN_VIEW, CURRENCY_VIEW
from app.inventory_search import search as inventory_search
from app.models import (
    Disposition,
    InventoryItem,
    ItemKind,
    ItemStatus,
    PurchaseOrder,
    Vendor,
)
from app.reports.base import ReportResult
from app.reports.money import (
    MN_BASIS,
    MN_TAX,
    MN_VALUE,
    BasisParams,
    TaxParams,
    ValueParams,
)
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, build_purchase_order, code_id

# ---------------------------------------------------------------------------
# mn_basis
# ---------------------------------------------------------------------------


def _basis_item(
    db: Session, status: str, disposition: str, cost: Decimal
) -> InventoryItem:
    """A live item in this status and disposition, with an exact cost basis."""
    return build_bare_item(
        db,
        status_id=code_id(db, ItemStatus, status),
        disposition_id=code_id(db, Disposition, disposition),
        item_cost=cost,
        tax_rate=Decimal("0"),
        shipping_cost=Decimal("0"),
    )


def _row(result: ReportResult, status: str, disposition: str) -> dict[str, object]:
    """The one row matching `status` and `disposition`."""
    return next(
        r
        for r in result.rows
        if r["status"] == status and r["disposition"] == disposition
    )


def test_basis_groups_by_status_and_disposition(db: Session) -> None:
    _basis_item(db, "received", "held", Decimal("10.00"))
    _basis_item(db, "received", "held", Decimal("20.00"))
    _basis_item(db, "received", "listed", Decimal("5.00"))
    _basis_item(db, "ordered", "held", Decimal("1.00"))
    db.commit()

    result = MN_BASIS.run(db, BasisParams())
    received_held = _row(result, "Received", "Held")
    assert received_held["items"] == 2
    assert received_held["total_cost"] == Decimal("30.00")
    received_listed = _row(result, "Received", "Listed")
    assert received_listed["items"] == 1
    assert received_listed["total_cost"] == Decimal("5.00")
    ordered_held = _row(result, "Ordered", "Held")
    assert ordered_held["items"] == 1


def test_basis_excludes_a_deleted_item(db: Session) -> None:
    _basis_item(db, "received", "held", Decimal("10.00"))
    deleted = _basis_item(db, "received", "held", Decimal("99.00"))
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = MN_BASIS.run(db, BasisParams())
    row = _row(result, "Received", "Held")
    assert row["items"] == 1
    assert row["total_cost"] == Decimal("10.00")


def test_basis_excludes_a_split_parent_counts_its_live_children(db: Session) -> None:
    parent = _basis_item(db, "received", "held", Decimal("10.00"))
    parent.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()
    _basis_item(db, "received", "held", Decimal("5.00"))
    _basis_item(db, "received", "held", Decimal("5.00"))
    db.commit()

    result = MN_BASIS.run(db, BasisParams())
    row = _row(result, "Received", "Held")
    assert row["items"] == 2
    assert row["total_cost"] == Decimal("10.00")


def test_basis_rows_ordered_by_each_vocabularys_sort_order(db: Session) -> None:
    """Status: ordered(10) < received(20); disposition: held(10) < listed(20)."""
    _basis_item(db, "received", "listed", Decimal("1.00"))
    _basis_item(db, "received", "held", Decimal("1.00"))
    _basis_item(db, "ordered", "held", Decimal("1.00"))
    db.commit()

    result = MN_BASIS.run(db, BasisParams())
    keys = [(r["status"], r["disposition"]) for r in result.rows]
    assert keys == [
        ("Ordered", "Held"),
        ("Received", "Held"),
        ("Received", "Listed"),
    ]


def test_basis_totals_sum_the_rows(db: Session) -> None:
    _basis_item(db, "received", "held", Decimal("10.00"))
    _basis_item(db, "received", "listed", Decimal("20.00"))
    _basis_item(db, "ordered", "held", Decimal("30.00"))
    db.commit()

    result = MN_BASIS.run(db, BasisParams())
    assert result.totals is not None
    assert result.totals["items"] == sum(cast("int", r["items"]) for r in result.rows)
    assert result.totals["total_cost"] == sum(
        (cast("Decimal", r["total_cost"]) for r in result.rows), Decimal("0")
    )
    assert result.totals["status"] == "All statuses"
    assert result.totals["disposition"] is None


def test_basis_drills_are_all_none(db: Session) -> None:
    _basis_item(db, "received", "held", Decimal("1.00"))
    db.commit()

    result = MN_BASIS.run(db, BasisParams())
    assert all(d is None for d in result.drills)


def test_basis_with_no_live_items_returns_no_rows(db: Session) -> None:
    result = MN_BASIS.run(db, BasisParams())
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []
    assert any("no live items" in note.lower() for note in result.notes)


# ---------------------------------------------------------------------------
# mn_tax
# ---------------------------------------------------------------------------


def _vendor(db: Session, name: str) -> Vendor:
    """A vendor by this name, committed so later orders can reference it."""
    vendor = Vendor(name=name)
    db.add(vendor)
    db.commit()
    db.refresh(vendor)
    return vendor


def _order_for(
    db: Session, vendor: Vendor, *, order_number: str, ordered_on: date | None
) -> PurchaseOrder:
    """A purchase order for an existing `vendor`."""
    order = PurchaseOrder(
        vendor_id=vendor.id, order_number=order_number, ordered_on=ordered_on
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _order(
    db: Session, vendor_name: str, *, order_number: str, ordered_on: date | None
) -> PurchaseOrder:
    """A purchase order under a fresh vendor of this name."""
    return build_purchase_order(
        db, vendor_name=vendor_name, order_number=order_number, ordered_on=ordered_on
    )


def _tax_item(
    db: Session,
    order: PurchaseOrder,
    *,
    item_cost: Decimal,
    tax_rate: Decimal,
    status: str = "received",
) -> InventoryItem:
    """A live item on `order`, with an item cost and tax rate that fix its tax."""
    item = build_bare_item(
        db,
        status_id=code_id(db, ItemStatus, status),
        item_cost=item_cost,
        shipping_cost=Decimal("0"),
        tax_rate=tax_rate,
    )
    item.purchase_order_id = order.id
    db.commit()
    return item


def test_tax_buckets_by_month_by_default(db: Session) -> None:
    order = _order(db, "Vendor MT1", order_number="MT-1", ordered_on=date(2026, 3, 10))
    _tax_item(db, order, item_cost=Decimal("100.00"), tax_rate=Decimal("0.05"))
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor MT1")
    assert row["period"] == "2026-03"
    assert row["purchases"] == 1
    assert row["sales_tax"] == Decimal("5.00")


def test_tax_year_bucketing(db: Session) -> None:
    order = _order(db, "Vendor MTY", order_number="MTY-1", ordered_on=date(2026, 6, 1))
    _tax_item(db, order, item_cost=Decimal("100.00"), tax_rate=Decimal("0.10"))
    db.commit()

    result = MN_TAX.run(db, TaxParams(period="year"))
    row = next(r for r in result.rows if r["vendor"] == "Vendor MTY")
    assert row["period"] == "2026"
    assert row["sales_tax"] == Decimal("10.00")


def test_tax_sums_sales_tax_across_several_items(db: Session) -> None:
    order = _order(db, "Vendor MT2", order_number="MT-2", ordered_on=date(2026, 3, 1))
    _tax_item(db, order, item_cost=Decimal("10.33"), tax_rate=Decimal("0.05"))
    _tax_item(db, order, item_cost=Decimal("7.77"), tax_rate=Decimal("0.05"))
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor MT2")
    # sales_tax is generated per row; with no shipping it is
    # round(item_cost * tax_rate, 2).
    expected = (Decimal("10.33") * Decimal("0.05")).quantize(Decimal("0.01")) + (
        Decimal("7.77") * Decimal("0.05")
    ).quantize(Decimal("0.01"))
    assert row["sales_tax"] == expected


def test_tax_period_subtotal_sums_its_vendors(db: Session) -> None:
    vendor_a = _vendor(db, "Vendor MTA")
    order_a = _order_for(
        db, vendor_a, order_number="MTA-1", ordered_on=date(2026, 4, 1)
    )
    _tax_item(db, order_a, item_cost=Decimal("100.00"), tax_rate=Decimal("0.05"))
    vendor_b = _vendor(db, "Vendor MTB")
    order_b = _order_for(
        db, vendor_b, order_number="MTB-1", ordered_on=date(2026, 4, 15)
    )
    _tax_item(db, order_b, item_cost=Decimal("200.00"), tax_rate=Decimal("0.05"))
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    period_rows = [r for r in result.rows if r["period"] == "2026-04"]
    assert [r["vendor"] for r in period_rows] == [
        "Vendor MTA",
        "Vendor MTB",
        "All vendors",
    ]
    subtotal = period_rows[-1]
    assert subtotal["purchases"] == 2
    assert subtotal["sales_tax"] == Decimal("15.00")


def test_tax_date_bounds_are_inclusive(db: Session) -> None:
    order_edge_from = _order(
        db, "Vendor MTD1", order_number="MTD-1", ordered_on=date(2026, 5, 1)
    )
    _tax_item(db, order_edge_from, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    order_edge_to = _order(
        db, "Vendor MTD2", order_number="MTD-2", ordered_on=date(2026, 5, 31)
    )
    _tax_item(db, order_edge_to, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    order_before = _order(
        db, "Vendor MTD3", order_number="MTD-3", ordered_on=date(2026, 4, 30)
    )
    _tax_item(db, order_before, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    order_after = _order(
        db, "Vendor MTD4", order_number="MTD-4", ordered_on=date(2026, 6, 1)
    )
    _tax_item(db, order_after, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    db.commit()

    result = MN_TAX.run(
        db, TaxParams(date_from=date(2026, 5, 1), date_to=date(2026, 5, 31))
    )
    vendors = {r["vendor"] for r in result.rows}
    assert vendors == {"Vendor MTD1", "Vendor MTD2", "All vendors"}


def test_tax_absent_bound_is_open(db: Session) -> None:
    order = _order(db, "Vendor MTO1", order_number="MTO-1", ordered_on=date(2020, 1, 1))
    _tax_item(db, order, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    db.commit()

    result = MN_TAX.run(db, TaxParams(date_to=date(2026, 1, 1)))
    assert "Vendor MTO1" in {r["vendor"] for r in result.rows}


def test_tax_undated_purchase_excluded_with_a_note(db: Session) -> None:
    dated = _order(db, "Vendor MTU1", order_number="MTU-1", ordered_on=date(2026, 5, 5))
    _tax_item(db, dated, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    undated = _order(db, "Vendor MTU2", order_number="MTU-2", ordered_on=None)
    _tax_item(db, undated, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    assert "Vendor MTU2" not in {r["vendor"] for r in result.rows}
    assert any("1 purchase" in note and "excluded" in note for note in result.notes)


def test_tax_no_undated_purchase_has_no_undated_note(db: Session) -> None:
    order = _order(db, "Vendor MTN", order_number="MTN-1", ordered_on=date(2026, 5, 5))
    _tax_item(db, order, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    assert not any("excluded" in note for note in result.notes)


def test_tax_notes_state_the_live_item_purchase_rule(db: Session) -> None:
    order = _order(db, "Vendor MTR", order_number="MTR-1", ordered_on=date(2026, 5, 5))
    _tax_item(db, order, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    assert any("live item" in note for note in result.notes)


def test_tax_a_purchase_with_no_live_item_does_not_count(db: Session) -> None:
    order = _order(db, "Vendor MTX", order_number="MTX-1", ordered_on=date(2026, 5, 5))
    item = _tax_item(db, order, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    item.deleted_at = datetime(2026, 5, 6, tzinfo=UTC)
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    assert "Vendor MTX" not in {r["vendor"] for r in result.rows}


def test_tax_a_split_parent_purchase_does_not_count(db: Session) -> None:
    order = _order(db, "Vendor MTS", order_number="MTS-1", ordered_on=date(2026, 5, 5))
    item = _tax_item(db, order, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    item.split_at = datetime(2026, 5, 6, tzinfo=UTC)
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    assert "Vendor MTS" not in {r["vendor"] for r in result.rows}


def test_tax_purchase_with_one_deleted_and_one_live_item_counts_the_live_one(
    db: Session,
) -> None:
    """The deleted item's own tax is left out; the purchase itself still counts."""
    order = _order(
        db, "Vendor MTDL", order_number="MTDL-1", ordered_on=date(2026, 5, 5)
    )
    _tax_item(db, order, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    deleted = _tax_item(
        db, order, item_cost=Decimal("999.00"), tax_rate=Decimal("0.10")
    )
    deleted.deleted_at = datetime(2026, 5, 6, tzinfo=UTC)
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    row = next(r for r in result.rows if r["vendor"] == "Vendor MTDL")
    assert row["purchases"] == 1
    assert row["sales_tax"] == Decimal("1.00")


def test_tax_totals_equal_the_sum_of_period_subtotals_across_two_periods(
    db: Session,
) -> None:
    order_a = _order(
        db, "Vendor MTGT1", order_number="MTGT-1", ordered_on=date(2026, 1, 10)
    )
    _tax_item(db, order_a, item_cost=Decimal("100.00"), tax_rate=Decimal("0.05"))
    order_b = _order(
        db, "Vendor MTGT2", order_number="MTGT-2", ordered_on=date(2026, 2, 20)
    )
    _tax_item(db, order_b, item_cost=Decimal("200.00"), tax_rate=Decimal("0.05"))
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    subtotal_rows = [r for r in result.rows if r["vendor"] == "All vendors"]
    assert len(subtotal_rows) == 2
    assert result.totals is not None
    assert result.totals["purchases"] == sum(
        cast("int", r["purchases"]) for r in subtotal_rows
    )
    assert result.totals["sales_tax"] == sum(
        (cast("Decimal", r["sales_tax"]) for r in subtotal_rows), Decimal("0")
    )


def test_tax_totals_row_sums_the_data_rows(db: Session) -> None:
    order_a = _order(
        db, "Vendor MTT1", order_number="MTT-1", ordered_on=date(2026, 7, 1)
    )
    _tax_item(db, order_a, item_cost=Decimal("40.00"), tax_rate=Decimal("0.05"))
    order_b = _order(
        db, "Vendor MTT2", order_number="MTT-2", ordered_on=date(2026, 8, 1)
    )
    _tax_item(db, order_b, item_cost=Decimal("60.00"), tax_rate=Decimal("0.05"))
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    assert result.totals is not None
    data_rows = [r for r in result.rows if r["vendor"] != "All vendors"]
    assert result.totals["purchases"] == sum(
        cast("int", r["purchases"]) for r in data_rows
    )
    assert result.totals["sales_tax"] == sum(
        (cast("Decimal", r["sales_tax"]) for r in data_rows), Decimal("0")
    )


def test_tax_nothing_in_range_returns_no_rows(db: Session) -> None:
    order = _order(db, "Vendor MTE", order_number="MTE-1", ordered_on=date(2020, 1, 1))
    _tax_item(db, order, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    db.commit()

    result = MN_TAX.run(
        db, TaxParams(date_from=date(2026, 1, 1), date_to=date(2026, 12, 31))
    )
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []


def test_tax_drills_are_all_none(db: Session) -> None:
    order = _order(
        db, "Vendor MTDR", order_number="MTDR-1", ordered_on=date(2026, 5, 5)
    )
    _tax_item(db, order, item_cost=Decimal("10.00"), tax_rate=Decimal("0.10"))
    db.commit()

    result = MN_TAX.run(db, TaxParams())
    assert all(d is None for d in result.drills)


# ---------------------------------------------------------------------------
# mn_value
# ---------------------------------------------------------------------------


def _parsed(path: str) -> tuple[str, dict[str, str]]:
    """A drill's own path and query string, split apart."""
    split = urlsplit(path)
    return split.path, {k: v[0] for k, v in parse_qs(split.query).items()}


def _value_item(
    db: Session,
    *,
    cost: Decimal,
    value: Decimal | None,
    kind: str = "coin",
) -> InventoryItem:
    """A live item of `kind`, with an exact cost basis and recorded value."""
    return build_bare_item(
        db,
        item_kind_id=code_id(db, ItemKind, kind),
        item_cost=cost,
        tax_rate=Decimal("0"),
        shipping_cost=Decimal("0"),
        numismatic_value=value,
    )


def _kind_row(result: ReportResult, kind_label: str) -> dict[str, object]:
    """The one row matching `kind_label`."""
    return next(r for r in result.rows if r["kind"] == kind_label)


def test_value_counts_items_with_and_without_a_value(db: Session) -> None:
    _value_item(db, cost=Decimal("10.00"), value=Decimal("15.00"))
    _value_item(db, cost=Decimal("20.00"), value=None)
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    row = _kind_row(result, "Coin")
    assert row["items"] == 2
    assert row["valued_items"] == 1
    assert row["unvalued_items"] == 1
    assert row["cost"] == Decimal("10.00")
    assert row["value"] == Decimal("15.00")
    assert row["difference"] == Decimal("5.00")


def test_value_difference_with_odd_cents(db: Session) -> None:
    _value_item(db, cost=Decimal("10.33"), value=Decimal("12.10"))
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    row = _kind_row(result, "Coin")
    assert row["cost"] == Decimal("10.33")
    assert row["value"] == Decimal("12.10")
    assert row["difference"] == Decimal("1.77")


def test_value_item_with_a_value_and_zero_cost(db: Session) -> None:
    _value_item(db, cost=Decimal("0.00"), value=Decimal("50.00"))
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    row = _kind_row(result, "Coin")
    assert row["cost"] == Decimal("0.00")
    assert row["value"] == Decimal("50.00")
    assert row["difference"] == Decimal("50.00")


def test_value_kind_with_no_valued_items_has_zero_valued_columns(db: Session) -> None:
    _value_item(db, cost=Decimal("20.00"), value=None)
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    row = _kind_row(result, "Coin")
    assert row["valued_items"] == 0
    assert row["cost"] == Decimal("0")
    assert row["value"] == Decimal("0")
    assert row["difference"] == Decimal("0")
    assert row["unvalued_items"] == 1
    # Money with nothing to add up is still money: two places, as every
    # other amount in the column reads.
    for key in ("cost", "value", "difference"):
        assert str(row[key]) == "0.00", key
        assert result.totals is not None
        assert str(result.totals[key]) == "0.00", key


def test_value_excludes_a_deleted_item(db: Session) -> None:
    _value_item(db, cost=Decimal("10.00"), value=Decimal("10.00"))
    deleted = _value_item(db, cost=Decimal("99.00"), value=Decimal("99.00"))
    deleted.deleted_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    row = _kind_row(result, "Coin")
    assert row["items"] == 1
    assert row["cost"] == Decimal("10.00")


def test_value_excludes_a_split_parent_counts_its_live_children(db: Session) -> None:
    parent = _value_item(db, cost=Decimal("10.00"), value=Decimal("10.00"))
    parent.split_at = datetime(2026, 1, 1, tzinfo=UTC)
    db.commit()
    _value_item(db, cost=Decimal("5.00"), value=Decimal("6.00"))
    _value_item(db, cost=Decimal("5.00"), value=Decimal("6.00"))
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    row = _kind_row(result, "Coin")
    assert row["items"] == 2
    assert row["cost"] == Decimal("10.00")
    assert row["value"] == Decimal("12.00")


def test_value_rows_ordered_by_kind_sort_order(db: Session) -> None:
    """Coin (10) sorts before Currency (20)."""
    _value_item(db, cost=Decimal("1.00"), value=None, kind="currency")
    _value_item(db, cost=Decimal("1.00"), value=None, kind="coin")
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    assert [r["kind"] for r in result.rows] == ["Coin", "Currency"]


def test_value_totals_sum_the_rows(db: Session) -> None:
    _value_item(db, cost=Decimal("10.00"), value=Decimal("15.00"), kind="coin")
    _value_item(db, cost=Decimal("20.00"), value=Decimal("18.00"), kind="currency")
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    assert result.totals is not None
    assert result.totals["items"] == sum(cast("int", r["items"]) for r in result.rows)
    assert result.totals["cost"] == sum(
        (cast("Decimal", r["cost"]) for r in result.rows), Decimal("0")
    )
    assert result.totals["value"] == sum(
        (cast("Decimal", r["value"]) for r in result.rows), Decimal("0")
    )
    assert result.totals["difference"] == sum(
        (cast("Decimal", r["difference"]) for r in result.rows), Decimal("0")
    )
    assert result.totals["kind"] == "All kinds"


def test_value_totals_valued_and_unvalued_items_equal_their_rows(db: Session) -> None:
    _value_item(db, cost=Decimal("10.00"), value=Decimal("15.00"), kind="coin")
    _value_item(db, cost=Decimal("20.00"), value=None, kind="coin")
    _value_item(db, cost=Decimal("5.00"), value=Decimal("5.00"), kind="currency")
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    assert result.totals is not None
    assert result.totals["valued_items"] == sum(
        cast("int", r["valued_items"]) for r in result.rows
    )
    assert result.totals["unvalued_items"] == sum(
        cast("int", r["unvalued_items"]) for r in result.rows
    )


def test_value_note_states_the_owner_entered_value_only(db: Session) -> None:
    _value_item(db, cost=Decimal("1.00"), value=Decimal("1.00"))
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    assert any("owner" in note.lower() for note in result.notes)
    assert any("valuation basis" in note.lower() for note in result.notes)
    # Plain words for the reader, never a column name.
    assert not any("numismatic_value" in note for note in result.notes)


def test_value_with_no_live_items_returns_no_rows(db: Session) -> None:
    result = MN_VALUE.run(db, ValueParams())
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []


def test_value_drill_matches_the_items_column_for_a_coin_kind(db: Session) -> None:
    """With the defaults (status=received, disposition=held)."""
    _value_item(db, cost=Decimal("10.00"), value=Decimal("10.00"), kind="coin")
    _value_item(db, cost=Decimal("20.00"), value=None, kind="coin")

    result = MN_VALUE.run(db, ValueParams())
    idx = next(i for i, r in enumerate(result.rows) if r["kind"] == "Coin")
    path, query = _parsed(cast("str", result.drills[idx]))
    assert path == "/inventory/coins"
    assert query["status"] == "received"
    assert query["disposition"] == "held"
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == result.rows[idx]["items"]


def test_value_drill_matches_the_items_column_for_currency(db: Session) -> None:
    """With the defaults (status=received, disposition=held)."""
    _value_item(db, cost=Decimal("10.00"), value=Decimal("10.00"), kind="currency")
    _value_item(db, cost=Decimal("20.00"), value=None, kind="currency")

    result = MN_VALUE.run(db, ValueParams())
    idx = next(i for i, r in enumerate(result.rows) if r["kind"] == "Currency")
    path, query = _parsed(cast("str", result.drills[idx]))
    assert path == "/inventory/currency"
    assert query["status"] == "received"
    assert query["disposition"] == "held"
    _, total = inventory_search(db, CURRENCY_VIEW, params=dict(query))
    assert total == result.rows[idx]["items"]


def test_value_default_status_and_disposition_exclude_other_items(db: Session) -> None:
    """`received`/`held` is the default filter -- an `ordered` item is left out."""
    _value_item(db, cost=Decimal("10.00"), value=Decimal("10.00"), kind="coin")
    other = _value_item(db, cost=Decimal("5.00"), value=None, kind="coin")
    other.status_id = code_id(db, ItemStatus, "ordered")
    db.commit()

    result = MN_VALUE.run(db, ValueParams())
    row = _kind_row(result, "Coin")
    assert row["items"] == 1


def test_value_drill_matches_the_items_column_with_status_and_disposition_all(
    db: Session,
) -> None:
    """The drill's own query string, not just the default one, must agree."""
    _value_item(db, cost=Decimal("10.00"), value=Decimal("10.00"), kind="coin")
    other_status = _value_item(db, cost=Decimal("5.00"), value=None, kind="coin")
    other_status.status_id = code_id(db, ItemStatus, "ordered")
    db.commit()

    result = MN_VALUE.run(db, ValueParams(status="all", disposition="all"))
    idx = next(i for i, r in enumerate(result.rows) if r["kind"] == "Coin")
    assert result.rows[idx]["items"] == 2
    path, query = _parsed(cast("str", result.drills[idx]))
    assert path == "/inventory/coins"
    assert "status" not in query
    assert "disposition" not in query
    _, total = inventory_search(db, COIN_VIEW, params=dict(query))
    assert total == result.rows[idx]["items"]
