"""Sales lots: the HTTP face of `app.lot_writes`.

Admin only, and deliberately so: a lot row carries what its coins cost and
what they are thought to be worth, and neither figure belongs anywhere a
customer can reach.

Every write here goes through `app.lot_writes`, which owns the rule that a
lot is editable only while `assembling`. This module resolves ids, owns the
transaction, and shapes the response; it decides nothing about what may be
grouped. Offering a lot is not here at all -- that is `POST /api/offers`,
because offering is `offering_writes`' decision and a lot is just another
thing to offer.

**A PATCH is all or nothing.** The whole change -- wording and every
membership move -- runs inside one transaction and a refusal rolls it back,
so a screenful of edits either lands or does not. `lot_writes` checks every
condition before it writes anything, so a rollback here has only the
successful rows of a doomed request to discard.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session, selectinload

from .. import lot_writes
from ..deps import AdminUser, DbSession
from ..models import SalesLot, SalesLotItem, SalesLotStatus
from ..schemas import (
    SalesLotIn,
    SalesLotListOut,
    SalesLotMemberOut,
    SalesLotOut,
    SalesLotUpdate,
)
from ._resolve import enum_member, found_or_404, item_by_id, refuse_stale_version
from ._tx import committing

router = APIRouter(prefix="/sales-lots", tags=["selling"])

_STALE = "This lot was changed by someone else. Reload and reapply your changes."

#: `status=all` on the lot list: every status. Any other value is one
#: `SalesLotStatus`, and `all` is also the default -- unlike the listings
#: list, which hides ended offers, because a lot that sold is the record of
#: which coins went out together and is looked up long after the fact.
_ALL = "all"

_ZERO = Decimal("0.00")


def _eager(stmt: Select[tuple[SalesLot]]) -> Select[tuple[SalesLot]]:
    """Load what `_out` reads, so a list of lots is not two queries per row."""
    return stmt.options(selectinload(SalesLot.members).selectinload(SalesLotItem.item))


def _get_lot(db: Session, lot_id: int) -> SalesLot:
    """One lot, or a 404."""
    return found_or_404(
        db.scalar(_eager(select(SalesLot).where(SalesLot.id == lot_id))),
        "No such lot",
    )


# --------------------------------------------------------------------------
# Shaping the response
# --------------------------------------------------------------------------


def _out(lot: SalesLot) -> SalesLotOut:
    """Shape one lot for the console, with its coins and its running totals.

    **Every membership row, not just the open ones.** `lot_writes` deletes a
    row when a coin is taken out of an assembling lot and only *releases* one
    when the lot sells or is dissolved, so the rows that exist are exactly
    the group as it stands -- and, once the lot has been offered, exactly the
    group that was offered. `offering_writes.offered_items` answers the
    narrower question "which coins does this listing still claim", which is
    about claims, not about what the lot is a record of; a sold lot shown
    with no members would be useless to the person looking up what went out
    together.

    Sorted by `inventory_item_id`, the one order `lot_writes.open_members`
    decides for the whole system, so the console shows a lot in the same
    sequence its shares and its snapshot use.
    """
    members = sorted(lot.members, key=lambda row: row.inventory_item_id)
    values = [row.item.numismatic_value for row in members]
    return SalesLotOut(
        id=lot.id,
        title=lot.title,
        description=lot.description,
        status=lot.status.value,
        version=lot.version,
        members=[
            SalesLotMemberOut(
                inventory_item_id=row.inventory_item_id,
                item_code=row.item.item_code,
                title=row.item.source_title,
                cost_basis=row.item.total_cost,
                value=row.item.numismatic_value,
            )
            for row in members
        ],
        cost_basis=sum((row.item.total_cost for row in members), _ZERO),
        value=sum((value for value in values if value is not None), _ZERO),
        unvalued_count=sum(1 for value in values if value is None),
    )


# --------------------------------------------------------------------------
# Reading
# --------------------------------------------------------------------------


@router.get("")
def list_sales_lots(
    db: DbSession,
    _admin: AdminUser,
    wanted_status: Annotated[
        str | None, Query(alias="status", description="One status, or `all`")
    ] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> SalesLotListOut:
    """Lots, newest first, a page at a time. `status=assembling` for open ones.

    Paged because the history only grows: every lot ever offered stays, and
    each comes back with its members. `total` is how many the filter
    matched, so the console can say when it is not showing all of them.
    """
    stmt = select(SalesLot)
    if wanted_status is not None and wanted_status != _ALL:
        stmt = stmt.where(
            SalesLot.status
            == enum_member(SalesLotStatus, wanted_status, "status", also=[_ALL])
        )
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        _eager(stmt).order_by(SalesLot.id.desc()).limit(limit).offset(offset)
    ).all()
    return SalesLotListOut(lots=[_out(row) for row in rows], total=total)


@router.get("/{lot_id}")
def get_sales_lot(lot_id: int, db: DbSession, _admin: AdminUser) -> SalesLotOut:
    """One lot with its coins, whatever state it is in."""
    return _out(_get_lot(db, lot_id))


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_201_CREATED)
def create_sales_lot(
    payload: SalesLotIn, db: DbSession, _admin: AdminUser
) -> SalesLotOut:
    """Start a lot. It begins `assembling` and empty; coins go in with a PATCH.

    Empty rather than taking a list of coins, because assembling a lot is a
    screenful of adds and removes and the first one is not special. An empty
    lot cannot be offered -- `offering_writes._lot_members` raises `EmptyLot`
    and `POST /api/offers` answers 422 -- so nothing downstream has to cope
    with one.
    """
    lot = lot_writes.create_lot(
        db, title=payload.title, description=payload.description
    )
    db.commit()
    return _out(_get_lot(db, lot.id))


def _refuse_overlap(added: list[int], removed: list[int]) -> None:
    """Refuse an id sent as both an addition and a removal.

    Either order of applying the two would be defensible and they disagree,
    so the request is ambiguous rather than merely redundant. Told, not
    guessed at.
    """
    both = sorted(set(added) & set(removed))
    if both:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"item_id {', '.join(str(item_id) for item_id in both)}: "
                "sent as both an addition and a removal"
            ),
        )


@router.patch("/{lot_id}")
def update_sales_lot(
    lot_id: int, payload: SalesLotUpdate, db: DbSession, _admin: AdminUser
) -> SalesLotOut:
    """Change an assembling lot's wording or its coins. All or nothing.

    `version` is the version the form loaded; a mismatch is a 409, as
    everywhere else in the console. It is checked **twice**, and neither
    check is redundant. The comparison below catches the ordinary case and
    answers before anything is written. `StaleDataError` from the commit
    catches the narrow race where another request moved the version between
    that comparison and this one's UPDATE.

    Removals run before additions, so a request that swaps one coin for
    another never has to hold both at once -- which matters the day the two
    coins are the same pieces of a split.

    `lot_writes.touch` at the end is what makes the version usable at all: a
    membership change writes `sales_lot_item` and would otherwise leave
    `sales_lot.version` exactly where it was, so a second request carrying
    the same stale token would be accepted.

    Plain ids are read into locals before the `try`, and every implicit
    autoflush stays inside it: after a failed flush, reading any ORM
    attribute raises `PendingRollbackError` instead of the refusal this was
    trying to send.
    """
    lot = _get_lot(db, lot_id)
    refuse_stale_version(payload.version, lot.version, _STALE)
    # Sorted, so two requests touching the same coins take the lot's members
    # in one order rather than in whatever order each body happened to list
    # them. The lot's own row lock, taken by `lot_writes` before the first
    # write, is what actually serializes them; this keeps the *effect* of a
    # request independent of how its list was typed.
    added = sorted(set(payload.add_item_ids))
    removed = sorted(set(payload.remove_item_ids))
    _refuse_overlap(added, removed)
    changed = bool(added or removed) or payload.title is not None
    changed = changed or payload.description is not None

    try:
        with committing(db, _STALE):
            # Unconditional, and first: `edit_lot` refuses a frozen lot
            # whether or not there is wording to change, so a
            # membership-only request on an offered lot is refused by the
            # same line and with the same words as a rename of one.
            lot_writes.edit_lot(
                db, lot, title=payload.title, description=payload.description
            )
            for item_id in removed:
                lot_writes.remove_member(db, lot, item_by_id(db, item_id))
            for item_id in added:
                lot_writes.add_member(db, lot, item_by_id(db, item_id))
            if changed:
                lot_writes.touch(db, lot)
    # `EmptyLot` has no clause here, and adding a dead one would be worse
    # than saying why: nothing in assembly needs a lot to be non-empty, so
    # only `offering_writes._lot_members` raises it and only
    # `routers/offers.create_offers` can catch it. Should a writer reached
    # from here ever raise it, its clause must go **above** the one below --
    # `EmptyLot` is a `LotRefused` subclass, so this clause would otherwise
    # swallow it and answer 409 for what is bad input, and mypy cannot see
    # the mistake.
    except lot_writes.LotRefused as refused:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(refused)
        ) from refused
    return _out(_get_lot(db, lot_id))


@router.delete("/{lot_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sales_lot(lot_id: int, db: DbSession, _admin: AdminUser) -> Response:
    """Discard a lot that was never offered. An offered one is refused, 409.

    A lot that has been offered is a record of what was tried, and a sold one
    is how anybody later finds out which coins went out together; neither can
    be thrown away. `lot_writes.delete_lot` is what decides that.
    """
    lot = _get_lot(db, lot_id)
    try:
        with committing(db, _STALE):
            lot_writes.delete_lot(db, lot)
    except lot_writes.LotRefused as refused:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(refused)
        ) from refused
    return Response(status_code=status.HTTP_204_NO_CONTENT)
