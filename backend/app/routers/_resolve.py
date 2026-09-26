"""Resolving what a client sent to rows, or the error that names what was missing.

Shared by the routers so a missing row, an unknown code or a stale version
token is refused with one status and one wording wherever it is sent. Two
statuses, on purpose: an id in the **path** that names nothing is a 404 (the
resource does not exist), and an id or code in the **body** that names
nothing is a 422, like any other invalid field.
"""

from __future__ import annotations

import enum
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import InventoryItem, SalesLot, SalesVenue


def found_or_404[T](row: T | None, detail: str) -> T:
    """`row`, or a 404 with `detail` when the lookup that produced it found nothing."""
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=detail)
    return row


def get_or_404[T](db: Session, model: type[T], ident: int, detail: str) -> T:
    """The `model` row with primary key `ident`, or a 404 with `detail`."""
    return found_or_404(db.get(model, ident), detail)


def get_or_422[T](db: Session, model: type[T], ident: int, detail: str) -> T:
    """The `model` row with primary key `ident`, or a 422 with `detail`.

    For an id sent in a request body: it names nothing, so it is an invalid
    field, like an unknown code.
    """
    row = db.get(model, ident)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail
        )
    return row


def enum_member[E: enum.Enum](
    enum_type: type[E], code: str, label: str, *, also: Iterable[str] = ()
) -> E:
    """Resolve `code` to a member of `enum_type`, or a 422 naming the ones that exist.

    `label` is what the code is called in the message (`status`, `format`);
    `also` lists further values the caller accepts on its own, such as
    `all`, so the message names every value that would have worked.
    """
    try:
        return enum_type(code)
    except ValueError as exc:
        allowed = ", ".join([*(member.value for member in enum_type), *also])
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown {label}: {code!r}. Use one of: {allowed}",
        ) from exc


def venue_by_code(db: Session, code: str) -> SalesVenue:
    """Resolve a `sales_venue` code. An unknown platform is a 422."""
    venue = db.scalar(select(SalesVenue).where(SalesVenue.code == code))
    if venue is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown venue: {code!r}",
        )
    return venue


def item_by_id(db: Session, item_id: int) -> InventoryItem:
    """The item a request body names. An id no item wears is a 422."""
    return get_or_422(db, InventoryItem, item_id, f"Unknown item_id: {item_id}")


def lot_by_id(db: Session, lot_id: int) -> SalesLot:
    """The sales lot a request body names. An id no lot wears is a 422."""
    return get_or_422(db, SalesLot, lot_id, f"Unknown lot_id: {lot_id}")


def refuse_null_required(data: Mapping[str, Any], required: Iterable[str]) -> None:
    """422 naming every `required` column a PATCH sent as an explicit null.

    `required` is the columns that are `NOT NULL` on the table but optional on
    the update schema: omitting one leaves it alone, but an explicit null is
    a client mistake, not a request to clear a column that cannot be cleared.
    """
    nulled = sorted(
        field for field in required if field in data and data[field] is None
    )
    if nulled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{', '.join(nulled)} cannot be null",
        )


def refuse_future(field: str, value: date | None) -> None:
    """422 when a date that records something already done is in the future.

    An arrival or an order date that has not happened yet is a data-entry
    error, not a fact. "Today" is inherently local, but this check has only
    UTC to compare against. A caller's local calendar date can be a day ahead
    of UTC's (anywhere east of it, into the evening) or a day behind
    (anywhere west), so a bound of exactly UTC's today would refuse a
    genuine same-day entry for a large share of the world for several hours
    every day. The bound is UTC's today plus one day: that accepts every
    timezone's honest "today" -- the largest offset either side of UTC is a
    day -- while still refusing anything two or more days out, which is what
    an actual fat-fingered date looks like. The tradeoff: a typo exactly one
    day ahead of the true date is not caught, because it is
    indistinguishable from a legitimate ahead-of-UTC today.
    """
    limit: date = datetime.now(UTC).date() + timedelta(days=1)
    if value is not None and value > limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{field} {value.isoformat()} is too far "
            f"in the future. Latest accepted: {limit.isoformat()}.",
        )


def refuse_stale_version(expected: int | None, current: int, detail: str) -> None:
    """409 with `detail` when the caller sent a version the row no longer has.

    `None` means the caller did not send one, and nothing is checked.
    """
    if expected is not None and expected != current:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
