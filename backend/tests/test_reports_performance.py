"""A performance guard: every registered report runs in under a second.

A guard, not a benchmark (`docs/specs/reporting-design.md`, *Testing*):
`REPORTS` is parametrized directly, so a report a later phase adds is
covered the moment its module is registered, with no test of its own to
write. The data set is modest -- enough that a report's query touches more
than a handful of rows at every join, so an accidental N+1 (a Python loop
issuing one query per row rather than one query per report) would show up
as a slow test, not enough to make building it itself slow.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from decimal import Decimal

import pytest
from app.models import (
    Denomination,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    ListingStatus,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesVenue,
    SalesVenueKind,
)
from app.reports import REPORTS
from sqlalchemy.orm import Session

from tests.builders import build_bare_item, build_purchase_order, code_id, usd_id

#: However slow the slowest report may run against this data set -- well
#: under what a real N+1 query or an unindexed scan would take, and well
#: over what any of the five reports actually takes today (a few
#: milliseconds each, measured on the live collection -- see the spec's
#: "What exists, and is reused").
_BUDGET_SECONDS = 1.0

#: A spread of coin and note denominations, so `cb_holdings` groups more
#: than one row per kind and `dq_completeness` sees more than one value per
#: field.
_COIN_DENOMINATIONS = [
    "usd_coin_0_01",
    "usd_coin_0_05",
    "usd_coin_0_10",
    "usd_coin_0_25",
    "usd_coin_0_50",
    "usd_coin_1_00",
]
_NOTE_DENOMINATIONS = ["usd_note_1", "usd_note_5", "usd_note_10", "usd_note_20"]
_STATUSES = ["received", "ordered", "missing", "canceled", "returned"]


def _venue(db: Session, code: str) -> SalesVenue:
    """A marketplace platform to list against."""
    venue = SalesVenue(
        code=code,
        name=code.title(),
        sales_venue_kind_id=code_id(db, SalesVenueKind, "marketplace"),
    )
    db.add(venue)
    db.flush()
    return venue


def _listing(
    db: Session,
    item: InventoryItem,
    venue: SalesVenue,
    currency_id: int,
    *,
    status: ListingStatus,
    price: Decimal = Decimal("50.00"),
) -> Listing:
    """A bare listing offering `item` -- no claim, no offer history needed."""
    listing = Listing(
        inventory_item_id=item.id,
        sales_venue_id=venue.id,
        currency_id=currency_id,
        price=price,
        quantity_available=1,
        status=status,
        title="",
    )
    db.add(listing)
    db.flush()
    return listing


def _build_modest_collection(db: Session) -> None:
    """A few dozen items, purchases and listings -- not the collection's thousands.

    One of each shape a report groups by: coins and notes across several
    denominations and every acquisition status; some items with no
    denomination at all (`cb_holdings`'s "No denomination" row); purchase
    orders with items still `ordered` or `missing`, some old enough to be
    overdue; and a spread of listings -- active and paused, an item and a
    lot -- so `sl_offered` walks both of its row sources.
    """
    currency_id = usd_id(db)

    for index in range(36):
        denom = _COIN_DENOMINATIONS[index % len(_COIN_DENOMINATIONS)]
        status = _STATUSES[index % len(_STATUSES)]
        build_bare_item(
            db,
            denomination_id=code_id(db, Denomination, denom),
            status_id=code_id(db, ItemStatus, status),
            item_cost=Decimal("25.00") + index,
            tax_rate=Decimal("0"),
            shipping_cost=Decimal("0"),
        )

    for index in range(6):
        build_bare_item(
            db,
            denomination_id=None,
            item_cost=Decimal("10.00") + index,
            tax_rate=Decimal("0"),
            shipping_cost=Decimal("0"),
        )

    for index in range(12):
        denom = _NOTE_DENOMINATIONS[index % len(_NOTE_DENOMINATIONS)]
        build_bare_item(
            db,
            item_kind_id=code_id(db, ItemKind, "currency"),
            denomination_id=code_id(db, Denomination, denom),
            item_cost=Decimal("15.00") + index,
            tax_rate=Decimal("0"),
            shipping_cost=Decimal("0"),
        )

    today = date.today()
    for index in range(10):
        order = build_purchase_order(
            db,
            vendor_name=f"Vendor {index}",
            order_number=f"ORD-{index}",
            ordered_on=today - timedelta(days=5 + index * 8),
            commit=False,
        )
        db.flush()
        status = "ordered" if index % 2 == 0 else "missing"
        item = build_bare_item(
            db,
            status_id=code_id(db, ItemStatus, status),
            item_cost=Decimal("40.00"),
            tax_rate=Decimal("0"),
            shipping_cost=Decimal("0"),
        )
        item.purchase_order_id = order.id
        db.commit()

    venue = _venue(db, "test_venue")
    listing_items = [
        build_bare_item(
            db,
            item_cost=Decimal("60.00"),
            tax_rate=Decimal("0"),
            shipping_cost=Decimal("0"),
        )
        for _ in range(8)
    ]
    db.commit()
    for offset, item in enumerate(listing_items[:6]):
        _listing(
            db,
            item,
            venue,
            currency_id,
            status=ListingStatus.active if offset % 2 == 0 else ListingStatus.paused,
        )

    lot = SalesLot(title="A lot", status=SalesLotStatus.offered)
    db.add(lot)
    db.flush()
    for item in listing_items[6:]:
        db.add(SalesLotItem(sales_lot_id=lot.id, inventory_item_id=item.id))
    db.flush()
    lot_listing = Listing(
        sales_lot_id=lot.id,
        sales_venue_id=venue.id,
        currency_id=currency_id,
        price=Decimal("100.00"),
        quantity_available=1,
        status=ListingStatus.active,
        title="A lot",
    )
    db.add(lot_listing)
    db.commit()


@pytest.mark.parametrize("report_id", sorted(REPORTS))
def test_report_runs_under_a_second(report_id: str, db: Session) -> None:
    """`report.run`, with its default parameters, finishes inside the budget."""
    _build_modest_collection(db)
    report = REPORTS[report_id]
    params = report.params()

    started = time.perf_counter()
    report.run(db, params)
    elapsed = time.perf_counter() - started

    assert elapsed < _BUDGET_SECONDS, (
        f"{report_id} took {elapsed:.3f}s against a modest data set "
        f"(budget {_BUDGET_SECONDS}s)"
    )
