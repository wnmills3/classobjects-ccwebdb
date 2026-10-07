"""`sl_offered`, `sl_sales`, `sl_fulfilment`, `sl_aging` and `sl_auctions`."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import cast

import pytest
from app import lot_writes, offering_writes, order_writes, sales_writes
from app.auctions import SettlementLine, add_lot, close, schedule, settle
from app.buyers import venue_buyer
from app.lifecycle_writes import set_status
from app.models import (
    AuctionLotResult,
    AuctionStatus,
    Currency,
    Disposition,
    InventoryItem,
    ItemStatus,
    Listing,
    ListingFormat,
    ListingStatus,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesOrder,
    SalesOrderItem,
    SalesOrderItemShare,
    SalesVenue,
    SalesVenueKind,
    User,
    utcnow,
)
from app.offering_writes import OFFER_CURRENCY
from app.order_writes import Line
from app.reports.selling import (
    SL_AGING,
    SL_AUCTIONS,
    SL_FULFILMENT,
    SL_OFFERED,
    SL_SALES,
    AgingParams,
    AuctionsParams,
    FulfilmentParams,
    OfferedParams,
    SalesParams,
    _age_bucket,
    _months_since,
)
from app.sales_venues import store_venue_id
from app.sales_writes import FeeLine
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, build_auction, code_id, priced_item

_NOW = datetime(2026, 9, 28, tzinfo=UTC)


def _local_noon(days_ago: int) -> datetime:
    """Local noon `days_ago` days before today -- never near a DST boundary.

    A day-count fixture anchored to "now" can land inside the hour a DST
    transition adds or removes, on the one day a year that happens, and
    read as off by a day for no reason the test names. Noon is never
    inside that hour, in any zone this suite runs in.
    """
    local_date = (datetime.now() - timedelta(days=days_ago)).date()
    return datetime.combine(local_date, time(12, 0)).astimezone()


def _at_noon(day: date) -> datetime:
    """Local noon on `day`, tz-aware -- the same DST-safe anchor as `_local_noon`."""
    return datetime.combine(day, time(12, 0)).astimezone()


def _months_ago(n: int) -> date:
    """`n` whole calendar months before today, on today's own day of month.

    Capped at day 28 so a today-is-the-31st test never rolls into the wrong
    month. Deterministic against `_months_since`'s own arithmetic: `n`
    months back always measures as exactly `n`, never `n - 1` from a
    day-of-month rounding.
    """
    today = date.today()
    day = min(today.day, 28)
    month_index = today.year * 12 + (today.month - 1) - n
    year, month = divmod(month_index, 12)
    return date(year, month + 1, day)


def _offer_on(
    db: Session, item: InventoryItem, venue: SalesVenue, *, price: Decimal
) -> Listing:
    """Offer `item` on `venue` through the real writer, not a bare `Listing(...)`.

    `offering_writes.offer` is what pauses another of the item's own
    listings and sets `paused_by_listing_id` -- the shape the "paused
    elsewhere" tests below need, which a hand-built row cannot produce.
    """
    return offering_writes.offer(
        db,
        item=item,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=price,
        title="",
        description="",
        external_id=None,
        quantity=1,
    )


def _venue(db: Session, code: str) -> SalesVenue:
    """A marketplace platform, distinct per test by its code."""
    venue = SalesVenue(
        code=code,
        name=code.title(),
        sales_venue_kind_id=code_id(db, SalesVenueKind, "marketplace"),
    )
    db.add(venue)
    db.flush()
    return venue


def _item_listing(
    db: Session,
    item: InventoryItem,
    venue: SalesVenue,
    *,
    price: Decimal = Decimal("50.00"),
    status: ListingStatus = ListingStatus.active,
    currency: str = "USD",
    listed_at: datetime = _NOW,
    title: str = "",
) -> Listing:
    """A listing offering one item, built directly -- no claim, no history.

    The report reads only `listing` and the tables it joins, so a bare row
    exercises it without going through `offering_writes` and the claim it
    would create; `check_claim_invariant` (conftest.py) only inspects rows
    that actually carry a claim, so a claimless listing never trips it.
    """
    listing = Listing(
        inventory_item_id=item.id,
        sales_venue_id=venue.id,
        currency_id=code_id(db, Currency, currency),
        price=price,
        quantity_available=1,
        status=status,
        title=title,
        listed_at=listed_at,
    )
    db.add(listing)
    db.flush()
    return listing


def _lot(db: Session, *, title: str = "A lot") -> SalesLot:
    """An offered lot, so its open membership agrees with `check_lot_invariant`."""
    lot = SalesLot(title=title, status=SalesLotStatus.offered)
    db.add(lot)
    db.flush()
    return lot


def _lot_member(
    db: Session, lot: SalesLot, item: InventoryItem, *, released: bool = False
) -> SalesLotItem:
    """One item's membership of `lot`, open unless `released`."""
    row = SalesLotItem(
        sales_lot_id=lot.id,
        inventory_item_id=item.id,
        released_at=utcnow() if released else None,
    )
    db.add(row)
    db.flush()
    return row


def _lot_listing(
    db: Session,
    lot: SalesLot,
    venue: SalesVenue,
    *,
    price: Decimal = Decimal("50.00"),
    status: ListingStatus = ListingStatus.active,
    currency: str = "USD",
    listed_at: datetime = _NOW,
    title: str = "",
) -> Listing:
    """A listing offering `lot` as a whole, built directly, as `_item_listing` is."""
    listing = Listing(
        sales_lot_id=lot.id,
        sales_venue_id=venue.id,
        currency_id=code_id(db, Currency, currency),
        price=price,
        quantity_available=1,
        status=status,
        title=title,
        listed_at=listed_at,
    )
    db.add(listing)
    db.flush()
    return listing


# ---------------------------------------------------------------------------
# Which listings appear at all
# ---------------------------------------------------------------------------


def test_active_and_paused_are_included_ended_is_excluded(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    _item_listing(db, make_item(), venue, status=ListingStatus.active, title="Active")
    _item_listing(db, make_item(), venue, status=ListingStatus.paused, title="Paused")
    _item_listing(db, make_item(), venue, status=ListingStatus.ended, title="Ended")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())

    assert {r["listing"] for r in result.rows} == {"Active", "Paused"}
    # A row links on what it offers (its item code), not on its venue.
    assert result.link_column == "offers"


def test_a_deleted_or_split_item_listing_is_excluded(
    db: Session, make_item: ItemFactory
) -> None:
    """A deleted item's and a split parent's listings are both excluded.

    Neither state is reachable through the app's own write paths -- deleting
    or splitting an offered item is refused (`routers/inventory.py::delete_item`,
    `splitting.py`) -- but the report excludes it defensively, through the
    same shared predicate every other report reads, rather than trusting
    that invariant to hold forever.
    """
    venue = _venue(db, "ebay")
    deleted = make_item(title="Deleted item")
    _item_listing(db, deleted, venue, title="Deleted")
    split = make_item(title="Split item")
    _item_listing(db, split, venue, title="Split")
    deleted.deleted_at = utcnow()
    split.split_at = utcnow()
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())

    assert result.rows == []


def test_no_listings_returns_no_totals(db: Session) -> None:
    result = SL_OFFERED.run(db, OfferedParams())
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []


# ---------------------------------------------------------------------------
# Cost basis
# ---------------------------------------------------------------------------


def test_an_item_listings_cost_basis_is_its_own_total_cost(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    item = priced_item(make_item, "Priced item", Decimal("123.45"))
    _item_listing(db, item, venue, title="Priced")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    row = next(r for r in result.rows if r["listing"] == "Priced")
    assert row["cost_basis"] == Decimal("123.45")


def test_a_lots_cost_basis_is_the_sum_of_its_live_open_members(
    db: Session, make_item: ItemFactory
) -> None:
    """A deleted member and one released from the lot both contribute nothing.

    Released is the same kind of fact as deleted: `released_at` means "no
    longer in this lot" (`lot_writes.open_members` reads it the same way),
    so a row this report
    would otherwise double-count as still held is excluded here too.
    """
    venue = _venue(db, "ebay")
    lot = _lot(db)
    held_a = priced_item(make_item, "A", Decimal("100.00"))
    held_b = priced_item(make_item, "B", Decimal("50.00"))
    deleted = priced_item(make_item, "Deleted", Decimal("999.00"))
    released = priced_item(make_item, "Released", Decimal("777.00"))
    _lot_member(db, lot, held_a)
    _lot_member(db, lot, held_b)
    _lot_member(db, lot, deleted)
    _lot_member(db, lot, released, released=True)
    deleted.deleted_at = utcnow()
    _lot_listing(db, lot, venue, title="Lot X")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    row = next(r for r in result.rows if r["listing"] == "Lot X")
    assert row["cost_basis"] == Decimal("150.00")


# ---------------------------------------------------------------------------
# Listing name, offers, status
# ---------------------------------------------------------------------------


def test_listing_name_falls_back_to_the_items_description_when_the_title_is_empty(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    item = make_item(title="Coin title", description="What it actually is")
    _item_listing(db, item, venue, title="")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    assert result.rows[0]["listing"] == "What it actually is"


def test_listing_name_falls_back_to_the_lots_name_when_the_title_is_empty(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    lot = _lot(db, title="Three Morgans")
    _lot_member(db, lot, make_item())
    _lot_listing(db, lot, venue, title="")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    assert result.rows[0]["listing"] == "Three Morgans"


def test_offers_column_is_the_item_code_or_the_lot_name(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    item = make_item(title="Coin")
    _item_listing(db, item, venue, title="Coin")
    lot = _lot(db, title="A Group")
    _lot_member(db, lot, make_item())
    _lot_listing(db, lot, venue, title="Lot listing")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    coin_row = next(r for r in result.rows if r["listing"] == "Coin")
    lot_row = next(r for r in result.rows if r["listing"] == "Lot listing")
    assert coin_row["offers"] == item.item_code
    assert lot_row["offers"] == "Lot: A Group"


def test_status_column_reads_active_or_paused_in_words(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    _item_listing(db, make_item(), venue, status=ListingStatus.active, title="A")
    _item_listing(db, make_item(), venue, status=ListingStatus.paused, title="B")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    statuses = {r["listing"]: r["status"] for r in result.rows}
    assert statuses == {"A": "Active", "B": "Paused"}


# ---------------------------------------------------------------------------
# Days listed
# ---------------------------------------------------------------------------


def test_days_listed_is_today_minus_listed_at(
    db: Session, make_item: ItemFactory
) -> None:
    """Local calendar days, not a literal 168-hour subtraction.

    Anchored to local noon (`_local_noon`), not UTC midnight of a calendar
    date: `listed_at` is `timestamptz`, and the database session may hand
    it back tagged with a zone offset from UTC. Anchoring to UTC midnight
    instead would land on the wrong side of local midnight and be off by a
    day, which is the failure `local_date` prevents.
    """
    venue = _venue(db, "ebay")
    _item_listing(db, make_item(), venue, listed_at=_local_noon(7), title="Week old")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    row = next(r for r in result.rows if r["listing"] == "Week old")
    assert row["days_listed"] == 7


def test_local_date_uses_the_local_zone_not_the_session_zone(
    db: Session, make_item: ItemFactory
) -> None:
    """`local_date` matters exactly when the session's zone is not local.

    `SET LOCAL TIME ZONE 'UTC'` makes *this session* hand `listed_at` back
    tagged UTC regardless of what zone the test machine itself is in -- the
    situation `local_date`'s docstring describes. `23:30` local, seven days
    ago, is deliberately close to local midnight: read back under a UTC
    session, that instant's UTC calendar date can be a day later than its
    local one (it is in `America/New_York`), so a bare `.date()` with no
    conversion back to local would read the wrong day. `SET LOCAL` is scoped
    to the current transaction; `conftest.py`'s
    `db` fixture keeps one real transaction open for the whole test
    (`join_transaction_mode="create_savepoint"` makes `db.commit()` below
    only a savepoint release), so it is still in effect when the report
    itself reads the row back.

    Survives: removing `.astimezone()` from `local_date` (leaving a bare
    `.date()`) makes this fail with `days_listed == 6`, not 7.
    """
    venue = _venue(db, "ebay")
    db.execute(text("SET LOCAL TIME ZONE 'UTC'"))
    late_local = datetime.combine(
        (datetime.now() - timedelta(days=7)).date(), time(23, 30)
    ).astimezone()
    _item_listing(db, make_item(), venue, listed_at=late_local, title="Late")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    row = next(r for r in result.rows if r["listing"] == "Late")
    assert row["days_listed"] == 7


# ---------------------------------------------------------------------------
# Order: venue, then listed_at, then listing id
# ---------------------------------------------------------------------------


def test_ordered_by_venue_then_listed_at(db: Session, make_item: ItemFactory) -> None:
    venue_a = _venue(db, "avenue")
    venue_b = _venue(db, "bvenue")
    _item_listing(
        db, make_item(), venue_a, listed_at=_NOW - timedelta(days=1), title="Newer"
    )
    _item_listing(
        db, make_item(), venue_a, listed_at=_NOW - timedelta(days=2), title="Older"
    )
    _item_listing(
        db, make_item(), venue_b, listed_at=_NOW - timedelta(days=5), title="OtherVenue"
    )
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    assert [r["listing"] for r in result.rows] == ["Older", "Newer", "OtherVenue"]


def test_same_venue_same_moment_breaks_the_tie_by_listing_id(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    first = _item_listing(db, make_item(), venue, listed_at=_NOW, title="First")
    second = _item_listing(db, make_item(), venue, listed_at=_NOW, title="Second")
    db.commit()
    assert first.id < second.id

    result = SL_OFFERED.run(db, OfferedParams())
    assert [r["listing"] for r in result.rows] == ["First", "Second"]


# ---------------------------------------------------------------------------
# Totals
# ---------------------------------------------------------------------------


def test_totals_sum_the_rows_asking_total_is_usd_only(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    _item_listing(
        db,
        priced_item(make_item, "USD1", Decimal("10.00")),
        venue,
        price=Decimal("25.00"),
        currency="USD",
        title="USD1",
    )
    _item_listing(
        db,
        priced_item(make_item, "USD2", Decimal("20.00")),
        venue,
        price=Decimal("35.00"),
        currency="USD",
        title="USD2",
    )
    _item_listing(
        db,
        priced_item(make_item, "CAD1", Decimal("5.00")),
        venue,
        price=Decimal("15.00"),
        currency="CAD",
        title="CAD1",
    )
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    assert result.totals is not None
    assert result.totals["asking"] == sum(
        (cast("Decimal", r["asking"]) for r in result.rows if r["currency"] == "USD"),
        Decimal("0"),
    )
    assert result.totals["asking"] == Decimal("60.00")
    assert result.totals["cost_basis"] == sum(
        (cast("Decimal", r["cost_basis"]) for r in result.rows), Decimal("0")
    )
    assert result.totals["cost_basis"] == Decimal("35.00")


def test_a_non_usd_listing_adds_a_note_naming_how_many_were_excluded(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    _item_listing(db, make_item(), venue, currency="USD", title="USD")
    _item_listing(db, make_item(), venue, currency="CAD", title="CAD")
    _item_listing(db, make_item(), venue, currency="GBP", title="GBP")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    assert result.notes == [
        f"2 listings not priced in {OFFER_CURRENCY} excluded from the asking total."
    ]


def test_no_non_usd_listings_adds_no_note(db: Session, make_item: ItemFactory) -> None:
    venue = _venue(db, "ebay")
    _item_listing(db, make_item(), venue, currency="USD")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    assert result.notes == []


# ---------------------------------------------------------------------------
# Paused elsewhere: a coin must count once, not once per row that names it
# ---------------------------------------------------------------------------


def test_a_store_listing_paused_by_an_ebay_offer_counts_the_coin_once(
    db: Session, make_item: ItemFactory
) -> None:
    """The item is shown twice but counted once.

    `offering_writes.offer` pauses the store listing when the same item is
    offered on eBay, setting its `paused_by_listing_id` to the new eBay
    row. Both rows appear -- the store listing still exists and the owner
    should see it -- but the paused row is left out of both totals, and its
    status names the listing that set it aside.
    """
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    ebay = _venue(db, "ebay")
    item = priced_item(make_item, "Paused coin", Decimal("75.00"))
    store_listing = _offer_on(db, item, store, price=Decimal("100.00"))
    _offer_on(db, item, ebay, price=Decimal("120.00"))
    db.commit()
    db.refresh(store_listing)
    assert store_listing.status is ListingStatus.paused

    result = SL_OFFERED.run(db, OfferedParams())

    assert len(result.rows) == 2
    store_row = next(r for r in result.rows if r["venue"] == store.name)
    ebay_row = next(r for r in result.rows if r["venue"] == ebay.name)
    assert store_row["status"] == f"Paused for {ebay.name} listing"
    assert ebay_row["status"] == "Active"

    assert result.totals is not None
    assert result.totals["asking"] == Decimal("120.00")  # the eBay row alone
    assert result.totals["cost_basis"] == Decimal("75.00")  # the item, once
    assert result.notes == [
        "1 paused listing left out of the totals: their item is on offer "
        "in another listing."
    ]

    counted = [
        r for r in result.rows if not cast("str", r["status"]).startswith("Paused for")
    ]
    assert (
        sum((cast("Decimal", r["asking"]) for r in counted), Decimal("0"))
        == (result.totals["asking"])
    )
    assert (
        sum((cast("Decimal", r["cost_basis"]) for r in counted), Decimal("0"))
        == (result.totals["cost_basis"])
    )


def test_a_store_listing_paused_by_a_lot_offer_counts_the_coin_once(
    db: Session, make_item: ItemFactory
) -> None:
    """The same guarantee, when what paused the coin's listing is a lot.

    Offering a lot pauses each member's own store listing the same way
    offering the item elsewhere does (`offering_writes.offer`, the lot
    branch). The lot's own cost basis already counts the member's cost
    once, as part of the lot's sum -- so without the exclusion, the paused
    store row's separate cost basis would count that same coin a second
    time.
    """
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    ebay = _venue(db, "ebay")
    member = priced_item(make_item, "Lot member", Decimal("60.00"))
    other_member = priced_item(make_item, "Other member", Decimal("40.00"))
    store_listing = _offer_on(db, member, store, price=Decimal("90.00"))
    db.commit()

    lot = lot_writes.create_lot(db, title="Grouped lot", description="")
    lot_writes.add_member(db, lot, member)
    lot_writes.add_member(db, lot, other_member)
    offering_writes.offer(
        db,
        lot=lot,
        venue=ebay,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("150.00"),
        title="Grouped lot",
        description="",
        external_id=None,
    )
    db.commit()
    db.refresh(store_listing)
    assert store_listing.status is ListingStatus.paused

    result = SL_OFFERED.run(db, OfferedParams())

    assert len(result.rows) == 2
    store_row = next(r for r in result.rows if r["venue"] == store.name)
    lot_row = next(r for r in result.rows if r["listing"] == "Grouped lot")
    assert store_row["status"] == f"Paused for {ebay.name} listing"
    assert lot_row["status"] == "Active"

    assert result.totals is not None
    # The lot's own sum already counts member (60.00) and other_member
    # (40.00) once each; the paused store row's separate 60.00 is excluded.
    assert result.totals["asking"] == Decimal("150.00")
    assert result.totals["cost_basis"] == Decimal("100.00")
    assert result.notes == [
        "1 paused listing left out of the totals: their item is on offer "
        "in another listing."
    ]

    counted = [
        r for r in result.rows if not cast("str", r["status"]).startswith("Paused for")
    ]
    assert (
        sum((cast("Decimal", r["asking"]) for r in counted), Decimal("0"))
        == (result.totals["asking"])
    )
    assert (
        sum((cast("Decimal", r["cost_basis"]) for r in counted), Decimal("0"))
        == (result.totals["cost_basis"])
    )


# ---------------------------------------------------------------------------
# Drills
# ---------------------------------------------------------------------------


def test_drill_for_a_coin_listing_is_its_editor(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    item = make_item(title="Coin")
    _item_listing(db, item, venue, title="Coin")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    idx = next(i for i, r in enumerate(result.rows) if r["listing"] == "Coin")
    assert result.drills[idx] == f"/inventory/coins?item={item.item_code}"


def test_drill_for_a_currency_listing_is_its_editor(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    item = make_item(kind="currency", title="Note")
    _item_listing(db, item, venue, title="Note")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    idx = next(i for i, r in enumerate(result.rows) if r["listing"] == "Note")
    assert result.drills[idx] == f"/inventory/currency?item={item.item_code}"


def test_drill_for_a_lot_listing_is_the_lots_page(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    lot = _lot(db)
    _lot_member(db, lot, make_item())
    _lot_listing(db, lot, venue, title="A lot")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    idx = next(i for i, r in enumerate(result.rows) if r["listing"] == "A lot")
    assert result.drills[idx] == "/lots"


# ===========================================================================
# sl_sales
# ===========================================================================


def _sold(
    db: Session,
    item: InventoryItem,
    venue: SalesVenue,
    admin_user: User,
    *,
    price: Decimal,
    fee: Decimal = Decimal("0"),
    buyer: str = "amy",
) -> SalesOrder:
    """Sell `item` on `venue` through the real writers: offer, then record."""
    listing = _offer_on(db, item, venue, price=price)
    fees = [FeeLine("commission", fee)] if fee else []
    return sales_writes.record_sale(
        db,
        listing,
        price=price,
        buyer_username=buyer,
        external_order_id=None,
        fees=fees,
        recorded_by=admin_user,
    )


def test_gross_fees_net_basis_and_gain_are_known_amounts(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue = _venue(db, "ebay")
    item = priced_item(make_item, "Coin", Decimal("40.00"))
    _sold(db, item, venue, admin_user, price=Decimal("133.75"), fee=Decimal("13.32"))
    db.commit()

    result = SL_SALES.run(db, SalesParams())
    assert result.rows[0]["orders"] == 1
    assert result.rows[0]["gross"] == Decimal("133.75")
    assert result.rows[0]["fees"] == Decimal("13.32")
    assert result.rows[0]["net"] == Decimal("120.43")
    assert result.rows[0]["cost_basis"] == Decimal("40.00")
    assert result.rows[0]["gain"] == Decimal("80.43")


def test_rows_are_grouped_by_month_and_venue(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue_a = _venue(db, "avenue")
    venue_b = _venue(db, "bvenue")
    order_a = _sold(
        db,
        priced_item(make_item, "A", Decimal("10.00")),
        venue_a,
        admin_user,
        price=Decimal("20.00"),
    )
    order_a.placed_at = _at_noon(date(2026, 1, 15))
    order_b = _sold(
        db,
        priced_item(make_item, "B", Decimal("10.00")),
        venue_b,
        admin_user,
        price=Decimal("30.00"),
    )
    order_b.placed_at = _at_noon(date(2026, 1, 15))
    order_c = _sold(
        db,
        priced_item(make_item, "C", Decimal("10.00")),
        venue_a,
        admin_user,
        price=Decimal("40.00"),
    )
    order_c.placed_at = _at_noon(date(2026, 2, 15))
    db.commit()

    result = SL_SALES.run(db, SalesParams())
    keys = [(r["period"], r["venue"]) for r in result.rows]
    assert keys == [
        ("2026-01", venue_a.name),
        ("2026-01", venue_b.name),
        ("2026-02", venue_a.name),
    ]


@pytest.mark.parametrize("status_code", ["cancelled", "refunded"])
def test_a_cancelled_or_refunded_order_is_excluded(
    db: Session, make_item: ItemFactory, admin_user: User, status_code: str
) -> None:
    """A refund is not a sale: the money went back.

    Neither a refunded order's returned money nor a cancelled
    order's never-happened sale counts here -- the same exclusion, tested
    with one parametrized case each.
    """
    venue = _venue(db, "ebay")
    buyer = venue_buyer(db, venue, "amy")
    item = priced_item(make_item, "Excluded item", Decimal("10.00"))
    listing = _offer_on(db, item, venue, price=Decimal("50.00"))
    order_writes.place_order(
        db,
        buyer,
        [Line(listing_id=listing.id, quantity=1, unit_price=Decimal("50.00"))],
        admin_user,
        venue=venue,
        status_code=status_code,
    )
    db.commit()

    result = SL_SALES.run(db, SalesParams())
    assert result.rows == []


def test_excluded_order_count_names_how_many_were_left_out(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue = _venue(db, "ebay")
    buyer = venue_buyer(db, venue, "amy")
    for status_code in ("cancelled", "refunded"):
        listing = _offer_on(
            db,
            priced_item(make_item, status_code, Decimal("10.00")),
            venue,
            price=Decimal("50.00"),
        )
        order_writes.place_order(
            db,
            buyer,
            [Line(listing_id=listing.id, quantity=1, unit_price=Decimal("50.00"))],
            admin_user,
            venue=venue,
            status_code=status_code,
        )
    db.commit()

    result = SL_SALES.run(db, SalesParams())
    assert result.rows == []
    assert any("2 cancelled or refunded orders" in note for note in result.notes), (
        result.notes
    )


def test_gain_is_the_sum_of_its_items_own_gain(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    """Two items on one lot sale, split unevenly: order gain equals their sum.

    Specific identification (spec Decisions): each item's gain is its own
    share (`amount - fee_amount`) less its own `total_cost`, and the order's
    reported `gain` must be exactly the two added together, not a separate
    `total_amount`-based figure that could drift from them.
    """
    venue = _venue(db, "ebay")
    lot = lot_writes.create_lot(db, title="Two coins", description="")
    a = priced_item(make_item, "A", Decimal("30.00"))
    b = priced_item(make_item, "B", Decimal("70.00"))
    lot_writes.add_member(db, lot, a)
    lot_writes.add_member(db, lot, b)
    listing = offering_writes.offer(
        db,
        lot=lot,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("150.00"),
        title="Two coins",
        description="",
        external_id=None,
    )
    sales_writes.record_sale(
        db,
        listing,
        price=Decimal("150.00"),
        buyer_username="amy",
        external_order_id=None,
        fees=[FeeLine("commission", Decimal("15.00"))],
        recorded_by=admin_user,
    )
    db.commit()

    result = SL_SALES.run(db, SalesParams())
    # a: 30% of 150.00 = 45.00, 30% of 15.00 fee = 4.50, gain 45-4.5-30 = 10.50
    # b: 70% of 150.00 = 105.00, 70% of 15.00 fee = 10.50, gain 105-10.5-70 = 24.50
    assert result.rows[0]["gain"] == Decimal("10.50") + Decimal("24.50")
    assert result.rows[0]["gain"] == Decimal("35.00")


def test_an_odd_cent_three_way_split_still_sums_exactly(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    """100.00 split three equal ways leaves a leftover cent (`allocate`).

    It has to land on one share or another rather than vanish, and the
    row's own `gross`/`cost_basis`/`gain` -- summed in SQL and Python over
    the shares that actually exist -- must still land on the whole exact
    figure regardless of which share it landed on.
    """
    venue = _venue(db, "ebay")
    lot = lot_writes.create_lot(db, title="Three coins", description="")
    a = priced_item(make_item, "A", Decimal("10.00"))
    b = priced_item(make_item, "B", Decimal("10.00"))
    c = priced_item(make_item, "C", Decimal("10.00"))
    lot_writes.add_member(db, lot, a)
    lot_writes.add_member(db, lot, b)
    lot_writes.add_member(db, lot, c)
    listing = offering_writes.offer(
        db,
        lot=lot,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("100.00"),
        title="Three coins",
        description="",
        external_id=None,
    )
    sales_writes.record_sale(
        db,
        listing,
        price=Decimal("100.00"),
        buyer_username="amy",
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )
    db.commit()

    # The leftover cent actually landed somewhere -- not an equal 33.33
    # three ways, which would lose a penny of the 100.00 it came from.
    shares = db.scalars(
        select(SalesOrderItemShare.amount)
        .join(
            SalesOrderItem, SalesOrderItem.id == SalesOrderItemShare.sales_order_item_id
        )
        .where(SalesOrderItem.listing_id == listing.id)
        .order_by(SalesOrderItemShare.amount)
    ).all()
    assert shares == [Decimal("33.33"), Decimal("33.33"), Decimal("33.34")]

    result = SL_SALES.run(db, SalesParams())
    assert result.rows[0]["gross"] == Decimal("100.00")
    assert result.rows[0]["cost_basis"] == Decimal("30.00")
    assert result.rows[0]["net"] == Decimal("100.00")
    assert result.rows[0]["gain"] == Decimal("70.00")
    assert result.totals is not None
    assert result.totals["gross"] == Decimal("100.00")


def test_a_deleted_items_share_is_excluded_from_every_figure(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue = _venue(db, "ebay")
    lot = lot_writes.create_lot(db, title="One live one deleted", description="")
    live = priced_item(make_item, "Live", Decimal("50.00"))
    deleted = priced_item(make_item, "Deleted", Decimal("50.00"))
    lot_writes.add_member(db, lot, live)
    lot_writes.add_member(db, lot, deleted)
    listing = offering_writes.offer(
        db,
        lot=lot,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("100.00"),
        title="Lot",
        description="",
        external_id=None,
    )
    sales_writes.record_sale(
        db,
        listing,
        price=Decimal("100.00"),
        buyer_username="amy",
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )
    deleted.deleted_at = utcnow()
    db.commit()

    result = SL_SALES.run(db, SalesParams())
    assert result.rows[0]["gross"] == Decimal("50.00")
    assert result.rows[0]["cost_basis"] == Decimal("50.00")
    assert result.rows[0]["gain"] == Decimal("0.00")


def test_a_split_items_share_is_excluded_from_every_figure(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue = _venue(db, "ebay")
    lot = lot_writes.create_lot(db, title="One live one split", description="")
    live = priced_item(make_item, "Live", Decimal("50.00"))
    split = priced_item(make_item, "Split", Decimal("50.00"))
    lot_writes.add_member(db, lot, live)
    lot_writes.add_member(db, lot, split)
    listing = offering_writes.offer(
        db,
        lot=lot,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("100.00"),
        title="Lot",
        description="",
        external_id=None,
    )
    sales_writes.record_sale(
        db,
        listing,
        price=Decimal("100.00"),
        buyer_username="amy",
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )
    split.split_at = utcnow()
    db.commit()

    result = SL_SALES.run(db, SalesParams())
    assert result.rows[0]["gross"] == Decimal("50.00")
    assert result.rows[0]["cost_basis"] == Decimal("50.00")


def test_date_range_excludes_orders_outside_it(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue = _venue(db, "ebay")
    order = _sold(
        db,
        priced_item(make_item, "Item", Decimal("10.00")),
        venue,
        admin_user,
        price=Decimal("20.00"),
    )
    order.placed_at = _at_noon(date(2026, 6, 15))
    db.commit()

    in_range = SL_SALES.run(
        db, SalesParams(date_from=date(2026, 6, 1), date_to=date(2026, 6, 30))
    )
    out_of_range = SL_SALES.run(
        db, SalesParams(date_from=date(2026, 7, 1), date_to=date(2026, 7, 31))
    )
    assert len(in_range.rows) == 1
    assert out_of_range.rows == []


def test_totals_equal_the_sum_of_the_rows(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue_a = _venue(db, "avenue")
    venue_b = _venue(db, "bvenue")
    _sold(
        db,
        priced_item(make_item, "A", Decimal("10.00")),
        venue_a,
        admin_user,
        price=Decimal("25.00"),
        fee=Decimal("2.50"),
    )
    _sold(
        db,
        priced_item(make_item, "B", Decimal("20.00")),
        venue_b,
        admin_user,
        price=Decimal("45.00"),
        fee=Decimal("4.50"),
    )
    db.commit()

    result = SL_SALES.run(db, SalesParams())
    assert result.totals is not None
    assert result.totals["orders"] == sum(cast(int, r["orders"]) for r in result.rows)
    for key in ("gross", "fees", "net", "cost_basis", "gain"):
        assert result.totals[key] == sum(
            (cast(Decimal, r[key]) for r in result.rows), Decimal("0")
        )
    assert result.drills == ["/sales"] * len(result.rows)


def test_note_names_which_statuses_count(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue = _venue(db, "ebay")
    _sold(
        db,
        priced_item(make_item, "Item", Decimal("10.00")),
        venue,
        admin_user,
        price=Decimal("20.00"),
    )
    db.commit()

    result = SL_SALES.run(db, SalesParams())
    assert any(
        "Pending" in note and "Paid" in note and "Delivered" in note
        for note in result.notes
    )
    assert not any("Cancelled" in note or "Refunded" in note for note in result.notes)


def test_no_sales_in_range_returns_no_totals(db: Session) -> None:
    result = SL_SALES.run(db, SalesParams())
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []


# ===========================================================================
# sl_fulfilment
# ===========================================================================


def _placed(
    db: Session,
    item: InventoryItem,
    venue: SalesVenue,
    admin_user: User,
    *,
    price: Decimal,
    status_code: str,
    buyer_name: str = "amy",
) -> SalesOrder:
    """Place an order for `item` on `venue`, at `status_code`, unshipped or not."""
    buyer = venue_buyer(db, venue, buyer_name)
    listing = _offer_on(db, item, venue, price=price)
    return order_writes.place_order(
        db,
        buyer,
        [Line(listing_id=listing.id, quantity=1, unit_price=price)],
        admin_user,
        venue=venue,
        status_code=status_code,
    )


def test_fulfilment_row_values_are_known(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue = _venue(db, "ebay")
    order = _placed(
        db,
        priced_item(make_item, "Coin", Decimal("40.00")),
        venue,
        admin_user,
        price=Decimal("133.75"),
        status_code="paid",
        buyer_name="amy",
    )
    order.placed_at = _at_noon(date.today() - timedelta(days=5))
    db.commit()

    result = SL_FULFILMENT.run(db, FulfilmentParams())
    row = result.rows[0]
    assert row["order"] == f"#{order.id}"
    assert row["customer"] == "amy"
    assert row["items"] == 1
    assert row["amount"] == Decimal("133.75")
    assert row["days_waiting"] == 5
    assert result.drills == ["/sales"] * len(result.rows)


@pytest.mark.parametrize(
    "status_code", ["shipped", "delivered", "cancelled", "refunded"]
)
def test_shipped_delivered_cancelled_and_refunded_orders_are_excluded(
    db: Session, make_item: ItemFactory, admin_user: User, status_code: str
) -> None:
    venue = _venue(db, "ebay")
    _placed(
        db,
        priced_item(make_item, "Item", Decimal("10.00")),
        venue,
        admin_user,
        price=Decimal("20.00"),
        status_code=status_code,
    )
    db.commit()

    result = SL_FULFILMENT.run(db, FulfilmentParams())
    assert result.rows == []


@pytest.mark.parametrize("status_code", ["pending", "paid", "packed"])
def test_pending_paid_and_packed_orders_are_included(
    db: Session, make_item: ItemFactory, admin_user: User, status_code: str
) -> None:
    venue = _venue(db, "ebay")
    _placed(
        db,
        priced_item(make_item, "Item", Decimal("10.00")),
        venue,
        admin_user,
        price=Decimal("20.00"),
        status_code=status_code,
    )
    db.commit()

    result = SL_FULFILMENT.run(db, FulfilmentParams())
    assert len(result.rows) == 1


def test_oldest_first(db: Session, make_item: ItemFactory, admin_user: User) -> None:
    venue = _venue(db, "ebay")
    newer = _placed(
        db,
        priced_item(make_item, "Newer", Decimal("10.00")),
        venue,
        admin_user,
        price=Decimal("20.00"),
        status_code="paid",
        buyer_name="amy",
    )
    newer.placed_at = _at_noon(date.today() - timedelta(days=1))
    older = _placed(
        db,
        priced_item(make_item, "Older", Decimal("10.00")),
        venue,
        admin_user,
        price=Decimal("20.00"),
        status_code="paid",
        buyer_name="bo",
    )
    older.placed_at = _at_noon(date.today() - timedelta(days=10))
    db.commit()

    result = SL_FULFILMENT.run(db, FulfilmentParams())
    assert [r["order"] for r in result.rows] == [f"#{older.id}", f"#{newer.id}"]


def test_a_deleted_items_share_does_not_count_toward_items(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue = _venue(db, "ebay")
    buyer = venue_buyer(db, venue, "amy")
    lot = lot_writes.create_lot(db, title="Two coins", description="")
    live = priced_item(make_item, "Live", Decimal("10.00"))
    deleted = priced_item(make_item, "Deleted", Decimal("10.00"))
    lot_writes.add_member(db, lot, live)
    lot_writes.add_member(db, lot, deleted)
    listing = offering_writes.offer(
        db,
        lot=lot,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("50.00"),
        title="Lot",
        description="",
        external_id=None,
    )
    order = order_writes.place_order(
        db,
        buyer,
        [Line(listing_id=listing.id, quantity=1, unit_price=Decimal("50.00"))],
        admin_user,
        venue=venue,
        status_code="paid",
    )
    deleted.deleted_at = utcnow()
    db.commit()

    result = SL_FULFILMENT.run(db, FulfilmentParams())
    row = next(r for r in result.rows if r["order"] == f"#{order.id}")
    assert row["items"] == 1
    assert row["amount"] == Decimal("50.00")
    assert result.notes == [
        "1 deleted or split item is left out of its order's item count."
    ]


def test_no_left_out_note_when_nothing_was_left_out(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    _placed(
        db,
        priced_item(make_item, "Item", Decimal("10.00")),
        _venue(db, "ebay"),
        admin_user,
        price=Decimal("20.00"),
        status_code="paid",
    )
    db.commit()

    result = SL_FULFILMENT.run(db, FulfilmentParams())
    assert len(result.rows) == 1
    assert result.notes == []


def test_fulfilment_totals_amount_is_the_sum_of_the_rows(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    venue = _venue(db, "ebay")
    _placed(
        db,
        priced_item(make_item, "A", Decimal("10.00")),
        venue,
        admin_user,
        price=Decimal("25.00"),
        status_code="paid",
        buyer_name="amy",
    )
    _placed(
        db,
        priced_item(make_item, "B", Decimal("20.00")),
        venue,
        admin_user,
        price=Decimal("45.00"),
        status_code="pending",
        buyer_name="bo",
    )
    db.commit()

    result = SL_FULFILMENT.run(db, FulfilmentParams())
    assert result.totals is not None
    assert len(result.rows) == 2  # orders count is the row count
    assert result.totals["amount"] == sum(
        (cast(Decimal, r["amount"]) for r in result.rows), Decimal("0")
    )
    assert result.totals["items"] == sum(cast(int, r["items"]) for r in result.rows)


def test_nothing_waiting_to_ship_returns_no_totals(db: Session) -> None:
    result = SL_FULFILMENT.run(db, FulfilmentParams())
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []


# ===========================================================================
# sl_aging
# ===========================================================================


def _received(db: Session, item: InventoryItem, *, arrived_on: date) -> None:
    """Move `item` through a real `ordered` -> `received` transition."""
    item.status_id = code_id(db, ItemStatus, "ordered")
    db.flush()
    set_status(db, item, code_id(db, ItemStatus, "received"), arrived_on=arrived_on)


def test_bucket_by_months_since_received(db: Session, make_item: ItemFactory) -> None:
    item = priced_item(make_item, "Aged", Decimal("50.00"))
    _received(db, item, arrived_on=_months_ago(8))
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert result.rows[0]["age"] == "6-11"
    assert result.rows[0]["items"] == 1
    assert result.rows[0]["total_cost"] == Decimal("50.00")


def test_months_since_boundaries_are_exact() -> None:
    """Boundary arithmetic on the two private helpers `_sl_aging` buckets with.

    5 months is still "0-5", 6 is already "6-11"; 11 is still "6-11", 12 is
    already "12-23". Tested directly against `_months_since`/`_age_bucket`
    rather than through a full report run, since nothing here can hold
    `date.today()` still.
    """
    assert _months_since(date(2026, 1, 1), date(2026, 6, 1)) == 5
    assert _age_bucket(_months_since(date(2026, 1, 1), date(2026, 6, 1))) == "0-5"
    assert _months_since(date(2026, 1, 1), date(2026, 7, 1)) == 6
    assert _age_bucket(_months_since(date(2026, 1, 1), date(2026, 7, 1))) == "6-11"
    assert _months_since(date(2025, 1, 1), date(2025, 12, 1)) == 11
    assert _age_bucket(_months_since(date(2025, 1, 1), date(2025, 12, 1))) == "6-11"
    assert _months_since(date(2025, 1, 1), date(2026, 1, 1)) == 12
    assert _age_bucket(_months_since(date(2025, 1, 1), date(2026, 1, 1))) == "12-23"


def test_months_since_rounds_down_before_the_day_of_month_is_reached() -> None:
    """Received the 20th, "today" the 10th, six calendar months later.

    Only 5 whole months have actually passed, not 6: `_months_since` must
    read the day of month, not just subtract year/month fields.
    """
    assert _months_since(date(2026, 1, 20), date(2026, 7, 10)) == 5


def test_buckets_are_ordered_youngest_first(
    db: Session, make_item: ItemFactory
) -> None:
    older = priced_item(make_item, "Older", Decimal("10.00"))
    _received(db, older, arrived_on=_months_ago(8))
    newer = priced_item(make_item, "Newer", Decimal("10.00"))
    _received(db, newer, arrived_on=_months_ago(2))
    also_old = priced_item(make_item, "Also old", Decimal("10.00"))
    _received(db, also_old, arrived_on=_months_ago(30))
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    ages = [r["age"] for r in result.rows]
    assert ages.index("0-5") < ages.index("6-11") < ages.index("24+")


def test_no_receipt_transition_is_unknown(db: Session, make_item: ItemFactory) -> None:
    priced_item(make_item, "No history", Decimal("25.00"))
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert result.rows[0]["age"] == "Unknown"
    assert any("no recorded receipt" in note for note in result.notes)
    assert any("which is not an arrival" in note for note in result.notes)


def test_kinds_within_a_bucket_are_ordered_by_sort_order(
    db: Session, make_item: ItemFactory
) -> None:
    make_item(
        kind="currency", title="Note", item_cost=Decimal("15.00"), tax_rate=Decimal("0")
    )
    make_item(
        kind="coin", title="Coin", item_cost=Decimal("15.00"), tax_rate=Decimal("0")
    )
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    kinds = [r["kind"] for r in result.rows]
    assert kinds.index("Coin") < kinds.index("Currency")


def test_an_item_on_an_active_listing_is_excluded(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    item = priced_item(make_item, "Listed", Decimal("50.00"))
    _item_listing(db, item, venue, status=ListingStatus.active)
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert result.rows == []


def test_an_item_on_a_paused_listing_is_excluded(
    db: Session, make_item: ItemFactory
) -> None:
    venue = _venue(db, "ebay")
    item = priced_item(make_item, "Paused", Decimal("50.00"))
    _item_listing(db, item, venue, status=ListingStatus.paused)
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert result.rows == []


def test_an_item_in_an_open_lot_is_excluded(
    db: Session, make_item: ItemFactory
) -> None:
    lot = _lot(db)
    item = priced_item(make_item, "In lot", Decimal("50.00"))
    _lot_member(db, lot, item)
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert result.rows == []


def test_a_released_lot_membership_does_not_exclude_the_item(
    db: Session, make_item: ItemFactory
) -> None:
    lot = _lot(db)
    item = priced_item(make_item, "Released", Decimal("50.00"))
    _lot_member(db, lot, item, released=True)
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert len(result.rows) == 1


def test_a_deleted_item_is_excluded(db: Session, make_item: ItemFactory) -> None:
    item = priced_item(make_item, "Deleted", Decimal("50.00"))
    item.deleted_at = utcnow()
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert result.rows == []


def test_a_split_parent_item_is_excluded(db: Session, make_item: ItemFactory) -> None:
    item = priced_item(make_item, "Split", Decimal("50.00"))
    item.split_at = utcnow()
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert result.rows == []


def test_a_non_received_or_non_held_item_is_excluded(
    db: Session, make_item: ItemFactory
) -> None:
    make_item(
        status_id=code_id(db, ItemStatus, "ordered"),
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
    )
    make_item(
        disposition_id=code_id(db, Disposition, "sold"),
        item_cost=Decimal("10.00"),
        tax_rate=Decimal("0"),
    )
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert result.rows == []


def test_a_split_childs_age_comes_from_its_split_parents_receipt(
    db: Session, make_item: ItemFactory
) -> None:
    parent = priced_item(make_item, "Parent", Decimal("30.00"))
    _received(db, parent, arrived_on=_months_ago(8))
    child = priced_item(make_item, "Child", Decimal("20.00"))
    child.parent_item_id = parent.id
    parent.split_at = utcnow()
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert len(result.rows) == 1
    assert result.rows[0]["age"] == "6-11"
    assert result.rows[0]["items"] == 1
    assert result.rows[0]["total_cost"] == Decimal("20.00")


def test_aging_totals_equal_the_sum_of_the_rows(
    db: Session, make_item: ItemFactory
) -> None:
    priced_item(make_item, "A", Decimal("10.00"))
    priced_item(make_item, "B", Decimal("20.00"))
    db.commit()

    result = SL_AGING.run(db, AgingParams())
    assert result.totals is not None
    assert result.totals["items"] == sum(cast(int, r["items"]) for r in result.rows)
    assert result.totals["total_cost"] == sum(
        (cast(Decimal, r["total_cost"]) for r in result.rows), Decimal("0")
    )
    assert result.drills == [None] * len(result.rows)


def test_the_receipt_lookup_binds_no_parameter_per_held_item(
    db: Session, make_item: ItemFactory
) -> None:
    """At live scale (~8,000 held items) an expanded `IN` list would bind each id.

    Measured on the statements actually sent: the parameter count of every
    one stays the same with 2 held items as with 12.
    """

    def _max_params() -> int:
        """The most parameters any one statement binds while the aging report runs."""
        counts: list[int] = []

        def _record(
            conn: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            """Keep each statement's bound-parameter count."""
            counts.append(len(parameters) if isinstance(parameters, dict) else 0)

        bind = db.get_bind()
        event.listen(bind, "before_cursor_execute", _record)
        try:
            SL_AGING.run(db, AgingParams())
        finally:
            event.remove(bind, "before_cursor_execute", _record)
        return max(counts)

    for index in range(2):
        priced_item(make_item, f"Held {index}", Decimal("1.00"))
    db.commit()
    few = _max_params()
    for index in range(10):
        priced_item(make_item, f"More {index}", Decimal("1.00"))
    db.commit()
    assert _max_params() == few


def test_nothing_held_returns_no_totals(db: Session) -> None:
    result = SL_AGING.run(db, AgingParams())
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []
    assert result.notes == ["Nothing is held and not offered."]


def test_the_unknown_note_agrees_in_number_and_gives_its_reason(
    db: Session, make_item: ItemFactory
) -> None:
    priced_item(make_item, "No history", Decimal("25.00"))
    received = priced_item(make_item, "Received", Decimal("5.00"))
    _received(db, received, arrived_on=_months_ago(2))
    db.commit()

    (note,) = SL_AGING.run(db, AgingParams()).notes
    assert note.startswith("1 item of 2 has no recorded receipt and is bucketed")
    assert "which is not an arrival" in note
    assert "most" not in note

    priced_item(make_item, "Also no history", Decimal("25.00"))
    db.commit()
    (note,) = SL_AGING.run(db, AgingParams()).notes
    assert note.startswith("2 items of 3 have no recorded receipt and are bucketed")


# ===========================================================================
# sl_auctions
# ===========================================================================


def test_a_draft_auction_shows_no_figures(
    db: Session, heritage_venue: SalesVenue
) -> None:
    build_auction(db, heritage_venue, title="Draft sale")
    db.commit()

    result = SL_AUCTIONS.run(db, AuctionsParams())
    row = next(r for r in result.rows if r["auction"] == "Draft sale")
    assert row["status"] == "Draft"
    assert row["lots"] is None
    assert row["sold"] is None
    assert row["unsold"] is None
    assert row["hammer_total"] is None
    assert row["fees"] is None


def test_a_settled_auctions_figures_are_known(
    db: Session, heritage_venue: SalesVenue, make_item: ItemFactory, admin_user: User
) -> None:
    auction = build_auction(db, heritage_venue, title="September sale")
    lots = [
        add_lot(
            db,
            auction,
            priced_item(make_item, f"Lot {n}", Decimal("100.00")),
            lot_number=str(n),
            reserve=None,
            price=Decimal("10.00"),
        )
        for n in range(1, 4)
    ]
    schedule(db, auction)
    close(db, auction)
    settle(
        db,
        auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.sold, Decimal("150.00"), "amy"),
            SettlementLine(lots[1].id, AuctionLotResult.sold, Decimal("250.00"), "amy"),
            SettlementLine(lots[2].id, AuctionLotResult.unsold),
        ],
        fees={"amy": [FeeLine("commission", Decimal("40.00"))]},
        settled_by=admin_user,
    )
    db.commit()

    result = SL_AUCTIONS.run(db, AuctionsParams())
    row = next(r for r in result.rows if r["auction"] == "September sale")
    assert row["status"] == "Settled"
    assert row["lots"] == 3
    assert row["sold"] == 2
    assert row["unsold"] == 1
    assert row["hammer_total"] == Decimal("400.00")
    assert row["fees"] == Decimal("40.00")


def test_unsold_includes_a_withdrawn_lot(
    db: Session, heritage_venue: SalesVenue, make_item: ItemFactory, admin_user: User
) -> None:
    auction = build_auction(db, heritage_venue, title="Mixed sale")
    lots = [
        add_lot(
            db,
            auction,
            priced_item(make_item, f"L{n}", Decimal("50.00")),
            lot_number=str(n),
            reserve=None,
            price=Decimal("5.00"),
        )
        for n in range(1, 3)
    ]
    schedule(db, auction)
    close(db, auction)
    settle(
        db,
        auction,
        lines=[
            SettlementLine(lots[0].id, AuctionLotResult.unsold),
            SettlementLine(lots[1].id, AuctionLotResult.withdrawn),
        ],
        fees={},
        settled_by=admin_user,
    )
    db.commit()

    result = SL_AUCTIONS.run(db, AuctionsParams())
    row = next(r for r in result.rows if r["auction"] == "Mixed sale")
    assert row["sold"] == 0
    assert row["unsold"] == 2
    assert row["hammer_total"] == Decimal("0")
    assert row["fees"] == Decimal("0")


def test_auction_rows_are_ordered_by_status(
    db: Session, heritage_venue: SalesVenue
) -> None:
    settled = build_auction(db, heritage_venue, title="Settled one")
    settled.status = AuctionStatus.settled
    scheduled = build_auction(db, heritage_venue, title="Scheduled one")
    scheduled.status = AuctionStatus.scheduled
    build_auction(db, heritage_venue, title="Draft one")
    db.commit()

    result = SL_AUCTIONS.run(db, AuctionsParams())
    order = [r["auction"] for r in result.rows]
    assert (
        order.index("Draft one")
        < order.index("Scheduled one")
        < order.index("Settled one")
    )


def test_auction_drill_is_the_auctions_page(
    db: Session, heritage_venue: SalesVenue
) -> None:
    build_auction(db, heritage_venue, title="Any sale")
    db.commit()

    result = SL_AUCTIONS.run(db, AuctionsParams())
    assert result.drills == ["/auctions"] * len(result.rows)


def test_no_auctions_returns_no_totals(db: Session) -> None:
    result = SL_AUCTIONS.run(db, AuctionsParams())
    assert result.rows == []
    assert result.totals is None
    assert result.drills == []
    assert result.notes == ["No auctions recorded."]
