"""Data quality: what is missing or wrong in the record.

Six reports. `dq_issues` counts every named check from `app.issues` across
both inventory views, reusing `inventory_search.count_issues` so a row's
count can never disagree with its own drill-down's search. `dq_completeness`
reports, per item kind, the percent of live items with each of ten fields
filled in -- counted with the exact same SQL text the `missing=<field>`
filter (`inventory_search.MISSING_FIELDS`) uses to find a field empty, so a
report cell and its drill-down search agree by construction, not only by
test. `dq_photos` finds live items with no photograph, by kind and status,
plus photographs nobody has filed against any item. `dq_derived` finds
fields a machine pass filled in that nobody has confirmed by examination.
`dq_purchases` finds purchases with a data gap -- a placeholder order
number, a missing or implausible order date, no web address, a zero-cost
item, or no items at all. `dq_locations` totals live items by where they
physically are.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import cast
from urllib.parse import urlencode

from pydantic import BaseModel
from sqlalchemy import RowMapping, and_, case, func, select, text
from sqlalchemy.orm import Session, selectinload

from ..field_sources import HELD
from ..inventory_search import (
    COIN_VIEW,
    CURRENCY_VIEW,
    MISSING_FIELDS,
    count_issues,
    view_path,
)
from ..item_history import location_label
from ..models import (
    CurrencyDetail,
    Image,
    ItemFieldReview,
    ItemFieldSource,
    ItemImage,
    ItemStatus,
    PurchaseOrder,
    StorageLocation,
    Vendor,
)
from ..routers.acquisitions import _GENERATED, _WEB_ADDRESS
from .base import Column, Report, ReportResult
from .registry import register
from .tables import ITEM as _I
from .tables import KIND as _K
from .tables import LIVE as _LIVE

__all__ = [
    "DQ_COMPLETENESS",
    "DQ_DERIVED",
    "DQ_ISSUES",
    "DQ_LOCATIONS",
    "DQ_PHOTOS",
    "DQ_PURCHASES",
]


class DqIssuesParams(BaseModel):
    """No parameters: every check, on every live item, every time."""


def _dq_issues(db: Session, _params: DqIssuesParams) -> ReportResult:
    """One row per named check per view -- coins, then currency.

    Delegates counting to `inventory_search.count_issues`, the same function
    the inventory search's own issue panel calls, so a row's `items` is
    exactly what its drill-down (`?issue=<check>`) would return. That
    function omits a check that counted zero; every check is listed here
    regardless, with 0 filled in, so a clean check is visible as a check that
    ran rather than one that was never asked.
    """
    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for spec, label in ((COIN_VIEW, "Coins"), (CURRENCY_VIEW, "Currency")):
        counts = count_issues(db, spec, params={})
        for code, issue in spec.issues.items():
            n = counts.get(code, 0)
            rows.append(
                {
                    "view": label,
                    "check": code,
                    "description": issue.description,
                    "items": n,
                }
            )
            drills.append(f"/inventory/{spec.name}?issue={code}" if n else None)
    return ReportResult(
        columns=[
            Column("view", "View", "text"),
            Column("check", "Check", "text"),
            Column("description", "Description", "text"),
            Column("items", "Items", "count"),
        ],
        rows=rows,
        drills=drills,
        link_column="check",
        notes=[
            "No total: one item can carry several issues at once, so a sum "
            "of these counts is not the number of items with an issue."
        ],
    )


DQ_ISSUES = register(
    Report(
        id="dq_issues",
        group="Data quality",
        title="Open issues",
        purpose="Every named data-quality check, counted across coins and currency.",
        params=DqIssuesParams,
        run=_dq_issues,
    )
)


class DqCompletenessParams(BaseModel):
    """No parameters: completeness is always computed over every live item."""


#: A reader's label for each percent column. The dict's order is the
#: column order, and its keys are exactly `inventory_search.MISSING_FIELDS`'
#: keys -- the `missing=` values -- which is the contract this report's
#: columns are built to: a column's `key` IS the `missing=` field name, so
#: the console builds a cell's own drill-down by adding `missing=<key>` to
#: this row's own drill (`/inventory/currency` or
#: `/inventory/coins?kind=<code>`) with `URLSearchParams`, not string
#: concatenation -- `/inventory/currency` carries no `?` of its own to
#: concatenate onto.
_FIELD_LABELS: dict[str, str] = {
    "year": "Year",
    "denomination": "Denomination",
    "grade": "Grade",
    "country": "Country",
    "series": "Series",
    "metal": "Metal",
    "photo": "Photograph",
    "storage_location": "Storage location",
    "listing_link": "Listing link",
    "sellers_item_id": "Seller's item id",
}

#: `currency_detail` aliased `cud`, as `inventory_search` aliases it
#: (`_J_CUR_DETAIL`), alongside the shared `i`/`k` of `.tables`: together
#: they let `MISSING_FIELDS[key].sql`'s text, written for the `missing=`
#: filter, run here verbatim. One definition of "is this field missing",
#: read by the filter and this report alike.
_CUD = CurrencyDetail.__table__.alias("cud")

_ONE_DP = Decimal("0.1")


def _percent(filled: int, live: int) -> Decimal:
    """`filled` of `live`, as a Decimal percentage to one decimal place."""
    return (Decimal(filled) / Decimal(live) * 100).quantize(
        _ONE_DP, rounding=ROUND_HALF_UP
    )


def _drill(kind_code: str) -> str:
    """The kind's own inventory search -- what the row (not a cell) drills to."""
    if kind_code == "currency":
        return view_path(kind_code)
    return f"{view_path(kind_code)}?kind={kind_code}"


def _dq_completeness(db: Session, _params: DqCompletenessParams) -> ReportResult:
    """One row per item kind with at least one live item.

    Each percent column's key is a `missing=` field name (see
    `_FIELD_LABELS`); a kind this field does not apply to -- metal on a
    note, grade or denomination on bullion -- renders that cell `None`
    rather than 0% or 100%, because "no field to be missing" is a different
    fact from "every field is missing". Where `app.issues` states no kind
    restriction for a field (country, series, photograph, storage location,
    listing link, seller's item id), this report follows it in applying that
    field to every kind -- a judgment call, not a rule read from `issues.py`.

    Every `count(*) FILTER` below runs `MISSING_FIELDS[key].sql` -- the same
    text the `missing=` filter itself runs -- so a cell's implied "missing"
    count and its drill-down search's count cannot drift apart: they are the
    same predicate, not two that happen to agree today.
    """
    stmt = (
        select(
            _K.c.code.label("kind_code"),
            _K.c.label.label("kind_label"),
            func.count().label("live_items"),
            *(
                func.count().filter(text(f"NOT ({field.sql})")).label(key)
                for key, field in MISSING_FIELDS.items()
            ),
        )
        .select_from(_I)
        .join(_K, _K.c.id == _I.c.item_kind_id)
        .outerjoin(_CUD, _CUD.c.inventory_item_id == _I.c.id)
        .where(_LIVE)
        .group_by(_K.c.id, _K.c.code, _K.c.label, _K.c.sort_order)
        .having(func.count() > 0)
        # The kinds' own order, as `cb_holdings` lists them; `id` breaks a
        # tie between two kinds sharing a `sort_order`.
        .order_by(_K.c.sort_order, _K.c.id)
    )

    columns = [
        Column("kind", "Kind", "text"),
        Column("live_items", "Live items", "count"),
        *(Column(key, label, "percent") for key, label in _FIELD_LABELS.items()),
    ]
    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for row in db.execute(stmt).mappings().all():
        live = row["live_items"]
        rows.append(
            {
                "kind": row["kind_label"],
                "live_items": live,
                **{
                    key: (
                        _percent(row[key], live)
                        if MISSING_FIELDS[key].applies_to(row["kind_code"])
                        else None
                    )
                    for key in _FIELD_LABELS
                },
            }
        )
        drills.append(_drill(row["kind_code"]))

    return ReportResult(
        columns=columns,
        rows=rows,
        drills=drills,
        notes=[
            "A blank cell means the field does not apply to that kind, not "
            "that it is 0% or 100% filled.",
            "Denomination and grade apply only to coins and banknotes; metal "
            "does not apply to currency or sets, and series is optional for "
            "currency, whose year is its series year. Every other field "
            "applies to every kind.",
        ],
    )


DQ_COMPLETENESS = register(
    Report(
        id="dq_completeness",
        group="Data quality",
        title="Field completeness",
        purpose="Percent of live items with each field filled in, by kind.",
        params=DqCompletenessParams,
        run=_dq_completeness,
    )
)


# ---------------------------------------------------------------------------
# dq_photos
# ---------------------------------------------------------------------------


class DqPhotosParams(BaseModel):
    """No parameters: every live item's photo status, every time."""


#: `item_status`, aliased as `.tables` aliases `i`/`k` -- this module's own
#: alias, since no other report here needs a status join.
_PHOTO_ST = ItemStatus.__table__.alias("pst")


def _photo_drill(kind_code: str, status_code: str) -> str:
    """A kind x status cell's own search: `missing=photo`, narrowed by status.

    `kind=` is added the same way `_query_string` (`collection.py`) adds it --
    omitted for currency, which has its own view and needs no `kind=` at all.
    """
    query: dict[str, str] = {}
    if kind_code != "currency":
        query["kind"] = kind_code
    query["status"] = status_code
    query["missing"] = "photo"
    return f"{view_path(kind_code)}?{urlencode(query)}"


def _dq_photos(db: Session, _params: DqPhotosParams) -> ReportResult:
    """Live items with no photograph, by kind and status; then unfiled ones.

    Each kind x status row runs `MISSING_FIELDS["photo"].sql` verbatim -- the
    same text the `missing=photo` filter runs -- so a row's `items` is
    exactly what its own drill-down search returns.

    The final row is a different question in the same table: not which
    *items* lack a photograph, but which *photographs* the import and the
    console have not filed against any item at all -- the same test
    `GET /api/images?unattached=true` (`routers.images.list_images`) uses to
    build `/photos`' own list, so this row's count is exactly what that page
    shows.
    """
    stmt = (
        select(
            _K.c.code.label("kind_code"),
            _K.c.label.label("kind_label"),
            _PHOTO_ST.c.code.label("status_code"),
            _PHOTO_ST.c.label.label("status_label"),
            func.count().label("items"),
        )
        .select_from(_I)
        .join(_K, _K.c.id == _I.c.item_kind_id)
        .join(_PHOTO_ST, _PHOTO_ST.c.id == _I.c.status_id)
        .where(_LIVE, text(f"({MISSING_FIELDS['photo'].sql})"))
        .group_by(
            _K.c.id,
            _K.c.code,
            _K.c.label,
            _K.c.sort_order,
            _PHOTO_ST.c.id,
            _PHOTO_ST.c.code,
            _PHOTO_ST.c.label,
            _PHOTO_ST.c.sort_order,
        )
        .order_by(_K.c.sort_order, _K.c.id, _PHOTO_ST.c.sort_order, _PHOTO_ST.c.id)
    )

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for row in db.execute(stmt).mappings().all():
        rows.append(
            {
                "kind": row["kind_label"],
                "status": row["status_label"],
                "items": row["items"],
            }
        )
        drills.append(_photo_drill(row["kind_code"], row["status_code"]))

    # An image counts as unfiled when no `item_image` row links it to an
    # item -- `inventory_item_id IS NOT NULL` -- the exact predicate
    # `list_images(unattached=True)` runs, so this row's count is what
    # `/photos` shows, not a restatement that could drift from it.
    linked_images = select(ItemImage.image_id).where(
        ItemImage.inventory_item_id.is_not(None)
    )
    unfiled = db.execute(
        select(func.count()).select_from(Image).where(Image.id.not_in(linked_images))
    ).scalar_one()
    rows.append({"kind": "Unfiled photographs", "status": None, "items": unfiled})
    drills.append("/photos")

    return ReportResult(
        columns=[
            Column("kind", "Kind", "text"),
            Column("status", "Status", "text"),
            Column("items", "Items", "count"),
        ],
        rows=rows,
        drills=drills,
        notes=[
            "Unfiled photographs counts photographs, not items: a "
            "photograph exists before anyone has decided which item it "
            "depicts."
        ],
    )


DQ_PHOTOS = register(
    Report(
        id="dq_photos",
        group="Data quality",
        title="Photographs",
        purpose="Live items with no photograph, by kind and status, plus "
        "photographs filed against no item.",
        params=DqPhotosParams,
        run=_dq_photos,
    )
)


# ---------------------------------------------------------------------------
# dq_derived
# ---------------------------------------------------------------------------


class DqDerivedParams(BaseModel):
    """No parameters: every live item's derived, unconfirmed field, every time."""


#: `item_field_source` and `item_field_review`, aliased for the same reason
#: `.tables` aliases `inventory_item` and `item_kind`: so the join below reads
#: as what it is rather than restating the base table names.
_IFS = ItemFieldSource.__table__.alias("ifs")
_IFR = ItemFieldReview.__table__.alias("ifr")


def _dq_derived(db: Session, _params: DqDerivedParams) -> ReportResult:
    """Field x rule: `item_field_source` rows a person has not confirmed.

    "Confirmed" means an `item_field_review` row for the *same item and the
    same field* -- the pairing `item_field_review`'s own docstring describes
    (per field, not per item) and the one `ItemFieldSource`/`item_field_review`
    both key on (`inventory_item_id`, `field_name`). `HELD` rows are excluded:
    a field a person emptied on purpose is not "filled by a rule" at all.

    This is a different, finer question than `issue=unreviewed`
    (`app.issues`), which asks only whether an item carries *any* review row,
    regardless of field -- an item reviewed on one field and derived on
    another is "unreviewed" nowhere in this report but still trips
    `issue=unreviewed` if that other field has no review either, or the
    reverse: an item with one field reviewed no longer trips
    `issue=unreviewed` at all, yet can still have other fields counted here.
    Because the two measures are taken at different granularities -- items
    for the search check, field x rule pairs here -- a row's count here
    cannot be relied on to equal what `issue=unreviewed` would return, so no
    row links to it.
    """
    stmt = (
        select(
            _IFS.c.field_name.label("field_name"),
            _IFS.c.derived_by.label("rule"),
            func.count().label("items"),
        )
        .select_from(_IFS)
        .join(_I, _I.c.id == _IFS.c.inventory_item_id)
        .outerjoin(
            _IFR,
            and_(
                _IFR.c.inventory_item_id == _IFS.c.inventory_item_id,
                _IFR.c.field_name == _IFS.c.field_name,
            ),
        )
        .where(_LIVE, _IFS.c.derived_by != HELD, _IFR.c.id.is_(None))
        .group_by(_IFS.c.field_name, _IFS.c.derived_by)
        .order_by(_IFS.c.field_name, _IFS.c.derived_by)
    )

    rows: list[dict[str, object]] = []
    for row in db.execute(stmt).mappings().all():
        rows.append(
            {
                "field": row["field_name"],
                "rule": row["rule"],
                "items": row["items"],
            }
        )

    return ReportResult(
        columns=[
            Column("field", "Field", "text"),
            Column("rule", "Rule", "text"),
            Column("items", "Items", "count"),
        ],
        rows=rows,
        drills=[None] * len(rows),
        notes=[
            "Counts field x rule pairs: an item derived on two fields "
            "neither of which is confirmed counts once for each. "
            "`issue=unreviewed` counts items with no field confirmed at "
            "all, a coarser, item-level measure this report's rows cannot "
            "be relied on to sum to, so no row drills to it."
        ],
    )


DQ_DERIVED = register(
    Report(
        id="dq_derived",
        group="Data quality",
        title="Filled by a rule, not yet confirmed",
        purpose="Field x rule pairs a machine pass filled in that nobody "
        "has confirmed by examination.",
        params=DqDerivedParams,
        run=_dq_derived,
    )
)


# ---------------------------------------------------------------------------
# dq_purchases
# ---------------------------------------------------------------------------


class DqPurchasesParams(BaseModel):
    """No parameters: every purchase with at least one gap, every time."""


#: The gap words, in the fixed order a purchase's own row lists them --
#: never alphabetical or discovery order, so two purchases sharing the same
#: gaps always read the same.
_GENERATED_NUMBER = "generated number"
_NO_ORDER_DATE = "no order date"
_NO_WEB_ADDRESS = "no web address"
_DATE_AFTER_ENTRY = "order date after entry"
_DATE_OVER_A_YEAR_BEFORE = "order date over a year before entry"
_ZERO_COST_ITEM = "item with zero cost"
_NO_ITEMS = "no items"


def _a_year_before(day: date) -> date:
    """The calendar date one year before `day`; Feb 29 falls back to Feb 28."""
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def _purchase_gaps(row: RowMapping) -> list[str]:
    """This purchase's own gaps, in the fixed order (Ruling P2-4 for the dates).

    "Entry" is `created_at`'s own local calendar date -- `astimezone()` with
    no argument converts to the system's local zone before taking the date,
    the same conversion `selling._local_date` uses, since the database
    session's zone need not be the application's.
    """
    gaps: list[str] = []
    order_number = row["order_number"]
    if order_number is not None and _GENERATED.match(order_number):
        gaps.append(_GENERATED_NUMBER)

    ordered_on = cast("date | None", row["ordered_on"])
    if ordered_on is None:
        gaps.append(_NO_ORDER_DATE)

    source_url = row["source_url"]
    if not source_url or not _WEB_ADDRESS.match(source_url):
        gaps.append(_NO_WEB_ADDRESS)

    if ordered_on is not None:
        entry_date = row["created_at"].astimezone().date()
        if ordered_on > entry_date:
            gaps.append(_DATE_AFTER_ENTRY)
        if ordered_on < _a_year_before(entry_date):
            gaps.append(_DATE_OVER_A_YEAR_BEFORE)

    if row["zero_cost_items"]:
        gaps.append(_ZERO_COST_ITEM)
    if not row["live_items"]:
        gaps.append(_NO_ITEMS)

    return gaps


def _dq_purchases(db: Session, _params: DqPurchasesParams) -> ReportResult:
    """One row per purchase with at least one gap, newest purchase first.

    `live_items` and `zero_cost_items` are grouped aggregates -- `live_item()`
    sits in the join's `ON` clause, the same shape `pr_outstanding` uses, so a
    purchase whose only items are deleted or split is not silently dropped by
    the aggregation; its `live_items` comes back 0 and it is flagged "no
    items" rather than disappearing.
    """
    zero_cost_items = func.count(case((_I.c.item_cost == 0, _I.c.id)))
    stmt = (
        select(
            PurchaseOrder.id,
            PurchaseOrder.order_number,
            PurchaseOrder.ordered_on,
            PurchaseOrder.source_url,
            PurchaseOrder.created_at,
            Vendor.name.label("vendor_name"),
            func.count(_I.c.id).label("live_items"),
            zero_cost_items.label("zero_cost_items"),
        )
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .outerjoin(_I, and_(_I.c.purchase_order_id == PurchaseOrder.id, _LIVE))
        .group_by(
            PurchaseOrder.id,
            PurchaseOrder.order_number,
            PurchaseOrder.ordered_on,
            PurchaseOrder.source_url,
            PurchaseOrder.created_at,
            Vendor.name,
        )
        .order_by(PurchaseOrder.id.desc())
    )

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for row in db.execute(stmt).mappings().all():
        gaps = _purchase_gaps(row)
        if not gaps:
            continue
        rows.append(
            {
                "purchase": f"#{row['id']}",
                "order_number": row["order_number"] or "",
                "vendor": row["vendor_name"],
                "ordered": row["ordered_on"],
                "gaps": ", ".join(gaps),
            }
        )
        drills.append(f"/receiving?order={row['id']}")

    return ReportResult(
        columns=[
            Column("purchase", "Purchase", "text"),
            Column("order_number", "Order number", "text"),
            Column("vendor", "Vendor", "text"),
            Column("ordered", "Ordered", "date"),
            Column("gaps", "Gaps", "text"),
        ],
        rows=rows,
        drills=drills,
    )


DQ_PURCHASES = register(
    Report(
        id="dq_purchases",
        group="Data quality",
        title="Purchases with gaps",
        purpose="Purchases with a placeholder number, a missing or "
        "implausible order date, no web address, a zero-cost item, or no "
        "items at all.",
        params=DqPurchasesParams,
        run=_dq_purchases,
    )
)


# ---------------------------------------------------------------------------
# dq_locations
# ---------------------------------------------------------------------------


class DqLocationsParams(BaseModel):
    """No parameters: every live item's storage location, every time."""


#: The row for items with no `storage_location_id` at all -- not one of the
#: console's own location labels, so it cannot collide with one.
_NONE_RECORDED = "None recorded"

_LOCATION_COLUMNS = [
    Column("location", "Location", "text"),
    Column("items", "Items", "count"),
    Column("total_cost", "Total cost", "money"),
]


def _dq_locations(db: Session, _params: DqLocationsParams) -> ReportResult:
    """Live items by storage location, labeled as the console's own picker.

    Grouped in SQL by `storage_location_id`, then relabeled and re-grouped in
    Python by `item_history.location_label` -- the same function
    `LocationSelect` reads its options from -- because two different
    locations can share one label (two boxes of the same kind, neither
    naming an institution or an identifier, both fall back to their kind's
    own label) and this report counts by what a reader sees, not by a row id
    a reader never does.

    No drill: the inventory search has no filter for a specific storage
    location (`app.inventory_search`'s filters), so no search page could
    show exactly one row's items.
    """
    agg_stmt = (
        select(
            _I.c.storage_location_id.label("location_id"),
            func.count().label("items"),
            func.sum(_I.c.total_cost).label("total_cost"),
        )
        .select_from(_I)
        .where(_LIVE)
        .group_by(_I.c.storage_location_id)
    )
    agg_rows = db.execute(agg_stmt).mappings().all()

    location_ids = [r["location_id"] for r in agg_rows if r["location_id"] is not None]
    labels_by_id: dict[int, str] = {}
    if location_ids:
        locations = db.scalars(
            select(StorageLocation)
            .where(StorageLocation.id.in_(location_ids))
            .options(selectinload(StorageLocation.kind))
        ).all()
        labels_by_id = {loc.id: location_label(loc) for loc in locations}

    by_label: dict[str, dict[str, object]] = {}
    none_items = 0
    none_cost = Decimal("0")
    for row in agg_rows:
        cost = cast("Decimal | None", row["total_cost"]) or Decimal("0")
        location_id = row["location_id"]
        if location_id is None:
            none_items += row["items"]
            none_cost += cost
            continue
        slot = by_label.setdefault(
            labels_by_id[location_id], {"items": 0, "total_cost": Decimal("0")}
        )
        slot["items"] = cast("int", slot["items"]) + row["items"]
        slot["total_cost"] = cast("Decimal", slot["total_cost"]) + cost

    rows: list[dict[str, object]] = [
        {"location": label, "items": data["items"], "total_cost": data["total_cost"]}
        for label, data in sorted(by_label.items(), key=lambda kv: kv[0].casefold())
    ]
    rows.append(
        {"location": _NONE_RECORDED, "items": none_items, "total_cost": none_cost}
    )

    total_items = none_items + sum(
        cast("int", data["items"]) for data in by_label.values()
    )
    total_cost = none_cost + sum(
        (cast("Decimal", data["total_cost"]) for data in by_label.values()),
        Decimal("0"),
    )

    totals: dict[str, object] = {
        "location": "All locations",
        "items": total_items,
        "total_cost": total_cost,
    }

    return ReportResult(
        columns=_LOCATION_COLUMNS,
        rows=rows,
        totals=totals,
        drills=[None] * len(rows),
        notes=[
            "No drill: the inventory search has no filter for a specific "
            "storage location."
        ],
    )


DQ_LOCATIONS = register(
    Report(
        id="dq_locations",
        group="Data quality",
        title="Where items are",
        purpose="Live items by storage location, with items and total cost.",
        params=DqLocationsParams,
        run=_dq_locations,
    )
)
