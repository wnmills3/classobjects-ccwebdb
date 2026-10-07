"""Selling: what is on offer, what has sold, and what is left over.

`sl_offered`: every active or paused listing -- an item or a sales lot --
with its venue, its asking price against its cost basis, and how long it
has been offered. An item listing's cost basis is its own `total_cost`; a
lot listing's is the SQL sum of `total_cost` over the lot's current, live
members -- `SalesLotItem.released_at IS NULL` (the same "still in the lot"
test `lot_writes.open_members` reads) and `live_item()` (no deleted or
split-parent member) -- summed in the database, the same discipline
`cb_holdings` uses, so a lot's own total can never drift from what its rows
actually add up to.

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

`sl_sales`: month x venue, over orders placed in range whose status is a
completed or in-progress sale -- never `cancelled` or `refunded` -- orders,
gross, fees, net, cost basis and gain, the last two from
`sales_order_item_share` (specific identification, spec Decisions) so an
order's own gain is exactly the sum of its items'. `sl_fulfilment`: orders
still open and unshipped (`sale_state.OPEN_ORDER_STATUSES`), oldest first.
`sl_aging`: live items received, held, and not on offer or in an open lot,
by months since received x kind -- the receipt itself read through
`app.reports.receipts`, the one definition `pr_received` also reads.
`sl_auctions`: one row per auction, by status, with a settled one's lots,
sold, unsold, hammer total and fees.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast
from urllib.parse import urlencode

from pydantic import BaseModel
from sqlalchemy import (
    ColumnElement,
    CompoundSelect,
    and_,
    case,
    exists,
    func,
    or_,
    select,
    union,
)
from sqlalchemy.orm import Session, aliased

from ..inventory_search import view_path
from ..live import live_item
from ..models import (
    Auction,
    AuctionLot,
    AuctionLotResult,
    AuctionStatus,
    Currency,
    Customer,
    Disposition,
    Image,
    InventoryItem,
    ItemImage,
    ItemKind,
    ItemStatus,
    ItemStatusHistory,
    Listing,
    ListingStatus,
    SalesLot,
    SalesLotItem,
    SalesOrder,
    SalesOrderFee,
    SalesOrderItem,
    SalesOrderItemShare,
    SalesOrderStatus,
    SalesVenue,
)
from ..offering_writes import OFFER_CURRENCY, ON_OFFER
from ..sale_state import OPEN_ORDER_STATUSES
from .base import Column, DateRange, Report, ReportResult, local_date, period_label
from .receipts import receipt_day, received_transitions
from .registry import register

__all__ = [
    "SL_AGING",
    "SL_AUCTIONS",
    "SL_FULFILMENT",
    "SL_OFFERED",
    "SL_READY",
    "SL_SALES",
    "AgingParams",
    "AuctionsParams",
    "FulfilmentParams",
    "OfferedParams",
    "ReadyParams",
    "SalesParams",
]

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
    return f"{view_path(kind_code)}?{urlencode({'item': item_code})}"


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
            "days_listed": (today - local_date(listed_at)).days,
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
            "days_listed": (today - local_date(listed_at)).days,
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
            link_column="offers",
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
        columns=_COLUMNS,
        rows=rows,
        totals=totals,
        drills=drills,
        notes=notes,
        link_column="offers",
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


# ---------------------------------------------------------------------------
# sl_sales
# ---------------------------------------------------------------------------

#: `sales_order_status` codes (`backend/data/reference/operations.json`) that
#: are a completed or in-progress sale, so its money belongs on a tax
#: return -- never a `cancelled` order, whose sale never happened, and never
#: a `refunded` one, whose money went back. `pending`, `paid` and `packed`
#: are `sale_state.OPEN_ORDER_STATUSES`
#: -- reused, not restated, so this report and `sale_state`'s own "is this
#: order still open" question can never drift on what those three mean --
#: widened with `shipped` and `delivered`, which are further along but still
#: real sales. Named once so a future status is a deliberate addition here,
#: never a silent default either way.
_SALE_STATUSES = OPEN_ORDER_STATUSES | frozenset({"shipped", "delivered"})

_SALES_COLUMNS = [
    Column("period", "Period", "text"),
    Column("venue", "Venue", "text"),
    Column("orders", "Orders", "count"),
    Column("gross", "Gross", "money"),
    Column("fees", "Fees", "money"),
    Column("net", "Net", "money"),
    Column("cost_basis", "Cost basis", "money"),
    Column("gain", "Gain", "money"),
]


class SalesParams(DateRange):
    """`sl_sales` takes no parameters beyond the date range.

    A sale is in range by its order's own `placed_at`, read as a local
    calendar date (`local_date`) since that column is `timestamptz`.
    """


@dataclass
class _SalesGroup:
    """One month x venue bucket's running totals, before its row is built."""

    orders: set[int] = field(default_factory=set)
    gross: Decimal = Decimal("0")
    fees: Decimal = Decimal("0")
    cost_basis: Decimal = Decimal("0")


def _sales_prefilter(params: SalesParams) -> list[ColumnElement[bool]]:
    """A coarse, SQL-side narrowing of `sl_sales`'s rows by date range.

    Never the final word -- `_sl_sales` still runs the exact test
    (`local_date`, per row) in Python -- only a way to avoid fetching every
    order ever placed when a narrow range is asked for. Widened a day past
    each bound, the same margin `pr_received`'s own prefilter gives, since a
    `timestamptz` instant's local calendar day can fall a day either side of
    the bound depending on the server's own time zone.
    """
    conditions: list[ColumnElement[bool]] = []
    if params.date_from is not None:
        widened = datetime.combine(
            params.date_from - timedelta(days=1), time.min, tzinfo=UTC
        )
        conditions.append(SalesOrder.placed_at >= widened)
    if params.date_to is not None:
        widened = datetime.combine(
            params.date_to + timedelta(days=1), time.max, tzinfo=UTC
        )
        conditions.append(SalesOrder.placed_at <= widened)
    return conditions


def _counted_statuses_note(db: Session) -> str:
    """Which order statuses count as a sale here, read from the vocabulary itself."""
    labels = db.scalars(
        select(SalesOrderStatus.label)
        .where(SalesOrderStatus.code.in_(_SALE_STATUSES))
        .order_by(SalesOrderStatus.sort_order)
    ).all()
    return (
        "Counts orders that are a completed or in-progress sale: "
        + ", ".join(labels)
        + "."
    )


def _excluded_order_count(db: Session, params: SalesParams) -> int:
    """Orders placed in range that `sl_sales` leaves out for their status.

    A cancelled or refunded order today -- read from the same date bounds
    `_sl_sales` itself applies, exactly (`_sales_prefilter` narrows the
    fetch, then `local_date` per row decides), so this count and the rows
    it explains always agree about what "in range" means.
    """
    stmt = (
        select(SalesOrder.placed_at)
        .join(SalesOrderStatus, SalesOrderStatus.id == SalesOrder.sales_order_status_id)
        .where(SalesOrderStatus.code.not_in(_SALE_STATUSES), *_sales_prefilter(params))
    )
    count = 0
    for (placed_at,) in db.execute(stmt).all():
        day = local_date(placed_at)
        if params.date_from is not None and day < params.date_from:
            continue
        if params.date_to is not None and day > params.date_to:
            continue
        count += 1
    return count


def _sales_notes(db: Session, excluded: int) -> list[str]:
    """This run's notes: which statuses counted, and how many did not."""
    notes = [_counted_statuses_note(db)]
    if excluded:
        plural = "s" if excluded != 1 else ""
        notes.append(
            f"{excluded} cancelled or refunded order{plural} in this range "
            "excluded: their money is not a sale."
        )
    return notes


def _sl_sales(db: Session, params: SalesParams) -> ReportResult:
    """Month x venue: orders, gross, fees, net, cost basis and gain.

    One row per `sales_order_item_share`, joined back to its order and
    venue: `orders` is the count of distinct order ids a bucket's shares
    name, and `gross`/`fees`/`cost_basis` are Python `Decimal` running sums
    over each share's own `amount`, `fee_amount` and item `total_cost` --
    never `sales_order.total_amount` or `sales_order_fee` directly, so a
    row's own `gain` (`net` less `cost_basis`) is exactly the sum of its
    items' gains, per share, with no second path to drift from it.
    `live_item()` excludes a deleted or split item's share outright -- an
    order with no live item at all does not appear here, and the app's own
    writers have no path that leaves a sold item non-live, so no note
    describes it.
    """
    stmt = (
        select(
            SalesOrder.id.label("order_id"),
            SalesOrder.placed_at,
            SalesVenue.name.label("venue_name"),
            SalesOrderItemShare.amount,
            SalesOrderItemShare.fee_amount,
            InventoryItem.total_cost,
        )
        .select_from(SalesOrder)
        .join(SalesVenue, SalesVenue.id == SalesOrder.sales_venue_id)
        .join(SalesOrderStatus, SalesOrderStatus.id == SalesOrder.sales_order_status_id)
        .join(SalesOrderItem, SalesOrderItem.sales_order_id == SalesOrder.id)
        .join(
            SalesOrderItemShare,
            SalesOrderItemShare.sales_order_item_id == SalesOrderItem.id,
        )
        .join(InventoryItem, InventoryItem.id == SalesOrderItemShare.inventory_item_id)
        .where(
            SalesOrderStatus.code.in_(_SALE_STATUSES),
            live_item(),
            *_sales_prefilter(params),
        )
    )

    groups: dict[tuple[date, str], _SalesGroup] = {}
    for row in db.execute(stmt).mappings().all():
        day = local_date(row["placed_at"])
        if params.date_from is not None and day < params.date_from:
            continue
        if params.date_to is not None and day > params.date_to:
            continue
        key = (day.replace(day=1), row["venue_name"])
        group = groups.setdefault(key, _SalesGroup())
        group.orders.add(row["order_id"])
        group.gross += row["amount"]
        group.fees += row["fee_amount"]
        group.cost_basis += row["total_cost"]

    if not groups:
        return ReportResult(
            columns=_SALES_COLUMNS,
            rows=[],
            totals=None,
            drills=[],
            notes=_sales_notes(db, _excluded_order_count(db, params)),
        )

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    total_orders = 0
    total_gross = Decimal("0")
    total_fees = Decimal("0")
    total_basis = Decimal("0")
    for key in sorted(groups):
        period_start_date, venue_name = key
        group = groups[key]
        net = group.gross - group.fees
        gain = net - group.cost_basis
        rows.append(
            {
                "period": period_label("month", period_start_date),
                "venue": venue_name,
                "orders": len(group.orders),
                "gross": group.gross,
                "fees": group.fees,
                "net": net,
                "cost_basis": group.cost_basis,
                "gain": gain,
            }
        )
        drills.append("/sales")
        total_orders += len(group.orders)
        total_gross += group.gross
        total_fees += group.fees
        total_basis += group.cost_basis

    total_net = total_gross - total_fees
    totals: dict[str, object] = {
        "period": "All periods",
        "venue": None,
        "orders": total_orders,
        "gross": total_gross,
        "fees": total_fees,
        "net": total_net,
        "cost_basis": total_basis,
        "gain": total_net - total_basis,
    }

    return ReportResult(
        columns=_SALES_COLUMNS,
        rows=rows,
        totals=totals,
        drills=drills,
        notes=_sales_notes(db, _excluded_order_count(db, params)),
    )


SL_SALES = register(
    Report(
        id="sl_sales",
        group="Selling",
        title="Sales",
        purpose="Month x venue: orders, gross, fees, net, cost basis and "
        "gain, for sales orders placed in range.",
        params=SalesParams,
        run=_sl_sales,
    )
)


# ---------------------------------------------------------------------------
# sl_fulfilment
# ---------------------------------------------------------------------------

_FULFILMENT_COLUMNS = [
    Column("order", "Order", "text"),
    Column("placed", "Placed", "date"),
    Column("customer", "Customer", "text"),
    Column("items", "Items", "count"),
    Column("amount", "Amount", "money"),
    Column("days_waiting", "Days waiting", "count"),
]


class FulfilmentParams(BaseModel):
    """`sl_fulfilment` takes no parameters: every order still owed shipment."""


def _sl_fulfilment(db: Session, _params: FulfilmentParams) -> ReportResult:
    """One row per order still open and unshipped, oldest first.

    "Still owed shipment" is `sale_state.OPEN_ORDER_STATUSES` --
    `pending`, `paid` or `packed` -- read from there rather than written out
    again as an excluded list here: an excluded list silently treats every
    *future* status as "to ship" too, which is wrong for a `refunded` order
    (its money already went back). An included list
    only ever adds a status on purpose.

    `items` is a grouped `COUNT`, over a live item's own share, in one
    query per report run -- the same shape `pr_outstanding` uses -- rather
    than fetched per order. `days_waiting` is `today` (computed once in
    Python, not SQL `CURRENT_DATE`) less the order's own local placement
    date.
    """
    stmt = (
        select(
            SalesOrder.id,
            SalesOrder.placed_at,
            SalesOrder.total_amount,
            Customer.display_name.label("customer_name"),
            func.count(InventoryItem.id).label("items"),
            # A share whose item is deleted or split: counted by the share,
            # not by the (outer-joined, live-only) item.
            (
                func.count(SalesOrderItemShare.inventory_item_id)
                - func.count(InventoryItem.id)
            ).label("left_out"),
        )
        .select_from(SalesOrder)
        .join(Customer, Customer.id == SalesOrder.customer_id)
        .join(SalesOrderStatus, SalesOrderStatus.id == SalesOrder.sales_order_status_id)
        .outerjoin(SalesOrderItem, SalesOrderItem.sales_order_id == SalesOrder.id)
        .outerjoin(
            SalesOrderItemShare,
            SalesOrderItemShare.sales_order_item_id == SalesOrderItem.id,
        )
        .outerjoin(
            InventoryItem,
            and_(
                InventoryItem.id == SalesOrderItemShare.inventory_item_id, live_item()
            ),
        )
        .where(SalesOrderStatus.code.in_(OPEN_ORDER_STATUSES))
        .group_by(
            SalesOrder.id,
            SalesOrder.placed_at,
            SalesOrder.total_amount,
            Customer.display_name,
        )
        .order_by(SalesOrder.placed_at.asc(), SalesOrder.id.asc())
    )

    today = date.today()
    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    total_items = 0
    total_amount = Decimal("0")
    left_out = 0
    for row in db.execute(stmt).mappings().all():
        left_out += row["left_out"]
        placed = local_date(row["placed_at"])
        rows.append(
            {
                "order": f"#{row['id']}",
                "placed": placed,
                "customer": row["customer_name"],
                "items": row["items"],
                "amount": row["total_amount"],
                "days_waiting": (today - placed).days,
            }
        )
        drills.append("/sales")
        total_items += row["items"]
        total_amount += row["total_amount"]

    if not rows:
        return ReportResult(
            columns=_FULFILMENT_COLUMNS,
            rows=[],
            totals=None,
            drills=[],
            notes=["Nothing is waiting to ship."],
        )

    totals: dict[str, object] = {
        "order": "All orders",
        "placed": None,
        "customer": None,
        "items": total_items,
        "amount": total_amount,
        "days_waiting": None,
    }

    notes: list[str] = []
    if left_out == 1:
        notes.append("1 deleted or split item is left out of its order's item count.")
    elif left_out:
        notes.append(
            f"{left_out} deleted or split items are left out of their orders' "
            "item counts."
        )

    return ReportResult(
        columns=_FULFILMENT_COLUMNS,
        rows=rows,
        totals=totals,
        drills=drills,
        notes=notes,
    )


SL_FULFILMENT = register(
    Report(
        id="sl_fulfilment",
        group="Selling",
        title="To ship",
        purpose="Orders still open and unshipped -- pending, paid or "
        "packed -- oldest first: customer, items, amount and days waiting.",
        params=FulfilmentParams,
        run=_sl_fulfilment,
    )
)


# ---------------------------------------------------------------------------
# sl_aging
# ---------------------------------------------------------------------------

_AGING_COLUMNS = [
    Column("age", "Months since received", "text"),
    Column("kind", "Kind", "text"),
    Column("items", "Items", "count"),
    Column("total_cost", "Total cost", "money"),
]

#: Label, and the inclusive month bounds it covers; `None` for the open top
#: of "24+". Order matters: rows and the totals loop below both read this
#: sequence to keep buckets in listed order rather than sorted some other
#: way.
_AGE_BUCKETS: tuple[tuple[str, int, int | None], ...] = (
    ("0-5", 0, 5),
    ("6-11", 6, 11),
    ("12-23", 12, 23),
    ("24+", 24, None),
)
_UNKNOWN_AGE = "Unknown"

#: Sort rank for every age label, the buckets above in their listed order
#: and then "Unknown" last.
_AGE_RANK: dict[str, int] = {
    label: rank for rank, (label, _lo, _hi) in enumerate(_AGE_BUCKETS)
}
_AGE_RANK[_UNKNOWN_AGE] = len(_AGE_BUCKETS)


class AgingParams(BaseModel):
    """`sl_aging` takes no parameters: every held, unoffered live item."""


def _age_bucket(months: int) -> str:
    """The bucket label `months` (whole months since receipt) falls into."""
    for label, low, high in _AGE_BUCKETS:
        if months >= low and (high is None or months <= high):
            return label
    return _AGE_BUCKETS[-1][0]  # pragma: no cover - the last bucket is open-ended


def _months_since(received: date, today: date) -> int:
    """Whole calendar months between `received` and `today`, never negative."""
    months = (today.year - received.year) * 12 + (today.month - received.month)
    if today.day < received.day:
        months -= 1
    return max(months, 0)


def _receipt_dates(db: Session, item_ids: CompoundSelect[Any]) -> dict[int, date]:
    """Each item's own latest transition to `received`, keyed by item id.

    `item_ids` is a statement selecting the ids to look up, used as an `IN`
    subquery so no id is bound as its own parameter.
    `receipts.received_transitions` is the one definition of a genuine
    arrival, `pr_received`'s own base query too, so the two reports can
    never disagree about what "received" means. An item
    transitioned more than once (returned and received again) keeps its
    latest one, by `changed_at`.
    """
    rows = db.execute(
        received_transitions().where(ItemStatusHistory.inventory_item_id.in_(item_ids))
    ).all()
    latest: dict[int, tuple[datetime, date]] = {}
    for item_id, arrived_on, changed_at in rows:
        day = receipt_day(arrived_on, changed_at)
        if item_id not in latest or changed_at > latest[item_id][0]:
            latest[item_id] = (changed_at, day)
    return {item_id: day for item_id, (_changed_at, day) in latest.items()}


def _sl_aging(db: Session, _params: AgingParams) -> ReportResult:
    """Months-since-received x kind: items and total cost, for held stock.

    "Held and not offered": live, `received`, `held` -- and, defensively,
    not on any listing that is `offering_writes.ON_OFFER` and not an open member
    of a sales lot (`SalesLotItem.released_at IS NULL`), the same "still in
    the lot" test `_lot_rows` above and `lot_writes.open_members` read.
    Ordinarily `offering_writes.offer` already moves a member's own
    disposition off `held` the moment it is offered, so these two checks
    should never find anything `disposition == held` did not already
    exclude -- but this report reads the state directly rather than
    trusting that invariant to hold forever, the same discipline
    `_item_rows`' `live_item()` follows.

    A split child has only its own opening row, never a transition to
    `received` (`receipts.received_transitions`), so a child with no receipt
    of its own falls back to its split parent's.
    """
    offered_now = exists(
        select(1).where(
            Listing.inventory_item_id == InventoryItem.id, Listing.status.in_(ON_OFFER)
        )
    )
    open_lot_member = exists(
        select(1).where(
            SalesLotItem.inventory_item_id == InventoryItem.id,
            SalesLotItem.released_at.is_(None),
        )
    )
    stmt = (
        select(
            InventoryItem.id,
            InventoryItem.parent_item_id,
            InventoryItem.total_cost,
            ItemKind.label.label("kind_label"),
            ItemKind.sort_order.label("kind_sort"),
        )
        .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
        .join(ItemStatus, ItemStatus.id == InventoryItem.status_id)
        .join(Disposition, Disposition.id == InventoryItem.disposition_id)
        .where(
            live_item(),
            ItemStatus.code == "received",
            Disposition.code == "held",
            ~offered_now,
            ~open_lot_member,
        )
    )
    candidates = db.execute(stmt).all()
    if not candidates:
        return ReportResult(
            columns=_AGING_COLUMNS,
            rows=[],
            totals=None,
            drills=[],
            notes=["Nothing is held and not offered."],
        )

    # The receipt lookup narrows by the candidates' own ids (and their split
    # parents') through a subquery of this same statement, not an expanded
    # `IN` list: at live scale that would bind one parameter per held item.
    held = stmt.subquery()
    lookup_ids = union(
        select(held.c.id),
        select(held.c.parent_item_id).where(held.c.parent_item_id.is_not(None)),
    )
    receipts = _receipt_dates(db, lookup_ids)

    today = date.today()
    unknown = 0
    kind_sort: dict[str, int] = {}
    buckets: dict[tuple[str, str], list[object]] = {}
    for row in candidates:
        received = receipts.get(row.id)
        if received is None and row.parent_item_id is not None:
            received = receipts.get(row.parent_item_id)
        if received is None:
            unknown += 1
            age = _UNKNOWN_AGE
        else:
            age = _age_bucket(_months_since(received, today))
        kind_sort[row.kind_label] = row.kind_sort
        key = (age, row.kind_label)
        bucket = buckets.setdefault(key, [0, Decimal("0")])
        bucket[0] = cast(int, bucket[0]) + 1
        bucket[1] = cast(Decimal, bucket[1]) + row.total_cost

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    total_items = 0
    total_cost = Decimal("0")
    for age, kind_label in sorted(
        buckets, key=lambda k: (_AGE_RANK[k[0]], kind_sort[k[1]])
    ):
        items, cost = buckets[(age, kind_label)]
        items = cast(int, items)
        cost = cast(Decimal, cost)
        rows.append(
            {"age": age, "kind": kind_label, "items": items, "total_cost": cost}
        )
        drills.append(None)
        total_items += items
        total_cost += cost

    totals: dict[str, object] = {
        "age": "All items",
        "kind": None,
        "items": total_items,
        "total_cost": total_cost,
    }

    notes: list[str] = []
    if unknown:
        subject = (
            f"1 item of {total_items} has no recorded receipt and is"
            if unknown == 1
            else f"{unknown} items of {total_items} have no recorded receipt and are"
        )
        notes.append(
            f'{subject} bucketed "Unknown": their status history starts with '
            "the item already received, which is not an arrival."
        )

    return ReportResult(
        columns=_AGING_COLUMNS, rows=rows, totals=totals, drills=drills, notes=notes
    )


SL_AGING = register(
    Report(
        id="sl_aging",
        group="Selling",
        title="Held and not offered",
        purpose="Live items received and held, not on offer or in an open "
        "lot, by months since received and kind: items and total cost.",
        params=AgingParams,
        run=_sl_aging,
    )
)


# ---------------------------------------------------------------------------
# sl_auctions
# ---------------------------------------------------------------------------

_AUCTIONS_COLUMNS = [
    Column("auction", "Auction", "text"),
    Column("venue", "Venue", "text"),
    Column("status", "Status", "text"),
    Column("lots", "Lots", "count"),
    Column("sold", "Sold", "count"),
    Column("unsold", "Unsold", "count"),
    Column("hammer_total", "Hammer total", "money"),
    Column("fees", "Fees", "money"),
]

#: `AuctionStatus`'s own declaration order is an auction's life story --
#: draft, scheduled, consigned, closed, settled, cancelled -- and this
#: report's rows follow it rather than a second, hand-written order that
#: could drift from the enum.
_STATUS_RANK: dict[AuctionStatus, int] = {
    status: rank for rank, status in enumerate(AuctionStatus)
}

_SOLD_OR_NOT: tuple[AuctionLotResult, ...] = (
    AuctionLotResult.unsold,
    AuctionLotResult.withdrawn,
)


class AuctionsParams(BaseModel):
    """`sl_auctions` takes no parameters: every auction, every time."""


def _auction_fees(db: Session, auction_ids: set[int]) -> dict[int, Decimal]:
    """Each settled auction's own fee total, from its sold lots' orders.

    An auction house bills per buyer *order*, not per lot
    (`sales_writes.record_sale_lines`), so an order covering two of this
    auction's lots must not have its fee summed twice: `order_ids` is
    distinct on `(auction, order)` before `sales_order_fee` is ever joined,
    so a fan-out from a second lot on the same order cannot double a fee
    row that belongs to it only once.
    """
    if not auction_ids:
        return {}
    order_ids = (
        select(
            AuctionLot.auction_id.label("auction_id"),
            SalesOrderItem.sales_order_id.label("order_id"),
        )
        .join(Listing, Listing.id == AuctionLot.listing_id)
        .join(SalesOrderItem, SalesOrderItem.listing_id == Listing.id)
        .where(
            AuctionLot.auction_id.in_(auction_ids),
            AuctionLot.result == AuctionLotResult.sold,
        )
        .distinct()
        .subquery()
    )
    rows = db.execute(
        select(
            order_ids.c.auction_id,
            func.coalesce(func.sum(SalesOrderFee.amount), 0).label("fees"),
        )
        .select_from(order_ids)
        .join(SalesOrderFee, SalesOrderFee.sales_order_id == order_ids.c.order_id)
        .group_by(order_ids.c.auction_id)
    ).all()
    return {row.auction_id: row.fees for row in rows}


def _sl_auctions(db: Session, _params: AuctionsParams) -> ReportResult:
    """One row per auction, ordered by status: lots, sold, unsold, hammer, fees.

    Lots, sold, unsold and hammer total are read from `auction_lot` for
    every auction alike -- one query, so a draft auction with no lots costs
    nothing extra -- and fees from `sales_order_fee` in a second query over
    the settled auctions only (`_auction_fees`). All five are shown only for
    a `settled` one: a `closed`
    auction's lots may already carry a `result` mid-settlement, and showing
    a half-settled total would read as the finished figure. `unsold` folds
    in `withdrawn`: this report has no separate column for it, and both mean
    "did not sell".
    """
    is_sold = AuctionLot.result == AuctionLotResult.sold
    is_unsold = AuctionLot.result.in_(_SOLD_OR_NOT)
    stmt = (
        select(
            Auction.id,
            Auction.title,
            Auction.status,
            SalesVenue.name.label("venue_name"),
            func.count(AuctionLot.id).label("lots"),
            func.count(case((is_sold, AuctionLot.id))).label("sold"),
            func.count(case((is_unsold, AuctionLot.id))).label("unsold"),
            func.coalesce(func.sum(case((is_sold, AuctionLot.hammer_price))), 0).label(
                "hammer_total"
            ),
        )
        .select_from(Auction)
        .join(SalesVenue, SalesVenue.id == Auction.sales_venue_id)
        .outerjoin(AuctionLot, AuctionLot.auction_id == Auction.id)
        .group_by(Auction.id, Auction.title, Auction.status, SalesVenue.name)
    )
    fetched = db.execute(stmt).all()
    if not fetched:
        return ReportResult(
            columns=_AUCTIONS_COLUMNS,
            rows=[],
            totals=None,
            drills=[],
            notes=["No auctions recorded."],
        )

    settled_ids = {row.id for row in fetched if row.status is AuctionStatus.settled}
    fees_by_auction = _auction_fees(db, settled_ids)

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for row in sorted(fetched, key=lambda r: (_STATUS_RANK[r.status], r.id)):
        settled = row.status is AuctionStatus.settled
        rows.append(
            {
                "auction": row.title,
                "venue": row.venue_name,
                "status": row.status.value.capitalize(),
                "lots": row.lots if settled else None,
                "sold": row.sold if settled else None,
                "unsold": row.unsold if settled else None,
                "hammer_total": row.hammer_total if settled else None,
                "fees": fees_by_auction.get(row.id, Decimal("0")) if settled else None,
            }
        )
        drills.append("/auctions")

    return ReportResult(
        columns=_AUCTIONS_COLUMNS,
        rows=rows,
        totals=None,
        drills=drills,
        notes=[
            "Lots, sold, unsold, hammer total and fees are shown only for a "
            'settled auction. "Unsold" counts a withdrawn lot too.'
        ],
    )


SL_AUCTIONS = register(
    Report(
        id="sl_auctions",
        group="Selling",
        title="Auctions",
        purpose="One row per auction, by status; for a settled one: lots, "
        "sold, unsold, hammer total and fees.",
        params=AuctionsParams,
        run=_sl_auctions,
    )
)


# ---------------------------------------------------------------------------
# sl_ready
# ---------------------------------------------------------------------------


class ReadyParams(BaseModel):
    """`sl_ready` takes no parameters: every item in hand and not yet offered."""


#: The kinds a grade describes, and the kinds a weight describes instead.
_GRADED_KINDS = ("coin", "currency")
_WEIGHED_KINDS = ("bullion", "medal", "token")

_READY_COLUMNS = [
    Column("kind", "Kind", "text"),
    Column("items", "In hand, not offered", "count"),
    Column("photographed", "Own photograph", "count"),
    Column("described", "Graded or weighed", "count"),
    Column("located", "Location", "count"),
    Column("costed", "Cost", "count"),
    Column("ready", "Ready to sell", "count"),
]


def _sl_ready(db: Session, _params: ReadyParams) -> ReportResult:
    """By kind: items in hand and not offered, and how many could be listed now.

    An item is counted when it is live, received and held -- in hand, and
    not listed, sold or on its way to a buyer. Four things then stand
    between it and a listing a buyer can trust, each counted on its own:

    - **an own photograph**: one filed against it that was not fetched from
      a web address. A seller's listing picture shows what was bought, not
      the piece as it is now, and is not the owner's to publish;
    - **graded or weighed**: a grade on a coin or a note; a fine weight on
      bullion, a medal or a token, which are sold by their metal. A set, and
      a kind with neither, needs nothing here;
    - **a storage location**, so a sold piece can be found;
    - **a cost**: a total cost above zero, the basis a sale's gain is
      measured from.

    `ready` is the items with all four. The counts are one grouped query,
    so a kind's `ready` can never exceed any of its other columns.
    """
    own_photograph = exists(
        select(ItemImage.id)
        .join(Image, Image.id == ItemImage.image_id)
        .where(
            ItemImage.inventory_item_id == InventoryItem.id,
            Image.source_url.is_(None),
        )
    )
    described = or_(
        and_(ItemKind.code.in_(_GRADED_KINDS), InventoryItem.grade_id.is_not(None)),
        and_(
            ItemKind.code.in_(_WEIGHED_KINDS),
            InventoryItem.fine_weight_ozt.is_not(None),
        ),
        ItemKind.code.not_in(_GRADED_KINDS + _WEIGHED_KINDS),
    )
    located = InventoryItem.storage_location_id.is_not(None)
    costed = InventoryItem.total_cost > 0

    def how_many(test: ColumnElement[bool]) -> ColumnElement[int]:
        """A count of the group's items that pass `test`."""
        return func.count(case((test, InventoryItem.id)))

    stmt = (
        select(
            ItemKind.code.label("kind_code"),
            ItemKind.label.label("kind_label"),
            func.count(InventoryItem.id).label("items"),
            how_many(own_photograph).label("photographed"),
            how_many(described).label("described"),
            how_many(located).label("located"),
            how_many(costed).label("costed"),
            how_many(and_(own_photograph, described, located, costed)).label("ready"),
        )
        .select_from(InventoryItem)
        .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
        .join(ItemStatus, ItemStatus.id == InventoryItem.status_id)
        .join(Disposition, Disposition.id == InventoryItem.disposition_id)
        .where(live_item(), ItemStatus.code == "received", Disposition.code == "held")
        .group_by(ItemKind.id, ItemKind.code, ItemKind.label, ItemKind.sort_order)
        .order_by(ItemKind.sort_order, ItemKind.id)
    )
    measures = [column.key for column in _READY_COLUMNS[1:]]
    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    totals: dict[str, object] = {"kind": "All kinds", **dict.fromkeys(measures, 0)}
    for row in db.execute(stmt).mappings().all():
        rows.append({"kind": row["kind_label"], **{key: row[key] for key in measures}})
        query = {"status": "received"}
        if row["kind_code"] != "currency":
            query["kind"] = row["kind_code"]
        drills.append(f"{view_path(row['kind_code'])}?{urlencode(query)}")
        for key in measures:
            totals[key] = cast("int", totals[key]) + row[key]

    return ReportResult(
        columns=_READY_COLUMNS,
        rows=rows,
        totals=totals if rows else None,
        drills=drills,
        notes=[
            "Counts items in hand (received) and held: not listed, sold or shipped.",
            "Own photograph: one that was not fetched from a web address. A "
            "seller's listing picture does not count.",
            "Graded or weighed: a grade on a coin or a note; a fine weight on "
            "bullion, a medal or a token. Other kinds need neither.",
            "Ready to sell: all four of own photograph, graded or weighed, "
            "location and cost.",
        ],
    )


SL_READY = register(
    Report(
        id="sl_ready",
        group="Selling",
        title="Ready to sell",
        purpose="By kind: items in hand and not offered, and how many have an "
        "own photograph, a grade or weight, a location and a cost -- "
        "and all four.",
        params=ReadyParams,
        run=_sl_ready,
    )
)
