"""Offering items for sale: the HTTP face of `offering_writes`.

Admin only, and deliberately so: an offer row carries what the item cost, and
the shop's own catalog endpoints are what a customer reads.

Every offer and ending here goes through `app.offering_writes`, which is the
only writer of `listing.status` and `offer_claim`; a sale is recorded through
`app.sales_writes`, and `update_listing` sets a listing's price, wording and
listing number itself. This module resolves codes to rows, owns the
transaction, and shapes the response; it decides nothing about what may be
offered.

**A batch is all or nothing.** The whole batch runs inside one transaction and
a refusal rolls it back, rather than committing each item and trying to undo
the ones already written -- an undo has its own failure mode, and the state it
would leave behind is exactly the half-offered mess the claim table exists to
prevent.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy import Select, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.orm.strategy_options import _AbstractLoad

from .. import lot_writes, offering_writes, order_writes, sales_writes
from .. import offer_titles as offer_titles_module
from ..auction_holding import auction_ids_by_listing
from ..deps import AdminUser, DbSession
from ..models import (
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
    AuctionRefusedOut,
    ListingOut,
    ListingUpdate,
    OfferBatchOut,
    OfferIn,
    OfferRefusalOut,
    OfferRefusedOut,
    OfferTitlesOut,
    RecordSaleIn,
    SaleRecordedOut,
)
from ._paging import DEFAULT_LIMIT, MAX_LIMIT, Limit, Offset, page
from ._resolve import (
    enum_member,
    found_or_404,
    get_or_404,
    item_by_id,
    lot_by_id,
    refuse_null_required,
    refuse_stale_version,
    venue_by_code,
)
from ._tx import commit, committing

router = APIRouter(tags=["selling"])

_STALE = "This offer was changed by someone else. Reload and reapply your changes."

#: `status=all` on the listing list: every status, ended ones included. Any
#: other value is one `ListingStatus`; the default is what is on offer now.
_ALL = "all"


# --------------------------------------------------------------------------
# Shaping the response
# --------------------------------------------------------------------------


def listing_loads() -> tuple[_AbstractLoad, ...]:
    """The loader options for what `listing_out` reads from a listing.

    Relative to `Listing`, so a caller that reaches listings through another
    row -- `routers.auctions`, through each auction lot -- chains them with
    `.options(*listing_loads())` and loads exactly what this module does.

    The lot chain is loaded for every listing, item listings included: a
    `selectinload` of a null foreign key costs nothing, and branching the
    options on a per-row value is not something one statement can do.
    """
    return (
        selectinload(Listing.inventory_item),
        selectinload(Listing.sales_venue),
        selectinload(Listing.currency),
        selectinload(Listing.auction_lot),
        selectinload(Listing.sales_lot)
        .selectinload(SalesLot.members)
        .selectinload(SalesLotItem.item),
    )


def _eager(stmt: Select[tuple[Listing]]) -> Select[tuple[Listing]]:
    """Load what `listing_out` reads, so a list of offers is not a query per row."""
    return stmt.options(*listing_loads())


def external_url(listing: Listing, venue: SalesVenue) -> str | None:
    """The listing's page on its platform, stored or derived.

    A stored URL wins, because a platform that moved one listing is exactly
    the case the column exists for. Otherwise it is built from the platform's
    template, and **not written back**: a derived value saved to the row
    would outlive the template it came from.

    Nothing in this application writes `listing.external_url`: neither
    `OfferIn` nor `ListingUpdate` carries it, so the stored branch answers
    only for a row given one directly in the database.

    The listing number is percent-encoded whole (`safe=""`) before it goes
    into the template, so a `/`, `?` or `#` in it cannot reach another path
    or add a query. Either way the result is a link only if it is `http` or
    `https`: the console renders it as an `href`, where a `javascript:` URL
    would run.
    """
    url = listing.external_url
    if not url and listing.external_id and venue.listing_url_template:
        url = venue.listing_url_template.replace(
            "{external_id}", quote(listing.external_id, safe="")
        )
    if url and url.lower().startswith(("http://", "https://")):
        return url
    return None


def listing_out(listing: Listing) -> ListingOut:
    """Shape one offer for the console, item listing or lot listing.

    `ck_listing_item_xor_lot` makes the two mutually exclusive, so exactly one of
    the two branches below fills its half of `ListingOut` and the other half
    stays null. Reading `listing.inventory_item.item_code` unconditionally
    would make the whole Listings page an `AttributeError` the moment a lot
    is offered.

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

    **Public, not private:** `routers.auctions` shapes an auction lot's own
    listing through this same function, since an auction lot's listing is a
    listing like any other and a second, copied implementation would have to
    be told by hand every time this one changed. Import this rather than
    reimplementing it.
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
        auction_id=listing.auction_lot.auction_id if listing.auction_lot else None,
        version=listing.version,
    )


def _reload(db: Session, listing_ids: list[int]) -> list[ListingOut]:
    """Read the written offers back, in the order they were asked for."""
    rows = db.scalars(_eager(select(Listing).where(Listing.id.in_(listing_ids)))).all()
    by_id = {row.id: row for row in rows}
    return [listing_out(by_id[listing_id]) for listing_id in listing_ids]


def _get_listing(db: Session, listing_id: int) -> Listing:
    """One offer, or a 404."""
    return found_or_404(
        db.scalar(_eager(select(Listing).where(Listing.id == listing_id))),
        "No such offer",
    )


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
    lot = lot_by_id(db, lot_id)
    price = payload.price
    if price is None:
        # A backstop, not a branch a request can reach: `_one_subject`
        # refuses a lot body with no price. It stays so that a future caller
        # building an `OfferIn` in code is told, rather than writing a lot
        # listing priced at nothing.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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


def _refuse_unlistable(
    payload: OfferIn, venue: SalesVenue, listing_format: ListingFormat
) -> None:
    """422 for a batch whose shape this platform cannot list, before any write.

    Rules that read only the request and the platform, so they are decided
    ahead of the first item or lot being looked up: the quantity, the
    format, then a shop lot's title.
    """
    # An offer is one unit, wherever it is made. An item is one record with
    # one cost, and gain reporting counts that cost against every share an
    # order line writes for it, so units sold separately would count it once
    # each. An outside sale is also recorded as a line of one that ends the
    # listing (`sales_writes.SaleLine`), so a listing of several would end
    # with units still on it and its item still `listed` behind a sale.
    if payload.quantity > 1:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"quantity must be 1 on {venue.name}: an item is offered as one unit"
            ),
        )

    # The shop sells at a fixed price through checkout and nothing else: an
    # auction listing there would hold its items while the catalog could not
    # show it, checkout could not sell it and Record sale refuses a store
    # listing.
    if venue.is_own_store and listing_format is not ListingFormat.fixed_price:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{venue.name} sells at a fixed price through checkout: "
                "offer an auction on another platform"
            ),
        )

    if payload.lot_id is not None and venue.is_own_store and not payload.title.strip():
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=_UNTITLED_LOT
        )


#: Why a shop lot needs a title. A lot offered in the shop is named to buyers
#: by its listing's title and nothing else: an item listing left untitled is
#: named by its item, but a lot has no item, and its own title is the group's
#: working name, which the catalog does not show
#: (`routers.catalog._lot_entry`). Asked when the lot is offered and when its
#: listing is edited.
_UNTITLED_LOT = (
    "title is required for a lot in the web store: it is the only name a buyer sees"
)


def _refuse_untitled_shop_lot(listing: Listing, data: dict[str, Any]) -> None:
    """422 if an edit would blank the title of a lot listing in the shop."""
    if (
        "title" in data
        and listing.sales_lot_id is not None
        and listing.sales_venue.is_own_store
        and not str(data["title"]).strip()
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=_UNTITLED_LOT
        )


@router.get("/offers/titles")
def offer_titles(
    db: DbSession,
    _admin: AdminUser,
    item_ids: Annotated[list[int], Query(max_length=500)],
) -> OfferTitlesOut:
    """Suggested public titles for items about to be offered.

    What the offer dialog pre-fills each title with, instead of the seller's
    `source_title` -- see `app.offer_titles` for how one is composed. Read
    only; nothing about an item or a listing changes.
    """
    return OfferTitlesOut(titles=offer_titles_module.suggested_titles(db, item_ids))


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
    a conflict. `record_listing_sale` avoids the identical trap with
    `SaleInputInvalid` and `SaleRefused` by leaving that pair to handlers
    `app.main` registers by class; this `EmptyLot`/`LotRefused` pair is not
    handled that way. Nothing in the type system, the linter or the test
    names would catch a later reordering here, so the test that proves it
    asserts on the **body** -- an empty lot's 422 must name the lot -- rather
    than on the status alone, which pydantic would produce anyway.
    """
    venue = venue_by_code(db, payload.venue)
    listing_format = enum_member(ListingFormat, payload.format, "format")
    _refuse_unlistable(payload, venue, listing_format)

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
        # Every item of the batch in one ascending statement, before the
        # first offer: `offer` locks only the item it is given, so a batch
        # taken one item at a time, in the order it was typed, can hold one
        # coin while a checkout holds another, each waiting on the other.
        item_ids = [line.item_id for line in payload.items]
        locked = offering_writes.lock_for_sale(db, item_ids=item_ids)
        # Each `offer` below takes its own pass, which takes the lot rows of
        # the live offers holding its item. A lot offered between this pass's
        # unlocked read and its item lock has no lot row held here, and that
        # later pass would take it while these items are held: lots after
        # items, the inversion a checkout of that lot deadlocks against.
        offering_writes.refuse_if_lot_unheld(
            offering_writes.offers_holding(db, item_ids), locked.lot_ids
        )
        for line in payload.items:
            item = item_by_id(db, line.item_id)
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
    # `record_listing_sale` below carries no trap of this shape: its
    # `SaleInputInvalid`/`SaleRefused` pair goes to handlers `app.main`
    # registers by class, which makes that particular reversal unwritable
    # there. This pair is not handled that way.
    # ------------------------------------------------------------------
    except lot_writes.EmptyLot as empty:
        # Bad input, not a conflict: a lot with nothing in it is a request
        # that could never make sense, not one that lost a race (spec,
        # *Errors*). The message names the lot, which is what the test
        # asserts on -- a status-only assertion would pass on pydantic's own
        # 422 and prove nothing about this clause existing.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(empty)
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
    response: Response,
    venue: Annotated[str | None, Query(description="A sales_venue code")] = None,
    listing_format: Annotated[str | None, Query(alias="format")] = None,
    wanted_status: Annotated[
        str | None, Query(alias="status", description="One status, or `all`")
    ] = None,
    item_id: int | None = None,
    ids: Annotated[
        list[int] | None,
        Query(max_length=MAX_LIMIT, description="Listing ids; any status"),
    ] = None,
    limit: Limit = DEFAULT_LIMIT,
    offset: Offset = 0,
) -> list[ListingOut]:
    """What is offered. Active and paused by default; `status=all` adds ended.

    `ids` (repeated: `?ids=1&ids=2`) answers those listings whatever their
    status, unless `status` is also given -- what the order editor reads to
    price the lines of an order, ended listings included. One page at a time
    (`limit`, `offset`), oldest first, with how many match in the
    `X-Total-Count` header.
    """
    stmt = select(Listing)
    if ids is not None:
        stmt = stmt.where(Listing.id.in_(ids))
    if venue is not None:
        stmt = stmt.where(Listing.sales_venue_id == venue_by_code(db, venue).id)
    if listing_format is not None:
        stmt = stmt.where(
            Listing.format == enum_member(ListingFormat, listing_format, "format")
        )
    if item_id is not None:
        # Not `Listing.inventory_item_id == item_id`: that column is NULL on
        # a **lot** listing, so a coin offered inside a lot would match
        # nothing, and the offers panel would say "Not offered anywhere yet"
        # about a coin that is on sale and offer an "Offer for sale..."
        # button the writer refuses (`offering_writes._refuse_grouped`).
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
        # about what "offered" means. Not for named ids: an order's line
        # may be on a listing that has since ended, and it still needs a
        # price.
        if ids is None:
            stmt = stmt.where(Listing.status.in_(offering_writes.ON_OFFER))
    elif wanted_status != _ALL:
        stmt = stmt.where(
            Listing.status
            == enum_member(ListingStatus, wanted_status, "status", also=[_ALL])
        )

    paged = page(db, stmt, response, limit=limit, offset=offset)
    rows = db.scalars(_eager(paged).order_by(Listing.id)).all()
    return [listing_out(row) for row in rows]


#: Columns that are `NOT NULL` on `Listing` but optional on `ListingUpdate` --
#: omitting one leaves it alone, but an explicit null is a client mistake.
_REQUIRED_ON_UPDATE = frozenset({"price", "title", "description"})


#: What may still change on an offer that has ended: the platform's own
#: listing number, which is often only looked up afterwards -- the eBay item
#: number of something that already sold. Everything else is the record.
_EDITABLE_AFTER_ENDING = frozenset({"external_id"})


def _refuse_rewriting_an_ended_offer(listing: Listing, data: dict[str, Any]) -> None:
    """409 if an edit would change the terms of an offer that has ended.

    An ended listing's price, title and description are what it was offered
    at and as -- part of the record a sale, or a settlement, reconciles
    against. The console hides the edit for an ended row; this is what
    stops a stale tab or a script from rewriting history.
    """
    if listing.status is not ListingStatus.ended:
        return
    # Changed values only, so a caller that sends the whole row back with
    # only the listing number altered is let through; refusing by key would
    # refuse the one edit this permits.
    frozen = sorted(
        field
        for field, value in data.items()
        if field not in _EDITABLE_AFTER_ENDING and getattr(listing, field) != value
    )
    if frozen:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"listing #{listing.id} has ended, so its {', '.join(frozen)} "
                "cannot change: they are part of the record. Only its listing "
                "number can still be set."
            ),
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
    # it -- except the NOT NULL columns, which refuse_null_required rejects
    # rather than silently leaving unchanged.
    data: dict[str, Any] = payload.model_dump(exclude_unset=True)
    refuse_stale_version(data.pop("version", None), listing.version, _STALE)
    refuse_null_required(data, _REQUIRED_ON_UPDATE)
    _refuse_rewriting_an_ended_offer(listing, data)
    _refuse_untitled_shop_lot(listing, data)

    for field, value in data.items():
        setattr(listing, field, value)

    commit(db, _STALE)
    return listing_out(_get_listing(db, listing_id))


def _refuse_auction_lot(db: Session, listing: Listing) -> None:
    """Refuse to end an auction lot's listing directly.

    An auction-format listing has **at most one** `auction_lot`
    (`uq_auction_lot_listing_id`), and `app.auctions` alone decides whether
    that row exists -- `remove_lot`, `cancel` and `settle` each end the listing
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

    So this guard holds: an auction lot is ended through its
    auction -- `remove_lot`, `cancel` (both a withdrawal) or `settle` (a
    result) -- never directly, the same way `routers.auctions` has no
    endpoint that writes `listing.status` for an auction lot itself.

    **Keyed on the `auction_lot` row, not on `format` alone.** An
    auction-format listing need not belong to an auction: the Offer dialog
    offers a coin directly on eBay by auction, and `remove_lot` deletes the
    row while the listing keeps `format = auction`. Neither has an auction
    to end it through, so both take the ordinary path -- keying on format
    would leave a directly offered auction listing with no way to be ended
    at all.
    """
    auction_id = auction_ids_by_listing(db, [listing]).get(listing.id)
    if auction_id is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"listing #{listing.id} is a lot of auction #{auction_id}; end it "
                "through the auction (remove the lot, cancel, or settle), not "
                "directly"
            ),
        )


@router.post("/listings/{listing_id}/end")
def end_listing(listing_id: int, db: DbSession, _admin: AdminUser) -> ListingOut:
    """End an offer, resuming any store listing it set aside.

    Withdrawal, not a sale: `sold=True` belongs to the record-a-sale path,
    which has a buyer and a price to record alongside the ending.

    Refuses a listing that is a lot of an auction -- see `_refuse_auction_lot`
    -- and one whose stock an open order holds (`_refuse_held_by_an_order`).
    """
    listing = _get_listing(db, listing_id)
    _refuse_auction_lot(db, listing)
    already_ended = listing.status is ListingStatus.ended
    with committing(db, _STALE):
        offering_writes.end_offer(db, listing, sold=False)
        if not already_ended:
            _refuse_held_by_an_order(db, listing_id)
    return listing_out(_get_listing(db, listing_id))


def _refuse_held_by_an_order(db: Session, listing_id: int) -> None:
    """409 if an order holds stock of this listing that it could still return.

    Cancelling or refunding such an order puts its stock back on the
    listing (`order_writes.return_stock`), and an ended listing has nowhere
    to put it: the cancellation would be refused for good
    (`routers.orders._no_stock_to_return`), stranding the order. So the
    order is dealt with first -- shipped, or cancelled -- and the listing
    ended after.

    Asked after `end_offer`, inside the same transaction, because that call
    holds the listing's row lock: a checkout taking the last unit either
    committed before the lock was granted, and its order is found here, or
    waits on the lock and is refused the ended listing. Asked before it,
    the check could miss an order placed in between. A refusal raised here
    rolls the ending back with everything else in the block. An ending of a
    listing already ended changes nothing, so it is not asked then.
    """
    holding = order_writes.orders_holding_stock(db, listing_id)
    if holding:
        orders = ", ".join(f"#{order_id}" for order_id in holding)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"listing #{listing_id} cannot be ended while order {orders} "
                "holds its stock: ending it would leave that order unable to "
                "be cancelled. Ship or cancel the order first."
            ),
        )


# --------------------------------------------------------------------------
# Recording a sale
# --------------------------------------------------------------------------


def sale_recorded(db: Session, order: SalesOrder) -> SaleRecordedOut:
    """Shape a recorded sale for the console: gross, fees, net, buyer, items.

    Both figures are read with their own `select()` rather than through
    `order.fees` and each line's `.shares`: the fee total is a `sum()` the
    database can do without loading a row per fee, and the item codes need a
    join to `inventory_item` anyway, because they come back ordered by
    `item_code` rather than by share id.

    `net_amount` is computed here and only here: net payout is never stored,
    so this is the one place the subtraction happens. **Public, not private:**
    `routers.auctions.settle_auction` shapes every order a settlement writes
    through this same function -- one buyer's purchase looks identical
    whether it came from Record sale or from a settled auction lot, and a
    second, copied implementation is exactly how that claim would quietly
    stop being true. Import this rather than
    reimplementing it; a caller with a different shape needs a different
    function, not a fork of this one.
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
    status_code=status.HTTP_201_CREATED,
    # `SaleInputInvalid`/`SaleRefused` go to the handlers `app.main`
    # registers by class, which answer `{detail, refused: [...]}`
    # (`main._refusal_body`'s single-entry fallback for a `sales_writes`
    # exception, which carries no `refusals` attribute of its own) -- the
    # same shape `AuctionRefusedOut` names for `routers.auctions`. Declared
    # here so this endpoint's OpenAPI contract matches what it answers, not
    # the plain `{detail}` shape of an `HTTPException`.
    responses={
        status.HTTP_409_CONFLICT: {"model": AuctionRefusedOut},
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": AuctionRefusedOut},
    },
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
    malformed input is the narrower `SaleInputInvalid`, 422. From here the
    money half of that is unreachable -- the request schema's `ge=0` and
    `decimal_places=2` refuse a negative or sub-cent price or fee first --
    but the checks stay in `sales_writes.record_sale_lines`, which
    `auctions.settle` reaches without this schema. Both are raised before
    anything is written, so either status means nothing was written; an
    unknown fee kind fails the same way, as the 422 `require_code` raises.

    **Neither is caught here.** Both propagate to the handlers `app.main`
    registers for them by class, as for every `app.auctions` caller, so no
    ordering of `except` clauses decides the status.
    `test_sale_input_invalid_from_record_sale_is_a_422_not_a_409`
    (`tests/test_record_sale_api.py`) proves the dispatch.
    """
    try:
        listing = get_or_404(db, Listing, listing_id, f"No listing {listing_id}")
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
    except StaleDataError as stale:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, _STALE) from stale
    except Exception:
        # Every other exception this can raise, `HTTPException` and
        # `sales_writes.SaleRefused`/`SaleInputInvalid` included: rolled back
        # here, unconditionally, and re-raised unchanged -- this decides no
        # status, so it creates no ordering hazard of its own. The `client`
        # fixture in `tests/conftest.py` shares one session across every
        # request in a test with no per-request teardown, so a path that
        # skipped this would leave that shared session dirty for the next
        # assertion, not merely for the next request in production, where
        # `database.get_db`'s own `finally: db.close()` would have done it
        # anyway.
        db.rollback()
        raise
    db.commit()
    return sale_recorded(db, order)
