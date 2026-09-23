"""Item-level operations, on what you own rather than on what is for sale.

Separate from `/catalog`, which is listing-centric. An item exists before it is
listed and after it is sold, and operations like splitting a lot apply to the
object, not to the offer.

Staff-only throughout: everything here exposes cost basis.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.sql.selectable import ScalarSelect

from .. import grades, item_attributes, lot_writes, offering_writes, sale_state
from ..classifier_defaults import refresh_items
from ..config import settings
from ..deps import AdminUser, DbSession
from ..field_sources import (
    SUGGESTION,
    derived_fields,
    forget,
    hold,
    record_derived,
)
from ..inventory_search import (
    VIEWS,
    UnknownIssue,
    count_facets,
    count_issues,
    plain,
    search,
)
from ..lifecycle_writes import record_initial_status, set_location, set_status
from ..models import (
    AuctionLot,
    Authenticity,
    BullionForm,
    CoinDetail,
    Country,
    CurrencyDetail,
    Customer,
    Denomination,
    DenominationKind,
    Disposition,
    ErrorType,
    FedDistrict,
    Grade,
    GradeDesignation,
    GradingService,
    InventoryItem,
    ItemCertification,
    ItemError,
    ItemFieldReview,
    ItemKind,
    ItemStatus,
    ItemStatusHistory,
    Listing,
    ListingFormat,
    Metal,
    Mint,
    NoteType,
    ProvenanceSource,
    PurchaseOrder,
    SalesOrder,
    SalesOrderItem,
    SalesOrderItemShare,
    SalesOrderStatus,
    SealColor,
    Series,
    SignatureCombination,
    StorageForm,
    StorageLocation,
    StrikeType,
    ValuationBasis,
)
from ..references import code_to_id, require_code
from ..schemas import (
    RECEIVE_OUTCOMES,
    BulkEditRequest,
    InventoryItemOut,
    InventoryItemUpdate,
    InventoryPageOut,
    ItemAttributeOut,
    ItemCreate,
    ItemDetailOut,
    ItemErrorOut,
    ItemErrorsOut,
    ItemErrorsRequest,
    ItemReviewOut,
    ItemSaleOut,
    ReceiveRequest,
    ReviewRequest,
    SaleUseOut,
    SplitPieceIn,
    SplitRequest,
    SplitResultOut,
)
from ..splitting import SplitError, SplitPiece, split_item
from ..years import YEAR_FIELDS, backwards, refuse_backwards, resolve_years

router = APIRouter(prefix="/inventory", tags=["inventory"])

#: Per-piece classifier overrides a caller may supply, and where each resolves.
PIECE_CLASSIFIERS: dict[str, type] = {
    "denomination": Denomination,
    "grade": Grade,
    "strike_type": StrikeType,
    "metal": Metal,
}


def _get_item(db: Session, item_id: int) -> InventoryItem:
    item = db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Inventory item not found"
        )
    return item


def _to_piece(db: Session, spec: SplitPieceIn) -> SplitPiece:
    overrides: dict[str, object] = {}
    codes = {field: getattr(spec, field) for field in PIECE_CLASSIFIERS}
    codes["grade"], codes["strike_type"] = grades.split_fields(
        spec.grade, spec.strike_type
    )
    for field, model in PIECE_CLASSIFIERS.items():
        value = codes[field]
        if value is not None:
            overrides[f"{field}_id"] = code_to_id(db, model, value, field)
    if spec.year_start is not None:
        overrides["year_start"] = spec.year_start
        overrides["year_end"] = spec.year_start

    # Pieces come out of a tube or a set as individual items, whatever the lot
    # was packaged as.
    single = db.scalar(select(StorageForm.id).where(StorageForm.code == "single"))
    if single is not None:
        overrides.setdefault("storage_form_id", single)

    return SplitPiece(
        source_title=spec.source_title,
        piece_count=spec.piece_count,
        relative_value=spec.relative_value,
        overrides=overrides,
    )


@router.get("/{view}/search")
def search_inventory(
    view: str,
    request: Request,
    db: DbSession,
    _admin: AdminUser,
    q: Annotated[
        str | None, Query(description="Free text over source title, code, notes")
    ] = None,
    sort: str | None = None,
    desc: bool = False,
    facets: Annotated[
        bool, Query(description="Include value counts for the panel")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InventoryPageOut:
    """Browse one inventory. `view` is `coins` or `currency`.

    Two views rather than one grid with a kind filter, because the columns
    that matter differ: a coin has a mint mark and a variety, a banknote has a
    series letter, a seal colour and its own printed serial. Sharing one grid
    would leave most columns blank most of the time.

    Filters are whatever the view's specification names -- anything else is a
    422 rather than being ignored, because a silently dropped filter returns
    the whole collection and looks like a matching result.
    """
    spec = VIEWS.get(view)
    if spec is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown inventory view: {view!r}. Expected one of "
            f"{sorted(VIEWS)}.",
        )

    reserved = {"q", "sort", "desc", "facets", "limit", "offset"}
    params = {k: v for k, v in request.query_params.items() if k not in reserved}

    lot_code = params.get("lot")
    if lot_code and not db.scalar(
        select(InventoryItem.id).where(InventoryItem.item_code == lot_code)
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"No item has code {lot_code!r}.",
        )

    try:
        rows, total = search(
            db,
            spec,
            params=params,
            query=q,
            sort=sort,
            descending=desc,
            limit=limit,
            offset=offset,
        )
    except UnknownIssue as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown issue {exc.args[0]!r} for {view}. Available: "
            f"{sorted(spec.issues)}",
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown filter {exc.args[0]!r} for {view}. Available: "
            f"{sorted(spec.filters)}",
        ) from exc
    except ValueError as exc:
        # Two different failures reach here: an unsortable column, and an
        # unrecognised `deleted` mode. Appending the sortable list to both
        # sends the wrong person looking in the wrong place.
        hint = f" Sortable: {sorted(spec.sortable)}" if "sort" in str(exc) else ""
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{exc}{hint}"
        ) from exc

    return InventoryPageOut(
        view=view,
        rows=rows,
        total=total,
        limit=limit,
        offset=offset,
        sort=sort or spec.default_sort,
        descending=desc,
        sortable=sorted(spec.sortable),
        facets=count_facets(db, spec, params=params, query=q) if facets else {},
        issues=count_issues(db, spec, params=params, query=q) if facets else {},
        issue_descriptions=(
            {name: issue.description for name, issue in spec.issues.items()}
            if facets
            else {}
        ),
    )


#: Fields a piece inherits from its lot, and so may be overriding.
#:
#: A subset of splitting.INHERITED: only what a person actually re-decides
#: while attributing. Showing the lot's storage_form beside a piece's would be
#: noise, since a piece always comes out of the tube as a single.
#:
#: Named by API field, not by column: a classifier here (`grade`, `country`,
#: ...) is a key into ITEM_CLASSIFIERS below and crosses the wire as a code,
#: the same as every other classifier on this router. The plain scalars
#: (`year_start`, `year_end`, `fineness`, `fine_weight_ozt`) keep their own
#: column names since there is nothing to resolve.
LOT_CLAIM_FIELDS: tuple[str, ...] = (
    "year_start",
    "year_end",
    "strike_type",
    "grade",
    "grade_designation",
    "grading_service",
    "denomination",
    "country",
    "metal",
    "series",
    "fineness",
    "fine_weight_ozt",
    "authenticity",
)


def _classifier_code(db: Session, model: type, fk: int | None) -> str | None:
    """The code a classifier's foreign key resolves to, or None when unset.

    Looked up by id rather than through an ORM relationship, because not
    every entry in ITEM_CLASSIFIERS has one -- `series` is set and read as a
    plain `series_id` column with no `InventoryItem.series` relationship
    declared -- and one lookup path that works for all of them is simpler
    than two.
    """
    if fk is None:
        return None
    row = db.get(model, fk)
    return row.code if row is not None else None


def _refuse_auction_lots(
    db: Session, listings: Sequence[Listing], outcome: str
) -> None:
    """Refuse a receipt that would end an auction lot's offer. 409.

    The third door onto an orphaned `auction_lot`, after
    `routers.offers.end_listing` and `sales_writes.record_sale`, and the one
    a whole-branch review found still open. `app.auctions` is the sole writer
    of `auction_lot`, and `remove_lot`, `cancel` and `settle` each end the
    lot's listing *and* delete or resolve its row in one transaction. This
    endpoint calls `offering_writes.end_offer`, which has never heard of
    `auction_lot`.

    **The premise that made this look unreachable was false by one line.**
    The "already received" refusal in `receive_items` is conditioned on
    `payload.outcome == "received"`, so the three outcomes that end an offer
    -- `missing`, `returned`, `canceled` -- skip it entirely; an
    already-received coin is exactly this path's input. And
    `offering_writes.offers_holding` filters on claims and listing status
    only. It is **format-blind**, so an `active` auction lot listing comes
    back through the *derived* half and is ended like any other.

    What that costs, all three measured against this branch rather than
    imagined:

    - **Before the auction closes**, one missing coin ends the whole lot
      listing, dissolves its `sales_lot` and releases **every other member**
      back to `held`, while the `auction_lot` row keeps its lot number.
      `app.auctions.consign` then reads `offering_writes.offered_items` ->
      `[]` and silently skips the lot: the auction reports itself consigned
      and those coins never left.
    - **After it closes, consigned**, `settle`'s `_return_from_consignment`
      reads the same empty list, returns nothing, and then clears
      `auction.consigned_on` on the claim that everything came home. The
      lot's healthy members are left filed at the auction house with nothing
      linking them to the auction -- the stranding rulings R9 and R13 exist
      to prevent, through a third door.
    - **After it closes, sold**, `sales_writes.record_sale_lines` refuses
      that lot "not on offer" and the auction cannot be settled until the
      lot is re-entered as withdrawn.

    **The trade this makes, deliberately:** a coin in an auction that goes
    missing is now a **two-step** operation -- take the lot out of the
    auction (remove it before the auction closes; settle it as withdrawn, or
    cancel, after), then record the loss -- which is the same trade ruling
    R25 already accepted for Record sale. The message says so, because the
    operator is the one who has to do the second step.

    **Keyed on the `auction_lot` row, not on `format` alone.** The Offer
    dialog offers a coin directly on eBay by auction, with no auction behind
    it; `offers_holding` returns only live listings, so a live
    auction-format listing with no `auction_lot` row is always such a direct
    offer, and it is ended here like any other (review of the final fix
    wave, Important #1).

    Placed beside `offering_writes.refuse_if_lot_unheld`, over the
    *authoritative* `offers_holding` read and under the locks, not at the top
    of the endpoint: the set is derived from claims and is only known here,
    and this is already the endpoint's "refuse before the first `end_offer`,
    all or nothing" point. The rows are held by then, so the refusal is
    deterministic rather than something to retry -- and the locks are taken
    either way, which is what keeps
    `test_settling_a_consigned_auction_races_a_coin_going_missing` a race
    test rather than a refusal test.
    """
    lots = [live for live in listings if live.format is ListingFormat.auction]
    if not lots:
        return
    auctions_by_listing: dict[int, int] = dict(
        db.execute(
            select(AuctionLot.listing_id, AuctionLot.auction_id).where(
                AuctionLot.listing_id.in_([live.id for live in lots])
            )
        )
        .tuples()
        .all()
    )
    if not auctions_by_listing:
        return
    named = ", ".join(
        f"listing #{listing_id} of auction #{auction_id}"
        for listing_id, auction_id in sorted(auctions_by_listing.items())
    )
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            f"Recording these items {outcome} would end an auction lot's offer: "
            f"{named}. Take the lot out of the auction first -- remove it, or "
            "once the auction has closed, settle it as withdrawn or cancel the "
            f"auction -- then record the items {outcome}."
        ),
    )


@router.post("/receive")
def receive_items(
    payload: ReceiveRequest, db: DbSession, admin: AdminUser
) -> dict[str, int | str]:
    """Record what arrived, for one item or a whole box of them.

    All or nothing, in one transaction, the same as `POST /bulk` and for the
    same reason: a partial receipt across twenty coins leaves a state nobody
    can describe, and "which of the twenty applied?" is not a question the UI
    should have to answer. Every id is resolved and every code checked before
    anything is written.

    An outcome other than `received` (missing, returned, canceled) says a
    coin will not be delivered as promised, which a buyer looking at it needs
    to know about first: `app.sale_state.guard` refuses the request until
    `acknowledge_for_sale` says the caller has seen that, the same warning
    used elsewhere an item that is for sale is about to change underneath a
    buyer. Once acknowledged, any listing still offering the item is ended
    through `offering_writes.end_offer` -- a coin that cannot be delivered
    must not stay offered. `received` itself needs none of this: an item that
    has not yet been received cannot be offered in the first place.
    """
    if payload.outcome not in RECEIVE_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown outcome {payload.outcome!r}. "
            f"Known: {sorted(RECEIVE_OUTCOMES)}",
        )

    # A future arrived_on is a data-entry error, not a fact: the thing has
    # not physically turned up yet. Checked here rather than as a lower
    # bound against the purchase order's ordered_on, which is frequently
    # null and would refuse legitimate data -- today's date is the only
    # bound with a real justification.
    #
    # "Today" is inherently local, but this check has only UTC to compare
    # against. A caller's local calendar date can be a day ahead of UTC's
    # (anywhere east of it, into the evening) or a day behind (anywhere
    # west), so a bound of exactly `utc_today` would refuse a genuine,
    # same-day receipt for a large share of the world for several hours
    # every day. Widening the bound to `utc_today + 1 day` accepts every
    # timezone's honest "today" -- the max offset either side of UTC is a
    # day -- while still refusing anything two or more days out, which is
    # what an actual fat-fingered date looks like. The tradeoff: a typo that
    # is exactly one day ahead of the true date no longer gets caught here,
    # because it is indistinguishable from a legitimate ahead-of-UTC today.
    limit: date = datetime.now(UTC).date() + timedelta(days=1)
    if payload.arrived_on is not None and payload.arrived_on > limit:
        raise HTTPException(
            status_code=422,
            detail=f"arrived_on {payload.arrived_on.isoformat()} is too far "
            f"in the future. Latest accepted: {limit.isoformat()}.",
        )

    items = db.scalars(
        select(InventoryItem).where(InventoryItem.id.in_(payload.item_ids))
    ).all()
    missing = sorted(set(payload.item_ids) - {i.id for i in items})
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown item ids: {missing}")

    received_id = require_code(db, ItemStatus, "received", "status")
    already = [i for i in items if i.status_id == received_id]
    if already and payload.outcome == "received":
        # Naming the status and the arrival date, not just the code, is what
        # lets an operator tell a double-submitted form (same date, moments
        # apart) from the wrong row (a date that means nothing to them) --
        # "already received" alone answers neither question.
        arrival_rows = db.execute(
            select(
                ItemStatusHistory.inventory_item_id,
                func.max(ItemStatusHistory.arrived_on),
            )
            .where(
                ItemStatusHistory.inventory_item_id.in_([i.id for i in already]),
                ItemStatusHistory.to_status_id == received_id,
            )
            .group_by(ItemStatusHistory.inventory_item_id)
        ).all()
        arrivals: dict[int, date | None] = {row[0]: row[1] for row in arrival_rows}

        def _arrival_label(item_id: int) -> str:
            arrived = arrivals.get(item_id)
            return arrived.isoformat() if arrived is not None else "unknown date"

        detail_items = sorted(
            f"{i.item_code} (received {_arrival_label(i.id)})" for i in already
        )
        raise HTTPException(
            status_code=409,
            detail=f"Already received: {detail_items}. "
            "Use PATCH to correct a receipt rather than repeating it.",
        )

    # An item that has not been received cannot be offered
    # (`offering_writes.offer` refuses it) and an order can only hold a
    # listing, so a plain receipt has nothing to warn about. The other three
    # outcomes say a coin will not be delivered, and that is exactly what a
    # buyer needs protecting from.
    ends_offer = payload.outcome != "received"
    if ends_offer:
        sale_state.guard(db, list(items), acknowledged=payload.acknowledge_for_sale)

    if payload.storage_location_id is not None:
        exists = db.get(StorageLocation, payload.storage_location_id)
        if exists is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown storage_location_id: {payload.storage_location_id}",
            )

    to_status = require_code(db, ItemStatus, payload.outcome, "status")

    # Every row this receipt will touch, taken **before the first write** and
    # in the canonical order, through the one function that owns it
    # (`offering_writes.lock_for_sale`: lot rows, then items, then listings).
    #
    # Load-bearing, and it was the last inversion left after the lock order
    # was given a single owner. `set_status` below writes `inventory_item`
    # rows -- taking their exclusive row locks at the flush that follows --
    # and only then did `end_offer` run, whose own pass waits on the *lot*
    # row. So receiving used to acquire items before lots, the inverse of
    # every other writer, and an administrator marking a lot's member
    # `missing` while a shopper checked that lot out could each hold what the
    # other waited for: `order_writes._lock_listings` takes the lot row as
    # its first statement and then waits on the member row this endpoint
    # holds. Taking the whole set here puts receiving in the same order as
    # everything else, and the `end_offer` calls below then re-lock rows this
    # transaction already holds.
    #
    # Measured, and the abort is not the only loss the inversion permits --
    # nor the one observed. With the pass removed the race test loses eight
    # of eight to a `StaleDataError`, because the receipt's unlocked UPDATE
    # of the member row queues behind the checkout and then writes through a
    # version that has moved. Both are a 500 for an operator, and locking
    # and re-reading first removes both -- the second is the same false
    # conflict `offering_writes._lock_items`' docstring describes.
    #
    # `including_paused=True` so the item set derived here is exactly the
    # `_affected_items` set `end_offer` will ask for, rather than a narrower
    # one that would leave it taking an item row for the first time while
    # this transaction already held listings.
    #
    # The `offers_holding` read here **chooses what to lock and nothing
    # else**; the authoritative one is the second call, below, after the
    # writes and under these locks. That split is the same read-lock-re-read
    # discipline `lock_for_sale` applies to a listing's member set, and it is
    # needed here for the same reason: this read is unlocked, so between it
    # and the locks a concurrent checkout can end one of these offers, or a
    # concurrent `offer` can create a new one. Acting on this list would then
    # end a listing somebody else had already ended -- which for a lot
    # listing rewrites `sales_lot.status` from `sold` to `dissolved`, two
    # histories the spec says never collapse into one. The race test caught
    # exactly that.
    #
    # Re-reading is enough rather than a loop **for the listing rows**:
    # `_lock_listing_rows` takes every live offer holding one of these items
    # in the same statement as the ones named here, evaluated *after* the item
    # rows are held, and a listing comes to hold an item only through a claim
    # whose writer holds the item row first. So every listing row the second
    # read can return is one this transaction already holds.
    #
    # That argument is silent about **lot** rows, and it has to be: a lot
    # listing reached only through the derived half has its listing row locked
    # and its lot row taken only if `lock_for_sale`'s own `offers_holding`
    # read saw it. A lot offered in the window between that read and the item
    # lock would not be. Ending such a listing takes its lot row *late* --
    # `end_offer` -> `lock_for_sale` -> `_lock_lots` -- while this
    # transaction already holds items and listings, which is the original
    # inversion one level down, against a checkout holding that lot row and
    # waiting on a member. `refuse_if_lot_unheld` below is the check, and
    # `lot_ids` is why this keeps the result rather than discarding it.
    locked = None
    if ends_offer:
        locked = offering_writes.lock_for_sale(
            db,
            listing_ids=[
                live.id
                for live in offering_writes.offers_holding(
                    db, [item.id for item in items]
                )
            ],
            item_ids=[item.id for item in items],
            including_paused=True,
        )

    for item in items:
        set_status(
            db,
            item,
            to_status,
            user_id=admin.id,
            note=payload.note,
            arrived_on=payload.arrived_on if payload.outcome == "received" else None,
        )
        if payload.outcome == "received" and payload.storage_location_id is not None:
            set_location(
                db,
                item,
                payload.storage_location_id,
                user_id=admin.id,
                note=payload.note,
            )

    # Load-bearing, not tidiness. `end_offer` below re-reads exactly these
    # rows -- `offering_writes.lock_for_sale` locks them with
    # `populate_existing=True`, which overwrites whatever the session holds
    # and clears the attribute's dirty flag. Production's `SessionLocal` sets
    # `autoflush=False`, so without this the status assigned just above is
    # still pending when that re-read lands, is silently thrown away, and the
    # commit writes the history row and the ended listing while leaving
    # `inventory_item.status_id` untouched -- a history asserting a
    # transition the item never made.
    db.flush()

    # A coin that cannot be delivered must not stay offered. Ended through
    # `offering_writes`, the only writer of listing status and claims -- never
    # by assigning `listing.status` here.
    #
    # `offers_holding` rather than a query on `inventory_item_id`: a piece of
    # a lot is offered by the *lot's* listing and has no listing of its own,
    # so the direct query left the lot on sale after one of its pieces went
    # missing.
    #
    # This is the *authoritative* read of that set, not the one above, which
    # only chose what to lock -- see there. Every listing row it can return is
    # already held, so re-reading costs a SELECT and buys the guarantee that
    # nothing here ends an offer another transaction has already ended.
    #
    # Checked before the first `end_offer`, not per listing: this is all or
    # nothing like the rest of the receipt, and a refusal must not leave half
    # the offers ended. Nothing is written when it fires -- the router commits
    # once, at the end.
    if ends_offer:
        # Asserted, not tolerated. `locked` is set by the pass above under
        # exactly this condition, so `is not None` is true today -- and an
        # `if` here instead would mean that the day it stopped being true,
        # this endpoint would **silently skip ending the offers of a coin it
        # had just marked `missing`**, leaving it on sale with no error
        # anywhere. That is the worst outcome this endpoint has, and it is
        # not one to guard against by doing nothing. The assert is also what
        # gives `locked.lot_ids` below a non-optional type.
        assert locked is not None
        live_offers = list(
            offering_writes.offers_holding(db, [item.id for item in items])
        )
        offering_writes.refuse_if_lot_unheld(live_offers, locked.lot_ids)
        # An auction lot is ended through its auction, never here -- see
        # `_refuse_auction_lots`. Beside `refuse_if_lot_unheld` and for the
        # same reason: both are "this set cannot be ended by this endpoint",
        # asked once over the whole set before the first `end_offer`.
        _refuse_auction_lots(db, live_offers, payload.outcome)
        for live in live_offers:
            offering_writes.end_offer(db, live, note=f"item recorded {payload.outcome}")

    db.commit()
    # The outcome as well as the count. This endpoint records `missing`,
    # `returned` and `canceled` too, and answering a cancellation with
    # `{"received": 12}` describes the opposite of what happened.
    return {"outcome": payload.outcome, "items": len(items)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemCreate, db: DbSession, admin: AdminUser) -> ItemDetailOut:
    """Add an item to an existing purchase: a coin, banknote or other object.

    Mirrors `SchemaLoader.load()`: every classifier code is resolved first,
    so a typo in the last field never leaves the earlier ones already
    written; only then is the item built, flushed, given exactly one detail
    row (`CurrencyDetail` for `currency`, `CoinDetail` otherwise), certified
    if a certificate number was given, and handed its opening status-history
    row -- all inside one transaction.

    Returns the same shape `GET /api/inventory/{id}` does, built by calling
    that route's own function, so a freshly entered item is described no
    differently from one read back later.
    """
    order = db.get(PurchaseOrder, payload.purchase_order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown purchase_order_id: {payload.purchase_order_id}",
        )

    # A year given alone is a single year; an explicit end before the start
    # is refused rather than silently swapped. There is no existing item to
    # name in the message yet, so the title given stands in for it.
    given_years = {
        field: getattr(payload, field)
        for field in YEAR_FIELDS
        if getattr(payload, field) is not None
    }
    years = resolve_years((None, None), given_years)
    if years is not None:
        refuse_backwards(years, payload.source_title)
    else:
        years = (None, None)

    # Every code resolved before anything is written -- the first unknown one
    # is the 422 the caller sees, and nothing has been added to the session
    # yet for any of them to leave behind.
    item_kind_id = require_code(db, ItemKind, payload.item_kind, "item_kind")
    country_id = code_to_id(db, Country, payload.country, "country")
    denomination_id = code_to_id(db, Denomination, payload.denomination, "denomination")
    if payload.denomination:
        # No item row exists yet on this path, so the payload's own
        # `item_kind` is compared against the denomination's kind directly,
        # rather than through `_refuse_mismatched_denomination`.
        denomination_kind = db.scalar(
            select(Denomination.kind).where(Denomination.code == payload.denomination)
        )
        is_note_denomination = denomination_kind == DenominationKind.note
        if (payload.item_kind == "currency") != is_note_denomination:
            side = "banknotes" if is_note_denomination else "coins"
            raise HTTPException(
                status_code=422,
                detail=(
                    f"denomination {payload.denomination} belongs to {side}: "
                    f"item_kind {payload.item_kind!r} cannot take it."
                ),
            )
    grade_code, strike_code = grades.split_fields(payload.grade, payload.strike_type)
    grade_id = code_to_id(db, Grade, grade_code, "grade")
    strike_type_id = code_to_id(db, StrikeType, strike_code, "strike_type")
    grade_designation_id = code_to_id(
        db, GradeDesignation, payload.grade_designation, "grade_designation"
    )
    grading_service_id = code_to_id(
        db, GradingService, payload.grading_service, "grading_service"
    )
    metal_id = code_to_id(db, Metal, payload.metal, "metal")
    series_id = code_to_id(db, Series, payload.series, "series")
    bullion_form_id = code_to_id(db, BullionForm, payload.bullion_form, "bullion_form")
    storage_form_id = require_code(
        db, StorageForm, payload.storage_form or "single", "storage_form"
    )
    authenticity_id = require_code(
        db, Authenticity, payload.authenticity or "unverified", "authenticity"
    )
    status_id = require_code(db, ItemStatus, payload.status, "status")

    is_currency = payload.item_kind == "currency"
    mint_id = None if is_currency else code_to_id(db, Mint, payload.mint, "mint")
    note_type_id = (
        code_to_id(db, NoteType, payload.note_type, "note_type")
        if is_currency
        else None
    )
    seal_color_id = (
        code_to_id(db, SealColor, payload.seal_color, "seal_color")
        if is_currency
        else None
    )
    fed_district_id = (
        code_to_id(db, FedDistrict, payload.fed_district, "fed_district")
        if is_currency
        else None
    )
    signature_combination_id = (
        code_to_id(
            db,
            SignatureCombination,
            payload.signature_combination,
            "signature_combination",
        )
        if is_currency
        else None
    )
    unknown_suggestions = sorted(set(payload.suggested) - SUGGESTABLE_FIELDS)
    if unknown_suggestions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Not a suggestable field: {unknown_suggestions}. "
            f"Available: {sorted(SUGGESTABLE_FIELDS)}",
        )

    tax_kwargs: dict[str, object] = {}
    if payload.tax_rate is not None:
        tax_kwargs["tax_rate"] = payload.tax_rate
    if payload.tax_includes_shipping is not None:
        tax_kwargs["tax_includes_shipping"] = payload.tax_includes_shipping

    item = InventoryItem(
        purchase_order_id=order.id,
        item_kind_id=item_kind_id,
        source_title=payload.source_title,
        description=payload.description,
        year_start=years[0],
        year_end=years[1],
        piece_count=payload.piece_count,
        item_cost=payload.item_cost,
        shipping_cost=payload.shipping_cost,
        country_id=country_id,
        denomination_id=denomination_id,
        strike_type_id=strike_type_id,
        grade_id=grade_id,
        grade_designation_id=grade_designation_id,
        grading_service_id=grading_service_id,
        metal_id=metal_id,
        series_id=series_id,
        bullion_form_id=bullion_form_id,
        storage_form_id=storage_form_id,
        authenticity_id=authenticity_id,
        status_id=status_id,
        disposition_id=require_code(db, Disposition, "held", "disposition"),
        valuation_basis_id=require_code(
            db, ValuationBasis, "numismatic", "valuation_basis"
        ),
        source=ProvenanceSource.manual,
        **tax_kwargs,
    )
    db.add(item)
    db.flush()

    if is_currency:
        db.add(
            CurrencyDetail(
                inventory_item_id=item.id,
                note_type_id=note_type_id,
                series_year=payload.series_year,
                series_letter=payload.series_letter,
                seal_color_id=seal_color_id,
                fed_district_id=fed_district_id,
                signature_combination_id=signature_combination_id,
                serial_number=payload.serial_number,
            )
        )
    else:
        db.add(
            CoinDetail(
                inventory_item_id=item.id, mint_id=mint_id, variety=payload.variety
            )
        )

    if payload.cert_number:
        db.add(
            ItemCertification(
                inventory_item_id=item.id,
                grading_service_id=grading_service_id,
                cert_number=payload.cert_number,
                raw=payload.cert_number,
            )
        )

    record_initial_status(db, item, user_id=admin.id, note="entered in the console")
    # Only a suggestion that was actually filled is a derived default.
    record_derived(
        db,
        item.id,
        [_column(f) for f in payload.suggested if getattr(payload, f) is not None],
        SUGGESTION,
    )
    # Whatever the form did not fill, the facts may: a coin's composition, a
    # note's signatures. Nothing the person sent is replaced.
    refresh_items(db, [item.id])
    db.commit()

    return get_item(item.id, db, admin)


@router.get("/{item_id}")
def get_item(item_id: int, db: DbSession, _admin: AdminUser) -> ItemDetailOut:
    """One item, with what its lot claimed and what has been confirmed.

    Both in one response because the edit form needs both on every field, and
    three round trips per coin is three per coin across the collection
    (7,656 items as of 2026-09-20).

    Carries every field EDITABLE_SCALARS and ITEM_CLASSIFIERS accept, not a
    hand-picked subset -- see test_the_detail_payload_covers_every_editable_field.
    A field the client can set but this endpoint never returns renders blank
    in the form regardless of what is stored, which is how a coin already
    holding MS65 shows an empty Grade box and gets overwritten with nothing.
    """
    return item_detail(db, _get_item(db, item_id))


def item_detail(db: Session, item: InventoryItem) -> ItemDetailOut:
    """The editor's whole view of one item; also what a sale snapshot copies."""
    parent_code: str | None = None
    claims: dict[str, object] = {}
    if item.parent_item_id is not None:
        parent = db.get(InventoryItem, item.parent_item_id)
        if parent is not None:
            parent_code = parent.item_code
            # Every inherited field, not only the ones that now differ. A
            # piece that still agrees with its lot is the important case, not
            # the boring one: it agrees *because* it inherited the seller's
            # claim and nobody has checked it yet. Reporting only the
            # differences would drop exactly the fields that need the
            # warning. What to do with an agreeing field is the form's call,
            # not this endpoint's -- it has `reviewed` alongside, so it can
            # tell "inherited and unchecked" from "confirmed" from
            # "overridden".
            #
            # A relationship may claim nothing at all -- a lot with no grade
            # recorded says nothing about grade -- and that is omitted rather
            # than sent as null, so the form can tell "the lot said nothing"
            # from "the lot said none of these apply".
            for name in LOT_CLAIM_FIELDS:
                value: object
                if name in ITEM_CLASSIFIERS:
                    value = _classifier_code(
                        db, ITEM_CLASSIFIERS[name], getattr(parent, f"{name}_id")
                    )
                else:
                    value = plain(getattr(parent, name))
                if value is not None:
                    claims[name] = value

    classifiers = {
        field: _classifier_code(db, model, getattr(item, f"{field}_id"))
        for field, model in ITEM_CLASSIFIERS.items()
    }
    note: dict[str, object] = {}
    if (detail := item.currency_detail) is not None:
        note = {
            field: _classifier_code(db, model, getattr(detail, f"{field}_id"))
            for field, model in NOTE_CLASSIFIERS.items()
        }
        note.update({field: getattr(detail, field) for field in NOTE_SCALARS})

    return ItemDetailOut(
        **{
            column: plain(getattr(item, column))
            for column in (
                "id",
                "item_code",
                "version",
                "source_title",
                "description",
                "year_start",
                "year_end",
                "fineness",
                "gross_weight_ozt",
                "fine_weight_ozt",
                "numismatic_value",
                "piece_count",
                "item_cost",
                "shipping_cost",
                "tax_rate",
                "tax_includes_shipping",
                "sales_tax",
                "total_cost",
                "parent_item_id",
                "split_at",
            )
        },
        **classifiers,
        **note,
        grade_display=grades.display_item(item),
        default_tax_rate=settings.sales_tax_rate,
        parent_item_code=parent_code,
        lot_claims=claims,
        reviewed=_reviewed_fields(db, item.id),
        derived=derived_fields(db, item.id),
        attributes=[
            ItemAttributeOut(**vars(held))
            for held in item_attributes.held_attributes(db, item.id)
        ],
        sale_state=[
            SaleUseOut(**vars(use))
            for use in sale_state.for_sale(db, [item.id]).get(item.id, [])
        ],
    )


def _share_of(item_id: int) -> ScalarSelect[Decimal]:
    """What this one item was credited with on a line, as a scalar subquery.

    A correlated subquery rather than a join, for the reason
    `sale_state.sold_this_item` uses one: joining `sales_order_item_share`
    would repeat a lot line once per member and report a single sale three
    times. At most one share row can match -- one per (line, item) pair --
    so this cannot multiply rows.

    Typed `ScalarSelect[Decimal]` after the column, not `Decimal | None`: a
    scalar subquery that matches nothing still yields SQL NULL, which is why
    `ItemSaleOut.share_amount` is optional. The annotation describes the
    column being selected, as SQLAlchemy's own stubs do.
    """
    return (
        select(SalesOrderItemShare.amount)
        .where(
            SalesOrderItemShare.sales_order_item_id == SalesOrderItem.id,
            SalesOrderItemShare.inventory_item_id == item_id,
        )
        .scalar_subquery()
    )


@router.get("/{item_id}/sales")
def get_item_sales(item_id: int, db: DbSession, _admin: AdminUser) -> list[ItemSaleOut]:
    """Every sale of this item, newest first, each with the item as sold.

    Sales **inside a lot** included. Until lots could be sold this asked only
    `listing.inventory_item_id`, which is NULL on a lot listing, so a coin
    sold as part of a group showed no sale history at all -- while this
    docstring promised every sale of it. The lot's own listing is reached
    through the shares the sale wrote, one per member.

    A subquery rather than a join to `sales_order_item_share`, so a line
    stays one row however many members its lot had: joining would repeat the
    line once per share and the response would list the same sale three
    times. The ordering is unchanged.

    **`quantity` and `unit_price` belong to the line, not to the coin**, and
    for a lot line they are the whole group's -- one lot at 1,000.00 against
    a member that cost 200. Reaching a lot's sale without saying so would put
    the group's price beside one coin on the screen an owner uses to ask what
    happened to that coin, which is a worse answer than the empty list it
    replaced. So every row also carries `sales_lot_id`, which says the sale
    was a group sale, and `share_amount`, this coin's own cost-weighted share
    of it. The fields that were already here keep their meanings exactly, so
    an item sale reads as it always did.
    """
    item = _get_item(db, item_id)
    rows = db.execute(
        select(
            SalesOrderItem,
            SalesOrder,
            SalesOrderStatus.code,
            Customer.display_name,
            Listing.sales_lot_id,
            _share_of(item.id),
        )
        .join(Listing, Listing.id == SalesOrderItem.listing_id)
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.sales_order_id)
        .join(SalesOrderStatus, SalesOrderStatus.id == SalesOrder.sales_order_status_id)
        .join(Customer, Customer.id == SalesOrder.customer_id)
        .where(sale_state.sold_this_item(item.id))
        .order_by(SalesOrder.placed_at.desc(), SalesOrderItem.id.desc())
    ).tuples()
    return [
        ItemSaleOut(
            order_id=order.id,
            status=status_code,
            placed_at=order.placed_at,
            customer_name=customer_name,
            quantity=line.quantity,
            unit_price=line.unit_price,
            sales_lot_id=sales_lot_id,
            share_amount=share_amount,
            snapshot=line.item_snapshot,
            snapshot_at=line.snapshot_at,
        )
        for line, order, status_code, customer_name, sales_lot_id, share_amount in rows
    ]


#: Editable classifiers on an item, and where each code resolves.
#:
#: Wider than PIECE_CLASSIFIERS above, which covers only what a split may
#: override per piece. Everything here is a correction someone makes while
#: attributing an item in hand.
ITEM_CLASSIFIERS: dict[str, type] = {
    "item_kind": ItemKind,
    "country": Country,
    "denomination": Denomination,
    "bullion_form": BullionForm,
    "strike_type": StrikeType,
    "grade": Grade,
    "grade_designation": GradeDesignation,
    "grading_service": GradingService,
    "metal": Metal,
    "series": Series,
    "storage_form": StorageForm,
    "authenticity": Authenticity,
    "status": ItemStatus,
    "disposition": Disposition,
}

#: The subset of ITEM_CLASSIFIERS whose column is NOT NULL.
#:
#: `code_to_id` returns None for a null or empty code, and setting a NOT NULL
#: foreign key to None is an IntegrityError from the database -- an unhandled
#: 500, not a message a caller can act on. Every classifier column that is
#: NOT NULL needs this guard, not only `item_kind`, which is why this names
#: the whole set rather than special-casing one field.
REQUIRED_CLASSIFIERS: frozenset[str] = frozenset(
    {"item_kind", "storage_form", "authenticity", "status", "disposition"}
)

#: Plain columns a client may set. Named identically on the wire and in the
#: database: this surface speaks the item's own vocabulary throughout, the
#: same names the Excel round trip uses, so no translation is needed and a
#: tuple is enough. Contrast the shop's vocabulary -- `ListingUpdate`
#: (`app.routers.offers`) speaks in `title` and `price`, because what the
#: shop calls a listing and what it is offered for are not the item's
#: `source_title` and `item_cost`.
EDITABLE_SCALARS: tuple[str, ...] = (
    "source_title",
    "description",
    "year_start",
    "year_end",
    "fineness",
    "gross_weight_ozt",
    "fine_weight_ozt",
    "piece_count",
    "item_cost",
    "shipping_cost",
    "tax_rate",
    "tax_includes_shipping",
)

#: EDITABLE_SCALARS refused by name when sent as null. Both columns are NOT
#: NULL, and an explicit null would otherwise reach the database as a
#: constraint violation -- an unhandled 500 rather than a message a caller can
#: act on. The same reason REQUIRED_CLASSIFIERS exists.
REQUIRED_SCALARS: frozenset[str] = frozenset({"tax_rate", "tax_includes_shipping"})

#: Classifiers on a note's currency detail, and where each code resolves.
NOTE_CLASSIFIERS: dict[str, type] = {
    "note_type": NoteType,
    "seal_color": SealColor,
    "fed_district": FedDistrict,
    "signature_combination": SignatureCombination,
}

#: Plain columns on a note's currency detail a client may set.
NOTE_SCALARS: tuple[str, ...] = ("series_year", "series_letter", "serial_number")

#: Columns `app.classifier_defaults` fills, and so may hold empty.
DEFAULTED_COLUMNS: frozenset[str] = frozenset(
    {
        "note_type_id",
        "seal_color_id",
        "signature_combination_id",
        "fed_district_id",
        "metal_id",
        "fineness",
        "gross_weight_ozt",
        "fine_weight_ozt",
    }
)

#: Fields the New item form may fill from `/api/defaults` and
#: report back as suggestions the person left alone.
SUGGESTABLE_FIELDS: frozenset[str] = frozenset(
    {"note_type", "seal_color", "fed_district", "signature_combination", "metal"}
)


def _split_grade(data: dict[str, Any]) -> None:
    """A compound grade in a change set, taken apart in place: MS65 -> 65.

    The strike type it implies is set too, unless the change names one.
    """
    if data.get("grade"):
        grade, strike = grades.split_fields(data["grade"], data.get("strike_type"))
        data["grade"] = grade
        if strike is not None:
            data["strike_type"] = strike


def _column(field: str) -> str:
    """The column an API field sets: `grade` -> `grade_id`, `fineness` itself."""
    if field in ITEM_CLASSIFIERS or field in NOTE_CLASSIFIERS:
        return f"{field}_id"
    return field


def _emptied(data: dict[str, Any]) -> list[str]:
    """The columns a request empties, which a pass must then leave empty.

    Only fields a pass could fill: emptying a description holds nothing.
    """
    return [
        _column(field)
        for field, value in data.items()
        if value is None and _column(field) in DEFAULTED_COLUMNS
    ]


def _note_changes(
    db: Session, data: dict[str, Any], held: CurrencyDetail | None = None
) -> dict[str, object]:
    """The currency-detail columns a request sets, with codes resolved.

    Resolved before anything is written, like every other code here, so an
    unknown seal colour is a 422 that leaves the item untouched. `held` is
    the one note being edited, when there is one: a retired value it already
    holds stays saveable (ruling S5). A bulk edit passes none.
    """
    changes: dict[str, object] = {}
    for field, model in NOTE_CLASSIFIERS.items():
        if field in data:
            keep = getattr(held, f"{field}_id") if held is not None else None
            changes[f"{field}_id"] = code_to_id(
                db, model, data[field], field, keep=keep
            )
    for field in NOTE_SCALARS:
        if field in data:
            value = data[field]
            if field == "series_letter" and isinstance(value, str):
                value = value.strip().upper() or None
            changes[field] = value
    return changes


def _apply_note_changes(items: list[InventoryItem], changes: dict[str, object]) -> None:
    """Set currency-detail columns, refusing items that are not notes.

    A serial number sent for a coin is refused by name rather than dropped:
    the same rule `ItemCreate` applies, for the same reason.
    """
    if not changes:
        return
    not_notes = sorted(i.item_code for i in items if i.currency_detail is None)
    if not_notes:
        fields = sorted(c.removesuffix("_id") for c in changes)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{', '.join(fields)}: only a banknote has these, and "
            f"{not_notes} are not banknotes. Nothing was changed.",
        )
    for item in items:
        for column, value in changes.items():
            setattr(item.currency_detail, column, value)


#: Fields a banknote does not have. `metal` is a coin-view column and filter
#: in `inventory_search` and has no meaning on paper, so writing one onto a
#: note would make a row no view can show and no search can find.
_COIN_ONLY_FIELDS = ("metal",)


def _effective_is_currency(
    data: dict[str, object], item: InventoryItem, currency_id: int | None
) -> bool:
    """Whether `item` will be a `currency` item once this edit is applied.

    The payload's own `item_kind` wins when the edit sets one; otherwise the
    item keeps the kind it already has. Both guards below need this -- the
    invariant each enforces is about the item's *resulting* state, not about
    which keys the caller happened to send, so a bare `item_kind` edit has to
    be checked against a field the request never touched.
    """
    if "item_kind" in data:
        return data["item_kind"] == "currency"
    return item.item_kind_id == currency_id


def _refuse_coin_only_fields(
    data: dict[str, object], items: Sequence[InventoryItem], db: Session
) -> None:
    """Raise a 422 if the edit leaves a banknote holding a coin-only field.

    Checked against the item's state as it would stand after the edit, not
    only what this request sends: a bare `item_kind` edit that would strand
    a metal the item already carries is refused exactly like sending that
    metal directly would be, and a combined `item_kind` + `metal` edit to a
    consistent pair -- the metal cleared, or the item staying a coin -- is
    allowed. The console does not offer the field for a note, but a stale
    tab or a script can still send one, or send only the kind change and
    leave the metal it already had.
    """
    if "item_kind" not in data and not any(f in data for f in _COIN_ONLY_FIELDS):
        return
    currency_id = db.scalar(select(ItemKind.id).where(ItemKind.code == "currency"))
    sent_fields = [field for field in _COIN_ONLY_FIELDS if data.get(field) is not None]
    sent_on: list[str] = []
    carried_on: list[str] = []
    for item in items:
        if not _effective_is_currency(data, item, currency_id):
            continue
        if sent_fields:
            sent_on.append(item.item_code)
        elif any(
            field not in data and getattr(item, f"{field}_id") is not None
            for field in _COIN_ONLY_FIELDS
        ):
            carried_on.append(item.item_code)
    if sent_on:
        # Plain 422, as the attributes guard below does: the named constant
        # is deprecated in Starlette and warns, and test output stays clean.
        raise HTTPException(
            status_code=422,
            detail=(
                f"{', '.join(sent_fields)} belongs to coins, not banknotes: "
                f"{', '.join(sorted(set(sent_on)))}. Nothing was changed."
            ),
        )
    if carried_on:
        target = f" to {data['item_kind']!r}" if "item_kind" in data else ""
        fields = " and ".join(_COIN_ONLY_FIELDS)
        raise HTTPException(
            status_code=422,
            detail=(
                f"{', '.join(sorted(set(carried_on)))} already carries a "
                f"{fields}; clear it before changing item_kind{target}. "
                "Nothing was changed."
            ),
        )


def _refuse_mismatched_denomination(
    data: dict[str, object], items: Sequence[InventoryItem], db: Session
) -> None:
    """Raise a 422 if the edit leaves an item's kind and denomination split.

    Checked against the item's state as it would stand after the edit, not
    only what this request sends: a bare `item_kind` edit that would strand
    an already-set denomination on the wrong side is refused exactly like
    sending that denomination directly would be, and a combined `item_kind`
    + `denomination` edit to a consistent pair is allowed. The same face
    value exists as a coin and as a note, which is what `denomination.kind`
    records.
    """
    if "item_kind" not in data and "denomination" not in data:
        return
    currency_id = db.scalar(select(ItemKind.id).where(ItemKind.code == "currency"))

    sending_denomination = "denomination" in data
    sent_kind: DenominationKind | None = None
    if sending_denomination:
        code = data["denomination"]
        if isinstance(code, str) and code:
            sent_kind = db.scalar(
                select(Denomination.kind).where(Denomination.code == code)
            )
            if sent_kind is None:  # an unknown code is code_to_id's 422 to raise
                return
        # A null or empty denomination clears it: `sent_kind` stays None,
        # which never conflicts with either item_kind below.

    # The kind of every denomination one of these items already carries, so a
    # bare `item_kind` edit can be checked against what it would strand
    # without a query per item.
    carried_kinds: dict[int, DenominationKind] = {}
    if not sending_denomination:
        carried_ids = {item.denomination_id for item in items if item.denomination_id}
        if carried_ids:
            carried = db.scalars(
                select(Denomination).where(Denomination.id.in_(carried_ids))
            ).all()
            carried_kinds = {d.id: d.kind for d in carried}

    sent_on: list[str] = []
    carried_on: list[str] = []
    for item in items:
        if sending_denomination:
            effective_kind = sent_kind
        elif item.denomination_id is not None:
            effective_kind = carried_kinds.get(item.denomination_id)
        else:
            effective_kind = None
        if effective_kind is None:
            continue
        is_note_denomination = effective_kind == DenominationKind.note
        if _effective_is_currency(data, item, currency_id) == is_note_denomination:
            continue
        (sent_on if sending_denomination else carried_on).append(item.item_code)

    if sent_on:
        side = "banknotes" if sent_kind == DenominationKind.note else "coins"
        raise HTTPException(
            status_code=422,
            detail=(
                f"denomination {data['denomination']} belongs to {side}: "
                f"{', '.join(sorted(set(sent_on)))} cannot take it. Nothing "
                "was changed."
            ),
        )
    if carried_on:
        target = f" to {data['item_kind']!r}" if "item_kind" in data else ""
        raise HTTPException(
            status_code=422,
            detail=(
                f"{', '.join(sorted(set(carried_on)))} already carries a "
                f"denomination for the other kind; clear it before changing "
                f"item_kind{target}. Nothing was changed."
            ),
        )


def _refuse_null_scalars(data: dict[str, object]) -> None:
    """Raise a 422 naming every required scalar that was sent as null."""
    nulled = sorted(f for f in REQUIRED_SCALARS if f in data and data[f] is None)
    if nulled:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{', '.join(nulled)} cannot be null.",
        )


@router.post("/bulk")
def bulk_edit(
    payload: BulkEditRequest, db: DbSession, admin: AdminUser
) -> dict[str, int]:
    """Set the same fields across many items, all or nothing.

    One transaction on purpose. A partial bulk edit across 50 coins leaves a
    state nobody can describe, and "which of the 50 applied?" is not a
    question the UI should ever have to answer -- so every id is resolved and
    every code checked before anything is written.

    Soft-deleted ids are not filtered out, deliberately and consistently with
    `PATCH /{item_id}`, which does not either -- correcting a field on a row
    that should never have existed is still a correction, and a caller who
    means to exclude it can filter its ids out before calling this.
    """
    data = payload.changes.model_dump(exclude_unset=True)
    data.pop("version", None)  # Meaningless across a set of rows.
    acknowledged = data.pop("acknowledge_for_sale", False)
    if "attributes" in data:
        # A whole set, per item: the same set across many items would wipe
        # whatever each carried that the others do not.
        raise HTTPException(
            status_code=422,
            detail="attributes are set one item at a time. Nothing was changed.",
        )
    _refuse_null_scalars(data)
    _split_grade(data)

    items = db.scalars(
        select(InventoryItem).where(InventoryItem.id.in_(payload.ids))
    ).all()
    found = {item.id for item in items}
    missing = sorted(set(payload.ids) - found)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No such item(s): {missing}. Nothing was changed.",
        )
    _refuse_coin_only_fields(data, list(items), db)
    _refuse_mismatched_denomination(data, list(items), db)
    if data and not acknowledged:
        sale_state.guard(db, list(items), acknowledged=False)

    # Every code resolved before anything is set, so a typo in the last field
    # does not leave the first three applied.
    resolved: dict[str, object] = {}
    # Status is the one classifier with a history table behind it. Pulled out
    # of `resolved` so it can go through set_status below instead of the
    # plain setattr loop -- the same reason PATCH /{item_id} special-cases it.
    status_id: int | None = None
    for field, model in ITEM_CLASSIFIERS.items():
        if field in data:
            value = data[field]
            value_id: int | None
            if field in REQUIRED_CLASSIFIERS:
                value_id = require_code(db, model, value, field)
            else:
                value_id = code_to_id(db, model, value, field)
            if field == "status":
                status_id = value_id
            else:
                resolved[f"{field}_id"] = value_id
    for field in EDITABLE_SCALARS:
        if field in data and field not in YEAR_FIELDS:
            resolved[field] = data[field]
    note_changes = _note_changes(db, data)

    # Per item, not once for the set: the same Year moves a single year's end
    # with it and leaves a range's end alone. Checked across every item before
    # any is changed, the same all-or-nothing as the codes above.
    years = {
        item.id: resolve_years((item.year_start, item.year_end), data) for item in items
    }
    broken = sorted(
        item.item_code
        for item in items
        if (pair := years[item.id]) is not None and backwards(pair)
    )
    if broken:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"That year would end a range before it starts on {broken}. "
            "Nothing was changed.",
        )

    _apply_note_changes(list(items), note_changes)
    for item in items:
        for column, value in resolved.items():
            setattr(item, column, value)
        if (pair := years[item.id]) is not None:
            item.year_start, item.year_end = pair
        if status_id is not None:
            set_status(db, item, status_id, user_id=admin.id)
    # What a person sets is theirs from now on: no pass refreshes it. What
    # follows from the new facts is refreshed now.
    forget(db, found, [_column(field) for field in data])
    hold(db, found, _emptied(data))
    refresh_items(db, found)

    db.commit()
    return {"updated": len(items)}


@router.patch("/{item_id}", response_model=InventoryItemOut)
def update_item(
    item_id: int,
    payload: InventoryItemUpdate,
    db: DbSession,
    admin: AdminUser,
) -> InventoryItem:
    """Correct an item. Send `version` to be told about conflicts.

    This is the editing path for the collection. `PATCH /api/listings/{id}`
    (`app.routers.offers`) needs a listing, and an item is owned long before
    it is offered and after it is sold -- most of this collection will never
    have a listing at all.
    """
    item = _get_item(db, item_id)

    # exclude_unset so an omitted field is left alone rather than nulled.
    data = payload.model_dump(exclude_unset=True)
    expected = data.pop("version", None)
    acknowledged = data.pop("acknowledge_for_sale", False)
    attributes = data.pop("attributes", None)
    if "attributes" in payload.model_fields_set and attributes is None:
        raise HTTPException(
            status_code=422,
            detail="attributes may not be null; send [] to clear them.",
        )
    _refuse_null_scalars(data)
    _refuse_coin_only_fields(data, [item], db)
    _refuse_mismatched_denomination(data, [item], db)
    _split_grade(data)

    # This is what catches the ordinary lost-update case: two staff, each with
    # a form loaded at a different time, and the second one saving over the
    # first. The item is re-fetched fresh at the top of every request, so
    # nothing later in this function -- including the database's own
    # version_id_col check -- ever sees a token from an earlier request; only
    # this comparison, against the value the caller actually sent, does.
    if expected is not None and expected != item.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{item.item_code} was changed by someone else (you have "
                f"version {expected}, current is {item.version}). Reload and "
                f"reapply your changes."
            ),
        )

    # A change to an item on offer, or in an order that has not shipped,
    # shows to a buyer at once: the caller must say it knows.
    if (data or attributes is not None) and not acknowledged:
        sale_state.guard(db, [item], acknowledged=False)

    # Before anything is set: a refused year leaves the item untouched.
    years = resolve_years((item.year_start, item.year_end), data)
    if years is not None:
        refuse_backwards(years, item.item_code)
    _apply_note_changes([item], _note_changes(db, data, item.currency_detail))

    for field, model in ITEM_CLASSIFIERS.items():
        if field in data:
            value = data[field]
            resolved: int | None
            # `keep`: a value this item already holds stays saveable after it
            # is retired (ruling S5); only a new use of one is refused.
            held = getattr(item, f"{field}_id")
            if field in REQUIRED_CLASSIFIERS:
                resolved = require_code(db, model, value, field, keep=held)
            else:
                resolved = code_to_id(db, model, value, field, keep=held)
            # Status is the one classifier with a history table behind it.
            # Going through set_status is what keeps that table true; a plain
            # setattr here is the bug this endpoint used to have. Status is
            # a REQUIRED_CLASSIFIER, so require_code already refused a null
            # code above and resolved is never None here.
            if field == "status":
                assert resolved is not None
                set_status(db, item, resolved, user_id=admin.id)
            else:
                setattr(item, f"{field}_id", resolved)

    for field in EDITABLE_SCALARS:
        if field in data and field not in YEAR_FIELDS:
            setattr(item, field, data[field])
    if years is not None:
        item.year_start, item.year_end = years
    # After the classifiers: a kind changed in this request decides which
    # attributes fit.
    if attributes is not None:
        try:
            changed = item_attributes.set_attributes(
                db, item, attributes, user_id=admin.id
            )
        except item_attributes.AttributeRefused as exc:
            db.rollback()
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if changed:
            # The links are another table. Touching the item is what moves
            # its version, so a form opened before this save gets a 409
            # rather than putting the old set back.
            item.updated_at = datetime.now(UTC)
    forget(db, [item.id], [_column(field) for field in data])
    hold(db, [item.id], _emptied(data))

    try:
        # Inside the try: the refresh flushes, and a version conflict found
        # there is the same 409 as one found at commit.
        refresh_items(db, [item.id])
        db.commit()
    except StaleDataError as exc:
        # A narrower race than the check above: another commit landed inside
        # this request's own read-to-commit window, after `item` was loaded
        # here but before this commit. The UPDATE carried `WHERE version =
        # ...`, matched no rows, and overwrote nothing.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{item.item_code} was changed while saving. Reload and retry.",
        ) from exc

    db.refresh(item)
    return item


@router.post("/{item_id}/split")
def split(
    item_id: int, payload: SplitRequest, db: DbSession, _admin: AdminUser
) -> SplitResultOut:
    """Break a lot into its pieces, dividing the cost between them.

    `equal` gives every piece the same share -- right for twenty identical
    rounds in a tube. `relative` divides in proportion to a value supplied per
    piece -- right for a mint set, where charging the cent and the half dollar
    the same cost basis would make one look like a disaster and the other a
    windfall, and both figures would be wrong.

    **`item_cost` and `shipping_cost` always reconcile to the penny**, by construction:
    the allocation floors every share and hands the remainder out one cent at a
    time to the parts cut hardest.

    **`total_cost` may differ by a cent or two, and the difference is
    reported.** `sales_tax` is a generated column, `round((item_cost + shipping_cost) *
    tax_rate, 2)`, computed per row. The sum of several rounded taxes is not
    always the rounded tax of the sum -- splitting $100 three ways at 6.35%
    gives 2.12 + 2.12 + 2.12 = 6.36 against the lot's 6.35. That penny is
    inherent to dividing a rounded figure, so it is surfaced rather than
    quietly absorbed into one piece.
    """
    parent = _get_item(db, item_id)
    # Listings only. An item in an order is refused by `split_item` below and
    # that refusal is not negotiable, so offering to acknowledge it would be a
    # confirmation that does not let the caller through.
    sale_state.guard(
        db,
        [parent],
        acknowledged=payload.acknowledge_for_sale,
        kinds={"listing"},
    )
    pieces = [_to_piece(db, spec) for spec in payload.pieces]

    try:
        children = split_item(db, parent, pieces, payload.mode)
    except SplitError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(exc)
        ) from exc

    db.commit()
    for child in children:
        db.refresh(child)
    db.refresh(parent)

    allocated_cost = sum((c.item_cost for c in children), Decimal("0.00"))
    allocated_shipping = sum((c.shipping_cost for c in children), Decimal("0.00"))
    allocated_total = sum((c.total_cost for c in children), Decimal("0.00"))

    return SplitResultOut(
        parent_item_code=parent.item_code,
        mode=payload.mode,
        parent_cost=parent.item_cost,
        allocated_cost=allocated_cost,
        parent_shipping=parent.shipping_cost,
        allocated_shipping=allocated_shipping,
        parent_total_cost=parent.total_cost,
        allocated_total_cost=allocated_total,
        total_cost_difference=allocated_total - parent.total_cost,
        pieces=[InventoryItemOut.model_validate(c) for c in children],
    )


#: Fields worth confirming by examination.
#:
#: A whitelist rather than "any column": a review of `created_at` means
#: nothing, and a typo that became a record would be a row nobody can ever
#: query for. Held here rather than as a check constraint because which
#: fields are worth confirming is a product decision that will change, and a
#: constraint would need a migration every time it did.
REVIEWABLE_FIELDS: frozenset[str] = frozenset(
    {
        "year_start",
        "year_end",
        "strike_type_id",
        "grade_id",
        "grade_designation_id",
        "grading_service_id",
        "denomination_id",
        "country_id",
        "metal_id",
        "series_id",
        "authenticity_id",
        "fineness",
        "fine_weight_ozt",
        "gross_weight_ozt",
        "piece_count",
        "mint_id",
        "variety",
        "serial_number",
        "series_year",
        "series_letter",
        "seal_color_id",
        "fed_district_id",
        "friedberg_id",
    }
)


def _reviewed_fields(db: Session, item_id: int) -> list[str]:
    return sorted(
        db.scalars(
            select(ItemFieldReview.field_name).where(
                ItemFieldReview.inventory_item_id == item_id
            )
        ).all()
    )


@router.get("/{item_id}/reviewed")
def get_item_review(item_id: int, db: DbSession, _admin: AdminUser) -> ItemReviewOut:
    """Which of this item's fields a person has confirmed."""
    item = _get_item(db, item_id)
    return ItemReviewOut(
        inventory_item_id=item.id, reviewed=_reviewed_fields(db, item.id)
    )


@router.post("/{item_id}/reviewed")
def set_item_review(
    item_id: int,
    payload: ReviewRequest,
    db: DbSession,
    admin: AdminUser,
) -> ItemReviewOut:
    """Record that a person has confirmed these fields by examination.

    Idempotent: confirming a field twice is the same fact, not an error.
    Looking at a coin again and agreeing with yourself should not be a 409.
    """
    item = _get_item(db, item_id)

    unknown = sorted(set(payload.fields) - REVIEWABLE_FIELDS)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Not reviewable: {unknown}. Available: {sorted(REVIEWABLE_FIELDS)}",
        )

    existing = set(_reviewed_fields(db, item.id))
    wanted = set(payload.fields)

    if payload.replace:
        for gone in existing - wanted:
            db.execute(
                delete(ItemFieldReview).where(
                    ItemFieldReview.inventory_item_id == item.id,
                    ItemFieldReview.field_name == gone,
                )
            )

    for name in wanted - existing:
        db.add(
            ItemFieldReview(
                inventory_item_id=item.id,
                field_name=name,
                reviewed_by_id=admin.id,
            )
        )

    try:
        db.commit()
    except IntegrityError:
        # Another request recorded the same confirmation between this one's
        # read and its commit. The fact the caller asked for is now true, so
        # this is success, not a conflict -- the same reasoning that makes a
        # sequential repeat a no-op rather than a 409.
        db.rollback()

    return ItemReviewOut(
        inventory_item_id=item.id, reviewed=_reviewed_fields(db, item.id)
    )


def _item_errors(db: Session, item_id: int) -> list[ItemErrorOut]:
    """Every error recorded against an item, as the API returns them."""
    rows = db.scalars(
        select(ItemError)
        .where(ItemError.inventory_item_id == item_id)
        .order_by(ItemError.error_type_id)
    ).all()
    return [
        ItemErrorOut(
            error_type=_classifier_code(db, ErrorType, row.error_type_id) or "",
            details=row.details,
            source=row.source.value,
            noted_by_id=row.noted_by_id,
            noted_at=row.noted_at,
        )
        for row in rows
    ]


@router.get("/{item_id}/errors")
def get_item_errors(item_id: int, db: DbSession, _admin: AdminUser) -> ItemErrorsOut:
    """Every mint or printing error recorded against this item."""
    item = _get_item(db, item_id)
    return ItemErrorsOut(inventory_item_id=item.id, errors=_item_errors(db, item.id))


@router.put("/{item_id}/errors")
def set_item_errors(
    item_id: int, payload: ItemErrorsRequest, db: DbSession, admin: AdminUser
) -> ItemErrorsOut:
    """Replace the whole set of errors recorded against this item.

    A replace, not an add/remove pair: miscut and overprint commonly appear
    on the same bill, a caller editing the set already has the current one
    from `GET`, and sending back the whole intended set is simpler to reason
    about than two calls that could disagree with each other mid-flight.
    """
    item = _get_item(db, item_id)
    sale_state.guard(db, [item], acknowledged=payload.acknowledge_for_sale)

    db.execute(delete(ItemError).where(ItemError.inventory_item_id == item.id))
    for entry in payload.errors:
        db.add(
            ItemError(
                inventory_item_id=item.id,
                error_type_id=require_code(
                    db, ErrorType, entry.error_type, "error_type"
                ),
                details=entry.details,
                source=ProvenanceSource.manual,
                noted_by_id=admin.id,
            )
        )
    db.commit()

    return ItemErrorsOut(inventory_item_id=item.id, errors=_item_errors(db, item.id))


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: DbSession, _admin: AdminUser) -> None:
    """Soft delete: this row should never have existed.

    Guarded three times, because every failure is silent. A lot with pieces
    holds the cost basis they were allocated from, and deleting it would
    leave four coins descended from nothing. An item that has ever been
    offered is referenced by the offer -- and through it by any order --
    which would then point at a row the reports exclude. An item in a sales
    lot is a coin the group still names, and deleting it would leave the lot
    offering something no view can find.

    The offer guard is permanent, not a "do this first": any offer refuses,
    ended ones included, and nothing removes a listing row (the catalogue's
    `DELETE /api/catalog/{listing_id}` was retired in phase 2, and
    `offer_claim` references the row `ON DELETE RESTRICT` anyway). That is the
    intended rule -- once a coin has been offered, the offer is part of the
    sales history -- so the message says so rather than naming a step that
    cannot clear it.

    It asks `sale_state.ever_offered`, not a query of its own. The old query
    was `Listing.inventory_item_id == item.id`, which a lot listing -- whose
    `inventory_item_id` is null -- can never match, so a coin offered only
    inside a lot was deleted silently. Routing through the shared predicate
    is what stops that happening again for the next shape of offer: the claim
    half it reads is written one row per member.

    The lot guard is the clearable one, and deliberately separate -- but
    `lot_holding` matches *any* lot with an open membership, `offered`
    included, not only `assembling`. A member of a lot that is currently
    offered therefore matches both guards, and the order below is
    load-bearing: the permanent guard has to run first, or that coin would be
    told "take it out of the lot first" -- a remedy `remove_member` refuses
    for exactly that lot state (`lot_writes._refuse_unless_assembling`),
    naming a step that cannot clear it. `tests/test_inventory_delete.py`'s
    `test_an_offered_lot_s_member_cannot_be_deleted` pins this order; swap
    the two blocks and it goes red while its
    `test_an_item_in_an_assembling_lot_cannot_be_deleted` -- whose lot never
    matches the permanent guard -- stays green.

    Only an `assembling` lot reaches the clearable message in practice: a
    coin whose lot has been offered, sold or dissolved is caught by the
    permanent guard above first, and `remove_member` itself refuses every
    lot state but `assembling`.

    **Reachable from the lot panel, not only from search.** After its last
    child is detached a parent still has `split_at` set, so it is invisible in
    every view and not deleted -- a row that exists and cannot be found.
    Search is exactly where it is not.
    """
    item = _get_item(db, item_id)

    if item.deleted_at is not None:
        return  # Already gone. Deleting twice is not an error.

    pieces = db.scalar(
        select(func.count())
        .select_from(InventoryItem)
        .where(InventoryItem.parent_item_id == item.id)
    )
    if pieces:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{item.item_code} has {pieces} piece(s) split from it and "
                f"cannot be deleted. Detach them first."
            ),
        )

    # Order is load-bearing: `lot_holding` below also matches an `offered`
    # lot's member, and its message names a remedy (`remove_member`) that
    # refuses every lot state but `assembling`. Asking the permanent guard
    # first is what keeps that member from being told a step that cannot
    # clear it. See the docstring above and
    # `test_an_offered_lot_s_member_cannot_be_deleted`.
    if item.id in sale_state.ever_offered(db, [item.id]):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{item.item_code} has been offered and cannot be deleted. "
                f"An item that has ever been offered -- on its own or inside "
                f"a sales lot -- stays in the record permanently: ending the "
                f"listing takes it off sale but does not remove it, because "
                f"the offer is part of the business's sales history."
            ),
        )

    in_lot = lot_writes.lot_holding(db, item.id)
    if in_lot is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{item.item_code} is in sales lot #{in_lot.id} "
                f"({in_lot.title}) and cannot be deleted. Take it out of the "
                f"lot first."
            ),
        )

    item.deleted_at = datetime.now(UTC)
    db.commit()


@router.delete("/{item_id}/parent", response_model=InventoryItemOut)
def detach_item(item_id: int, db: DbSession, _admin: AdminUser) -> InventoryItem:
    """Set an item's `parent_item_id` back to null.

    An item with no parent is complete, not orphaned: as of 2026-09-20 all
    7,656 items have none, because nothing has been split yet. So this moves
    nothing and repairs nothing -- the piece keeps the cost it was allocated,
    and simply stops recording where it came from.

    Idempotent, because the end state is exactly what was asked for.
    """
    item = _get_item(db, item_id)
    item.parent_item_id = None
    db.commit()
    db.refresh(item)
    return item
