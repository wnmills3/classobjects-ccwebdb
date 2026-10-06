"""Data quality: what is missing or wrong in the record.

Eight reports. `dq_issues` counts every named check from `app.issues` across
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
physically are. `dq_series_years` lists coins dated outside their design
series' years, with the same predicate as `issue=year_outside_series`.
`dq_series_review` lists the items `app.series_classify` leaves for a
person, by running that pass's own decision.
"""

from __future__ import annotations

from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Literal, cast
from urllib.parse import urlencode

from pydantic import BaseModel, Field
from sqlalchemy import RowMapping, and_, case, func, select, text
from sqlalchemy.orm import Session, selectinload

from ..field_sources import (
    COMPOSITION,
    HELD,
    NOTE_ISSUE,
    RATING,
    SERIAL_DISTRICT,
    SERIES_BACKFILL,
    SERIES_CLASSIFY,
    SERIES_MATCH,
    SUGGESTION,
)
from ..inventory_search import (
    COIN_VIEW,
    CURRENCY_VIEW,
    MISSING_FIELDS,
    count_issues,
    view_path,
)
from ..issues import COIN_ISSUES
from ..item_history import location_label
from ..models import (
    CurrencyDetail,
    Denomination,
    Image,
    ItemFieldReview,
    ItemFieldSource,
    ItemImage,
    ItemStatus,
    PurchaseOrder,
    Series,
    StorageLocation,
    Vendor,
)
from ..purchases import GENERATED, WEB_ADDRESS
from ..series_classify import classify
from .base import Column, Report, ReportResult, local_date
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
    "DQ_SERIES_REVIEW",
    "DQ_SERIES_YEARS",
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
    restriction for a field (year, country, photograph, storage location,
    listing link, seller's item id), this report follows it in applying that
    field to every kind -- a judgment call, not a rule read from `issues.py`.
    Series is the exception, optional for currency
    (`MISSING_FIELDS["series"]`).

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

    `kind=` is added the same way `kind_query_string` (`live_params.py`) adds it --
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

#: A reader's title for the columns `item_field_source.field_name` actually
#: holds (`app.classifier_defaults`' `NOTE_COLUMNS`/`COMPOSITION_COLUMNS`,
#: and `series_id`) -- not exhaustive, since a future pass can derive a
#: field no rule fills today; a field missing here still shows, as its own
#: raw column name, rather than vanishing from the report.
_FIELD_TITLES: dict[str, str] = {
    "series_id": "Series",
    "note_type_id": "Note type",
    "seal_color_id": "Seal color",
    "signature_combination_id": "Signature combination",
    "fed_district_id": "Federal Reserve district",
    "composition_id": "Composition",
    "metal_id": "Metal",
    "fineness": "Fineness",
    "gross_weight_ozt": "Gross weight",
    "fine_weight_ozt": "Fine weight",
    "rating": "Rating",
}

#: A reader's name for each rule code `item_field_source.derived_by` records
#: (`app.field_sources`' own constants). `HELD` is deliberately absent: a
#: held field is excluded from this report entirely, never shown as a rule.
_RULE_TITLES: dict[str, str] = {
    NOTE_ISSUE: "Note issue lookup",
    SERIAL_DISTRICT: "Serial-to-district lookup",
    COMPOSITION: "Composition lookup",
    SERIES_MATCH: "Series match",
    SERIES_CLASSIFY: "Series classification",
    SERIES_BACKFILL: "Series backfill",
    SUGGESTION: "Suggestion",
    RATING: "Rating",
}


def _dq_derived(db: Session, _params: DqDerivedParams) -> ReportResult:
    """Field x rule: `item_field_source` rows a person has not confirmed.

    "Confirmed" means an `item_field_review` row for the *same item and the
    same field* -- the pairing `item_field_review`'s own docstring describes
    (per field, not per item) and the one `ItemFieldSource`/`item_field_review`
    both key on (`inventory_item_id`, `field_name`). `HELD` rows are excluded:
    a field a person emptied on purpose is not "filled by a rule" at all.

    This is a different, finer question than `issue=unreviewed`
    (`app.issues`), which asks only whether an item carries *any* review row,
    regardless of field. An item reviewed on one field (say, grade) and
    derived but unconfirmed on another (say, series) is counted here, on its
    series row -- but it does *not* trip `issue=unreviewed` at all, since it
    does have a review row, just not one for series. The reverse also
    happens: an item with no review rows at all trips `issue=unreviewed` but
    contributes nothing here unless some rule has also derived one of its
    fields. Because the two measures disagree in both directions, a row's
    count here cannot be relied on to equal what `issue=unreviewed` would
    return, so no row links to it.
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
        field_name = row["field_name"]
        rule = row["rule"]
        rows.append(
            {
                "field": _FIELD_TITLES.get(field_name, field_name),
                "rule": _RULE_TITLES.get(rule, rule),
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
            "Each row counts one field and the rule that filled it; an "
            "item with two such fields still needing confirmation counts "
            "once for each. That is a different question from how many "
            "items have nothing confirmed at all, so no row opens a "
            "search."
        ],
    )


DQ_DERIVED = register(
    Report(
        id="dq_derived",
        group="Data quality",
        title="Filled by a rule, not yet confirmed",
        purpose="Fields a machine pass filled in, and the rule that filled "
        "each one, that nobody has confirmed by examination.",
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
    """This purchase's own gaps, in the fixed order.

    "Entry" is `created_at`'s own local calendar date (`base.local_date`),
    since the database session's zone need not be the application's.
    """
    gaps: list[str] = []
    order_number = row["order_number"]
    if order_number is not None and GENERATED.match(order_number):
        gaps.append(_GENERATED_NUMBER)

    ordered_on = cast("date | None", row["ordered_on"])
    if ordered_on is None:
        gaps.append(_NO_ORDER_DATE)

    source_url = row["source_url"]
    if not source_url or not WEB_ADDRESS.match(source_url):
        gaps.append(_NO_WEB_ADDRESS)

    if ordered_on is not None:
        entry_date = local_date(row["created_at"])
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
    """Live items by storage location: one row per location, as the console lists them.

    Grouped in SQL by `storage_location_id`, one row per id: two boxes that
    happen to share a label (`item_history.location_label`, the same
    function `LocationSelect` reads its options from -- both falling back to
    their kind's own label, say, because neither names an institution or an
    identifier) are still two different places the owner might need to tell
    apart, so they are never merged into one row. A label more than one
    location carries is disambiguated with that location's own id --
    `Home (#12)` -- so a repeated label never reads as one place when it is
    two; a label naming exactly one location is shown plain.

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

    label_counts: dict[str, int] = {}
    for label in labels_by_id.values():
        label_counts[label] = label_counts.get(label, 0) + 1

    none_items = 0
    none_cost = Decimal("0")
    #: (label, location id, items, total cost) -- one entry per location,
    #: sorted below by label then id before it becomes a row.
    entries: list[tuple[str, int, int, Decimal]] = []
    for row in agg_rows:
        cost = cast("Decimal | None", row["total_cost"]) or Decimal("0")
        location_id = row["location_id"]
        if location_id is None:
            none_items += row["items"]
            none_cost += cost
            continue
        entries.append((labels_by_id[location_id], location_id, row["items"], cost))
    entries.sort(key=lambda entry: (entry[0].casefold(), entry[1]))

    rows: list[dict[str, object]] = []
    for label, location_id, items, cost in entries:
        display = f"{label} (#{location_id})" if label_counts[label] > 1 else label
        rows.append({"location": display, "items": items, "total_cost": cost})
    rows.append(
        {"location": _NONE_RECORDED, "items": none_items, "total_cost": none_cost}
    )

    total_items = none_items + sum(entry[2] for entry in entries)
    total_cost = none_cost + sum((entry[3] for entry in entries), Decimal("0"))

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


# ---------------------------------------------------------------------------
# dq_series_years
# ---------------------------------------------------------------------------


class DqSeriesYearsParams(BaseModel):
    """No parameters: every live coin with a series and a year, every time."""


#: The same SQL text `?issue=year_outside_series` runs, so the report and
#: its drill-down search can never disagree about which coins are listed.
_OUTSIDE_SERIES = COIN_ISSUES["year_outside_series"].sql


def _years_text(start: int, end: int | None) -> str:
    """``1990``, or ``1999-2009`` for a range."""
    return str(start) if end in (None, start) else f"{start}-{end}"


def _dq_series_years(db: Session, _params: DqSeriesYearsParams) -> ReportResult:
    """One row per live coin dated outside its series' years, by series then year.

    Coins only: a note's year is its series year, and the note lookup has
    its own check. Each row drills to the coin search narrowed to that one
    item, where it can be opened and corrected.
    """
    stmt = (
        select(
            _I.c.item_code,
            _I.c.source_title,
            _I.c.year_start,
            _I.c.year_end,
            Series.label.label("series"),
            Series.year_start.label("series_start"),
            Series.year_end.label("series_end"),
        )
        .select_from(_I)
        .join(_K, _K.c.id == _I.c.item_kind_id)
        .join(Series, Series.id == _I.c.series_id)
        .where(_LIVE, _K.c.code != "currency", text(_OUTSIDE_SERIES))
        .order_by(Series.label, _I.c.year_start, _I.c.item_code)
    )
    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for row in db.execute(stmt).mappings().all():
        start, end = row["series_start"], row["series_end"]
        rows.append(
            {
                "item": row["item_code"],
                "title": row["source_title"],
                "year": _years_text(row["year_start"], row["year_end"]),
                "series": row["series"],
                "series_years": f"{start} on" if end is None else f"{start}-{end}",
            }
        )
        drills.append(
            f"{view_path('coin')}?{urlencode({'item_code': row['item_code']})}"
        )
    return ReportResult(
        columns=[
            Column("item", "Item", "text"),
            Column("title", "Title", "text"),
            Column("year", "Year", "text"),
            Column("series", "Series", "text"),
            Column("series_years", "Series years", "text"),
        ],
        rows=rows,
        drills=drills,
    )


DQ_SERIES_YEARS = register(
    Report(
        id="dq_series_years",
        group="Data quality",
        title="Coins dated outside their series",
        purpose="Coins whose year falls outside their design series' years -- "
        "a typo, a tribute piece, or the wrong series.",
        params=DqSeriesYearsParams,
        run=_dq_series_years,
    )
)


# ---------------------------------------------------------------------------
# dq_series_review
# ---------------------------------------------------------------------------

#: Why an item is listed, as `app.series_classify` names it, and as a reader
#: is told it. In the order the report lists them: what is recorded wrong
#: first, then what only the piece in hand can settle.
_REVIEW_REASONS: dict[str, str] = {
    "conflict": "Description names a design the facts rule out",
    "disagrees": "Series set, but the facts rule it out",
    "boundary": "Facts allow several designs",
}


class DqSeriesReviewParams(BaseModel):
    """Which of the pass's three kinds of case to list."""

    show: Literal["all", "conflict", "disagrees", "boundary"] = Field(
        default="all", title="Show"
    )


def _dq_series_review(db: Session, params: DqSeriesReviewParams) -> ReportResult:
    """One row per live item the facts pass leaves for a person.

    The cases are `series_classify.classify`'s own, decided as the pass
    decides them, so this list and the pass's printed report cannot
    disagree. Nothing is written. Each row drills to the search narrowed to
    that one item, where it can be opened and corrected.
    """
    cases = [
        found_case
        for found_case in classify(db).review
        if params.show in ("all", found_case.reason)
    ]
    labels = dict(db.execute(select(Series.code, Series.label)).tuples().all())
    stmt = (
        select(
            _I.c.item_code,
            _K.c.code.label("kind"),
            _I.c.source_title,
            _I.c.description,
            _I.c.rating,
            _I.c.year_start,
            _I.c.year_end,
            CurrencyDetail.series_year,
            CurrencyDetail.series_letter,
            Denomination.label.label("denomination"),
            Series.label.label("series"),
        )
        .select_from(_I)
        .join(_K, _K.c.id == _I.c.item_kind_id)
        .join(Denomination, Denomination.id == _I.c.denomination_id, isouter=True)
        .join(Series, Series.id == _I.c.series_id, isouter=True)
        .join(CurrencyDetail, CurrencyDetail.inventory_item_id == _I.c.id, isouter=True)
        .where(
            _LIVE, _I.c.item_code.in_([found_case.item_code for found_case in cases])
        )
    )
    found = {row["item_code"]: row for row in db.execute(stmt).mappings().all()}

    order = list(_REVIEW_REASONS)
    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    counts: dict[str, int] = dict.fromkeys(order, 0)
    for found_case in sorted(
        cases, key=lambda c: (order.index(c.reason), c.designs, c.item_code)
    ):
        row = found.get(found_case.item_code)
        if row is None:
            continue  # the pass reads deleted items too; the report is live ones
        counts[found_case.reason] += 1
        if row["kind"] == "currency":
            year = (
                f"{row['series_year']}{row['series_letter'] or ''}"
                if row["series_year"] is not None
                else ""
            )
        else:
            year = (
                _years_text(row["year_start"], row["year_end"])
                if row["year_start"] is not None
                else ""
            )
        rows.append(
            {
                "item": found_case.item_code,
                "why": _REVIEW_REASONS[found_case.reason],
                "designs": " / ".join(
                    labels.get(code, code) for code in found_case.designs
                ),
                "denomination": row["denomination"] or "",
                "year": year,
                "series": row["series"] or "",
                # The description is what names a design; a title is often
                # only the face value.
                "described": row["description"] or row["source_title"],
                # The pass reads the rating too, and for a piece of a lot it
                # is the only text read: a design named nowhere in the
                # description is named here.
                "rating": row["rating"] or "",
            }
        )
        drills.append(
            f"{view_path(row['kind'])}?{urlencode({'item_code': found_case.item_code})}"
        )

    notes = [
        f"{label}: {counts[reason]}"
        for reason, label in _REVIEW_REASONS.items()
        if counts[reason]
    ]
    if rows:
        notes.append(
            "Designs is what the description or rating names (a conflict), the "
            "series now set (ruled out), or the designs the facts allow "
            "(several). Fix the denomination, year, series or rating on the "
            "item; nothing here is changed by running the report."
        )
    return ReportResult(
        columns=[
            Column("item", "Item", "text"),
            Column("why", "Why", "text"),
            Column("designs", "Designs", "text"),
            Column("denomination", "Denomination", "text"),
            Column("year", "Year", "text"),
            Column("series", "Series", "text"),
            Column("described", "Description", "text"),
            Column("rating", "Rating", "text"),
        ],
        rows=rows,
        drills=drills,
        notes=notes,
    )


DQ_SERIES_REVIEW = register(
    Report(
        id="dq_series_review",
        group="Data quality",
        title="Series to review",
        purpose="Items whose design series a person must settle: the "
        "description names a design the recorded denomination and year rule "
        "out, the series already set is ruled out by them, or they allow "
        "several designs.",
        params=DqSeriesReviewParams,
        run=_dq_series_review,
    )
)
