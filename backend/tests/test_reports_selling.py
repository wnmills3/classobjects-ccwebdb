"""`sl_offered`: active and paused listings, asking price against cost basis."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast

from app.models import (
    Currency,
    InventoryItem,
    Listing,
    ListingStatus,
    SalesLot,
    SalesLotItem,
    SalesLotStatus,
    SalesVenue,
    SalesVenueKind,
    utcnow,
)
from app.reports.selling import SL_OFFERED, OfferedParams
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, code_id, priced_item

_NOW = datetime(2026, 9, 28, tzinfo=UTC)


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

    Anchored to `datetime.now().astimezone()` -- the current instant in the
    system's own local zone -- rather than UTC midnight of a calendar date:
    `listed_at` is `timestamptz`, and the database session may hand it back
    tagged with a zone offset from UTC (this machine's, `America/New_York`,
    measured directly). Subtracting whole days from "now, in local time"
    keeps the same local calendar offset from `date.today()` that
    `_local_date` is meant to measure; anchoring to UTC midnight instead
    would land on the wrong side of local midnight and be off by a day,
    which is exactly the failure this test caught before `_local_date`
    existed.
    """
    venue = _venue(db, "ebay")
    seven_days_ago = datetime.now().astimezone() - timedelta(days=7)
    _item_listing(db, make_item(), venue, listed_at=seven_days_ago, title="Week old")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    row = next(r for r in result.rows if r["listing"] == "Week old")
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
    assert any("2" in note for note in result.notes)


def test_no_non_usd_listings_adds_no_note(db: Session, make_item: ItemFactory) -> None:
    venue = _venue(db, "ebay")
    _item_listing(db, make_item(), venue, currency="USD")
    db.commit()

    result = SL_OFFERED.run(db, OfferedParams())
    assert result.notes == []


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
