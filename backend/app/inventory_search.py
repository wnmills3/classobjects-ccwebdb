"""Searching the coin and currency inventory views.

The two views exist so that coins and currency read as independent
inventories: a coin has a mint mark and a variety, a banknote has a series
letter and a seal colour, and forcing them into one grid means most columns
are blank most of the time. So the search is one implementation driven by two
specifications, rather than one query with a pile of conditionals.

**No user input ever reaches the SQL as text.** Column names come from the
specification's own allowlists -- a filter or sort the spec does not name is
rejected before a query is built -- and every value is a bound parameter.

**Decimals are rendered as strings on the way out.** FastAPI's encoder turns a
`Decimal` in a plain dict into a *float*, which is the one thing the rest of
this schema is careful never to do. Money and weight leave as strings.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

__all__ = ["COIN_VIEW", "CURRENCY_VIEW", "ViewSpec", "count_facets", "search"]


@dataclass(frozen=True)
class Filter:
    """One query parameter, and how it narrows the result."""

    column: str
    #: eq, ilike, gte or lte
    op: str = "eq"


@dataclass(frozen=True)
class ViewSpec:
    name: str
    view: str
    #: Returned columns, listed rather than `select *` so the response stays
    #: deliberate and a later view change cannot silently widen it.
    columns: tuple[str, ...]
    filters: dict[str, Filter]
    #: Columns the free-text `q` searches.
    search_columns: tuple[str, ...]
    sortable: tuple[str, ...]
    #: Columns worth counting, so a search panel can offer only what exists.
    facet_columns: tuple[str, ...]
    default_sort: str = "item_code"


_SHARED_COLUMNS = (
    "id",
    "item_code",
    "title",
    "denomination",
    "denomination_label",
    "country",
    "year_start",
    "year_end",
    "grade",
    "grade_value",
    "grade_designation",
    "grading_service",
    "authenticity",
    "status",
    "disposition",
    "storage_form",
    "storage_quantity",
    "error_type",
    "price",
    "shipping",
    "taxes",
    "total_cost",
    "numismatic_value",
    "local_catalog_number",
    "parent_item_id",
    "created_at",
)

_SHARED_FILTERS = {
    "item_code": Filter("item_code", "ilike"),
    "country": Filter("country"),
    "grade": Filter("grade"),
    "grading_service": Filter("grading_service"),
    "status": Filter("status"),
    "disposition": Filter("disposition"),
    "denomination": Filter("denomination"),
    "error_type": Filter("error_type"),
    "year_min": Filter("year_start", "gte"),
    "year_max": Filter("year_start", "lte"),
    "grade_min": Filter("grade_value", "gte"),
    "grade_max": Filter("grade_value", "lte"),
}

_SHARED_SORTABLE = (
    "item_code",
    "title",
    "year_start",
    "price",
    "total_cost",
    "grade_value",
    "created_at",
)


COIN_VIEW = ViewSpec(
    name="coins",
    view="coin_inventory",
    columns=_SHARED_COLUMNS
    + (
        "item_kind",
        "bullion_form",
        "set_form",
        "mint",
        "mint_mark",
        "variety",
        "metal",
        "fineness",
        "gross_weight_ozt",
        "fine_weight_ozt",
        "weight_raw",
    ),
    filters={
        **_SHARED_FILTERS,
        "kind": Filter("item_kind"),
        "metal": Filter("metal"),
        "mint": Filter("mint_mark"),
        "bullion_form": Filter("bullion_form"),
        "set_form": Filter("set_form"),
    },
    search_columns=("title", "description", "item_code", "variety"),
    sortable=_SHARED_SORTABLE + ("fine_weight_ozt",),
    facet_columns=(
        "item_kind",
        "metal",
        "grade",
        "mint_mark",
        "country",
        "status",
        "disposition",
        "bullion_form",
    ),
)


CURRENCY_VIEW = ViewSpec(
    name="currency",
    view="currency_inventory",
    columns=_SHARED_COLUMNS
    + (
        "note_type",
        "series_year",
        "series_letter",
        "seal_color",
        "fed_district_letter",
        "fed_district_city",
        "serial_number",
        "friedberg_number",
        "friedberg_status",
        "treasurer",
        "secretary",
    ),
    filters={
        **_SHARED_FILTERS,
        "note_type": Filter("note_type"),
        "seal_color": Filter("seal_color"),
        "fed_district": Filter("fed_district_letter"),
        "series_year": Filter("series_year"),
        "series_letter": Filter("series_letter"),
        "friedberg_status": Filter("friedberg_status"),
        # A note's own printed serial, not a grading certificate.
        "serial_number": Filter("serial_number", "ilike"),
    },
    search_columns=("title", "description", "item_code", "serial_number"),
    sortable=_SHARED_SORTABLE + ("series_year",),
    facet_columns=(
        "note_type",
        "seal_color",
        "fed_district_letter",
        "grade",
        "series_year",
        "country",
        "status",
        "disposition",
    ),
)

VIEWS: dict[str, ViewSpec] = {
    COIN_VIEW.name: COIN_VIEW,
    CURRENCY_VIEW.name: CURRENCY_VIEW,
}


def _where(
    spec: ViewSpec, params: dict[str, Any], query: str | None
) -> tuple[str, dict[str, Any]]:
    """Build the WHERE clause from parameters the spec recognises."""
    clauses: list[str] = []
    bound: dict[str, Any] = {}

    for key, value in params.items():
        if value in (None, ""):
            continue
        spec_filter = spec.filters.get(key)
        if spec_filter is None:
            # Silently ignoring an unknown filter would quietly return
            # everything when a caller misspells one.
            raise KeyError(key)

        placeholder = f"p_{key}"
        column = spec_filter.column
        if spec_filter.op == "ilike":
            clauses.append(f"{column} ILIKE :{placeholder}")
            bound[placeholder] = f"%{value}%"
        elif spec_filter.op == "gte":
            clauses.append(f"{column} >= :{placeholder}")
            bound[placeholder] = value
        elif spec_filter.op == "lte":
            clauses.append(f"{column} <= :{placeholder}")
            bound[placeholder] = value
        else:
            clauses.append(f"{column} = :{placeholder}")
            bound[placeholder] = value

    if query:
        parts = [f"coalesce({c}, '') ILIKE :p_q" for c in spec.search_columns]
        clauses.append("(" + " OR ".join(parts) + ")")
        bound["p_q"] = f"%{query}%"

    return (" WHERE " + " AND ".join(clauses) if clauses else ""), bound


def _plain(value: Any) -> Any:
    """JSON-safe without letting money or weight become a float."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def search(
    db: Session,
    spec: ViewSpec,
    *,
    params: dict[str, Any],
    query: str | None = None,
    sort: str | None = None,
    descending: bool = False,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """One page of rows, and how many matched in total."""
    sort_column = sort or spec.default_sort
    if sort_column not in spec.sortable:
        raise ValueError(f"cannot sort by {sort_column!r}")

    where, bound = _where(spec, params, query)
    direction = "DESC" if descending else "ASC"
    columns = ", ".join(spec.columns)

    total = db.execute(
        text(f"SELECT count(*) FROM {spec.view}{where}"), bound
    ).scalar_one()

    rows = db.execute(
        text(
            f"SELECT {columns} FROM {spec.view}{where} "
            # NULLS LAST so unrecorded values sort to the end rather than
            # occupying the first page of every ascending search.
            f"ORDER BY {sort_column} {direction} NULLS LAST, item_code "
            f"LIMIT :p_limit OFFSET :p_offset"
        ),
        {**bound, "p_limit": limit, "p_offset": offset},
    ).mappings().all()

    return [{k: _plain(v) for k, v in row.items()} for row in rows], total


def count_facets(
    db: Session, spec: ViewSpec, *, params: dict[str, Any], query: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """How many rows each classifier value would match.

    A search panel over several thousand items is close to useless without
    this: the vocabulary has fifty-odd grades and a real collection uses a
    fraction of them, so offering all of them buries the ones that exist.

    Counted against the *current* filters, so the numbers describe what
    narrowing further would actually do.
    """
    where, bound = _where(spec, params, query)
    facets: dict[str, list[dict[str, Any]]] = {}

    for column in spec.facet_columns:
        rows = db.execute(
            text(
                f"SELECT {column} AS value, count(*) AS n FROM {spec.view}{where} "
                f"{'AND' if where else 'WHERE'} {column} IS NOT NULL "
                f"GROUP BY {column} ORDER BY n DESC, {column} LIMIT 60"
            ),
            bound,
        ).mappings().all()
        facets[column] = [
            {"value": _plain(r["value"]), "count": r["n"]} for r in rows
        ]
    return facets
