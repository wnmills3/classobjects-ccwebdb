"""Offering items for sale: the HTTP face of `offering_writes`.

Admin only, and deliberately so: an offer row carries what the item cost, and
the shop's own catalogue endpoints are what a customer reads.

Every write here goes through `app.offering_writes`, which is the only writer
of `listing.status` and `offer_claim`. This module resolves codes to rows,
owns the transaction, and shapes the response; it decides nothing about what
may be offered.

**A batch is all or nothing.** The whole batch runs inside one transaction and
a refusal rolls it back, rather than committing each item and trying to undo
the ones already written -- an undo has its own failure mode, and the state it
would leave behind is exactly the half-offered mess the claim table exists to
prevent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import JSONResponse
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError

from .. import lot_writes, offering_writes, sales_writes
from ..deps import AdminUser, DbSession
from ..models import (
    AuctionLot,
    InventoryItem,
    Listing,
    ListingFormat,
    ListingStatus,
    SalesLot,
    SalesLotItem,
    SalesOrder,
    SalesOrderFee,
    SalesOrderItem,
    SalesOrderItemShare,
    SalesVenue,
)
from ..offering_writes import OfferRefused
from ..schemas import (
    ListingOut,
    ListingUpdate,
    OfferBatchOut,
    OfferIn,
    OfferRefusalOut,
    OfferRefusedOut,
    RecordSaleIn,
    SaleRecordedOut,
)

router = APIRouter(tags=["selling"])

_STALE = "This offer was changed by someone else. Reload and reapply your changes."

#: `status=all` on the listing list: every status, ended ones included. Any
#: other value is one `ListingStatus`; the default is what is on offer now.
_ALL = "all"


# --------------------------------------------------------------------------
# Resolving what the client sent
# --------------------------------------------------------------------------


def _venue_by_code(db: Session, code: str) -> SalesVenue:
    """Resolve a `sales_venue` code. An unknown platform is a 422."""
    venue = db.scalar(select(SalesVenue).where(SalesVenue.code == code))
    if venue is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown venue: {code!r}",
        )
    return venue


def _listing_format(code: str) -> ListingFormat:
    """Resolve a listing format code, naming the ones that exist."""
    try:
        return ListingFormat(code)
    except ValueError as exc:
        allowed = ", ".join(member.value for member in ListingFormat)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown format: {code!r}. Use one of: {allowed}",
        ) from exc


def _listing_status(code: str) -> ListingStatus:
    """Resolve a listing status code, naming the ones that exist."""
    try:
        return ListingStatus(code)
    except ValueError as exc:
        allowed = ", ".join([*(member.value for member in ListingStatus), _ALL])
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown status: {code!r}. Use one of: {allowed}",
        ) from exc


def _item_by_id(db: Session, item_id: int) -> InventoryItem:
    """The item to offer. An id no item wears is a 422, like an unknown code."""
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown item_id: {item_id}",
        )
    return item


# --------------------------------------------------------------------------
# Shaping the response
# --------------------------------------------------------------------------


def _eager(stmt: Select[tuple[Listing]]) -> Select[tuple[Listing]]:
    """Load what `_out` reads, so a list of offers is not a query per row.

    The lot chain is loaded for every listing, item listings included: a
    `selectinload` of a null foreign key costs nothing, and branching the
    options on a per-row value is not something one statement can do.
    """
    return stmt.options(
        selectinload(Listing.inventory_item),
        selectinload(Listing.sales_venue),
        selectinload(Listing.currency),
        selectinload(Listing.sales_lot)
        .selectinload(SalesLot.members)
        .selectinload(SalesLotItem.item),
    )


def external_url(listing: Listing, venue: SalesVenue) -> str | None:
    """The listing's page on its platform, stored or derived.

    A URL someone typed wins, because a platform that moved one listing is
    exactly the case the column exists for. Otherwise it is built from the
    platform's template, and **not written back**: a derived value saved to
    the row would outlive the template it came from.
    """
    if listing.external_url:
        return listing.external_url
    if listing.external_id and venue.listing_url_template:
        return venue.listing_url_template.replace("{external_id}", listing.external_id)
    return None


def _out(listing: Listing) -> ListingOut:
    """Shape one offer for the console, item listing or lot listing.

    `ck_listing_item_xor_lot` makes the two mutually exclusive, so exactly one of
    the two branches below fills its half of `ListingOut` and the other half
    stays null. This function read `listing.inventory_item.item_code`
    unconditionally until lots existed, which made the whole Listings page
    an `AttributeError` the moment the first lot was offered.

    A lot's `cost_basis` is the sum of its members', and `item_title` is the
    lot's own title, so the console has one title field to show whichever
    kind of listing a row is. Every membership row counts, not only the open
    ones: a lot's memberships are released when it sells, and a sold lot's
    row saying "0 items" would be wrong about what was sold.

    The three values are given subject-less defaults and then overridden,
    rather than branched three ways: `ck_listing_item_xor_lot` guarantees one of
    the two is set, so the defaults are unreachable, and a listing that
    somehow had neither should still render as a row an administrator can
    see and end -- not as a 500. `routers/orders._listing_title` handles the
    same impossible case the same way.
    """
    item = listing.inventory_item
    lot = listing.sales_lot
    venue = listing.sales_venue
    title = f"Listing #{listing.id}"
    cost_basis: Decimal | None = None
    member_count: int | None = None
    if item is not None:
        title = item.source_title
        cost_basis = item.total_cost
    elif lot is not None:
        title = lot.title
        cost_basis = sum((row.item.total_cost for row in lot.members), Decimal("0.00"))
        member_count = len(lot.members)
    return ListingOut(
        id=listing.id,
        item_id=listing.inventory_item_id,
        item_code=item.item_code if item is not None else None,
        item_title=title,
        venue=venue.code,
        venue_name=venue.name,
        format=listing.format.value,
        status=listing.status.value,
        price=listing.price,
        currency=listing.currency.code,
        quantity_available=listing.quantity_available,
        title=listing.title,
        description=listing.description,
        external_id=listing.external_id,
        external_url=external_url(listing, venue),
        listed_at=listing.listed_at,
        ended_at=listing.ended_at,
        paused_by_listing_id=listing.paused_by_listing_id,
        sales_lot_id=listing.sales_lot_id,
        member_count=member_count,
        cost_basis=cost_basis,
        version=listing.version,
    )


def _reload(db: Session, listing_ids: list[int]) -> list[ListingOut]:
    """Read the written offers back, in the order they were asked for."""
    rows = db.scalars(_eager(select(Listing).where(Listing.id.in_(listing_ids)))).all()
    by_id = {row.id: row for row in rows}
    return [_out(by_id[listing_id]) for listing_id in listing_ids]


def _get_listing(db: Session, listing_id: int) -> Listing:
    """One offer, or a 404."""
    listing = db.scalar(_eager(select(Listing).where(Listing.id == listing_id)))
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such offer"
        )
    return listing


# --------------------------------------------------------------------------
# Offering
# --------------------------------------------------------------------------


#: What a request that lost the race is told. The writer's own checks passed;
#: the partial unique index on `offer_claim` is what refused it.
_RACED = "was offered somewhere else a moment ago; reload and try again"


def _refused(detail: str, refused: list[OfferRefusalOut]) -> JSONResponse:
    """The one 409 body this endpoint has, whoever decided the refusal.

    Every 409 from `create_offers` goes through here, so the shape the OpenAPI
    publishes (`OfferRefusedOut`: `detail` and `refused`, both required) is the
    shape on the wire in every case. A console that reads `refused` on a 409
    must not throw on the one refusal it did not expect -- and the refusal it
    does not expect is the race, which is exactly when it most needs to say
    something clear.

    `mode="json"` rather than a bare `model_dump()`: a `JSONResponse` renders
    with `json.dumps` and bypasses `response_model`, so nothing but this call
    stands between a future `Decimal`, `datetime` or enum in the refusal body
    and a `TypeError` raised inside the response render.
    """
    return JSONResponse(
        status_code=status.HTTP_409_CONFLICT,
        content=OfferRefusedOut(detail=detail, refused=refused).model_dump(mode="json"),
    )


def _lot_by_id(db: Session, lot_id: int) -> SalesLot:
    """The lot to offer. An id no lot wears is a 422, like an unknown code."""
    lot = db.get(SalesLot, lot_id)
    if lot is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown lot_id: {lot_id}",
        )
    return lot


def _offer_lot(
    db: Session,
    payload: OfferIn,
    *,
    lot_id: int,
    venue: SalesVenue,
    listing_format: ListingFormat,
) -> Listing:
    """Offer one assembled lot as one thing. Raises; `create_offers` catches.

    Every refusal is left to rise: `EmptyLot`, `LotRefused` and
    `OfferRefused` are each mapped to a status by the caller, in one place,
    so this function only has to get the arguments right.

    `quantity` is not passed at all: `OfferIn._one_subject` refuses anything
    but 1 on a lot body, and `offer` caps a lot listing at one unit
    regardless, as `ck_listing_lot_quantity_one` requires.
    """
    lot = _lot_by_id(db, lot_id)
    price = payload.price
    if price is None:
        # A backstop, not a branch a request can reach: `_one_subject`
        # refuses a lot body with no price. It stays so that a future caller
        # building an `OfferIn` in code is told, rather than writing a lot
        # listing priced at nothing.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="price is required when offering a lot",
        )
    return offering_writes.offer(
        db,
        lot=lot,
        venue=venue,
        listing_format=listing_format,
        price=price,
        title=payload.title,
        description=payload.description,
        external_id=payload.external_id,
    )


@router.post(
    "/offers",
    status_code=status.HTTP_201_CREATED,
    response_model=OfferBatchOut,
    responses={status.HTTP_409_CONFLICT: {"model": OfferRefusedOut}},
)
def create_offers(
    payload: OfferIn, db: DbSession, _admin: AdminUser
) -> OfferBatchOut | JSONResponse:
    """Offer items, or one lot, on one platform. All of them or none of them.

    The refusals are collected rather than stopping at the first, so a person
    offering twenty items is told about all four problems at once instead of
    discovering them one request at a time. Nothing is written either way:
    `offering_writes.offer` decides every refusal before it writes, so the
    rollback below has only the successful rows of a doomed batch to discard.

    **The `except` order below is load-bearing and mypy cannot check it.**
    `lot_writes.EmptyLot` is a subclass of `lot_writes.LotRefused`, so its
    clause must come first: reversing the two would route every 422 into the
    409 branch, silently, and the only sign would be an empty lot reported as
    a conflict. It is the same trap `record_listing_sale` carries with
    `SaleInputInvalid` and `SaleRefused`. Nothing in the type system, the
    linter or the test names would catch a later reordering, so the test that
    proves it asserts on the **body** -- an empty lot's 422 must name the lot
    -- rather than on the status alone, which pydantic would produce anyway.
    """
    venue = _venue_by_code(db, payload.venue)
    listing_format = _listing_format(payload.format)

    listing_ids: list[int] = []
    refusals: list[OfferRefusalOut] = []
    already: set[int] = set()
    try:
        if payload.lot_id is not None:
            try:
                listing = _offer_lot(
                    db,
                    payload,
                    lot_id=payload.lot_id,
                    venue=venue,
                    listing_format=listing_format,
                )
            except OfferRefused as refused:
                # One member cannot be offered, so the lot cannot: a lot is
                # all or nothing by construction, and the member's own code
                # is what a person has to act on.
                db.rollback()
                return _refused(
                    f"lot #{payload.lot_id} cannot be offered",
                    [
                        OfferRefusalOut(
                            item_code=refused.item_code, reason=refused.reason
                        )
                    ],
                )
            listing_ids.append(listing.id)
        for line in payload.items:
            item = _item_by_id(db, line.item_id)
            # Read before the write that may fail: after a failed flush the
            # session refuses further SQL, and this is what names the item in
            # the refusal below.
            item_code = item.item_code
            if line.item_id in already:
                # Caught here rather than left to the writer, which would see
                # the second copy as an item already offered and answer "is
                # already offered in the shop, listing #N" -- true, and no
                # help at all to someone who simply listed it twice.
                refusals.append(
                    OfferRefusalOut(
                        item_code=item_code,
                        reason="appears more than once in this batch; offer it once",
                    )
                )
                continue
            already.add(line.item_id)
            try:
                listing = offering_writes.offer(
                    db,
                    item=item,
                    venue=venue,
                    listing_format=listing_format,
                    price=line.price,
                    title=line.title,
                    description=line.description,
                    external_id=line.external_id,
                    quantity=payload.quantity,
                )
            except OfferRefused as refused:
                refusals.append(
                    OfferRefusalOut(item_code=refused.item_code, reason=refused.reason)
                )
                continue
            except IntegrityError:
                # `offer` flushes its claim, so the index refuses the loser of
                # a race here, inside the loop, where the item is still known.
                db.rollback()
                return _refused(
                    "1 item(s) cannot be offered",
                    [OfferRefusalOut(item_code=item_code, reason=_RACED)],
                )
            listing_ids.append(listing.id)
        if refusals:
            db.rollback()
            return _refused(f"{len(refusals)} item(s) cannot be offered", refusals)
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    # ------------------------------------------------------------------
    # ORDER-SENSITIVE. `EmptyLot` is a subclass of `LotRefused`, so it must
    # be caught FIRST. Swap these two clauses and every empty-lot 422 turns
    # into a 409 with no error anywhere: mypy does not check `except` order,
    # ruff does not either, and both clauses would still be "reachable".
    # `record_listing_sale` below carries the identical trap with
    # `SaleInputInvalid` and `SaleRefused`.
    # ------------------------------------------------------------------
    except lot_writes.EmptyLot as empty:
        # Bad input, not a conflict: a lot with nothing in it is a request
        # that could never make sense, not one that lost a race (spec,
        # *Errors*). The message names the lot, which is what the test
        # asserts on -- a status-only assertion would pass on pydantic's own
        # 422 and prove nothing about this clause existing.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(empty)
        ) from empty
    except lot_writes.LotRefused as refused_lot:
        # The lot itself cannot be offered -- already offered, sold,
        # dissolved. A conflict: it could have been offered a moment ago.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(refused_lot)
        ) from refused_lot
    except IntegrityError:
        # The same race, refused at commit rather than at a flush. Nothing
        # here knows which item lost -- the loop's own handler is what names
        # one -- so the list is empty rather than invented, and the body is
        # still the shape every other refusal has.
        db.rollback()
        return _refused("One of those items was offered somewhere else just now", [])
    return OfferBatchOut(listings=_reload(db, listing_ids))


# --------------------------------------------------------------------------
# Reading, editing and ending
# --------------------------------------------------------------------------


@router.get("/listings")
def list_listings(
    db: DbSession,
    _admin: AdminUser,
    venue: Annotated[str | None, Query(description="A sales_venue code")] = None,
    listing_format: Annotated[str | None, Query(alias="format")] = None,
    wanted_status: Annotated[
        str | None, Query(alias="status", description="One status, or `all`")
    ] = None,
    item_id: int | None = None,
) -> list[ListingOut]:
    """What is offered. Active and paused by default; `status=all` adds ended."""
    stmt = select(Listing)
    if venue is not None:
        stmt = stmt.where(Listing.sales_venue_id == _venue_by_code(db, venue).id)
    if listing_format is not None:
        stmt = stmt.where(Listing.format == _listing_format(listing_format))
    if item_id is not None:
        # Not `Listing.inventory_item_id == item_id`: that column is NULL on
        # a **lot** listing, so a coin offered inside a lot matched nothing
        # and the offers panel said "Not offered anywhere yet" about a coin
        # that was on sale -- and then offered an "Offer for sale..." button
        # the writer would refuse (`offering_writes._refuse_grouped`). A
        # lookup miss must not default silently.
        #
        # Through `offering_writes` rather than a claim query written here:
        # that module owns "is this item spoken for", and the released
        # claims this needs are exactly what a fourth local copy would
        # forget. Past-tense, because this endpoint serves offer *history*
        # -- the panel asks with `status=all`, and a lot listing that has
        # ended holds only released claims.
        stmt = stmt.where(offering_writes.ever_named_any([item_id]))
    if wanted_status is None:
        # The two statuses that mean the item is spoken for -- the same pair
        # `offering_writes.ON_OFFER` names, so the list and the writer agree
        # about what "offered" means.
        stmt = stmt.where(Listing.status.in_(offering_writes.ON_OFFER))
    elif wanted_status != _ALL:
        stmt = stmt.where(Listing.status == _listing_status(wanted_status))

    rows = db.scalars(_eager(stmt).order_by(Listing.id)).all()
    return [_out(row) for row in rows]


#: Columns that are `NOT NULL` on `Listing` but optional on `ListingUpdate` --
#: omitting one leaves it alone, but an explicit null is a client mistake.
_REQUIRED_ON_UPDATE = frozenset({"price", "title", "description"})


def _refuse_null_required(data: dict[str, Any]) -> None:
    """Raise a 422 naming every required column a PATCH sent as an explicit null."""
    nulled = sorted(f for f in _REQUIRED_ON_UPDATE if f in data and data[f] is None)
    if nulled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{', '.join(nulled)} cannot be null",
        )


@router.patch("/listings/{listing_id}")
def update_listing(
    listing_id: int, payload: ListingUpdate, db: DbSession, _admin: AdminUser
) -> ListingOut:
    """Change an offer's price or wording. Send `version` to catch conflicts.

    Nothing here touches `status` or the claims: ending an offer is its own
    endpoint, because ending one has consequences for other listings that a
    field assignment cannot express.
    """
    listing = _get_listing(db, listing_id)

    # exclude_unset: an omitted field is left alone, an explicit null clears
    # it -- except the NOT NULL columns, which _refuse_null_required rejects
    # rather than silently leaving unchanged.
    data: dict[str, Any] = payload.model_dump(exclude_unset=True)
    expected = data.pop("version", None)
    if expected is not None and expected != listing.version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_STALE)
    _refuse_null_required(data)

    for field, value in data.items():
        setattr(listing, field, value)

    try:
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_STALE
        ) from exc
    return _out(_get_listing(db, listing_id))


def _refuse_auction_lot(db: Session, listing: Listing) -> None:
    """Refuse to end an auction-format listing directly. Task 5, defect 1.

    An auction-format listing always belongs to an `auction_lot`
    (`uq_auction_lot_listing_id`), and `app.auctions` is that row's sole
    writer -- `remove_lot`, `cancel` and `settle` each end the listing
    *and* either delete the `auction_lot` row or record its result, in one
    transaction. This endpoint calls only `offering_writes.end_offer`, which
    has no idea `auction_lot` exists: ending an auction listing through here
    would leave a live `auction_lot` row pointing at a listing that is no
    longer offered.

    That is not a hypothetical. `app.auctions.consign` reads an auction's
    coins through `offering_writes.offered_items(db, auction_lot.listing)`,
    which answers `[]` for an ended listing -- so a lot ended this way would
    be silently skipped, and the owner would see an auction reporting itself
    consigned while that lot's coins never moved. `GET /api/listings` would
    also go on showing the lot as `ended`, with no auction transition ever
    having produced that state.

    The fix is a guard, not a rewrite: an auction lot is ended through its
    auction -- `remove_lot`, `cancel` (both a withdrawal) or `settle` (a
    result) -- never directly, the same way `routers.auctions` has no
    endpoint that writes `listing.status` for an auction lot itself.
    """
    if listing.format is ListingFormat.auction:
        auction_id = db.scalar(
            select(AuctionLot.auction_id).where(AuctionLot.listing_id == listing.id)
        )
        where = f" of auction #{auction_id}" if auction_id is not None else ""
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"listing #{listing.id} is an auction lot{where}; end it through "
                "the auction (remove the lot, cancel, or settle), not directly"
            ),
        )


@router.post("/listings/{listing_id}/end")
def end_listing(listing_id: int, db: DbSession, _admin: AdminUser) -> ListingOut:
    """End an offer, resuming any store listing it set aside.

    Withdrawal, not a sale: `sold=True` belongs to the record-a-sale path,
    which has a buyer and a price to record alongside the ending.

    Refuses an auction-format listing outright -- see `_refuse_auction_lot`.
    """
    listing = _get_listing(db, listing_id)
    _refuse_auction_lot(db, listing)
    try:
        offering_writes.end_offer(db, listing, sold=False)
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_STALE
        ) from exc
    return _out(_get_listing(db, listing_id))


# --------------------------------------------------------------------------
# Recording a sale
# --------------------------------------------------------------------------


def _sale_recorded(db: Session, order: SalesOrder) -> SaleRecordedOut:
    """Shape a recorded sale for the console: gross, fees, net, buyer, items.

    Both figures are read with their own `select()` rather than through
    `order.fees` and each line's `.shares`: the fee total is a `sum()` the
    database can do without loading a row per fee, and the item codes need a
    join to `inventory_item` anyway, because they come back ordered by
    `item_code` rather than by share id. (Until `place_order` stopped
    consulting a new line's shares, these queries were also working around a
    collection left cached empty from before the row existed -- see
    `order_writes._sync_shares`. That hazard is gone; the queries stay for the
    two reasons above.)

    `net_amount` is computed here and only here: `record_sale`'s module
    docstring says net payout is never stored, so this is the one place the
    subtraction happens.
    """
    fee_total = db.scalar(
        select(func.sum(SalesOrderFee.amount)).where(
            SalesOrderFee.sales_order_id == order.id
        )
    ) or Decimal("0.00")
    item_codes = list(
        db.scalars(
            select(InventoryItem.item_code)
            .join(
                SalesOrderItemShare,
                SalesOrderItemShare.inventory_item_id == InventoryItem.id,
            )
            .join(
                SalesOrderItem,
                SalesOrderItemShare.sales_order_item_id == SalesOrderItem.id,
            )
            .where(SalesOrderItem.sales_order_id == order.id)
            .order_by(InventoryItem.item_code)
        )
    )
    return SaleRecordedOut(
        id=order.id,
        external_order_id=order.external_order_id,
        total_amount=order.total_amount,
        fee_total=fee_total,
        net_amount=order.total_amount - fee_total,
        buyer=order.customer.display_name,
        item_codes=item_codes,
    )


@router.post(
    "/listings/{listing_id}/sale",
    response_model=SaleRecordedOut,
    status_code=status.HTTP_201_CREATED,
)
def record_listing_sale(
    listing_id: int, body: RecordSaleIn, db: DbSession, user: AdminUser
) -> SaleRecordedOut:
    """Record that a listing sold on its platform, with the platform's fees.

    The sale is over by the time it is entered, so this both creates the
    order and ends the listing, in one transaction: a sale recorded with the
    listing left on offer would be an item for sale that is already gone.

    `record_sale`'s own refusals split by cause: a genuine conflict -- the
    listing not on offer, an unmapped venue kind -- is a `SaleRefused`, 409;
    malformed input is the narrower `SaleInputInvalid`, 422 (today that
    branch is unreachable from here -- the request schema's own `ge=0` and
    `decimal_places=2` already refuse a negative or sub-cent price or fee
    before this body ever runs -- but the guard stays in `record_sale` for
    phase-4 auction settlement, a future caller that will not pass through
    this schema at all). Both are decided, and both are caught,
    before anything is written, so either status means nothing was written;
    an unknown fee kind fails the same way, as the 422 `require_code`
    already raises. `SaleInputInvalid` is checked first because it is a
    `SaleRefused` subclass: reversing the two `except` clauses would route
    every 422 into the 409 branch instead, silently.
    """
    try:
        listing = db.get(Listing, listing_id)
        if listing is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"No listing {listing_id}")
        order = sales_writes.record_sale(
            db,
            listing,
            price=body.price,
            buyer_username=body.buyer_username,
            external_order_id=body.external_order_id,
            fees=[
                sales_writes.FeeLine(fee.kind, fee.amount, fee.note)
                for fee in body.fees
            ],
            recorded_by=user,
            equal_shares=body.equal_shares,
        )
    except HTTPException:
        db.rollback()
        raise
    except sales_writes.SaleInputInvalid as invalid:
        db.rollback()
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, str(invalid)
        ) from invalid
    except sales_writes.SaleRefused as refused:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from refused
    except StaleDataError as stale:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, _STALE) from stale
    db.commit()
    return _sale_recorded(db, order)
