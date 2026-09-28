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
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import cast
from urllib.parse import urlencode

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

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
from .base import Column, Report, ReportResult
from .registry import register

__all__ = ["SL_OFFERED", "OfferedParams"]

#: The owner's own currency, and the one `cb_holdings`-style money figures
#: (cost basis) are always in. Named once so the asking total's filter and
#: its note agree by construction.
_USD = "USD"

_OFFERED_STATUSES = (ListingStatus.active, ListingStatus.paused)

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

#: A sort key, a row, and its drill -- what `_item_rows` and `_lot_rows` each
#: build, before they are merged and sorted together in `_sl_offered`.
_SortKey = tuple[str, object, int]
_Entry = tuple[_SortKey, dict[str, object], str]


def _local_date(moment: datetime) -> date:
    """`moment`'s calendar date in the system's own local time zone.

    `listed_at` is `timestamptz`; the driver hands it back tagged with
    whatever zone the database session is in, not necessarily UTC. `today`
    (`date.today()`, in `_sl_offered`) is the system's own local date, so
    comparing it against `listed_at`'s date verbatim -- in whatever zone the
    driver happened to tag it -- can land on the wrong side of midnight and
    be off by a day. `astimezone()` with no argument converts to the local
    zone first, the same zone `date.today()` reads from, so the two always
    agree about which calendar day a moment falls on.
    """
    return moment.astimezone().date()


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
        )
        .select_from(Listing)
        .join(InventoryItem, InventoryItem.id == Listing.inventory_item_id)
        .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
        .join(SalesVenue, SalesVenue.id == Listing.sales_venue_id)
        .join(Currency, Currency.id == Listing.currency_id)
        .where(
            Listing.inventory_item_id.is_not(None),
            Listing.status.in_(_OFFERED_STATUSES),
            live_item(),
        )
    )
    entries: list[_Entry] = []
    for row in db.execute(stmt).mappings().all():
        listed_at = row["listed_at"]
        row_dict: dict[str, object] = {
            "venue": row["venue_name"],
            "listing": row["title"] or row["description"],
            "offers": row["item_code"],
            "status": _STATUS_LABELS[row["status"]],
            "asking": row["price"],
            "currency": row["currency_code"],
            "cost_basis": row["total_cost"],
            "days_listed": (today - _local_date(listed_at)).days,
        }
        drill = _item_drill(row["kind_code"], row["item_code"])
        entries.append(((row["venue_name"], listed_at, row["id"]), row_dict, drill))
    return entries


def _lot_rows(db: Session, today: date) -> list[_Entry]:
    """One entry per lot listing that is active or paused.

    `member_cost` is a correlated scalar subquery -- the SQL sum of a lot's
    open, live members' `total_cost` -- so the cost basis is exactly what
    the database adds up over `sales_lot_item`, never a Python running total
    that could drift from it.
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
        )
        .select_from(Listing)
        .join(SalesLot, SalesLot.id == Listing.sales_lot_id)
        .join(SalesVenue, SalesVenue.id == Listing.sales_venue_id)
        .join(Currency, Currency.id == Listing.currency_id)
        .where(
            Listing.sales_lot_id.is_not(None),
            Listing.status.in_(_OFFERED_STATUSES),
        )
    )
    entries: list[_Entry] = []
    for row in db.execute(stmt).mappings().all():
        listed_at = row["listed_at"]
        lot_title = row["lot_title"]
        row_dict: dict[str, object] = {
            "venue": row["venue_name"],
            "listing": row["title"] or lot_title,
            "offers": f"Lot: {lot_title}",
            "status": _STATUS_LABELS[row["status"]],
            "asking": row["price"],
            "currency": row["currency_code"],
            "cost_basis": row["cost_basis"],
            "days_listed": (today - _local_date(listed_at)).days,
        }
        entries.append(((row["venue_name"], listed_at, row["id"]), row_dict, "/lots"))
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

    asking_total = Decimal("0")
    cost_total = Decimal("0")
    excluded = 0
    for row in rows:
        cost_total += cast("Decimal", row["cost_basis"])
        if row["currency"] == _USD:
            asking_total += cast("Decimal", row["asking"])
        else:
            excluded += 1

    notes: list[str] = []
    if excluded:
        plural = "s" if excluded != 1 else ""
        notes.append(
            f"{excluded} listing{plural} not priced in {_USD} excluded from "
            "the asking total."
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
