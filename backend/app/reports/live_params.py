"""Status and disposition: the two parameters every held-items report takes.

Every Collection report and `mn_value` count live items
narrowed by acquisition status and sales disposition. The parameters
(`LiveParams`), the joins and filters they add (`live_where`), the pair a
drill-down restates (`status_disposition_params`), a kind's own drill
(`kind_query_string`) and the empty-result note (`NOTHING_MATCHES`) are
defined here once, so the reports that share them cannot drift apart on
what "received and held" selects or which search a row opens.
"""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlencode

from pydantic import BaseModel, Field
from sqlalchemy import ColumnElement, FromClause

from ..inventory_search import view_path
from ..models import Disposition, ItemStatus
from .tables import ITEM as _I
from .tables import LIVE as _LIVE

__all__ = [
    "NOTHING_MATCHES",
    "DispositionParam",
    "LiveParams",
    "StatusParam",
    "kind_query_string",
    "live_where",
    "status_disposition_params",
]

#: `item_status` and `disposition`, aliased as the search aliases them.
_ST = ItemStatus.__table__.alias("st")
_DISP = Disposition.__table__.alias("disp")

#: The seeded `item_status` codes (`backend/data/reference/operations.json`),
#: plus `all` for no filter.
StatusParam = Literal[
    "ordered", "received", "canceled", "returned", "missing", "unknown", "all"
]
#: The seeded `disposition` codes, plus `all` for no filter.
DispositionParam = Literal[
    "held", "listed", "sold", "shipped", "delivered", "returned_by_buyer", "all"
]

#: The note a status/disposition report shows in place of rows when nothing
#: matches its parameters -- one literal, so every such report reads the
#: same words.
NOTHING_MATCHES = "Nothing matches these settings."


class LiveParams(BaseModel):
    """Which live items to count: by acquisition status and sales disposition.

    Defaults to `received` and `held` -- what the owner has actually taken in
    and still has -- rather than everything ever ordered or ever sold. Either
    can be widened to `all`, which drops that filter entirely. Every report
    taking exactly these two parameters subclasses this rather than
    redeclaring the fields.
    """

    status: StatusParam = Field(default="received", title="Status")
    disposition: DispositionParam = Field(default="held", title="Disposition")


def live_where(
    src: FromClause, params: LiveParams
) -> tuple[FromClause, list[ColumnElement[bool]]]:
    """Add the status/disposition join and filter to `src`, atop `LIVE`.

    The status and disposition joins are added only when that parameter is
    not `all`, so a query that never joins `item_status` when every status
    counts reads as exactly what it is -- no filter there.
    """
    where: list[ColumnElement[bool]] = [_LIVE]
    if params.status != "all":
        src = src.join(_ST, _ST.c.id == _I.c.status_id)
        where.append(_ST.c.code == params.status)
    if params.disposition != "all":
        src = src.join(_DISP, _DISP.c.id == _I.c.disposition_id)
        where.append(_DISP.c.code == params.disposition)
    return src, where


def status_disposition_params(params: LiveParams) -> dict[str, str]:
    """The `status=`/`disposition=` pair a drill adds, omitted at `all`.

    Matches `LiveParams`' own no-filter value: a drill never states a filter
    the report itself is not applying.
    """
    query: dict[str, str] = {}
    if params.status != "all":
        query["status"] = params.status
    if params.disposition != "all":
        query["disposition"] = params.disposition
    return query


def kind_query_string(
    kind_code: str,
    params: LiveParams,
    *,
    denom_code: str | None = None,
    missing_denom: bool = False,
) -> str:
    """One drill's console path: the kind's search, narrowed as this row is.

    Currency has its own search page and needs no `kind=`; every other kind
    searches `/inventory/coins?kind=<code>`. `denom_code` adds
    `denomination=<code>`; `missing_denom` adds `missing=denomination`
    instead -- the two are mutually exclusive, one row is never both a named
    denomination and "no denomination". Then `status=`/`disposition=` from
    `params`, omitted at `all`, so the drill's own count equals the row's.
    """
    query: dict[str, str] = {}
    if kind_code != "currency":
        query["kind"] = kind_code
    if denom_code is not None:
        query["denomination"] = denom_code
    elif missing_denom:
        query["missing"] = "denomination"
    query.update(status_disposition_params(params))
    path = view_path(kind_code)
    return f"{path}?{urlencode(query)}" if query else path
