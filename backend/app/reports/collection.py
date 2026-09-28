"""Collection: what the collection is made of.

One report so far, `cb_holdings`: live items grouped by kind and then by
denomination, with items, pieces and total cost, a subtotal row per kind, and
an overall total. Money is summed in SQL -- three aggregate queries at
different grouping levels rather than one row's worth of Python addition --
so a subtotal and the grand total are each exactly what the database adds up,
never a value this module could get wrong by rounding or by missing a row.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, FromClause, func, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.util import ClauseAdapter

from ..inventory_search import MISSING_FIELDS
from ..live import live_item
from ..models import Denomination, Disposition, InventoryItem, ItemKind, ItemStatus
from .base import Column, Report, ReportResult
from .registry import register

__all__ = ["CB_HOLDINGS", "HoldingsParams"]

#: `inventory_item`, `item_kind` and `denomination`, aliased the way
#: `data_quality.py` aliases them -- `i`/`k` -- plus `d` for denomination, so
#: `live_item()` (written against the unaliased model) can be rewritten onto
#: `_I` with `ClauseAdapter` and composed into a query that joins
#: `inventory_item` under an alias.
_I = InventoryItem.__table__.alias("i")
_K = ItemKind.__table__.alias("k")
_D = Denomination.__table__.alias("d")
_ST = ItemStatus.__table__.alias("st")
_DISP = Disposition.__table__.alias("disp")

_LIVE = ClauseAdapter(_I).traverse(live_item())

#: The seeded `item_status` codes (`backend/data/reference/operations.json`),
#: plus `all` for no filter.
StatusParam = Literal[
    "ordered", "received", "canceled", "returned", "missing", "unknown", "all"
]
#: The seeded `disposition` codes, plus `all` for no filter.
DispositionParam = Literal[
    "held", "listed", "sold", "shipped", "delivered", "returned_by_buyer", "all"
]


class HoldingsParams(BaseModel):
    """Which live items to count: by acquisition status and sales disposition.

    Defaults to `received` and `held` -- what the owner has actually taken in
    and still has -- rather than everything ever ordered or ever sold. Either
    can be widened to `all`, which drops that filter entirely.
    """

    status: StatusParam = Field(default="received", title="Status")
    disposition: DispositionParam = Field(default="held", title="Disposition")


def _source_and_where(
    params: HoldingsParams,
) -> tuple[FromClause, list[ColumnElement[bool]]]:
    """The FROM clause and WHERE clauses every aggregate level shares.

    The status and disposition joins are added only when that parameter is
    not `all`: an unused join costs nothing to skip, and a query that never
    joins `item_status` when every status counts reads as exactly what it
    is -- no filter there.
    """
    src: FromClause = _I.join(_K, _K.c.id == _I.c.item_kind_id).outerjoin(
        _D, _D.c.id == _I.c.denomination_id
    )
    where: list[ColumnElement[bool]] = [_LIVE]
    if params.status != "all":
        src = src.join(_ST, _ST.c.id == _I.c.status_id)
        where.append(_ST.c.code == params.status)
    if params.disposition != "all":
        src = src.join(_DISP, _DISP.c.id == _I.c.disposition_id)
        where.append(_DISP.c.code == params.disposition)
    return src, where


def _query_string(
    kind_code: str,
    params: HoldingsParams,
    *,
    denom_code: str | None = None,
    missing_denom: bool = False,
) -> str:
    """One drill's console path: the kind's search, narrowed as this row is.

    Currency has its own search page and needs no `kind=`; every other kind
    searches `/inventory/coins?kind=<code>`. `denom_code` adds
    `denomination=<code>`; `missing_denom` adds `missing=denomination`
    instead -- the two are mutually exclusive, one row is never both a named
    denomination and "no denomination". `status`/`disposition` are added last,
    and only when that parameter is not `all`, matching `HoldingsParams`'
    own no-filter value.
    """
    query: dict[str, str] = {}
    if kind_code != "currency":
        query["kind"] = kind_code
    if denom_code is not None:
        query["denomination"] = denom_code
    elif missing_denom:
        query["missing"] = "denomination"
    if params.status != "all":
        query["status"] = params.status
    if params.disposition != "all":
        query["disposition"] = params.disposition
    path = "/inventory/currency" if kind_code == "currency" else "/inventory/coins"
    return f"{path}?{urlencode(query)}" if query else path


def _denomination_applies(kind_code: str) -> bool:
    """Whether `kind_code` can carry a denomination at all.

    Read from `MISSING_FIELDS["denomination"]` -- the same table the
    `missing=` filter and `dq_completeness` read -- so a "No denomination"
    row's drill agrees with that filter by construction: a kind outside its
    `kinds` set never gets a `missing=denomination` link, because that search
    would come back empty and disagree with this row's own count.
    """
    kinds = MISSING_FIELDS["denomination"].kinds
    return kinds is None or kind_code in kinds


def _cb_holdings(db: Session, params: HoldingsParams) -> ReportResult:
    """Kind x denomination: items, pieces and total cost, with subtotals.

    Three queries at three grouping levels -- by kind and denomination, by
    kind alone, and not grouped at all -- rather than one query whose rows
    this function then adds up in Python: each total is exactly what
    PostgreSQL's own `sum` returns for that group, so a subtotal cannot drift
    from the sum of its rows, nor the grand total from the sum of the
    subtotals, by so much as a cent.
    """
    columns = [
        Column("kind", "Kind", "text"),
        Column("denomination", "Denomination", "text"),
        Column("items", "Items", "count"),
        Column("pieces", "Pieces", "count"),
        Column("total_cost", "Total cost", "money"),
    ]
    src, where = _source_and_where(params)

    overall = (
        db.execute(
            select(
                func.count().label("items"),
                func.sum(_I.c.piece_count).label("pieces"),
                func.sum(_I.c.total_cost).label("total_cost"),
            )
            .select_from(src)
            .where(*where)
        )
        .mappings()
        .one()
    )

    if not overall["items"]:
        return ReportResult(
            columns=columns,
            rows=[],
            totals=None,
            drills=[],
            notes=["Nothing matches these settings."],
        )

    denom_rows = (
        db.execute(
            select(
                _K.c.id.label("kind_id"),
                _K.c.code.label("kind_code"),
                _K.c.label.label("kind_label"),
                _D.c.id.label("denom_id"),
                _D.c.code.label("denom_code"),
                _D.c.label.label("denom_label"),
                _D.c.sort_order.label("denom_sort"),
                func.count().label("items"),
                func.sum(_I.c.piece_count).label("pieces"),
                func.sum(_I.c.total_cost).label("total_cost"),
            )
            .select_from(src)
            .where(*where)
            .group_by(
                _K.c.id,
                _K.c.code,
                _K.c.label,
                _D.c.id,
                _D.c.code,
                _D.c.label,
                _D.c.sort_order,
            )
            .order_by(
                _K.c.sort_order,
                # A tiebreak, not a sort key of its own: two kinds sharing a
                # `sort_order` must still stay contiguous, which is what the
                # subtotal loop below depends on -- it closes a kind's
                # subtotal the moment `kind_code` changes, so an
                # out-of-order pair of rows for the same kind would split
                # into two subtotal rows for it.
                _K.c.id,
                _D.c.sort_order.asc().nulls_last(),
                _D.c.label.asc().nulls_last(),
            )
        )
        .mappings()
        .all()
    )

    kind_subtotals = {
        row["kind_code"]: row
        for row in db.execute(
            select(
                _K.c.code.label("kind_code"),
                _K.c.label.label("kind_label"),
                func.count().label("items"),
                func.sum(_I.c.piece_count).label("pieces"),
                func.sum(_I.c.total_cost).label("total_cost"),
            )
            .select_from(src)
            .where(*where)
            .group_by(_K.c.code, _K.c.label)
        )
        .mappings()
        .all()
    }

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    current_kind: str | None = None

    def _append_subtotal(kind_code: str) -> None:
        subtotal = kind_subtotals[kind_code]
        rows.append(
            {
                "kind": subtotal["kind_label"],
                "denomination": f"All {subtotal['kind_label']}",
                "items": subtotal["items"],
                "pieces": subtotal["pieces"],
                "total_cost": subtotal["total_cost"],
            }
        )
        drills.append(_query_string(kind_code, params))

    for row in denom_rows:
        kind_code = row["kind_code"]
        if current_kind is not None and kind_code != current_kind:
            _append_subtotal(current_kind)
        current_kind = kind_code

        denom_code = row["denom_code"]
        if denom_code is not None:
            rows.append(
                {
                    "kind": row["kind_label"],
                    "denomination": row["denom_label"],
                    "items": row["items"],
                    "pieces": row["pieces"],
                    "total_cost": row["total_cost"],
                }
            )
            drills.append(_query_string(kind_code, params, denom_code=denom_code))
        else:
            rows.append(
                {
                    "kind": row["kind_label"],
                    "denomination": "No denomination",
                    "items": row["items"],
                    "pieces": row["pieces"],
                    "total_cost": row["total_cost"],
                }
            )
            drills.append(
                _query_string(kind_code, params, missing_denom=True)
                if _denomination_applies(kind_code)
                else None
            )

    if current_kind is not None:
        _append_subtotal(current_kind)

    totals = {
        "kind": "All kinds",
        "denomination": None,
        "items": overall["items"],
        "pieces": overall["pieces"],
        "total_cost": overall["total_cost"],
    }

    return ReportResult(columns=columns, rows=rows, totals=totals, drills=drills)


CB_HOLDINGS = register(
    Report(
        id="cb_holdings",
        group="Collection",
        title="Holdings",
        purpose="What the collection is made of: kind x denomination, with "
        "items, pieces and total cost.",
        params=HoldingsParams,
        run=_cb_holdings,
    )
)
