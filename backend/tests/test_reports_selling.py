"""`sl_offered`: active and paused listings, asking price against cost basis."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from typing import cast

from app import lot_writes, offering_writes
from app.models import (
    Currency,
    InventoryItem,
    Listing,
    ListingFormat,
    ListingStatus,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesVenue,
    SalesVenueKind,
    utcnow,
)
from app.offering_writes import OFFER_CURRENCY
from app.reports.selling import SL_OFFERED, OfferedParams
from app.sales_venues import store_venue_id
from sqlalchemy import text
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, code_id, priced_item

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

    Released is not named in the task brief, but it is the same kind of
    fact as deleted: `released_at` means "no longer in this lot"
    (`lot_writes.open_members` reads it the same way), so a row this report
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
    it back tagged with a zone offset from UTC (this machine's,
    `America/New_York`, measured directly). Anchoring to UTC midnight
    instead would land on the wrong side of local midnight and be off by a
    day, which is exactly the failure this test caught before
    `local_date` existed.
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
    local one (measured directly on this machine, `America/New_York`), so a
    bare `.date()` with no conversion back to local would read the wrong
    day. `SET LOCAL` is scoped to the current transaction; `conftest.py`'s
    `db` fixture keeps one real transaction open for the whole test
    (`join_transaction_mode="create_savepoint"` makes `db.commit()` below
    only a savepoint release), so it is still in effect when the report
    itself reads the row back.

    Mutation-tested: removing `.astimezone()` from `local_date` (leaving a
    bare `.date()`) makes this fail with `days_listed == 6`, not 7 --
    confirmed by hand and reverted; see `task-5-report.md`.
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
