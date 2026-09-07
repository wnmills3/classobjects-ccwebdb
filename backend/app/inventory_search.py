"""Searching the coin and currency inventories.

Coins and currency are searched separately because the columns that matter
differ: a coin has a mint mark and a variety, a banknote has a series letter, a
seal colour and its own printed serial. One implementation, two
specifications.

**Why this queries base tables rather than the inventory views.** The obvious
implementation reads `coin_inventory`, which is what those views are for. It is
also thirty times slower, because the view joins fourteen tables and every
query pays for all of them -- a `count(*)` that needs no join at all, a facet
that needs one. Measured over 6,370 coins: a page plus facets took 220 ms
through the view and 7 ms against the base tables with only the joins each
query actually needs.

That measurement settled a design question. Copying classifier labels onto
every item would have made searching fast, at the price of a second copy of
every label that can drift from the first. It turned out to buy nothing: the
speed was available without giving up the single source of truth, because the
cost was never the normalisation. It was asking a fourteen-way join for one
column.

The views remain the right thing for reading a whole item.

**No user input reaches the SQL as text.** Column expressions come from the
specification's own allowlists; every value is a bound parameter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

__all__ = [
    "COIN_VIEW",
    "CURRENCY_VIEW",
    "VIEWS",
    "ViewSpec",
    "count_facets",
    "search",
]


@dataclass(frozen=True)
class Col:
    """A column: the SQL that produces it, and the joins it needs.

    Joins are a tuple of individual clauses rather than one string, so that a
    clause needed by two different columns is emitted once. Concatenating them
    makes deduplication compare whole strings, and PostgreSQL rejects the same
    alias twice.
    """

    sql: str
    join: tuple[str, ...] = ()


@dataclass(frozen=True)
class Filt:
    """A query parameter: the SQL it constrains, how, and the joins it needs."""

    sql: str
    op: str = "eq"
    join: tuple[str, ...] = ()


@dataclass(frozen=True)
class Facet:
    """How one facet is counted, and against which column.

    A facet counts an indexed id column on the item table, then resolves
    those ids to codes in a second, tiny query.

    Grouping by `grade_id` uses the foreign key index directly. Grouping by
    `grade.code` would force the join first, which is most of the cost.
    """

    id_column: str
    table: str


@dataclass(frozen=True)
class ViewSpec:
    """One searchable inventory: its columns, filters, sorts and facets."""

    name: str
    #: Everything reads from the item table; the views are for whole rows.
    base: str = "inventory_item i"
    where: tuple[str, ...] = ()
    columns: dict[str, Col] = field(default_factory=dict)
    filters: dict[str, Filt] = field(default_factory=dict)
    search_columns: tuple[str, ...] = ()
    sortable: tuple[str, ...] = ()
    facets: dict[str, Facet] = field(default_factory=dict)
    default_sort: str = "item_code"

    def joins_for(self, groups: list[tuple[str, ...]]) -> str:
        """Only the joins the current query needs, each emitted once."""
        seen: list[str] = []
        for group in groups:
            for clause in group:
                if clause not in seen:
                    seen.append(clause)
        return " ".join(seen)


# --- joins, named once -----------------------------------------------------
_J_GRADE = "LEFT JOIN grade g ON g.id = i.grade_id"
_J_GRADE_DES = "LEFT JOIN grade_designation gd ON gd.id = i.grade_designation_id"
_J_SERVICE = "LEFT JOIN grading_service gs ON gs.id = i.grading_service_id"
_J_KIND = "JOIN item_kind k ON k.id = i.item_kind_id"
_J_COUNTRY = "LEFT JOIN country c ON c.id = i.country_id"
_J_DENOM = "LEFT JOIN denomination d ON d.id = i.denomination_id"
_J_STATUS = "JOIN item_status st ON st.id = i.status_id"
_J_DISP = "JOIN disposition disp ON disp.id = i.disposition_id"
_J_STORAGE = "JOIN storage_form stf ON stf.id = i.storage_form_id"
_J_AUTH = "JOIN authenticity a ON a.id = i.authenticity_id"
_J_ERROR = "LEFT JOIN error_type et ON et.id = i.error_type_id"
_J_METAL = "LEFT JOIN metal mt ON mt.id = i.metal_id"
_J_BULLION = "LEFT JOIN bullion_form bf ON bf.id = i.bullion_form_id"
_J_SET = "LEFT JOIN set_form sf ON sf.id = i.set_form_id"
_J_COIN_DETAIL = "LEFT JOIN coin_detail cd ON cd.inventory_item_id = i.id"
_J_MINT = "LEFT JOIN mint m ON m.id = cd.mint_id"
_J_CUR_DETAIL = "LEFT JOIN currency_detail cud ON cud.inventory_item_id = i.id"
_J_NOTE_TYPE = "LEFT JOIN note_type nt ON nt.id = cud.note_type_id"
_J_SEAL = "LEFT JOIN seal_color sc ON sc.id = cud.seal_color_id"
_J_DISTRICT = "LEFT JOIN fed_district fd ON fd.id = cud.fed_district_id"
_J_FRIEDBERG = "LEFT JOIN friedberg_number fr ON fr.id = cud.friedberg_id"


_SHARED_COLUMNS: dict[str, Col] = {
    "id": Col("i.id"),
    "item_code": Col("i.item_code"),
    "title": Col("i.title"),
    # The spreadsheet's leftmost column was the denomination, so `title` holds
    # "0.25", "Mint Set", "5" -- not a name. What a person recognises the item
    # by lives in `description`, which is why it is returned as well and is
    # what the browse screens show.
    "description": Col("i.description"),
    "year_start": Col("i.year_start"),
    "year_end": Col("i.year_end"),
    "storage_quantity": Col("i.storage_quantity"),
    "price": Col("i.price"),
    "shipping": Col("i.shipping"),
    "taxes": Col("i.taxes"),
    "total_cost": Col("i.total_cost"),
    "numismatic_value": Col("i.numismatic_value"),
    "local_catalog_number": Col("i.local_catalog_number"),
    "parent_item_id": Col("i.parent_item_id"),
    "created_at": Col("i.created_at"),
    "grade": Col("g.code", (_J_GRADE,)),
    "grade_value": Col("g.numeric_value", (_J_GRADE,)),
    "grade_designation": Col("gd.code", (_J_GRADE_DES,)),
    "grading_service": Col("gs.code", (_J_SERVICE,)),
    "country": Col("c.code", (_J_COUNTRY,)),
    "denomination": Col("d.code", (_J_DENOM,)),
    "denomination_label": Col("d.label", (_J_DENOM,)),
    "status": Col("st.code", (_J_STATUS,)),
    "disposition": Col("disp.code", (_J_DISP,)),
    "storage_form": Col("stf.code", (_J_STORAGE,)),
    "authenticity": Col("a.code", (_J_AUTH,)),
    "error_type": Col("et.code", (_J_ERROR,)),
}

_SHARED_FILTERS: dict[str, Filt] = {
    "item_code": Filt("i.item_code", "ilike"),
    "country": Filt("c.code", join=(_J_COUNTRY,)),
    "grade": Filt("g.code", join=(_J_GRADE,)),
    "grading_service": Filt("gs.code", join=(_J_SERVICE,)),
    "status": Filt("st.code", join=(_J_STATUS,)),
    "disposition": Filt("disp.code", join=(_J_DISP,)),
    "denomination": Filt("d.code", join=(_J_DENOM,)),
    "error_type": Filt("et.code", join=(_J_ERROR,)),
    "year_min": Filt("i.year_start", "gte"),
    "year_max": Filt("i.year_start", "lte"),
    "grade_min": Filt("g.numeric_value", "gte", (_J_GRADE,)),
    "grade_max": Filt("g.numeric_value", "lte", (_J_GRADE,)),
}

_SHARED_SORT = (
    "item_code",
    "title",
    "year_start",
    "price",
    "total_cost",
    "grade_value",
    "created_at",
)

_SHARED_FACETS: dict[str, Facet] = {
    "grade": Facet("grade_id", "grade"),
    "country": Facet("country_id", "country"),
    "status": Facet("status_id", "item_status"),
    "disposition": Facet("disposition_id", "disposition"),
}


COIN_VIEW = ViewSpec(
    name="coins",
    where=(
        "i.split_at IS NULL",
        "k.code IN ('coin', 'bullion', 'set', 'medal', 'token')",
    ),
    columns={
        **_SHARED_COLUMNS,
        "item_kind": Col("k.code", (_J_KIND,)),
        "bullion_form": Col("bf.code", (_J_BULLION,)),
        "set_form": Col("sf.code", (_J_SET,)),
        "mint": Col("m.label", (_J_COIN_DETAIL, _J_MINT)),
        "mint_mark": Col("m.mark", (_J_COIN_DETAIL, _J_MINT)),
        "variety": Col("cd.variety", (_J_COIN_DETAIL,)),
        "metal": Col("mt.code", (_J_METAL,)),
        "fineness": Col("i.fineness"),
        "gross_weight_ozt": Col("i.gross_weight_ozt"),
        "fine_weight_ozt": Col("i.fine_weight_ozt"),
        "weight_raw": Col("i.weight_raw"),
    },
    filters={
        **_SHARED_FILTERS,
        "kind": Filt("k.code", join=(_J_KIND,)),
        "metal": Filt("mt.code", join=(_J_METAL,)),
        "mint": Filt("m.mark", join=(_J_COIN_DETAIL, _J_MINT)),
        "bullion_form": Filt("bf.code", join=(_J_BULLION,)),
        "set_form": Filt("sf.code", join=(_J_SET,)),
    },
    search_columns=("i.title", "i.description", "i.item_code"),
    sortable=(*_SHARED_SORT, "fine_weight_ozt"),
    facets={
        **_SHARED_FACETS,
        "item_kind": Facet("item_kind_id", "item_kind"),
        "metal": Facet("metal_id", "metal"),
        "bullion_form": Facet("bullion_form_id", "bullion_form"),
    },
)


CURRENCY_VIEW = ViewSpec(
    name="currency",
    where=("i.split_at IS NULL", "k.code = 'currency'"),
    columns={
        **_SHARED_COLUMNS,
        "item_kind": Col("k.code", (_J_KIND,)),
        "note_type": Col("nt.code", (_J_CUR_DETAIL, _J_NOTE_TYPE)),
        "series_year": Col("cud.series_year", (_J_CUR_DETAIL,)),
        "series_letter": Col("cud.series_letter", (_J_CUR_DETAIL,)),
        "seal_color": Col("sc.code", (_J_CUR_DETAIL, _J_SEAL)),
        "fed_district_letter": Col("fd.letter", (_J_CUR_DETAIL, _J_DISTRICT)),
        "fed_district_city": Col("fd.city", (_J_CUR_DETAIL, _J_DISTRICT)),
        "serial_number": Col("cud.serial_number", (_J_CUR_DETAIL,)),
        "friedberg_number": Col("fr.fr_number", (_J_CUR_DETAIL, _J_FRIEDBERG)),
        "friedberg_status": Col("cud.friedberg_status", (_J_CUR_DETAIL,)),
    },
    filters={
        **_SHARED_FILTERS,
        "note_type": Filt("nt.code", join=(_J_CUR_DETAIL, _J_NOTE_TYPE)),
        "seal_color": Filt("sc.code", join=(_J_CUR_DETAIL, _J_SEAL)),
        "fed_district": Filt("fd.letter", join=(_J_CUR_DETAIL, _J_DISTRICT)),
        "series_year": Filt("cud.series_year", join=(_J_CUR_DETAIL,)),
        "series_letter": Filt("cud.series_letter", join=(_J_CUR_DETAIL,)),
        "friedberg_status": Filt("cud.friedberg_status", join=(_J_CUR_DETAIL,)),
        "serial_number": Filt("cud.serial_number", "ilike", (_J_CUR_DETAIL,)),
    },
    search_columns=("i.title", "i.description", "i.item_code"),
    sortable=(*_SHARED_SORT, "series_year"),
    facets={
        **_SHARED_FACETS,
        "note_type": Facet("note_type_id", "note_type"),
        "seal_color": Facet("seal_color_id", "seal_color"),
    },
)

VIEWS: dict[str, ViewSpec] = {v.name: v for v in (COIN_VIEW, CURRENCY_VIEW)}

#: Facets that live on currency_detail rather than on the item itself.
_DETAIL_FACETS = {"note_type", "seal_color"}


def _conditions(
    spec: ViewSpec, params: dict[str, Any], query: str | None
) -> tuple[list[str], list[tuple[str, ...]], dict[str, Any]]:
    clauses = list(spec.where)
    joins: list[tuple[str, ...]] = [(_J_KIND,)]  # every spec filters on kind
    bound: dict[str, Any] = {}

    for key, value in params.items():
        if value in (None, ""):
            continue
        f = spec.filters.get(key)
        if f is None:
            # Ignoring an unknown filter silently returns the whole collection
            # and looks like a successful search.
            raise KeyError(key)

        placeholder = f"p_{key}"
        joins.append(f.join)
        if f.op == "ilike":
            clauses.append(f"{f.sql} ILIKE :{placeholder}")
            bound[placeholder] = f"%{value}%"
        elif f.op == "gte":
            clauses.append(f"{f.sql} >= :{placeholder}")
            bound[placeholder] = value
        elif f.op == "lte":
            clauses.append(f"{f.sql} <= :{placeholder}")
            bound[placeholder] = value
        else:
            clauses.append(f"{f.sql} = :{placeholder}")
            bound[placeholder] = value

    if query:
        parts = [f"coalesce({c}, '') ILIKE :p_q" for c in spec.search_columns]
        clauses.append("(" + " OR ".join(parts) + ")")
        bound["p_q"] = f"%{query}%"

    return clauses, joins, bound


def _plain(value: object) -> object:
    """JSON-safe without letting money or weight become a float.

    FastAPI's encoder turns a Decimal inside a plain dict into a float, which
    is the one thing the rest of this schema is careful never to do.
    """
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
    sort_key = sort or spec.default_sort
    if sort_key not in spec.sortable:
        raise ValueError(f"cannot sort by {sort_key!r}")

    clauses, joins, bound = _conditions(spec, params, query)
    where = " WHERE " + " AND ".join(clauses)

    # The count needs no display joins at all -- only whatever the filters
    # reach for.
    total = db.execute(
        text(f"SELECT count(*) FROM {spec.base} {spec.joins_for(joins)}{where}"),
        bound,
    ).scalar_one()

    select_parts = [f"{col.sql} AS {name}" for name, col in spec.columns.items()]
    display_joins = spec.joins_for([c.join for c in spec.columns.values()])
    sort_sql = spec.columns[sort_key].sql
    direction = "DESC" if descending else "ASC"

    # Two steps, because one is eleven times slower.
    #
    # Selecting every display column and *then* applying LIMIT makes
    # PostgreSQL join sixteen tables across all 6,370 matching rows before
    # discarding all but fifty. Picking the fifty ids first -- which needs only
    # the joins the filters and the sort actually use -- and joining the
    # display tables onto those fifty costs 8 ms against 96 ms.
    #
    # NULLS LAST so unrecorded values sort to the end rather than filling the
    # first page of every ascending search. The ordering is repeated on the
    # outer query: a join does not preserve the inner ordering.
    filter_joins = spec.joins_for([*joins, spec.columns[sort_key].join])
    rows = (
        db.execute(
            text(
                f"SELECT {', '.join(select_parts)} FROM ("
                f"  SELECT i.id, {sort_sql} AS sort_key, i.item_code"
                f"  FROM {spec.base} {filter_joins}{where}"
                f"  ORDER BY {sort_sql} {direction} NULLS LAST, i.item_code"
                f"  LIMIT :p_limit OFFSET :p_offset"
                f") picked "
                f"JOIN {spec.base} ON i.id = picked.id {display_joins} "
                f"ORDER BY picked.sort_key {direction} NULLS LAST, picked.item_code"
            ),
            {**bound, "p_limit": limit, "p_offset": offset},
        )
        .mappings()
        .all()
    )

    return [{k: _plain(v) for k, v in row.items()} for row in rows], total


def count_facets(
    db: Session, spec: ViewSpec, *, params: dict[str, Any], query: str | None = None
) -> dict[str, list[dict[str, Any]]]:
    """How many rows each classifier value would match.

    A panel over thousands of items is close to useless without this: the
    vocabulary defines fifty-odd grades and a real collection uses a dozen, so
    offering all of them buries the ones present. Counted against the current
    filters, so the numbers describe what narrowing further would do.

    Grouped by foreign key id, which uses the existing indexes, then resolved
    to codes in one small lookup per table. Grouping by the code instead would
    force every join first, which was most of the old cost.
    """
    clauses, joins, bound = _conditions(spec, params, query)
    where = " WHERE " + " AND ".join(clauses)

    results: dict[str, list[dict[str, Any]]] = {}
    for name, facet in spec.facets.items():
        source = spec.base
        facet_joins = list(joins)
        column = f"i.{facet.id_column}"
        if name in _DETAIL_FACETS:
            facet_joins.append((_J_CUR_DETAIL,))
            column = f"cud.{facet.id_column}"

        counts = db.execute(
            text(
                f"SELECT {column} AS fid, count(*) AS n "
                f"FROM {source} {spec.joins_for(facet_joins)}{where} "
                f"AND {column} IS NOT NULL "
                f"GROUP BY {column} ORDER BY n DESC LIMIT 60"
            ),
            bound,
        ).all()

        if not counts:
            results[name] = []
            continue

        ids = [row.fid for row in counts]
        labels: dict[int, str] = dict(
            db.execute(
                text(f"SELECT id, code FROM {facet.table} WHERE id = ANY(:ids)"),
                {"ids": ids},
            ).all()
        )
        results[name] = [
            {"value": labels.get(row.fid), "count": row.n}
            for row in counts
            if labels.get(row.fid) is not None
        ]

    return results
