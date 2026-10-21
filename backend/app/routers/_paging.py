"""Paging for the console's list endpoints that answer a bare JSON array.

`GET /api/orders`, `GET /api/listings` and `GET /api/auctions` grow for as
long as the shop trades, and an unbounded read loads every row and its
eager-loaded children in one request. Each takes `limit` and `offset`, and
says how many rows match in the `X-Total-Count` response header, so its
body keeps the shape the console already reads.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import Query, Response
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

#: Rows a page holds when the caller does not say.
DEFAULT_LIMIT = 200
#: The most rows one request may ask for.
MAX_LIMIT = 1000
#: The response header carrying how many rows match, across every page.
TOTAL_HEADER = "X-Total-Count"

Limit = Annotated[
    int, Query(ge=1, le=MAX_LIMIT, description="Rows per page, at most 1000")
]
Offset = Annotated[int, Query(ge=0, description="Rows to skip")]


def page[T: tuple[Any, ...]](
    db: Session, stmt: Select[T], response: Response, *, limit: int, offset: int
) -> Select[T]:
    """Count what `stmt` matches into the total header, and return one page of it.

    `stmt` is the filtered statement, before loader options or ordering: it
    is counted as a subquery, where neither means anything. The caller adds
    both to what this returns; SQLAlchemy renders `ORDER BY` ahead of
    `LIMIT` whichever was added first.
    """
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    response.headers[TOTAL_HEADER] = str(total)
    return stmt.limit(limit).offset(offset)
