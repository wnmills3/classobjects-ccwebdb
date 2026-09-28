"""Data quality: what is missing or wrong in the record.

Two reports. `dq_issues` counts every named check from `app.issues` across
both inventory views, reusing `inventory_search.count_issues` so a row's
count can never disagree with its own drill-down's search. `dq_completeness`
reports, per item kind, the percent of live items with each of ten fields
filled in -- counted with the exact same SQL text the `missing=<field>`
filter (`inventory_search.MISSING_FIELDS`) uses to find a field empty, so a
report cell and its drill-down search agree by construction, not only by
test.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from pydantic import BaseModel
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.sql.util import ClauseAdapter

from ..inventory_search import COIN_VIEW, CURRENCY_VIEW, MISSING_FIELDS, count_issues
from ..live import live_item
from ..models import CurrencyDetail, InventoryItem, ItemKind
from .base import Column, Report, ReportResult
from .registry import register

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

#: `inventory_item`, `item_kind` and `currency_detail`, aliased `i`/`k`/`cud`
#: -- exactly how `inventory_search`'s own queries alias them (`_J_KIND`,
#: `_J_CUR_DETAIL`) -- so `MISSING_FIELDS[key].sql`'s text, written against
#: those same aliases for the `missing=` filter, can be reused here verbatim
#: rather than restated against different table names. One definition of
#: "is this field missing", read by the filter and this report alike.
_I = InventoryItem.__table__.alias("i")
_K = ItemKind.__table__.alias("k")
_CUD = CurrencyDetail.__table__.alias("cud")

#: `live_item()` (`app.live`), the shared live-row predicate, names
#: `InventoryItem` unaliased; `ClauseAdapter` rewrites its column references
#: onto `_I` so it composes with a query that joins `inventory_item` as `i`,
#: the way `MISSING_FIELDS`'s text (`i.year_start`, and so on) requires.
_LIVE = ClauseAdapter(_I).traverse(live_item())

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
        .group_by(_K.c.code, _K.c.label)
        .having(func.count() > 0)
        .order_by(_K.c.code)
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
