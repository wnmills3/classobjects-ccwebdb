"""Selling: what is on offer and what has sold.

One report so far, `sl_offered`: every active or paused listing -- an item
or a sales lot -- with its venue, its asking price against its cost basis,
and how long it has been offered. An item listing's cost basis is its own
`total_cost`; a lot listing's is the SQL sum of `total_cost` over the lot's
current, live members -- `SalesLotItem.released_at IS NULL` (the same "still
in the lot" test `lot_writes.open_members` reads) and `live_item()` (no
deleted or split-parent member) -- summed in the database, the same
discipline `cb_holdings` uses, so a lot's own total can never drift from
what its rows actually add up to.

A coin can be named by two rows at once: `offering_writes.offer()` pauses
an item's own store listing when that same item is offered elsewhere or
grouped into a lot (`Listing.paused_by_listing_id`), and both the paused
row and the listing that paused it stay live here, each naming the item at
its own price and cost. Both rows are shown -- the owner should still see
that the store listing exists -- but a paused row whose
`paused_by_listing_id` points to a listing that is itself still offered
(active or paused) is left out of both totals, so the coin is counted
once, not twice; its `status` cell names the listing that set it aside
rather than reading a bare "Paused". That test is read entirely from the
data -- `paused_by_listing_id`'s own status -- never by comparing which
item two rows happen to name, which a lot listing (no `inventory_item_id`
of its own) could not support anyway.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import cast
from urllib.parse import urlencode

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session, aliased

from ..live import live_item
from ..models import (
    Currency,
    InventoryItem,
    ItemKind,
    Listing,
    ListingStatus,
    SalesLot,
    SalesLotItem,
    SalesVenue,
)
from ..offering_writes import OFFER_CURRENCY, ON_OFFER
from .base import Column, Report, ReportResult
from .registry import register

__all__ = ["SL_OFFERED", "OfferedParams"]

_STATUS_LABELS: dict[ListingStatus, str] = {
    ListingStatus.active: "Active",
    ListingStatus.paused: "Paused",
}

_COLUMNS = [
    Column("venue", "Venue", "text"),
    Column("listing", "Listing", "text"),
    Column("offers", "Offers", "text"),
    Column("status", "Status", "text"),
    Column("asking", "Asking", "money"),
    Column("currency", "Currency", "text"),
    Column("cost_basis", "Cost basis", "money"),
    Column("days_listed", "Days listed", "count"),
]

#: The listing a paused row's `paused_by_listing_id` names, and that
#: listing's own venue -- joined by both `_item_rows` and `_lot_rows` so a
#: paused row can read what set it aside without a second query per row.
_PAUSED_BY = aliased(Listing)
_PAUSED_BY_VENUE = aliased(SalesVenue)

#: A sort key, a row, its drill, and whether it is excluded from the
#: report's totals -- what `_item_rows` and `_lot_rows` each build, before
#: they are merged and sorted together in `_sl_offered`.
_SortKey = tuple[str, object, int]
_Entry = tuple[_SortKey, dict[str, object], str, bool]


def _local_date(moment: datetime) -> date:
    """`moment`'s calendar date in the system's own local time zone.

    `listed_at` is `timestamptz`; the driver hands it back tagged with
    whatever zone the *database session* is in, which need not be the
    zone the application server itself runs in. `today` (`date.today()`,
    in `_sl_offered`) is the system's own local date, so comparing it
    against `listed_at`'s date verbatim -- in whatever zone the driver
    happened to tag it -- can land on the wrong side of midnight and be
    off by a day when the two zones disagree. `astimezone()` with no
    argument converts to the local zone first, the same zone
    `date.today()` reads from, so the two always agree about which
    calendar day a moment falls on regardless of the session's own zone.
    This is a portability guarantee, not a fix for a bug in this
    environment specifically -- see
    `test_local_date_uses_the_local_zone_not_the_session_zone`, which pins
    it by forcing the session to a different zone than the system's own.
    """
    return moment.astimezone().date()


def _status_text(status: ListingStatus, paused_by_venue_name: str | None) -> str:
    """The status cell: plain, or naming the listing that paused this one."""
    if status is ListingStatus.paused and paused_by_venue_name is not None:
        return f"Paused for {paused_by_venue_name} listing"
    return _STATUS_LABELS[status]


def _excluded_from_totals(
    status: ListingStatus, paused_by_status: ListingStatus | None
) -> bool:
    """Whether this row's item is already counted by the listing that paused it.

    True exactly when this row is `paused` *and* the listing named by its
    `paused_by_listing_id` is itself still on offer (`active` or `paused`)
    -- read from that listing's own status, joined in SQL, never from
    comparing which item two rows happen to name.
    """
    return status is ListingStatus.paused and paused_by_status in ON_OFFER


def _item_drill(kind_code: str, item_code: str) -> str:
    """An item listing's console path: its own kind's search, opened on it."""
    path = "/inventory/currency" if kind_code == "currency" else "/inventory/coins"
    return f"{path}?{urlencode({'item': item_code})}"


def _item_rows(db: Session, today: date) -> list[_Entry]:
    """One entry per live item listing that is active or paused.

    `live_item()` sits in the `WHERE` clause, so a listing on a deleted or
    split-parent item is excluded outright -- a state neither `delete_item`
    nor `splitting` can actually produce for an offered item today, but this
    report reads the same shared predicate every other one does rather than
    trusting that to hold forever.
    """
    stmt = (
        select(
            Listing.id,
            Listing.title,
            Listing.price,
            Listing.status,
            Listing.listed_at,
            Currency.code.label("currency_code"),
            SalesVenue.name.label("venue_name"),
            InventoryItem.item_code,
            InventoryItem.description,
            InventoryItem.total_cost,
            ItemKind.code.label("kind_code"),
            _PAUSED_BY.status.label("paused_by_status"),
            _PAUSED_BY_VENUE.name.label("paused_by_venue_name"),
        )
        .select_from(Listing)
        .join(InventoryItem, InventoryItem.id == Listing.inventory_item_id)
        .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
        .join(SalesVenue, SalesVenue.id == Listing.sales_venue_id)
        .join(Currency, Currency.id == Listing.currency_id)
        .outerjoin(_PAUSED_BY, _PAUSED_BY.id == Listing.paused_by_listing_id)
        .outerjoin(_PAUSED_BY_VENUE, _PAUSED_BY_VENUE.id == _PAUSED_BY.sales_venue_id)
        .where(
            Listing.inventory_item_id.is_not(None),
            Listing.status.in_(ON_OFFER),
            live_item(),
        )
    )
    entries: list[_Entry] = []
    for row in db.execute(stmt).mappings().all():
        listed_at = row["listed_at"]
        status = row["status"]
        row_dict: dict[str, object] = {
            "venue": row["venue_name"],
            "listing": row["title"] or row["description"],
            "offers": row["item_code"],
            "status": _status_text(status, row["paused_by_venue_name"]),
            "asking": row["price"],
            "currency": row["currency_code"],
            "cost_basis": row["total_cost"],
            "days_listed": (today - _local_date(listed_at)).days,
        }
        drill = _item_drill(row["kind_code"], row["item_code"])
        excluded = _excluded_from_totals(status, row["paused_by_status"])
        entries.append(
            ((row["venue_name"], listed_at, row["id"]), row_dict, drill, excluded)
        )
    return entries


def _lot_rows(db: Session, today: date) -> list[_Entry]:
    """One entry per lot listing that is active or paused.

    `member_cost` is a correlated scalar subquery -- the SQL sum of a lot's
    open, live members' `total_cost` -- so *this row's own* cost basis is
    exactly what the database adds up over `sales_lot_item`. The report's
    overall totals are computed separately, in `_sl_offered`: an exact
    Python `Decimal` running sum over each row's own already-SQL-computed
    value, the same discipline `pr_outstanding`'s totals use -- not a
    second SQL aggregate, but not a source of drift either, since `Decimal`
    addition (unlike `float`) never rounds.
    """
    member_cost = (
        select(func.coalesce(func.sum(InventoryItem.total_cost), 0))
        .select_from(SalesLotItem)
        .join(InventoryItem, InventoryItem.id == SalesLotItem.inventory_item_id)
        .where(
            SalesLotItem.sales_lot_id == SalesLot.id,
            SalesLotItem.released_at.is_(None),
            live_item(),
        )
        .correlate(SalesLot)
        .scalar_subquery()
    )
    stmt = (
        select(
            Listing.id,
            Listing.title,
            Listing.price,
            Listing.status,
            Listing.listed_at,
            Currency.code.label("currency_code"),
            SalesVenue.name.label("venue_name"),
            SalesLot.title.label("lot_title"),
            member_cost.label("cost_basis"),
            _PAUSED_BY.status.label("paused_by_status"),
            _PAUSED_BY_VENUE.name.label("paused_by_venue_name"),
        )
        .select_from(Listing)
        .join(SalesLot, SalesLot.id == Listing.sales_lot_id)
        .join(SalesVenue, SalesVenue.id == Listing.sales_venue_id)
        .join(Currency, Currency.id == Listing.currency_id)
        .outerjoin(_PAUSED_BY, _PAUSED_BY.id == Listing.paused_by_listing_id)
        .outerjoin(_PAUSED_BY_VENUE, _PAUSED_BY_VENUE.id == _PAUSED_BY.sales_venue_id)
        .where(
            Listing.sales_lot_id.is_not(None),
            Listing.status.in_(ON_OFFER),
        )
    )
    entries: list[_Entry] = []
    for row in db.execute(stmt).mappings().all():
        listed_at = row["listed_at"]
        lot_title = row["lot_title"]
        status = row["status"]
        row_dict: dict[str, object] = {
            "venue": row["venue_name"],
            "listing": row["title"] or lot_title,
            "offers": f"Lot: {lot_title}",
            "status": _status_text(status, row["paused_by_venue_name"]),
            "asking": row["price"],
            "currency": row["currency_code"],
            "cost_basis": row["cost_basis"],
            "days_listed": (today - _local_date(listed_at)).days,
        }
        excluded = _excluded_from_totals(status, row["paused_by_status"])
        entries.append(
            ((row["venue_name"], listed_at, row["id"]), row_dict, "/lots", excluded)
        )
    return entries


class OfferedParams(BaseModel):
    """No parameters: every active or paused listing, every time."""


def _sl_offered(db: Session, _params: OfferedParams) -> ReportResult:
    """Active and paused listings, by venue and then oldest-listed first.

    "Today" is computed once in Python, as `pr_outstanding` does, rather
    than as SQL `CURRENT_DATE` -- the database server's clock and timezone
    are not necessarily the application's.
    """
    today = date.today()
    entries = _item_rows(db, today) + _lot_rows(db, today)
    entries.sort(key=lambda entry: entry[0])

    if not entries:
        return ReportResult(
            columns=_COLUMNS,
            rows=[],
            totals=None,
            drills=[],
            notes=["Nothing is on offer."],
        )

    rows = [entry[1] for entry in entries]
    drills: list[str | None] = [entry[2] for entry in entries]

    # Two independent reasons a row's asking/cost basis is left out of the
    # totals below, each counted and noted separately: `paused_excluded`
    # (its item is already counted by the listing that paused it) is
    # decided first and skips the row outright, so a paused-and-non-USD
    # row is never also counted toward `non_usd_excluded` -- it was never
    # a candidate for the asking total in the first place. Both running
    # sums are exact Python `Decimal` addition over each row's own
    # SQL-computed value, never a float and never re-queried.
    asking_total = Decimal("0")
    cost_total = Decimal("0")
    non_usd_excluded = 0
    paused_excluded = 0
    for entry in entries:
        row = entry[1]
        if entry[3]:
            paused_excluded += 1
            continue
        cost_total += cast("Decimal", row["cost_basis"])
        if row["currency"] == OFFER_CURRENCY:
            asking_total += cast("Decimal", row["asking"])
        else:
            non_usd_excluded += 1

    notes: list[str] = []
    if non_usd_excluded:
        plural = "s" if non_usd_excluded != 1 else ""
        notes.append(
            f"{non_usd_excluded} listing{plural} not priced in {OFFER_CURRENCY} "
            "excluded from the asking total."
        )
    if paused_excluded:
        plural = "s" if paused_excluded != 1 else ""
        notes.append(
            f"{paused_excluded} paused listing{plural} left out of the totals: "
            "their item is on offer in another listing."
        )

    totals: dict[str, object] = {
        "venue": "All listings",
        "listing": None,
        "offers": None,
        "status": None,
        "asking": asking_total,
        "currency": None,
        "cost_basis": cost_total,
        "days_listed": None,
    }

    return ReportResult(
        columns=_COLUMNS, rows=rows, totals=totals, drills=drills, notes=notes
    )


SL_OFFERED = register(
    Report(
        id="sl_offered",
        group="Selling",
        title="On offer",
        purpose="Active and paused listings, items and sales lots, by venue: "
        "asking price against cost basis, and days listed.",
        params=OfferedParams,
        run=_sl_offered,
    )
)
