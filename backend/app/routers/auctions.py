"""Auctions: the HTTP face of `app.auctions`.

Admin only, and deliberately so: an auction lot carries what its coins cost,
same as an offer, and a house's fees and hammer prices are staff-only figures
the shop's own catalog endpoints never touch.

Every transition goes through `app.auctions`: adding and removing lots,
scheduling, consigning, closing, cancelling and settling. Creating a `draft`
auction, editing its wording and dates, and changing a lot's `lot_number`
or `reserve` are plain writes here, the way `routers.offers.update_listing`
edits a listing's price and wording, because none of those fields carries a
consequence `app.auctions` needs to own. This module resolves ids and codes,
owns the transaction, and shapes the response; it decides nothing about
what may be added, removed, consigned, closed, cancelled or settled.

**`AuctionRefused` and its narrower `SettlementInputInvalid`, and
`sales_writes.SaleRefused` and its narrower `SaleInputInvalid`, are never
caught here.** `app.main` registers a handler for each class, so the HTTP
status a refusal gets is decided by the class, never by the order of
`except` clauses (see `app.main`'s note). `settle` can raise all four --
its own from the grid, and `sales_writes`' from the `record_sale_lines`
call inside it.

**A write is all or nothing.** Every endpoint that calls into
`app.auctions` does so inside `routers._tx.committing`, which rolls back on
any exception and re-raises, so a refusal leaves the session clean for
whatever uses it next.

Plain ids are read into locals before each such block, and every implicit
autoflush stays inside it, the same discipline `routers.lots.update_sales_lot`
documents: after a failed flush, reading any ORM attribute raises
`PendingRollbackError` instead of the refusal a handler was trying to send.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .. import auctions, lot_writes, offering_writes, sales_writes
from ..deps import AdminUser, DbSession
from ..models import (
    Auction,
    AuctionLot,
    AuctionLotResult,
    AuctionStatus,
    InventoryItem,
    SalesLot,
    SalesVenue,
    StorageLocation,
)
from ..schemas import (
    AuctionCancelIn,
    AuctionConsignIn,
    AuctionIn,
    AuctionListOut,
    AuctionLotIn,
    AuctionLotOut,
    AuctionLotUpdate,
    AuctionOut,
    AuctionRefusedOut,
    AuctionUpdate,
    SettleIn,
    SettleOut,
)
from ._resolve import (
    enum_member,
    found_or_404,
    get_or_422,
    item_by_id,
    lot_by_id,
    refuse_null_required,
    refuse_stale_version,
    venue_by_code,
)
from ._tx import commit, committing
from .offers import listing_loads, listing_out, sale_recorded

router = APIRouter(prefix="/auctions", tags=["selling"])

_STALE = "This auction was changed by someone else. Reload and reapply your changes."

_REFUSAL_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {"model": AuctionRefusedOut},
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuctionRefusedOut},
}


def _location_by_id(db: Session, location_id: int) -> StorageLocation:
    """A storage location to return items to. An id no location wears is a 422."""
    return get_or_422(
        db,
        StorageLocation,
        location_id,
        f"Unknown returned_to_location_id: {location_id}",
    )


# --------------------------------------------------------------------------
# Shaping the response
# --------------------------------------------------------------------------


def _eager(stmt: Select[tuple[Auction]]) -> Select[tuple[Auction]]:
    """Load what `_out` reads, so a list of auctions is not a query per row."""
    return stmt.options(
        selectinload(Auction.sales_venue).selectinload(SalesVenue.kind),
        selectinload(Auction.lots)
        .selectinload(AuctionLot.listing)
        .options(*listing_loads()),
        selectinload(Auction.lots).selectinload(AuctionLot.buyer),
    )


def _get_auction(db: Session, auction_id: int) -> Auction:
    """One auction with its lots, or a 404."""
    return found_or_404(
        db.scalar(_eager(select(Auction).where(Auction.id == auction_id))),
        "No such auction",
    )


def _auction_lot_by_id(db: Session, auction: Auction, lot_id: int) -> AuctionLot:
    """One lot of this auction, or a 404 naming the auction as well as the id."""
    return found_or_404(
        db.scalar(
            select(AuctionLot).where(
                AuctionLot.id == lot_id, AuctionLot.auction_id == auction.id
            )
        ),
        f"No such lot #{lot_id} in auction #{auction.id}",
    )


def _lot_out(auction_lot: AuctionLot) -> AuctionLotOut:
    """Shape one auction lot: its slot in the sale, and the listing behind it.

    The listing itself is shaped by `routers.offers.listing_out` (ruling
    R26, Task 5 fix round 1) -- imported, not copied, so the Listings page
    and the Auctions page show the very same listing through one
    implementation rather than two that can drift apart. It was private
    (`_out`) until this ruling made it public expressly so this could import
    it instead of duplicating it.
    """
    return AuctionLotOut(
        id=auction_lot.id,
        lot_number=auction_lot.lot_number,
        reserve=auction_lot.reserve,
        result=auction_lot.result.value if auction_lot.result is not None else None,
        hammer_price=auction_lot.hammer_price,
        buyer=auction_lot.buyer.display_name if auction_lot.buyer is not None else None,
        listing=listing_out(auction_lot.listing),
    )


def _out(auction: Auction) -> AuctionOut:
    """Shape one auction for the console, with every lot it currently holds.

    Sorted by id, never left as `auction.lots` returned it: that collection
    carries no `order_by` of its own (`app/models/auctions.py`), the same
    reason `app.auctions._lots_of` sorts before it writes to them.
    """
    venue = auction.sales_venue
    return AuctionOut(
        id=auction.id,
        venue=venue.code,
        venue_name=venue.name,
        title=auction.title,
        external_id=auction.external_id,
        starts_at=auction.starts_at,
        ends_at=auction.ends_at,
        status=auction.status.value,
        consigned_on=auction.consigned_on,
        notes=auction.notes,
        version=auction.version,
        lots=[_lot_out(row) for row in sorted(auction.lots, key=lambda row: row.id)],
    )


# --------------------------------------------------------------------------
# Reading and creating
# --------------------------------------------------------------------------


@router.get("")
def list_auctions(
    db: DbSession,
    _admin: AdminUser,
    venue: Annotated[str | None, Query(description="A sales_venue code")] = None,
    wanted_status: Annotated[str | None, Query(alias="status")] = None,
) -> AuctionListOut:
    """Every auction, newest first. Filterable by platform and by status."""
    stmt = select(Auction)
    if venue is not None:
        stmt = stmt.where(Auction.sales_venue_id == venue_by_code(db, venue).id)
    if wanted_status is not None:
        stmt = stmt.where(
            Auction.status == enum_member(AuctionStatus, wanted_status, "status")
        )
    rows = db.scalars(_eager(stmt).order_by(Auction.id.desc())).all()
    return AuctionListOut(auctions=[_out(row) for row in rows])


@router.post("", status_code=status.HTTP_201_CREATED)
def create_auction(payload: AuctionIn, db: DbSession, _admin: AdminUser) -> AuctionOut:
    """Start an auction. It begins `draft`, with no lots yet."""
    venue = venue_by_code(db, payload.venue)
    auction = Auction(
        sales_venue_id=venue.id,
        title=payload.title,
        external_id=payload.external_id,
        starts_at=payload.starts_at,
        ends_at=payload.ends_at,
        notes=payload.notes,
    )
    db.add(auction)
    db.commit()
    return _out(_get_auction(db, auction.id))


#: Columns that are `NOT NULL` on `Auction` but optional on `AuctionUpdate` --
#: omitting one leaves it alone, but an explicit null is a client mistake.
_REQUIRED_ON_UPDATE = frozenset({"title"})


@router.patch("/{auction_id}")
def update_auction(
    auction_id: int, payload: AuctionUpdate, db: DbSession, _admin: AdminUser
) -> AuctionOut:
    """Change an auction's own wording or dates. Send `version` to catch conflicts.

    Never the platform or the status -- see `AuctionUpdate`'s own docstring.
    """
    auction = _get_auction(db, auction_id)
    data: dict[str, Any] = payload.model_dump(exclude_unset=True)
    refuse_stale_version(data.pop("version", None), auction.version, _STALE)
    refuse_null_required(data, _REQUIRED_ON_UPDATE)

    for field, value in data.items():
        setattr(auction, field, value)

    commit(db, _STALE)
    return _out(_get_auction(db, auction_id))


# --------------------------------------------------------------------------
# Lots
# --------------------------------------------------------------------------


def _duplicate_lot_number(lot_number: str | None) -> HTTPException:
    """The 409 a repeated `lot_number` within one auction produces.

    `lot_number` is `str | None` only so `update_auction_lot`'s captured
    `payload.lot_number` (Minor #7, Task 5 fix round 1) can be passed
    straight through without an assertion: a reserve-only PATCH cannot
    trigger this in practice, since `uq_auction_lot_auction_lot_number`
    has nothing to do with `reserve`, but the type says so honestly rather
    than asserting a fact this function has no way to check.
    """
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=f"lot_number {lot_number!r} is already used in this auction",
    )


@router.post(
    "/{auction_id}/lots",
    status_code=status.HTTP_201_CREATED,
    responses=_REFUSAL_RESPONSES,
)
def add_auction_lot(
    auction_id: int, payload: AuctionLotIn, db: DbSession, _admin: AdminUser
) -> AuctionOut:
    """Add a lot: an assembled lot, or a single item as a lot of one.

    Same refusals and store-listing pausing as any offer (spec, *Add to an
    auction*), because that is exactly what this is -- `app.auctions.add_lot`
    calls `offering_writes.offer` underneath. `EmptyLot` is checked **before**
    `LotRefused`: it is that class's own subclass, and `routers.offers.py`
    carries the identical ordering warning for the identical reason.
    """
    auction = _get_auction(db, auction_id)
    lot_number = payload.lot_number
    if payload.lot_id is not None:
        subject: SalesLot | InventoryItem = lot_by_id(db, payload.lot_id)
    elif payload.item_id is not None:
        subject = item_by_id(db, payload.item_id)
    else:  # pragma: no cover - AuctionLotIn._one_subject already refuses this
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="Exactly one of item_id or lot_id is required",
        )

    try:
        with committing(db, _STALE):
            auctions.add_lot(
                db,
                auction,
                subject,
                lot_number=lot_number,
                reserve=payload.reserve,
                price=payload.price,
                title=payload.title,
                description=payload.description,
                external_id=payload.external_id,
            )
    # ----------------------------------------------------------------
    # ORDER-SENSITIVE. `EmptyLot` is a subclass of `LotRefused`, so it must
    # be caught first -- the identical trap, and the identical fix,
    # `routers.offers.create_offers` documents at its own matching clauses.
    # `committing` has already rolled back by the time any of these runs.
    # ----------------------------------------------------------------
    except lot_writes.EmptyLot as empty:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(empty)
        ) from empty
    except lot_writes.LotRefused as refused_lot:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(refused_lot)
        ) from refused_lot
    except offering_writes.OfferRefused as refused:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{refused.item_code}: {refused.reason}",
        ) from refused
    except IntegrityError as exc:
        raise _duplicate_lot_number(lot_number) from exc
    return _out(_get_auction(db, auction_id))


@router.delete("/{auction_id}/lots/{lot_id}", responses=_REFUSAL_RESPONSES)
def remove_auction_lot(
    auction_id: int,
    lot_id: int,
    db: DbSession,
    admin: AdminUser,
    returned_to_location_id: Annotated[
        int | None,
        Query(description="Required if the auction is consigned"),
    ] = None,
) -> AuctionOut:
    """Take a lot out of its auction: end its offer, as an ordinary End.

    `AuctionRefused` -- the auction has closed, settled or been cancelled, or
    the auction is consigned and no return location was given -- is left to
    propagate to the handler `app.main` registers for it; see this module's
    own docstring for why nothing here catches it.
    """
    auction = _get_auction(db, auction_id)
    auction_lot = _auction_lot_by_id(db, auction, lot_id)
    if returned_to_location_id is not None:
        _location_by_id(db, returned_to_location_id)
    with committing(db, _STALE):
        auctions.remove_lot(
            db,
            auction_lot,
            returned_to_location_id=returned_to_location_id,
            user_id=admin.id,
        )
    return _out(_get_auction(db, auction_id))


@router.patch("/{auction_id}/lots/{lot_id}", responses=_REFUSAL_RESPONSES)
def update_auction_lot(
    auction_id: int,
    lot_id: int,
    payload: AuctionLotUpdate,
    db: DbSession,
    _admin: AdminUser,
) -> AuctionOut:
    """Renumber a lot, or change its reserve.

    Gated by `app.auctions.refuse_unless_lot_editable` (ruling R22, Task 5
    follow-up): `draft`, `scheduled` or `consigned` only, the same boundary
    `remove_lot` already uses -- a lot number and a reserve are both set
    *before* the sale, and once `closed` the lot numbers are part of the
    record a house's statement is reconciled against. The write itself is
    still a plain `setattr`, not a further call into `app.auctions`: neither
    field is a status, a claim or a location, so nothing else in the
    codebase needs to agree on what changing one means -- the same reasoning
    `routers.offers.update_listing` gives for editing `price`, `title` and
    `description` on a `Listing` row directly. `AuctionRefused` from the gate
    is left to propagate to the handler `app.main` registers for it, the
    same as every other transition in this router.

    `payload.lot_number` is captured into a local before the `try` (Minor
    #7, Task 5 fix round 1): the `IntegrityError` clause used to read
    `auction_lot.lot_number` as `dict.get`'s default, which Python evaluates
    unconditionally -- a live ORM attribute read on the common path, after a
    failed flush on the error path, which is exactly what this module's own
    docstring says every other endpoint avoids. A repeated `lot_number` can
    only be the one this request just tried to set -- a reserve-only change
    cannot violate `uq_auction_lot_auction_lot_number` -- so the captured
    value is always the right one to name.
    """
    auction = _get_auction(db, auction_id)
    auction_lot = _auction_lot_by_id(db, auction, lot_id)
    auctions.refuse_unless_lot_editable(db, auction_lot)
    lot_number = payload.lot_number
    data: dict[str, Any] = payload.model_dump(exclude_unset=True)
    if "lot_number" in data and data["lot_number"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="lot_number cannot be null",
        )

    for field, value in data.items():
        setattr(auction_lot, field, value)

    try:
        commit(db, _STALE)
    except IntegrityError as exc:
        raise _duplicate_lot_number(lot_number) from exc
    return _out(_get_auction(db, auction_id))


# --------------------------------------------------------------------------
# Transitions
# --------------------------------------------------------------------------


@router.post("/{auction_id}/schedule", responses=_REFUSAL_RESPONSES)
def schedule_auction(auction_id: int, db: DbSession, _admin: AdminUser) -> AuctionOut:
    """Move a draft auction to scheduled."""
    auction = _get_auction(db, auction_id)
    with committing(db, _STALE):
        auctions.schedule(db, auction)
    return _out(_get_auction(db, auction_id))


@router.post("/{auction_id}/consign", responses=_REFUSAL_RESPONSES)
def consign_auction(
    auction_id: int, payload: AuctionConsignIn, db: DbSession, admin: AdminUser
) -> AuctionOut:
    """Move every member item into the house's consigned location.

    A migrated but unseeded database, with no `consigned` storage-location
    kind, makes `app.auctions.consign` raise `errors.ReferenceDataMissing`
    naming the missing seed. Nothing here catches it: `app.main` registers
    `_server_misconfigured` for that class, so an administrator sees the
    message in a 500 instead of a bare one (see that handler's docstring).
    """
    auction = _get_auction(db, auction_id)
    with committing(db, _STALE):
        auctions.consign(db, auction, on_date=payload.on_date, user_id=admin.id)
    return _out(_get_auction(db, auction_id))


@router.post("/{auction_id}/close", responses=_REFUSAL_RESPONSES)
def close_auction(auction_id: int, db: DbSession, _admin: AdminUser) -> AuctionOut:
    """Close the auction: lot results may now be entered."""
    auction = _get_auction(db, auction_id)
    with committing(db, _STALE):
        auctions.close(db, auction)
    return _out(_get_auction(db, auction_id))


@router.post("/{auction_id}/cancel", responses=_REFUSAL_RESPONSES)
def cancel_auction(
    auction_id: int, payload: AuctionCancelIn, db: DbSession, admin: AdminUser
) -> AuctionOut:
    """Cancel the auction and remove every lot it still holds."""
    auction = _get_auction(db, auction_id)
    if payload.returned_to_location_id is not None:
        _location_by_id(db, payload.returned_to_location_id)
    with committing(db, _STALE):
        auctions.cancel(
            db,
            auction,
            returned_to_location_id=payload.returned_to_location_id,
            user_id=admin.id,
        )
    return _out(_get_auction(db, auction_id))


@router.post(
    "/{auction_id}/settle", response_model=SettleOut, responses=_REFUSAL_RESPONSES
)
def settle_auction(
    auction_id: int, payload: SettleIn, db: DbSession, admin: AdminUser
) -> SettleOut:
    """Apply a whole settlement grid to a closed auction. One transaction.

    `AuctionRefused`, `SettlementInputInvalid`, `sales_writes.SaleRefused`
    and `sales_writes.SaleInputInvalid` are all left to propagate: every one
    of the four handlers `app.main` registers may fire from this single call,
    and none of them is caught here -- see this module's own docstring.
    """
    auction = _get_auction(db, auction_id)
    if payload.returned_to_location_id is not None:
        _location_by_id(db, payload.returned_to_location_id)
    lines = [
        auctions.SettlementLine(
            auction_lot_id=line.auction_lot_id,
            result=enum_member(AuctionLotResult, line.result, "result"),
            hammer_price=line.hammer_price,
            buyer_username=line.buyer_username,
        )
        for line in payload.lines
    ]
    fees = {
        group.buyer_username: [
            sales_writes.FeeLine(fee.kind, fee.amount, fee.note) for fee in group.fees
        ]
        for group in payload.fees
    }
    with committing(db, _STALE):
        orders = auctions.settle(
            db,
            auction,
            lines,
            fees,
            settled_by=admin,
            returned_to_location_id=payload.returned_to_location_id,
        )
    return SettleOut(
        auction=_out(_get_auction(db, auction_id)),
        orders=[sale_recorded(db, order) for order in orders],
    )
