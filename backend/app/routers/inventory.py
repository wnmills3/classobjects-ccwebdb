"""Item-level operations, on what you own rather than on what is for sale.

Separate from `/catalog`, which is listing-centric. An item exists before it is
listed and after it is sold, and operations like splitting a lot apply to the
object, not to the offer.

Staff-only throughout: everything here exposes cost basis.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.sql.selectable import ScalarSelect

from .. import (
    field_changes,
    grades,
    item_attributes,
    item_kinds,
    lot_writes,
    offering_writes,
    plates,
    sale_state,
    serial_patterns,
)
from ..auction_holding import auction_ids_by_listing
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
from ..item_descriptions import FANCY_SERIAL, Features, suggested_description
from ..item_history import timeline
from ..lifecycle_writes import record_initial_status, set_location, set_status
from ..models import (
    AppliesTo,
    Authenticity,
    BullionForm,
    CoinDetail,
    Composition,
    Country,
    CurrencyDetail,
    Customer,
    Denomination,
    DenominationKind,
    Disposition,
    ErrorType,
    FedDistrict,
    FriedbergNumber,
    Grade,
    GradeDesignation,
    GradingService,
    InventoryItem,
    ItemAttribute,
    ItemCertification,
    ItemError,
    ItemFieldReview,
    ItemKind,
    ItemStatus,
    ItemStatusHistory,
    Listing,
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
    SetForm,
    SignatureCombination,
    StorageForm,
    StorageLocation,
    StrikeType,
    ValuationBasis,
    Vendor,
)
from ..models.base import ReferenceMixin
from ..references import code_to_id, require_code
from ..schemas import (
    RECEIVE_OUTCOMES,
    BulkEditRequest,
    FieldChangeOut,
    InventoryItemOut,
    InventoryItemUpdate,
    InventoryPageOut,
    ItemAttributeOut,
    ItemCreate,
    ItemDetailOut,
    ItemDraftIn,
    ItemErrorOut,
    ItemErrorsOut,
    ItemErrorsRequest,
    ItemHistoryEventOut,
    ItemReviewOut,
    ItemSaleOut,
    ReceiveRequest,
    ReviewRequest,
    SaleUseOut,
    SplitPieceIn,
    SplitRequest,
    SplitResultOut,
    SuggestedDescriptionOut,
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
    if spec.description is not None:
        overrides["description"] = spec.description

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
    series letter, a seal color and its own printed serial. Sharing one grid
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown issue {exc.args[0]!r} for {view}. Available: "
            f"{sorted(spec.issues)}",
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown filter {exc.args[0]!r} for {view}. Available: "
            f"{sorted(spec.filters)}",
        ) from exc
    except ValueError as exc:
        # Two different failures reach here: an unsortable column, and an
        # unrecognised `deleted` mode. Appending the sortable list to both
        # sends the wrong person looking in the wrong place.
        hint = f" Sortable: {sorted(spec.sortable)}" if "sort" in str(exc) else ""
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"{exc}{hint}"
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
    auctions_by_listing = auction_ids_by_listing(db, listings)
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


class FieldConflicts(Exception):
    """A save refused because someone else changed a field it changes. 409.

    Carries each field with the value the edit began from (`was`), the value
    stored now (`theirs`) and the one being saved (`yours`), so the editor
    can show both and let the person choose. Rendered by `main.py` as
    `{detail, conflicts}` -- the same sentence-plus-list shape the offers
    refusal uses.
    """

    def __init__(self, detail: str, conflicts: list[dict[str, Any]]) -> None:
        """Keep the sentence and the per-field list for the handler."""
        super().__init__(detail)
        self.detail = detail
        self.conflicts = conflicts


def field_values(db: Session, item: InventoryItem) -> dict[str, Any]:
    """The item's fields as the editor holds them: `item_detail`, JSON-shaped.

    Attributes as a list of codes, the form the editor sends them in. What the
    field-by-field merge compares against, and what the change log records.
    """
    values = item_detail(db, item).model_dump(mode="json")
    values["attributes"] = [held["code"] for held in values.get("attributes", [])]
    return values


def _sent_values(
    db: Session, item: InventoryItem, fields: Iterable[str]
) -> dict[str, Any]:
    """Just these fields of an item, in `field_values`' terms, read directly.

    For the bulk edit's change log: `field_values` builds the editor's whole
    view of an item (sale state, attributes, reviews...), which is several
    queries per item, and a bulk edit has no limit on how many items it
    touches. Only the fields a bulk edit can set are needed, so only those are
    read.
    """
    detail = item.currency_detail
    values: dict[str, Any] = {}
    for field in fields:
        if field in ITEM_CLASSIFIERS:
            fk = getattr(item, f"{field}_id")
            values[field] = _classifier_code(db, ITEM_CLASSIFIERS[field], fk)
        elif field in NOTE_CLASSIFIERS:
            fk = getattr(detail, f"{field}_id") if detail else None
            values[field] = _classifier_code(db, NOTE_CLASSIFIERS[field], fk)
        elif field in NOTE_SCALARS:
            values[field] = plain(getattr(detail, field)) if detail else None
        else:
            values[field] = plain(getattr(item, field, None))
    return values


def _refuse_field_conflicts(
    db: Session,
    item: InventoryItem,
    current: dict[str, Any],
    sent: dict[str, Any],
    base: dict[str, Any],
) -> None:
    """Refuse a save only where someone else changed a field it changes.

    `base` must hold every field sent: a field without one would be saved
    unchecked, the silent overwrite this exists to prevent (422). A field
    conflicts when its stored value (`current`, from `field_values`) differs
    from where the edit began and from the value being saved -- two people
    making the same change agree. Each conflict names who made the other
    change and when, from the change log, where it knows.
    """
    unbased = sorted(field for field in sent if field not in base)
    if unbased:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"base must hold every field sent; missing: {', '.join(unbased)}",
        )
    clashing = [
        field
        for field, yours in sent.items()
        if not field_changes.same_value(current.get(field), base[field])
        and not field_changes.same_value(current.get(field), yours)
    ]
    if not clashing:
        return
    who = field_changes.latest(db, item.id)
    conflicts = [
        {
            "field": field,
            "was": base[field],
            "theirs": current.get(field),
            "yours": sent[field],
            "changed_by": who[field].by if field in who else None,
            "changed_at": who[field].at.isoformat() if field in who else None,
        }
        for field in clashing
    ]
    names = ", ".join(conflict["field"] for conflict in conflicts)
    raise FieldConflicts(
        f"{item.item_code} was changed by someone else since you opened it, "
        f"in the same field{'s' if len(conflicts) > 1 else ''} you changed: "
        f"{names}. Choose which value to keep.",
        conflicts,
    )


def _sale_standing_change(
    db: Session, item: InventoryItem, data: dict[str, Any]
) -> str | None:
    """The new status or disposition code, when an edit changes one of an offered item.

    None when neither is sent, when what is sent is what the item already
    holds, or when nothing offers the item -- an edit that leaves an item's
    standing alone, or an item not on sale, ends nothing. Status wins when
    both change: it is the one a buyer is protected from (a coin missing,
    returned or canceled cannot be delivered).
    """
    code = _standing_code(item, data)
    if code is None or not offering_writes.offers_holding(db, [item.id]):
        return None
    return code


def _standing_code(item: InventoryItem, data: dict[str, Any]) -> str | None:
    """The status (else disposition) code an edit gives `item`, if it differs."""
    for field in ("status", "disposition"):
        if field not in data or data[field] is None:
            continue
        held = getattr(item, field)
        if held is None or held.code != data[field]:
            return str(data[field])
    return None


def _offer_standing_code(
    db: Session, listing: Listing, standing: dict[int, str]
) -> str:
    """The standing change a bulk edit gives the items `listing` offers.

    Per offer, because one request can change one item's status and
    another's disposition (an item that already holds the status sent).
    A lot whose members changed differently names each change.
    """
    codes = {
        standing[item.id]
        for item in offering_writes.offered_items(db, listing)
        if item.id in standing
    }
    if not codes:
        raise LookupError(
            f"listing #{listing.id} holds none of the items this edit changes"
        )
    return ", ".join(sorted(codes))


def _end_offers_holding(
    db: Session,
    item_ids: Sequence[int],
    locked: offering_writes.LockedForSale,
    code_of: Callable[[Listing], str],
    verb: str,
) -> None:
    """End every live offer still holding these items, after the caller's writes.

    The post-write half of the lock discipline `receive_items`, `bulk_edit`
    and `update_item` share. Each caller takes `lock_for_sale` itself, under
    its own condition, **before its first write**; `locked` is what that pass
    returned. This runs after the writes, and every step is load-bearing:

    - **Flushed first.** `end_offer` re-reads the items -- `lock_for_sale`
      locks them with `populate_existing=True`, which overwrites whatever the
      session holds and clears the attribute's dirty flag. Production's
      `SessionLocal` sets `autoflush=False`, so without the flush a status
      assigned by the caller is still pending when that re-read lands, is
      silently thrown away, and the commit writes the history row and the
      ended listing while leaving `inventory_item.status_id` untouched -- a
      history asserting a transition the item never made.
    - **The authoritative read.** `offers_holding` rather than a query on
      `inventory_item_id`: a piece of a lot is offered by the *lot's* listing
      and has no listing of its own. This read, not the caller's unlocked one
      above the locks, is what is acted on: every listing row it can return is
      already held, so it costs a SELECT and guarantees nothing here ends an
      offer another transaction has already ended (for a lot listing that
      would rewrite `sales_lot.status` from `sold` to `dissolved`).
    - **Refused as a set before the first `end_offer`.** `refuse_if_lot_unheld`
      (a lot listing whose lot row this pass does not hold -- see
      `offering_writes.refuse_if_lot_unheld`) and `_refuse_auction_lots` (an
      auction lot is ended through its auction), once per standing code. All
      or nothing like the rest of the request: a refusal must not leave half
      the offers ended, and nothing is written when one fires -- the router
      commits once, at the end.

    `code_of` names the change each listing's items went through, and each
    offer is ended with the note `item {verb} {code}`. Ended through
    `offering_writes`, the only writer of listing status and claims.
    """
    db.flush()
    live_offers = list(offering_writes.offers_holding(db, item_ids))
    offering_writes.refuse_if_lot_unheld(live_offers, locked.lot_ids)
    codes = {live.id: code_of(live) for live in live_offers}
    for code in sorted(set(codes.values())):
        _refuse_auction_lots(
            db, [live for live in live_offers if codes[live.id] == code], code
        )
    for live in live_offers:
        offering_writes.end_offer(db, live, note=f"item {verb} {codes[live.id]}")


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
        select(InventoryItem)
        .where(InventoryItem.id.in_(payload.item_ids))
        # Each item's detail row is read per item below: one query each
        # for the set rather than one per item.
        .options(
            selectinload(InventoryItem.currency_detail),
            selectinload(InventoryItem.coin_detail),
        )
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
    # else**; the authoritative one is the second call, in
    # `_end_offers_holding` after the writes and under these locks. That
    # split is the same read-lock-re-read discipline `lock_for_sale` applies
    # to a listing's member set, and it is needed here for the same reason:
    # this read is unlocked, so between it and the locks a concurrent
    # checkout can end one of these offers, or a
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
    # waiting on a member. `refuse_if_lot_unheld`, in `_end_offers_holding`,
    # is the check, and `lot_ids` is why this keeps the result rather than
    # discarding it.
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

    # A coin that cannot be delivered must not stay offered: the flush,
    # the authoritative re-read under the locks, the refusals and the endings
    # are `_end_offers_holding`.
    if ends_offer:
        # Asserted, not tolerated. `locked` is set by the pass above under
        # exactly this condition, so `is not None` is true today -- and an
        # `if` here instead would mean that the day it stopped being true,
        # this endpoint would **silently skip ending the offers of a coin it
        # had just marked `missing`**, leaving it on sale with no error
        # anywhere. That is the worst outcome this endpoint has, and it is
        # not one to guard against by doing nothing. The assert is also what
        # gives `locked` a non-optional type.
        assert locked is not None
        _end_offers_holding(
            db,
            [item.id for item in items],
            locked,
            lambda _live: payload.outcome,
            "recorded",
        )

    db.commit()
    # The outcome as well as the count. This endpoint records `missing`,
    # `returned` and `canceled` too, and answering a cancellation with
    # `{"received": 12}` describes the opposite of what happened.
    return {"outcome": payload.outcome, "items": len(items)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_item(payload: ItemCreate, db: DbSession, admin: AdminUser) -> ItemDetailOut:
    """Add an item to an existing purchase: a coin, banknote or other object.

    Every classifier code is resolved first, so a typo in the last field
    never leaves the earlier ones already written; only then is the item
    built, flushed, given exactly one detail row (`CurrencyDetail` for
    `currency`, `CoinDetail` otherwise), certified if a certificate number
    was given, and handed its opening status-history row -- all inside one
    transaction.

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
    if payload.item_kind == "currency":
        # A note holds no year: its series year is its year (see
        # `_refuse_note_year`; ItemCreate refuses one sent for a note).
        years = None
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
    set_form_id = code_to_id(db, SetForm, payload.set_form, "set_form")
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
        set_form_id=set_form_id,
        storage_form_id=storage_form_id,
        authenticity_id=authenticity_id,
        status_id=status_id,
        disposition_id=require_code(db, Disposition, "held", "disposition"),
        valuation_basis_id=require_code(
            db, ValuationBasis, "numismatic", "valuation_basis"
        ),
        sellers_item_id=payload.sellers_item_id,
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
                face_plate_number=payload.face_plate_number,
                back_plate_number=payload.back_plate_number,
                printing_facility=(
                    _facility(payload.face_plate_number, payload.printing_facility)
                    if payload.face_plate_number
                    else payload.printing_facility
                ),
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
    three round trips per coin is three per coin across the collection.

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
    coin: dict[str, object] = {}
    if (struck := item.coin_detail) is not None:
        coin = {
            "mint": _classifier_code(db, Mint, struck.mint_id),
            "variety": struck.variety,
        }
    note: dict[str, object] = {}
    if (detail := item.currency_detail) is not None:
        note = {
            field: _classifier_code(db, model, getattr(detail, f"{field}_id"))
            for field, model in NOTE_CLASSIFIERS.items()
        }
        note.update({field: getattr(detail, field) for field in NOTE_SCALARS})
        friedberg = (
            db.get(FriedbergNumber, detail.friedberg_id)
            if detail.friedberg_id is not None
            else None
        )
        note.update(
            friedberg_id=detail.friedberg_id,
            friedberg_number=friedberg.fr_number if friedberg else None,
            friedberg_status=detail.friedberg_status,
            friedberg_verified=(
                friedberg.verified_at is not None if friedberg else None
            ),
        )

    order = (
        db.get(PurchaseOrder, item.purchase_order_id)
        if item.purchase_order_id is not None
        else None
    )
    vendor = db.get(Vendor, order.vendor_id) if order is not None else None
    return ItemDetailOut(
        purchase_order_id=item.purchase_order_id,
        order_number=order.order_number if order is not None else None,
        vendor=vendor.name if vendor is not None else None,
        sellers_item_id=item.sellers_item_id,
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
        **coin,
        **note,
        cert_numbers=_cert_numbers(db, item.id),
        grade_display=grades.display_item(item),
        default_tax_rate=settings.sales_tax_rate,
        parent_item_code=parent_code,
        piece_codes=list(
            db.scalars(
                select(InventoryItem.item_code)
                .where(InventoryItem.parent_item_id == item.id)
                .order_by(InventoryItem.id)
            )
        ),
        lot_claims=claims,
        reviewed=_reviewed_fields(db, item.id),
        derived=derived_fields(db, item.id),
        last_changes={
            field: FieldChangeOut(by=change.by, at=change.at)
            for field, change in field_changes.latest(db, item.id).items()
        },
        attributes=[
            ItemAttributeOut(**vars(held))
            for held in item_attributes.held_attributes(db, item.id)
        ],
        sale_state=[
            SaleUseOut(**vars(use))
            for use in sale_state.for_sale(db, [item.id]).get(item.id, [])
        ],
    )


def _cert_numbers(db: Session, item_id: int) -> list[str]:
    """The item's certificate numbers, in the order they were recorded."""
    return list(
        db.scalars(
            select(ItemCertification.cert_number)
            .where(ItemCertification.inventory_item_id == item_id)
            .order_by(ItemCertification.id)
        )
    )


def _set_certifications(db: Session, item: InventoryItem, numbers: list[str]) -> bool:
    """Make the item's certificates exactly `numbers`; whether anything changed.

    A number already held keeps its row, and with it the grading service and
    raw text it was recorded with. One no longer listed is deleted. A new one
    is recorded as graded by the item's own grading service -- the grader
    lives on the item, and a certificate added in the editor belongs to it.
    """
    held = {
        row.cert_number: row
        for row in db.scalars(
            select(ItemCertification).where(
                ItemCertification.inventory_item_id == item.id
            )
        )
    }
    changed = False
    for number, row in held.items():
        if number not in numbers:
            db.delete(row)
            changed = True
    for number in numbers:
        if number not in held:
            db.add(
                ItemCertification(
                    inventory_item_id=item.id,
                    grading_service_id=item.grading_service_id,
                    cert_number=number,
                )
            )
            changed = True
    return changed


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


def _is_note_after(item: InventoryItem, data: dict[str, object]) -> bool:
    """Whether the item is a banknote once this change is made."""
    return (data.get("item_kind") or item.item_kind.code) == "currency"


def _refuse_note_year(items: Sequence[InventoryItem], data: dict[str, object]) -> None:
    """A note has no year of its own: its series year is its year.

    Owner, 2026-09-25: the item's years exist for coins and for lots of
    mixed years; storing a note's series year a second time only gave the
    two a chance to disagree, and three did. A note's year is left empty and
    everything that shows or searches one reads `series_year`.
    """
    sent = [field for field in YEAR_FIELDS if data.get(field) is not None]
    if not sent:
        return
    notes = sorted(i.item_code for i in items if _is_note_after(i, data))
    if notes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"{' and '.join(sent)}: a note's year is its series year -- "
                f"send series_year instead ({', '.join(notes[:10])})"
            ),
        )


def _row[T: ReferenceMixin](
    db: Session, model: type[T], code: str | None, field: str
) -> T | None:
    """The vocabulary row for `code`, or None; an unknown code is a 422."""
    row_id = code_to_id(db, model, code, field)
    return db.get(model, row_id) if row_id is not None else None


def _draft_item(db: Session, draft: ItemDraftIn) -> tuple[InventoryItem, Features]:
    """An item built in memory from the New item form, and what no table holds.

    Never added to the session: it has no id, no item code and no rows, so
    nothing about it can be written. Its relationships point at the real
    vocabulary rows, which is all `suggested_description` reads. A coin's
    composition fills its metal and weights as the save would (only where
    empty, and only when one composition covers every year); a note's serial
    earns the attributes `app.serial_patterns` would record.
    """
    kind = db.get(ItemKind, require_code(db, ItemKind, draft.item_kind, "item_kind"))
    grade_code, strike_code = grades.split_fields(draft.grade, draft.strike_type)
    item = InventoryItem(
        item_kind=kind,
        year_start=draft.year_start,
        year_end=draft.year_end if draft.year_end is not None else draft.year_start,
        piece_count=draft.piece_count,
        denomination=_row(db, Denomination, draft.denomination, "denomination"),
        country=_row(db, Country, draft.country, "country"),
        grade=_row(db, Grade, grade_code, "grade"),
        strike_type=_row(db, StrikeType, strike_code, "strike_type"),
        grade_designation=_row(
            db, GradeDesignation, draft.grade_designation, "grade_designation"
        ),
        grading_service=_row(
            db, GradingService, draft.grading_service, "grading_service"
        ),
        metal=_row(db, Metal, draft.metal, "metal"),
        series_id=code_to_id(db, Series, draft.series, "series"),
    )
    attributes: list[str] = []
    if draft.item_kind == "currency":
        item.currency_detail = CurrencyDetail(
            series_year=draft.series_year,
            series_letter=draft.series_letter,
            note_type_id=code_to_id(db, NoteType, draft.note_type, "note_type"),
            seal_color_id=code_to_id(db, SealColor, draft.seal_color, "seal_color"),
            serial_number=draft.serial_number,
        )
        earned = serial_patterns.analyse(draft.serial_number or "")
        if len(earned) > 1:
            earned -= {FANCY_SERIAL}  # the pattern itself says more
        if earned:
            attributes = list(
                db.scalars(
                    select(ItemAttribute.label)
                    .where(ItemAttribute.code.in_(earned))
                    .order_by(ItemAttribute.sort_order, ItemAttribute.code)
                )
            )
    else:
        item.coin_detail = CoinDetail(
            mint_id=code_to_id(db, Mint, draft.mint, "mint"), variety=draft.variety
        )
        _fill_composition(db, item)
    return item, Features(attributes=attributes, errors=_draft_errors(db, draft))


def _fill_composition(db: Session, item: InventoryItem) -> None:
    """Metal, fineness and weights from the one composition covering the years."""
    if item.denomination is None or item.country is None or item.year_start is None:
        return
    last = item.year_end if item.year_end is not None else item.year_start
    found = db.scalars(
        select(Composition)
        .where(
            Composition.denomination_id == item.denomination.id,
            Composition.country_id == item.country.id,
            Composition.year_from <= item.year_start,
            or_(Composition.year_to.is_(None), Composition.year_to >= last),
        )
        .order_by(Composition.year_from.desc())
        .limit(1)
    ).first()
    if found is None:
        return
    if item.metal is None and found.metal_id is not None:
        item.metal = db.get(Metal, found.metal_id)
    for column in ("fineness", "gross_weight_ozt", "fine_weight_ozt"):
        if getattr(item, column) is None:
            setattr(item, column, getattr(found, column))


def _draft_errors(db: Session, draft: ItemDraftIn) -> list[tuple[str, str | None]]:
    """The form's errors as (label, details), in vocabulary order."""
    if not draft.errors:
        return []
    codes = {error.error_type for error in draft.errors}
    types = {
        t.code: t
        for t in db.scalars(select(ErrorType).where(ErrorType.code.in_(codes)))
    }
    unknown = sorted(codes - set(types))
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown error_type: {', '.join(unknown)}",
        )
    ordered = sorted(
        draft.errors,
        key=lambda e: (types[e.error_type].sort_order, types[e.error_type].label),
    )
    return [(types[e.error_type].label, e.details or None) for e in ordered]


@router.post("/suggested-description")
def suggest_draft_description(
    draft: ItemDraftIn, db: DbSession, _admin: AdminUser
) -> SuggestedDescriptionOut:
    """A description written from the New item form's fields, before saving.

    The same wording the editor's Suggest writes for a saved item
    (`app.item_descriptions`), from an item that exists only in memory.
    Writes nothing.
    """
    item, features = _draft_item(db, draft)
    return SuggestedDescriptionOut(
        description=suggested_description(db, item, features)
    )


@router.get("/{item_id}/suggested-description")
def get_suggested_description(
    item_id: int, db: DbSession, _admin: AdminUser
) -> SuggestedDescriptionOut:
    """A description written from the item's saved record, for the editor.

    Writes nothing: the editor puts it in its draft, and the owner's save is
    what keeps it (`app.item_descriptions`).
    """
    item = _get_item(db, item_id)
    return SuggestedDescriptionOut(description=suggested_description(db, item))


@router.get("/{item_id}/history")
def get_item_history(
    item_id: int, db: DbSession, _admin: AdminUser
) -> list[ItemHistoryEventOut]:
    """Everything logged about this item, newest first.

    Field edits, status moves and location moves in one list
    (`app.item_history`). A classifier is shown by its label; the location
    is admin-only like every other read of it, and so is this route.
    """
    item = _get_item(db, item_id)
    return [
        ItemHistoryEventOut(**vars(event))
        for event in timeline(db, item.id, HISTORY_CLASSIFIERS)
    ]


#: Editable classifiers on an item, and where each code resolves.
#:
#: Wider than PIECE_CLASSIFIERS above, which covers only what a split may
#: override per piece. Everything here is a correction someone makes while
#: attributing an item in hand.
ITEM_CLASSIFIERS: dict[str, type[ReferenceMixin]] = {
    "item_kind": ItemKind,
    "country": Country,
    "denomination": Denomination,
    "bullion_form": BullionForm,
    "set_form": SetForm,
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
#: same names the workbook backup uses, so no translation is needed and a
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
    "sellers_item_id",
)

#: EDITABLE_SCALARS refused by name when sent as null. Both columns are NOT
#: NULL, and an explicit null would otherwise reach the database as a
#: constraint violation -- an unhandled 500 rather than a message a caller can
#: act on. The same reason REQUIRED_CLASSIFIERS exists.
REQUIRED_SCALARS: frozenset[str] = frozenset({"tax_rate", "tax_includes_shipping"})

#: Classifiers on a note's currency detail, and where each code resolves.
NOTE_CLASSIFIERS: dict[str, type[ReferenceMixin]] = {
    "note_type": NoteType,
    "seal_color": SealColor,
    "fed_district": FedDistrict,
    "signature_combination": SignatureCombination,
}

#: Every field whose logged codes the history shows by label: the item's and
#: the note's classifiers, and attributes (logged as a list of codes).
HISTORY_CLASSIFIERS: dict[str, type[ReferenceMixin]] = {
    **ITEM_CLASSIFIERS,
    **NOTE_CLASSIFIERS,
    "mint": Mint,
    "attributes": ItemAttribute,
}

#: Plain columns on a note's currency detail a client may set.
NOTE_SCALARS: tuple[str, ...] = (
    "series_year",
    "series_letter",
    "serial_number",
    "face_plate_number",
    "back_plate_number",
    "printing_facility",
)

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
    unknown seal color is a 422 that leaves the item untouched. `held` is
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
    face = changes.get("face_plate_number")
    if isinstance(face, str):
        changes["printing_facility"] = _facility(face, data.get("printing_facility"))
    return changes


def _facility(face: str, sent: object) -> str:
    """The printing location a face plate names, refusing one that disagrees.

    FW before the face plate is Fort Worth, none is Washington
    (`app.plates`): sent beside a face plate that says otherwise, the
    location is refused by name rather than one of the two winning quietly.
    """
    facility = plates.facility_of(face)
    if sent is not None and sent != facility:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"printing_facility {sent}: face plate {face} was printed in "
                f"{plates.FACILITIES[facility]}"
            ),
        )
    return facility


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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{', '.join(fields)}: only a banknote has these, and "
            f"{not_notes} are not banknotes. Nothing was changed.",
        )
    for item in items:
        for column, value in changes.items():
            setattr(item.currency_detail, column, value)


def _set_notes_and_detail(
    db: Session,
    items: list[InventoryItem],
    changes: dict[str, object],
    *,
    kind_changed: bool,
) -> None:
    """Apply a request's note fields, and swap detail rows if the kind moved.

    Called after the kind is set, and in this order for one reason: a request
    may change the kind and the note fields together. Becoming a banknote, the
    note row must exist before its serial number can be written -- hence the
    first swap. Ceasing to be one, the note's fields must be cleared before
    its row can go -- hence the second, which `item_kinds` refuses while any
    remain. The second call leaves a banknote made by the first as it is.
    """
    if kind_changed:
        currency_id = db.scalar(select(ItemKind.id).where(ItemKind.code == "currency"))
        for item in items:
            if item.item_kind_id == currency_id:
                _match_detail(db, item)
    _apply_note_changes(items, changes)
    if kind_changed:
        for item in items:
            _match_detail(db, item)


def _match_detail(db: Session, item: InventoryItem) -> None:
    """`item_kinds.match_detail_to_kind`, its refusal as a 422."""
    try:
        item_kinds.match_detail_to_kind(db, item)
    except item_kinds.KindChangeRefused as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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


#: A coin's own fields, on `coin_detail` rather than the item.
COIN_DETAIL_FIELDS: tuple[str, ...] = ("mint", "variety")


def _refuse_coin_detail_on_a_note(
    data: dict[str, object],
    item: InventoryItem,
    coin_changes: dict[str, object],
    db: Session,
) -> None:
    """Raise a 422 if a mint or variety is sent for what will be a banknote.

    A kind change to currency already drops the coin's row with its mint
    (`app.item_kinds`), so only a value actually sent needs refusing.
    """
    sent = [field for field, value in coin_changes.items() if value not in (None, "")]
    if not sent:
        return
    currency_id = db.scalar(select(ItemKind.id).where(ItemKind.code == "currency"))
    if _effective_is_currency(data, item, currency_id):
        raise HTTPException(
            status_code=422,
            detail=(
                f"{', '.join(sent)} belongs to coins, not banknotes: "
                f"{item.item_code}. Nothing was changed."
            ),
        )


def _set_coin_detail(
    db: Session, item: InventoryItem, changes: dict[str, object]
) -> None:
    """Write a coin's mint and variety, creating its detail row if it has none."""
    detail = db.get(CoinDetail, item.id)
    if detail is None:
        # Through the relationship, not only the session: the change log's
        # "after" reads `item.coin_detail`, which would otherwise stay None.
        detail = CoinDetail(inventory_item_id=item.id)
        item.coin_detail = detail
    if "mint" in changes:
        code = changes["mint"]
        detail.mint_id = code_to_id(
            db,
            Mint,
            code if isinstance(code, str) else None,
            "mint",
            keep=detail.mint_id,
        )
    if "variety" in changes:
        variety = changes["variety"]
        detail.variety = (variety.strip() or None) if isinstance(variety, str) else None
    # Another table: touching the item moves its version, as with attributes.
    item.updated_at = datetime.now(UTC)


def _refuse_mismatched_designation(
    data: dict[str, object], items: Sequence[InventoryItem], db: Session
) -> None:
    """Raise a 422 if the edit leaves an item with the other kind's designation.

    EPQ and PPQ are a note's paper quality; DCAM, FBL, RD ... describe a
    coin's strike (`grade_designation.applies_to`). Checked against the
    item's state after the edit, like the denomination guard: sending one,
    or changing the kind of an item that holds one, is refused alike.
    """
    if "item_kind" not in data and "grade_designation" not in data:
        return
    currency_id = db.scalar(select(ItemKind.id).where(ItemKind.code == "currency"))
    # Comprehensions, not dict(result): a result has `.keys()`, so dict()
    # takes it for a mapping and indexes it by column name.
    rows = db.execute(
        select(GradeDesignation.id, GradeDesignation.code, GradeDesignation.applies_to)
    ).tuples()
    sides: dict[str, AppliesTo] = {}
    codes: dict[int, str] = {}
    for designation_id, designation_code, applies_to in rows:
        sides[designation_code] = applies_to
        codes[designation_id] = designation_code
    wrong: list[str] = []
    for item in items:
        sent = data.get("grade_designation", codes.get(item.grade_designation_id or 0))
        code = sent if isinstance(sent, str) else None
        side = sides.get(code) if code else None
        # A blank clears it; an unknown code is code_to_id's 422 to raise.
        if side is None or side == AppliesTo.any:
            continue
        if (side == AppliesTo.currency) != _effective_is_currency(
            data, item, currency_id
        ):
            owner = "banknotes" if side == AppliesTo.currency else "coins"
            wrong.append(f"{item.item_code} ({code} belongs to {owner})")
    if wrong:
        raise HTTPException(
            status_code=422,
            detail=(
                "designation of the other kind: "
                f"{', '.join(sorted(set(wrong)))}. Nothing was changed."
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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
    # The fields as sent, before `_split_grade` reshapes them: the change log
    # records these, in the editor's own terms.
    sent = list(data)
    _split_grade(data)

    items = db.scalars(
        select(InventoryItem)
        .where(InventoryItem.id.in_(payload.ids))
        # Each item's detail row is read per item below: one query each
        # for the set rather than one per item.
        .options(
            selectinload(InventoryItem.currency_detail),
            selectinload(InventoryItem.coin_detail),
        )
    ).all()
    found = {item.id for item in items}
    missing = sorted(set(payload.ids) - found)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No such item(s): {missing}. Nothing was changed.",
        )
    before = {item.id: _sent_values(db, item, sent) for item in items}
    _refuse_coin_only_fields(data, list(items), db)
    _refuse_mismatched_denomination(data, list(items), db)
    _refuse_mismatched_designation(data, list(items), db)
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
    _refuse_note_year(items, data)
    years = {
        item.id: (None, None)
        if _is_note_after(item, data)
        else resolve_years((item.year_start, item.year_end), data)
        for item in items
    }
    broken = sorted(
        item.item_code
        for item in items
        if (pair := years[item.id]) is not None and backwards(pair)
    )
    if broken:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"That year would end a range before it starts on {broken}. "
            "Nothing was changed.",
        )

    # As in `update_item`: a new status or disposition on an offered item
    # ends its offer, the guard above having had the caller acknowledge the
    # sale. Locked before the first write, in the canonical order.
    standing = {item.id: code for item in items if (code := _standing_code(item, data))}
    changing = sorted(standing)
    held_offers = offering_writes.offers_holding(db, changing) if changing else []
    locked = None
    if held_offers:
        locked = offering_writes.lock_for_sale(
            db,
            listing_ids=[live.id for live in held_offers],
            item_ids=changing,
            including_paused=True,
        )

    for item in items:
        for column, value in resolved.items():
            setattr(item, column, value)
    _set_notes_and_detail(
        db, list(items), note_changes, kind_changed="item_kind" in data
    )
    for item in items:
        if (pair := years[item.id]) is not None:
            item.year_start, item.year_end = pair
        if status_id is not None:
            set_status(db, item, status_id, user_id=admin.id)

    if locked is not None:
        _end_offers_holding(
            db,
            changing,
            locked,
            lambda live: _offer_standing_code(db, live, standing),
            "edited to",
        )

    # What a person sets is theirs from now on: no pass refreshes it. What
    # follows from the new facts is refreshed now.
    forget(db, found, [_column(field) for field in data])
    hold(db, found, _emptied(data))
    refresh_items(db, found)

    # Who changed what, per item, in the same transaction -- see update_item
    # (`refresh_items` above has flushed).
    for item in items:
        field_changes.record(
            db,
            item.id,
            before[item.id],
            _sent_values(db, item, sent),
            sent,
            user_id=admin.id,
        )

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
    base = data.pop("base", None)
    acknowledged = data.pop("acknowledge_for_sale", False)
    attributes = data.pop("attributes", None)
    if "attributes" in payload.model_fields_set and attributes is None:
        raise HTTPException(
            status_code=422,
            detail="attributes may not be null; send [] to clear them.",
        )
    certs = data.pop("cert_numbers", None)
    if "cert_numbers" in payload.model_fields_set and certs is None:
        raise HTTPException(
            status_code=422,
            detail="cert_numbers may not be null; send [] to clear them.",
        )
    # What the caller sent, before `_split_grade` below reshapes it: the
    # field-by-field merge compares these, in the editor's own terms.
    sent = dict(data)
    if attributes is not None:
        sent["attributes"] = attributes
    if certs is not None:
        sent["cert_numbers"] = certs
    # The item as it stands, in those same terms: the merge compares against
    # it, and the change log records it as each changed field's old value.
    before = field_values(db, item)
    # The coin's own fields live on its detail row, not on the item.
    coin_changes = {f: data.pop(f) for f in COIN_DETAIL_FIELDS if f in data}
    _refuse_coin_detail_on_a_note(data, item, coin_changes, db)
    _refuse_null_scalars(data)
    _refuse_coin_only_fields(data, [item], db)
    _refuse_mismatched_denomination(data, [item], db)
    _refuse_mismatched_designation(data, [item], db)
    _split_grade(data)

    # This is what catches the ordinary lost-update case: two staff, each with
    # a form loaded at a different time, and the second one saving over the
    # first. The item is re-fetched fresh at the top of every request, so
    # nothing later in this function -- including the database's own
    # version_id_col check -- ever sees a token from an earlier request; only
    # this comparison, against the value the caller actually sent, does.
    #
    # With a `base`, the save is merged field by field instead: a change made
    # since only stops it where it touched a field this save changes (owner's
    # ruling, 2026-09-23). The version is then not compared -- the base is
    # the finer check -- and the version column still guards the narrow
    # window between this read and the commit, below.
    if base is not None:
        _refuse_field_conflicts(db, item, before, sent, base)
    elif expected is not None and expected != item.version:
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
    if (data or attributes is not None or certs is not None) and not acknowledged:
        sale_state.guard(db, [item], acknowledged=False)

    # Before anything is set: a refused year leaves the item untouched. A
    # note holds none: its series year is its year.
    _refuse_note_year([item], data)
    years = (
        (None, None)
        if _is_note_after(item, data)
        else resolve_years((item.year_start, item.year_end), data)
    )
    if years is not None:
        refuse_backwards(years, item.item_code)

    # A new status or disposition on an item that is offered takes it off
    # sale: the guard above has already had the caller acknowledge that it is
    # for sale. Left alone, an offered coin marked missing would stay on
    # sale in the shop. The
    # rows are locked here, before the first write, in the canonical order --
    # the same discipline and for the same reasons as `receive_items`.
    standing = _sale_standing_change(db, item, data)
    locked = None
    if standing is not None:
        locked = offering_writes.lock_for_sale(
            db,
            listing_ids=[
                live.id for live in offering_writes.offers_holding(db, [item.id])
            ],
            item_ids=[item.id],
            including_paused=True,
        )

    note_changes = _note_changes(db, data, item.currency_detail)

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

    _set_notes_and_detail(db, [item], note_changes, kind_changed="item_kind" in data)
    if coin_changes:
        _set_coin_detail(db, item, coin_changes)

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
    # After the classifiers, so a new certificate takes the grading service
    # this same save sets; another table, so touched like the links above.
    if certs is not None and _set_certifications(db, item, certs):
        item.updated_at = datetime.now(UTC)

    if standing is not None:
        assert locked is not None
        _end_offers_holding(db, [item.id], locked, lambda _live: standing, "edited to")

    forget(db, [item.id], [_column(field) for field in data])
    hold(db, [item.id], _emptied(data))

    try:
        # Inside the try: the refresh flushes, and a version conflict found
        # there is the same 409 as one found at commit.
        refresh_items(db, [item.id])
        # Who changed what, in the same transaction as the change: one row
        # per sent field whose value actually moved. The "after" read sees
        # this edit's own writes (attribute links included) under
        # production's autoflush=False because `refresh_items` above has
        # flushed -- measured: an extra flush here changed nothing.
        field_changes.record(
            db, item.id, before, field_values(db, item), sent, user_id=admin.id
        )
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
        "face_plate_number",
        "back_plate_number",
        "printing_facility",
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
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
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

    # The set before and after, in the change log like any field an edit
    # changes, so the item's History shows errors recorded and removed.
    before = field_changes.error_set(db, item.id)
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
    after = [
        {"error_type": entry.error_type, "details": entry.details}
        for entry in payload.errors
    ]
    field_changes.record(
        db,
        item.id,
        {"errors": before},
        {"errors": field_changes.sorted_errors(after)},
        ["errors"],
        user_id=admin.id,
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
    ended ones included, and nothing removes a listing row (the catalog's
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

    An item with no parent is complete, not orphaned: only a split lot's
    pieces have one. So this moves nothing and repairs nothing -- the piece
    keeps the cost it was allocated, and simply stops recording where it
    came from.

    Idempotent, because the end state is exactly what was asked for.
    """
    item = _get_item(db, item_id)
    item.parent_item_id = None
    db.commit()
    db.refresh(item)
    return item
