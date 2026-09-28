"""Data quality: what is missing or wrong in the record.

Two reports. `dq_issues` counts every named check from `app.issues` across
both inventory views, reusing `inventory_search.count_issues` so a row's
count can never disagree with its own drill-down's search. `dq_completeness`
reports, per item kind, the percent of live items with each of ten fields
filled in -- the same ten fields the `missing=<field>` filter
(`inventory_search.MISSING_FIELDS`) finds empty, so a report cell and its
drill-down search can never disagree either.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from ..inventory_search import COIN_VIEW, CURRENCY_VIEW, MISSING_FIELDS, count_issues
from ..live import live_item
from ..models import CurrencyDetail, InventoryItem, ItemKind
from . import register
from .base import Column, Report, ReportResult

__all__ = ["DQ_COMPLETENESS", "DQ_ISSUES"]


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
#: the console can build a cell's own drill-down as this row's drill
#: (`/inventory/currency` or `/inventory/coins?kind=<code>`) plus
#: `&missing=<key>`, without a second table naming the correspondence.
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

#: The same ten checks as `inventory_search.MISSING_FIELDS`, restated against
#: the real (unaliased) table names rather than that module's `i`/`k`/`cud`
#: aliases. The restatement is needed, not stylistic: this report's query
#: filters on `live_item()`, the shared live-row predicate (`app.live`), and
#: that predicate names `InventoryItem` directly -- it cannot be composed
#: with a query that aliases `inventory_item`, which is what the `missing=`
#: filter's own text needs (built inside `inventory_search._conditions`,
#: whose base table is always `inventory_item i`). Kind applicability is
#: read once, not restated: both this report and the filter read
#: `MISSING_FIELDS[key].kinds`, the shared table that decides which kinds a
#: field applies to, so that part cannot drift.
_FIELD_SQL: dict[str, str] = {
    "year": (
        "(CASE WHEN item_kind.code = 'currency' "
        "THEN currency_detail.series_year IS NULL "
        "ELSE inventory_item.year_start IS NULL END)"
    ),
    "denomination": "inventory_item.denomination_id IS NULL",
    "grade": "inventory_item.grade_id IS NULL",
    "country": "inventory_item.country_id IS NULL",
    "series": "inventory_item.series_id IS NULL",
    "metal": "inventory_item.metal_id IS NULL",
    "photo": (
        "NOT EXISTS (SELECT 1 FROM item_image "
        "WHERE item_image.inventory_item_id = inventory_item.id)"
    ),
    "storage_location": "inventory_item.storage_location_id IS NULL",
    "listing_link": "coalesce(inventory_item.listing_url, '') = ''",
    "sellers_item_id": "coalesce(inventory_item.sellers_item_id, '') = ''",
}

_ONE_DP = Decimal("0.1")


def _percent(filled: int, live: int) -> Decimal:
    """`filled` of `live`, as a Decimal percentage to one decimal place."""
    return (Decimal(filled) / Decimal(live) * 100).quantize(
        _ONE_DP, rounding=ROUND_HALF_UP
    )


def _applies(field: str, kind_code: str) -> bool:
    """Whether `field` applies to `kind_code`, from the one shared table."""
    kinds = MISSING_FIELDS[field].kinds
    return kinds is None or kind_code in kinds


def _drill(kind_code: str) -> str:
    """The kind's own inventory search -- what the row (not a cell) drills to."""
    if kind_code == "currency":
        return "/inventory/currency"
    return f"/inventory/coins?kind={kind_code}"


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
    """
    stmt = (
        select(
            ItemKind.code.label("kind_code"),
            ItemKind.label.label("kind_label"),
            func.count().label("live_items"),
            *(
                func.count().filter(text(f"NOT ({sql})")).label(key)
                for key, sql in _FIELD_SQL.items()
            ),
        )
        .select_from(InventoryItem)
        .join(ItemKind, ItemKind.id == InventoryItem.item_kind_id)
        .outerjoin(CurrencyDetail, CurrencyDetail.inventory_item_id == InventoryItem.id)
        .where(live_item())
        .group_by(ItemKind.code, ItemKind.label)
        .having(func.count() > 0)
        .order_by(ItemKind.code)
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
                        if _applies(key, row["kind_code"])
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
            "Denomination and grade apply only to coins and banknotes, and "
            "metal does not apply to currency; every other field is judged "
            "to apply to every kind, since app.issues states no kind "
            "restriction for it.",
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
