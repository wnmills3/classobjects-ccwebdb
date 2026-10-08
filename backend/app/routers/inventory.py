"""Item-level operations, on what you own rather than on what is for sale.

Separate from `/catalog`, which is listing-centric. An item exists before it is
listed and after it is sold, and operations like splitting a lot apply to the
object, not to the offer.

Staff-only throughout: everything here exposes cost basis.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload
from sqlalchemy.orm.exc import StaleDataError
from sqlalchemy.sql.selectable import ScalarSelect

from .. import (
    field_changes,
    grades,
    item_attributes,
    item_kinds,
    listing_links,
    lot_writes,
    offering_writes,
    plates,
    sale_state,
)
from ..auction_holding import auction_ids_by_listing
from ..classifier_defaults import refresh_items
from ..config import settings
from ..deps import AdminUser, DbSession
from ..field_sources import (
    SUGGESTION,
    WEIGHT,
    derived_fields,
    forget,
    hold,
    record_derived,
)
from ..inventory_search import (
    MISSING_FIELDS,
    VIEWS,
    UnknownIssue,
    UnknownMissingField,
    count_facets,
    count_issues,
    names_matching,
    plain,
    search,
)
from ..item_descriptions import (
    FANCY_SERIAL,
    Features,
    fancy_is_redundant,
    saved_features,
    suggested_description,
)
from ..item_history import timeline
from ..lifecycle_writes import record_initial_status, set_location, set_status
from ..live import live_item
from ..models import (
    AppliesTo,
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
    FriedbergNumber,
    Grade,
    GradeDesignation,
    GradingService,
    InventoryItem,
    ItemAttribute,
    ItemCertification,
    ItemError,
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
from ..references import code_of, code_to_id, require_code
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
    ItemErrorIn,
    ItemErrorOut,
    ItemErrorsOut,
    ItemErrorsRequest,
    ItemHistoryEventOut,
    ItemSaleOut,
    ItemSuggestIn,
    ReceiveRequest,
    SaleUseOut,
    SplitPieceIn,
    SplitRequest,
    SplitResultOut,
    SuggestedDescriptionOut,
)
from ..splitting import SplitError, SplitPiece, split_item
from ..years import YEAR_FIELDS, backwards, refuse_backwards, resolve_years
from ._resolve import (
    get_or_404,
    get_or_422,
    refuse_future,
    refuse_null_required,
    refuse_stale_version,
    unprocessable,
)
from ._tx import committing

router = APIRouter(prefix="/inventory", tags=["inventory"])

#: What a write answers (409) when an item's version moved between this
#: request's read of it and its UPDATE: another writer got there first, and
#: nothing of this request was applied.
_STALE_ITEMS = "An item was changed by someone else while saving. Reload and retry."

#: The OpenAPI description of the 422 the routes that write an item's fields
#: answer -- entering, editing, bulk editing and the two dry runs.
_REFUSED_ITEM = (
    "Request validation failed, or a code is unknown or retired, or a field "
    "does not fit the item's kind or the item as the change would leave it."
)

#: Per-piece classifier overrides a caller may supply, and where each resolves.
PIECE_CLASSIFIERS: dict[str, type] = {
    "denomination": Denomination,
    "grade": Grade,
    "strike_type": StrikeType,
    "metal": Metal,
}


def _get_item(db: Session, item_id: int) -> InventoryItem:
    """The inventory item with this id, or a 404."""
    return get_or_404(db, InventoryItem, item_id, "Inventory item not found")


def _to_piece(db: Session, spec: SplitPieceIn) -> SplitPiece:
    """One piece of a split as the splitter takes it, its codes resolved to ids.

    Only what the request states overrides the lot; a piece is stored as a
    single item whatever the lot was packaged as.
    """
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
        overrides["no_date"] = False
    if spec.description is not None:
        overrides["description"] = spec.description

    # Pieces come out of a tube or a set as individual items, whatever the lot
    # was packaged as. `.one()`: a vocabulary row that is not there fails the
    # split loudly, rather than leaving every piece packaged as the lot was.
    single = db.scalars(
        select(StorageForm.id).where(StorageForm.code == "single")
    ).one()
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

    # Text pasted from an order page or a table cell brings a space or a tab
    # with it, and the match is a substring: `%51877 %` finds nothing.
    q = q.strip() if q else q
    # Read once for the page, the facets and the issue counts alike.
    names = names_matching(db, spec, q)
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
            names=names,
        )
    except UnknownIssue as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown issue {exc.args[0]!r} for {view}. Available: "
            f"{sorted(spec.issues)}",
        ) from exc
    except UnknownMissingField as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown missing field {exc.args[0]!r} for {view}. Available: "
            f"{sorted(MISSING_FIELDS)}",
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
        facets=(
            count_facets(db, spec, params=params, query=q, names=names)
            if facets
            else {}
        ),
        issues=(
            count_issues(db, spec, params=params, query=q, names=names)
            if facets
            else {}
        ),
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
    "fineness",
    "fine_weight_ozt",
    "authenticity",
)


def _refuse_auction_lots(
    db: Session, listings: Sequence[Listing], outcome: str
) -> None:
    """Refuse a receipt that would end an auction lot's offer. 409.

    One of three doors onto an orphaned `auction_lot`, with
    `routers.offers.end_listing` and `sales_writes.record_sale`, each closed
    the same way. `app.auctions` is the sole writer of `auction_lot`, and
    `remove_lot`, `cancel` and `settle` each end the lot's listing *and*
    delete or resolve its row in one transaction. This endpoint calls
    `offering_writes.end_offer`, which has never heard of `auction_lot`.

    **Reachable, though it looks otherwise.** The "already received" refusal
    in `receive_items` is conditioned on `payload.outcome == "received"`, so
    the three outcomes that end an offer -- `missing`, `returned`,
    `canceled` -- skip it entirely; an already-received coin is exactly this
    path's input. And `offering_writes.offers_holding` filters on claims and
    listing status only. It is **format-blind**, so an `active` auction lot
    listing comes back through the *derived* half and would be ended like
    any other.

    What ending it would cost:

    - **Before the auction closes**, one missing coin would end the whole
      lot listing, dissolve its `sales_lot` and release **every other
      member** back to `held`, while the `auction_lot` row kept its lot
      number. `app.auctions.consign` would then read
      `offering_writes.offered_items` -> `[]` and silently skip the lot: the
      auction would report itself consigned and those coins never left.
    - **After it closes, consigned**, `settle`'s `_return_from_consignment`
      would read the same empty list, return nothing, and then clear
      `auction.consigned_on` on the claim that everything came home. The
      lot's healthy members would be left filed at the auction house with
      nothing linking them to the auction -- the stranding custody tracking
      exists to prevent.
    - **After it closes, sold**, `sales_writes.record_sale_lines` would
      refuse that lot "not on offer" and the auction could not be settled
      until the lot was re-entered as withdrawn.

    **The trade this makes, deliberately:** a coin in an auction that goes
    missing is a **two-step** operation -- take the lot out of the auction
    (remove it before the auction closes; settle it as withdrawn, or cancel,
    after), then record the loss -- the same trade Record sale makes. The
    message says so, because the operator is the one who has to do the
    second step.

    **Keyed on the `auction_lot` row, not on `format` alone.** The Offer
    dialog offers a coin directly on eBay by auction, with no auction behind
    it; `offers_holding` returns only live listings, so a live
    auction-format listing with no `auction_lot` row is always such a direct
    offer, and it is ended here like any other.

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


def _require_location(db: Session, location_id: int) -> None:
    """422 for a storage location that does not exist."""
    get_or_422(
        db, StorageLocation, location_id, f"Unknown storage_location_id: {location_id}"
    )


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
    view of an item (sale state, attributes...), which is several
    queries per item, and a bulk edit has no limit on how many items it
    touches. Only the fields a bulk edit can set, and those it can move
    without being sent them (`_MOVED_UNSENT`), are needed, so only those are
    read.
    """
    return {field: _sent_value(db, item, field) for field in fields}


def _sent_value(db: Session, item: InventoryItem, field: str) -> object:
    """One field of an item as `_sent_values` reads it, from where it is kept.

    A coin's mint and variety are on its coin detail and a note's fields on
    its currency detail; an item with no such row holds none of them.
    """
    detail = item.currency_detail
    coin = item.coin_detail
    if field == "mint":
        return code_of(db, Mint, coin.mint_id) if coin else None
    if field == "variety":
        return coin.variety if coin else None
    if field in ITEM_CLASSIFIERS:
        fk = getattr(item, f"{field}_id")
        return code_of(db, ITEM_CLASSIFIERS[field], fk)
    if field in NOTE_CLASSIFIERS:
        fk = getattr(detail, f"{field}_id") if detail else None
        return code_of(db, NOTE_CLASSIFIERS[field], fk)
    if field in NOTE_SCALARS:
        return plain(getattr(detail, field)) if detail else None
    return plain(getattr(item, field, None))


def _same(field: str, a: object, b: object) -> bool:
    """Whether two values of `field` say the same thing, as the change log judges it."""
    return field_changes.same_value(a, b, numeric=field in field_changes.NUMERIC_FIELDS)


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
        if not _same(field, current.get(field), base[field])
        and not _same(field, current.get(field), yours)
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
    A lot whose members changed differently names each change. An offer
    whose items read as none of the edited ones -- it was found through
    them, so this is a lot read differently -- is named by every change
    the edit made, rather than failing the request.
    """
    codes = {
        standing[item.id]
        for item in offering_writes.offered_items(db, listing)
        if item.id in standing
    } or set(standing.values())
    return ", ".join(sorted(codes))


def _end_offers_holding(
    db: Session,
    item_ids: Sequence[int],
    locked: offering_writes.LockedForSale,
    standing_of: Callable[[Listing], str],
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

    `standing_of` names the change each listing's items went through, and each
    offer is ended with the note `item {verb} {code}`. Ended through
    `offering_writes`, the only writer of listing status and claims.
    """
    db.flush()
    live_offers = list(offering_writes.offers_holding(db, item_ids))
    offering_writes.refuse_if_lot_unheld(live_offers, locked.lot_ids)
    codes = {live.id: standing_of(live) for live in live_offers}
    for code in sorted(set(codes.values())):
        _refuse_auction_lots(
            db, [live for live in live_offers if codes[live.id] == code], code
        )
    for live in live_offers:
        offering_writes.end_offer(db, live, note=f"item {verb} {codes[live.id]}")


def _refuse_already_received(
    db: Session, items: Sequence[InventoryItem], received_id: int
) -> None:
    """Refuse a receipt of items that are already received. 409.

    Naming the status and the arrival date, not just the code, is what lets
    an operator tell a double-submitted form (same date, moments apart) from
    the wrong row (a date that means nothing to them) -- "already received"
    alone answers neither question.
    """
    already = [i for i in items if i.status_id == received_id]
    if not already:
        return
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
        """The day the item arrived, as ISO text, or "unknown date"."""
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


def _record_outcome(
    db: Session,
    item: InventoryItem,
    payload: ReceiveRequest,
    to_status: int,
    user_id: int,
) -> None:
    """Write one item's receipt: its new status, and where it was put.

    Only an arrival has an arrival date and a place: an item recorded
    missing, returned or canceled takes the status alone.
    """
    arrived = payload.outcome == "received"
    set_status(
        db,
        item,
        to_status,
        user_id=user_id,
        note=payload.note,
        arrived_on=payload.arrived_on if arrived else None,
    )
    if arrived and payload.storage_location_id is not None:
        set_location(
            db,
            item,
            payload.storage_location_id,
            user_id=user_id,
            note=payload.note,
        )


@router.post(
    "/receive",
    responses={
        404: {"description": "An item id names no item that can be received."},
        409: {
            "description": "An item is already received, is for sale and the "
            "caller has not acknowledged it, or its offer cannot be ended here."
        },
        422: unprocessable(
            "Request validation failed, or the outcome, the arrival date or "
            "the storage location is not one the receipt can take."
        ),
    },
)
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
    # not physically turned up yet. Checked against today rather than as a
    # lower bound against the purchase order's ordered_on, which is
    # frequently null and would refuse legitimate data -- today's date is the
    # only bound with a real justification.
    refuse_future("arrived_on", payload.arrived_on)

    items = db.scalars(
        select(InventoryItem)
        # Live rows only (`app.live`): a row deleted as never having existed
        # does not arrive, and neither does a lot that has been split -- its
        # pieces are what arrive. Either is an unknown id here.
        .where(InventoryItem.id.in_(payload.item_ids), live_item())
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
    if payload.outcome == "received":
        _refuse_already_received(db, items, received_id)

    # An item that has not been received cannot be offered
    # (`offering_writes.offer` refuses it) and an order can only hold a
    # listing, so a plain receipt has nothing to warn about. The other three
    # outcomes say a coin will not be delivered, and that is exactly what a
    # buyer needs protecting from.
    ends_offer = payload.outcome != "received"
    if ends_offer:
        sale_state.guard(db, list(items), acknowledged=payload.acknowledge_for_sale)

    if payload.storage_location_id is not None:
        _require_location(db, payload.storage_location_id)

    to_status = require_code(db, ItemStatus, payload.outcome, "status")

    # Every row this receipt will touch, taken **before the first write** and
    # in the canonical order, through the one function that owns it
    # (`offering_writes.lock_for_sale`: lot rows, then items, then listings).
    #
    # Load-bearing. `set_status` below writes `inventory_item` rows -- taking
    # their exclusive row locks at the flush that follows -- and `end_offer`
    # runs after it, whose own pass waits on the *lot* row. Without this pass
    # receiving would acquire items before lots, the inverse of every other
    # writer, and an administrator marking a lot's member `missing` while a
    # shopper checked that lot out could each hold what the other waited
    # for: `order_writes._lock_listings` takes the lot row as its first
    # statement and then waits on the member row this endpoint holds. Taking
    # the whole set here puts receiving in the same order as everything
    # else, and the `end_offer` calls below then re-lock rows this
    # transaction already holds.
    #
    # The deadlock is not the only loss the inversion permits. With the pass
    # removed the race test fails on a `StaleDataError`, because the
    # receipt's unlocked UPDATE of the member row queues behind the checkout
    # and then writes through a version that has moved. Both are a 500 for
    # an operator, and locking and re-reading first removes both -- the
    # second is the same false conflict `offering_writes._lock_items`'
    # docstring describes.
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
    # histories the spec says never collapse into one.
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

    # A plain receipt takes no row locks, so another writer can move an
    # item's version between this request's read and its UPDATE: that is the
    # caller's conflict to retry, not a server error.
    with committing(db, _STALE_ITEMS):
        for item in items:
            _record_outcome(db, item, payload, to_status, admin.id)

        # A coin that cannot be delivered must not stay offered: the flush,
        # the authoritative re-read under the locks, the refusals and the
        # endings are `_end_offers_holding`.
        if ends_offer:
            # Asserted, not tolerated. `locked` is set by the pass above
            # under exactly this condition, so `is not None` is true today --
            # and an `if` here instead would mean that the day it stopped
            # being true, this endpoint would **silently skip ending the
            # offers of a coin it had just marked `missing`**, leaving it on
            # sale with no error anywhere. That is the worst outcome this
            # endpoint has, and it is not one to guard against by doing
            # nothing. The assert is also what gives `locked` a non-optional
            # type.
            assert locked is not None
            _end_offers_holding(
                db,
                [item.id for item in items],
                locked,
                lambda _live: payload.outcome,
                "recorded",
            )

    # The outcome as well as the count. This endpoint records `missing`,
    # `returned` and `canceled` too, and answering a cancellation with
    # `{"received": 12}` describes the opposite of what happened.
    return {"outcome": payload.outcome, "items": len(items)}


def _entered_years(payload: ItemCreate) -> tuple[int | None, int | None]:
    """The years a new item holds, refusing a range that ends before it starts.

    A year given alone is a single year; an explicit end before the start is
    refused rather than silently swapped. There is no existing item to name
    in the message yet, so the title given stands in for it.
    """
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
    if years is None:
        return (None, None)
    refuse_backwards(years, payload.source_title)
    return years


def _refuse_entered_denomination_of_the_other_kind(
    db: Session, payload: ItemCreate
) -> None:
    """Raise a 422 if a new item's denomination belongs to the other kind.

    No item row exists yet on this path, so the payload's own `item_kind` is
    compared against the denomination's kind directly, rather than through
    `_refuse_mismatched_denomination`.
    """
    if not payload.denomination:
        return
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


def _detail_code_id(
    db: Session,
    model: type[ReferenceMixin],
    code: str | None,
    field: str,
    *,
    fits: bool,
) -> int | None:
    """A detail row's classifier id for a new item, or None where it has no such row.

    A coin's mint is resolved only for what is not a note, and a note's
    classifiers only for a note: the code of the other kind is not looked up
    at all.
    """
    return code_to_id(db, model, code, field) if fits else None


def _refuse_entered_fine_above_gross(payload: ItemCreate) -> None:
    """Raise a 422 if a new item is said to hold more fine weight than gross."""
    if (
        payload.gross_weight_ozt is not None
        and payload.fine_weight_ozt is not None
        and payload.fine_weight_ozt > payload.gross_weight_ozt
    ):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                f"fine_weight_ozt {payload.fine_weight_ozt} cannot be more than "
                f"gross_weight_ozt {payload.gross_weight_ozt}."
            ),
        )


def _entered_tax(payload: ItemCreate) -> dict[str, object]:
    """The tax columns a new item is given; one left out takes the configured value."""
    tax_kwargs: dict[str, object] = {}
    if payload.tax_rate is not None:
        tax_kwargs["tax_rate"] = payload.tax_rate
    if payload.tax_includes_shipping is not None:
        tax_kwargs["tax_includes_shipping"] = payload.tax_includes_shipping
    return tax_kwargs


def _entered_facility(payload: ItemCreate) -> str | None:
    """Where a new note was printed: what its face plate names, else what was sent."""
    if payload.face_plate_number:
        return _facility(payload.face_plate_number, payload.printing_facility)
    return payload.printing_facility


def _set_entered_attributes(
    db: Session, item: InventoryItem, codes: Sequence[str], user_id: int
) -> None:
    """Give a new item its attributes, by the same rules as an edit.

    A code unknown, or of the other kind (a star on a coin), refuses the
    whole entry (422) -- nothing has been committed yet.
    """
    if not codes:
        return
    try:
        item_attributes.set_attributes(db, item, codes, user_id=user_id)
    except item_attributes.AttributeRefused as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def _take_listing_as_the_purchase_s_address(
    db: Session, order: PurchaseOrder, listing_url: str | None
) -> None:
    """Give a purchase with no web address its first item's listing.

    At an auction house or a shop the lot's page is the purchase's too; a
    marketplace's order holds many listings, so it takes none.
    """
    vendor = db.get_one(Vendor, order.vendor_id)
    if (
        order.source_url is None
        and listing_url
        and not listing_links.is_marketplace(vendor)
    ):
        order.source_url = listing_url


@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    responses={422: unprocessable(_REFUSED_ITEM)},
)
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
    order = get_or_404(
        db,
        PurchaseOrder,
        payload.purchase_order_id,
        f"Unknown purchase_order_id: {payload.purchase_order_id}",
    )

    years = _entered_years(payload)

    # Every code resolved before anything is written -- the first unknown one
    # is the 422 the caller sees, and nothing has been added to the session
    # yet for any of them to leave behind.
    item_kind_id = require_code(db, ItemKind, payload.item_kind, "item_kind")
    country_id = code_to_id(db, Country, payload.country, "country")
    denomination_id = code_to_id(db, Denomination, payload.denomination, "denomination")
    _refuse_entered_denomination_of_the_other_kind(db, payload)
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
    mint_id = _detail_code_id(db, Mint, payload.mint, "mint", fits=not is_currency)
    note_type_id = _detail_code_id(
        db, NoteType, payload.note_type, "note_type", fits=is_currency
    )
    seal_color_id = _detail_code_id(
        db, SealColor, payload.seal_color, "seal_color", fits=is_currency
    )
    fed_district_id = _detail_code_id(
        db, FedDistrict, payload.fed_district, "fed_district", fits=is_currency
    )
    signature_combination_id = _detail_code_id(
        db,
        SignatureCombination,
        payload.signature_combination,
        "signature_combination",
        fits=is_currency,
    )
    unknown_suggestions = sorted(set(payload.suggested) - SUGGESTABLE_FIELDS)
    if unknown_suggestions:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Not a suggestable field: {unknown_suggestions}. "
            f"Available: {sorted(SUGGESTABLE_FIELDS)}",
        )

    # Before anything is built: a refused location leaves nothing behind.
    if payload.storage_location_id is not None:
        _require_location(db, payload.storage_location_id)
    # Likewise a piece said to hold more metal than it weighs, which the
    # database would refuse as the row is written.
    _refuse_entered_fine_above_gross(payload)

    tax_kwargs = _entered_tax(payload)

    item = InventoryItem(
        purchase_order_id=order.id,
        item_kind_id=item_kind_id,
        source_title=payload.source_title,
        description=payload.description,
        year_start=years[0],
        year_end=years[1],
        no_date=payload.no_date,
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
        listing_url=payload.listing_url,
        fineness=payload.fineness,
        gross_weight_ozt=payload.gross_weight_ozt,
        fine_weight_ozt=payload.fine_weight_ozt,
        weight_note=payload.weight_note,
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
                serial_number=_serial_or_none(payload.serial_number),
                face_plate_number=payload.face_plate_number,
                back_plate_number=payload.back_plate_number,
                printing_facility=_entered_facility(payload),
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

    if payload.storage_location_id is not None:
        set_location(
            db,
            item,
            payload.storage_location_id,
            user_id=admin.id,
            note="entered in the console",
        )

    _set_entered_attributes(db, item, payload.attributes, admin.id)
    _take_listing_as_the_purchase_s_address(db, order, payload.listing_url)

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
    """One item as the editor holds it, with what its lot claimed.

    The item, its lot's claims, which fields hold a derived default, who last
    changed each field and what offers it, in one response: the edit form
    needs all of them on every field, and a round trip each per coin is that
    many per coin across the collection.

    Carries every field EDITABLE_SCALARS and ITEM_CLASSIFIERS accept, not a
    hand-picked subset -- see test_the_detail_payload_covers_every_editable_field.
    A field the client can set but this endpoint never returns renders blank
    in the form regardless of what is stored, which is how a coin already
    holding MS65 shows an empty Grade box and gets overwritten with nothing.
    """
    return item_detail(db, _get_item(db, item_id))


def item_detail(
    db: Session, item: InventoryItem, *, editing: bool = True
) -> ItemDetailOut:
    """The editor's whole view of one item; also what a sale snapshot copies.

    `editing=False` leaves out what describes the editing rather than the
    item -- the lot's claims, derived defaults, who last
    changed each field and the sale warning -- which `app.sale_snapshot`
    drops from its copy anyway; they keep their empty defaults instead of
    being read.
    """
    parent_code: str | None = None
    claims: dict[str, object] = {}
    if item.parent_item_id is not None:
        parent = db.get(InventoryItem, item.parent_item_id)
        if parent is not None:
            parent_code = parent.item_code
        if parent is not None and editing:
            claims = _lot_claims(db, parent)

    classifiers = {
        field: code_of(db, model, getattr(item, f"{field}_id"))
        for field, model in ITEM_CLASSIFIERS.items()
    }
    coin = _coin_fields(db, item)
    note = _note_fields(db, item)
    order_number, vendor_name = _purchase_named(db, item)
    return ItemDetailOut(
        purchase_order_id=item.purchase_order_id,
        order_number=order_number,
        vendor=vendor_name,
        sellers_item_id=item.sellers_item_id,
        listing_url=item.listing_url,
        storage_location_id=item.storage_location_id,
        **{
            column: plain(getattr(item, column))
            for column in (
                "id",
                "item_code",
                "version",
                "source_title",
                "description",
                "rating",
                "year_start",
                "year_end",
                "no_date",
                "fineness",
                "gross_weight_ozt",
                "fine_weight_ozt",
                "weight_note",
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
        attributes=[
            ItemAttributeOut(**vars(held))
            for held in item_attributes.held_attributes(db, item.id)
        ],
        **(_editing_fields(db, item, claims) if editing else {}),
    )


def _lot_claims(db: Session, parent: InventoryItem) -> dict[str, object]:
    """What a piece's lot said of each inherited field, by API field.

    Every inherited field, not only the ones that now differ. A piece that
    still agrees with its lot is the important case, not the boring one: it
    agrees *because* it inherited the seller's claim and nobody has checked
    it yet. Reporting only the differences would drop exactly the fields
    that need the warning. What to do with an agreeing field is the form's
    call, not this endpoint's.

    A relationship may claim nothing at all -- a lot with no grade recorded
    says nothing about grade -- and that is omitted rather than sent as
    null, so the form can tell "the lot said nothing" from "the lot said
    none of these apply".
    """
    claims: dict[str, object] = {}
    for name in LOT_CLAIM_FIELDS:
        value: object
        if name in ITEM_CLASSIFIERS:
            value = code_of(db, ITEM_CLASSIFIERS[name], getattr(parent, f"{name}_id"))
        else:
            value = plain(getattr(parent, name))
        if value is not None:
            claims[name] = value
    return claims


def _coin_fields(db: Session, item: InventoryItem) -> dict[str, object]:
    """A coin's own fields from its detail row; none for an item with no such row."""
    struck = item.coin_detail
    if struck is None:
        return {}
    return {
        "mint": code_of(db, Mint, struck.mint_id),
        "variety": struck.variety,
    }


def _note_fields(db: Session, item: InventoryItem) -> dict[str, object]:
    """A note's own fields and its Friedberg number; none for what is not a note."""
    detail = item.currency_detail
    if detail is None:
        return {}
    note: dict[str, object] = {
        field: code_of(db, model, getattr(detail, f"{field}_id"))
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
        friedberg_verified=(friedberg.verified_at is not None if friedberg else None),
    )
    return note


def _purchase_named(db: Session, item: InventoryItem) -> tuple[str | None, str | None]:
    """The order number and the vendor of the purchase an item came on, if any."""
    order = (
        db.get(PurchaseOrder, item.purchase_order_id)
        if item.purchase_order_id is not None
        else None
    )
    vendor = db.get(Vendor, order.vendor_id) if order is not None else None
    return (
        order.order_number if order is not None else None,
        vendor.name if vendor is not None else None,
    )


def _editing_fields(
    db: Session, item: InventoryItem, claims: dict[str, object]
) -> dict[str, Any]:
    """The parts of `item_detail` that describe the editing, not the item."""
    return {
        "lot_claims": claims,
        "derived": derived_fields(db, item.id),
        "last_changes": {
            field: FieldChangeOut(by=change.by, at=change.at)
            for field, change in field_changes.latest(db, item.id).items()
        },
        "sale_state": [
            SaleUseOut(**vars(use))
            for use in sale_state.for_sale(db, [item.id]).get(item.id, [])
        ],
    }


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

    Sales **inside a lot** included. `listing.inventory_item_id` is NULL on a
    lot listing, so the lot's own listing is reached through the shares the
    sale wrote, one per member.

    A subquery rather than a join to `sales_order_item_share`, so a line
    stays one row however many members its lot had: joining would repeat the
    line once per share and the response would list the same sale three
    times.

    **`quantity` and `unit_price` belong to the line, not to the coin**, and
    for a lot line they are the whole group's -- one lot at 1,000.00 against
    a member that cost 200. Reaching a lot's sale without saying so would put
    the group's price beside one coin on the screen an owner uses to ask what
    happened to that coin, which would mislead. So every row also carries
    `sales_lot_id`, which says the sale was a group sale, and `share_amount`,
    this coin's own cost-weighted share of it. On an item sale `sales_lot_id`
    is null and the line's figures are the coin's own.
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


def _no_date_after(item: InventoryItem, data: dict[str, object]) -> bool:
    """Whether the item has no date once this change is made.

    `no_date` sent true clears the years, and is refused beside a year sent
    in the same request -- the two contradict -- and on a note, whose year is
    its series year. A year sent alone dates the piece, so it clears the
    flag; anything else leaves it as it was. A note never holds it: an item
    turned into a note loses it with its years.
    """
    sent = data.get("no_date")
    years_sent = [field for field in YEAR_FIELDS if data.get(field) is not None]
    if sent:
        if years_sent:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{item.item_code}: a piece with no date cannot also have "
                f"a year ({', '.join(sorted(years_sent))} sent).",
            )
        if _is_note_after(item, data):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"{item.item_code}: a note has no 'no date' -- its year is "
                "its series year.",
            )
        return True
    if _is_note_after(item, data) or years_sent:
        return False
    return item.no_date if sent is None else False


def _refuse_note_year(items: Sequence[InventoryItem], data: dict[str, object]) -> None:
    """A note has no year of its own: its series year is its year.

    The item's years exist for coins and for lots of mixed years; storing a
    note's series year a second time would only give the two a chance to
    disagree. A note's year is left empty and
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


def _error_labels(
    db: Session, errors: Sequence[ItemErrorIn]
) -> list[tuple[str, str | None]]:
    """Errors as (label, details), in vocabulary order; an unknown code is a 422."""
    if not errors:
        return []
    codes = {error.error_type for error in errors}
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
        errors,
        key=lambda e: (types[e.error_type].sort_order, types[e.error_type].label),
    )
    return [(types[e.error_type].label, e.details or None) for e in ordered]


def _attribute_labels(db: Session, codes: Sequence[str]) -> list[str]:
    """Attribute codes as a description lists them; an unknown code is a 422."""
    rows = list(
        db.scalars(
            select(ItemAttribute)
            .where(ItemAttribute.code.in_(codes))
            .order_by(ItemAttribute.sort_order, ItemAttribute.code)
        )
    )
    unknown = sorted(set(codes) - {row.code for row in rows})
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown attribute: {', '.join(unknown)}",
        )
    if fancy_is_redundant(row.code for row in rows):
        rows = [row for row in rows if row.code != FANCY_SERIAL]
    return [row.label for row in rows]


#: Classifiers a dry run leaves alone: status and disposition have history
#: and sale effects of their own, and a description reads neither.
_NOT_DRY_RUN = frozenset({"status", "disposition"})


def _dry_run_classifiers(
    db: Session, item: InventoryItem, data: dict[str, Any]
) -> None:
    """Set the item's classifiers a dry run applies, each code resolved as Save does.

    Not status or disposition (`_NOT_DRY_RUN`). A value the item already
    holds stays acceptable after it is retired (`keep`).
    """
    for field, model in ITEM_CLASSIFIERS.items():
        if field not in data or field in _NOT_DRY_RUN:
            continue
        held = getattr(item, f"{field}_id")
        if field in REQUIRED_CLASSIFIERS:
            row_id: int | None = require_code(db, model, data[field], field, keep=held)
        else:
            row_id = code_to_id(db, model, data[field], field, keep=held)
        setattr(item, f"{field}_id", row_id)


def _dry_run_scalars(item: InventoryItem, data: dict[str, Any]) -> None:
    """Set the plain fields a dry run applies; the years are set apart.

    A required field sent empty is left as it was: the column cannot hold
    nothing, and a dry run refuses nothing Save would name.
    """
    for field in EDITABLE_SCALARS:
        if field not in data or field in YEAR_FIELDS:
            continue
        if field in REQUIRED_SCALARS and data[field] is None:
            continue
        setattr(item, field, data[field])


def _dry_run_save(db: Session, item: InventoryItem, data: dict[str, Any]) -> None:
    """Apply an edit as Save does, defaults included, for the caller to roll back.

    Save's writes, in Save's order: the classifiers, a note's fields and
    the swap of detail rows when the kind moves, a coin's mint and variety,
    the plain fields and the years, and then `refresh_items`, which fills
    what follows from the facts -- a coin's metal, fineness and weights from
    its composition, a note's type, seal and signatures from its series.
    That pass reads the database, which is why this flushes rather than
    imitating it in memory. Writes inside the caller's transaction only; the
    caller rolls it back.

    Not every refusal of Save's. An unknown or retired code, a note's field
    on an item that is not a note, no date beside a year, a fine weight above
    the gross weight, a range that ends before it starts and whatever the
    database's own constraints refuse are refused here too; the checks Save
    makes on the resulting state -- a coin-only field, a denomination or a
    designation of the other kind, a year sent for a note -- are not made,
    a required field sent empty is left as it was, and status and
    disposition are not applied.
    """
    coin_changes = {f: data.pop(f) for f in COIN_DETAIL_FIELDS if f in data}
    _split_grade(data)
    no_date = _no_date_after(item, data)
    worked_out = _refuse_fine_above_gross(db, [item], data)
    _dry_run_classifiers(db, item, data)
    note_changes = _note_changes(db, data, item.currency_detail)
    _set_notes_and_detail(db, [item], note_changes, kind_changed="item_kind" in data)
    if coin_changes:
        _set_coin_detail(db, item, coin_changes)
    _dry_run_scalars(item, data)
    _empty_worked_out_fine_weight(db, worked_out)
    if _is_note_after(item, data) or no_date:
        years: tuple[int | None, int | None] | None = (None, None)
    else:
        years = resolve_years((item.year_start, item.year_end), data)
    if years is not None:
        refuse_backwards(years, item.item_code)
        item.year_start, item.year_end = years
    item.no_date = no_date
    # As Save does: a field the person sent is theirs, not a default, and one
    # they emptied stays empty rather than being filled straight back.
    forget(db, [item.id], [_column(field) for field in data])
    hold(db, [item.id], _emptied(data))
    refresh_items(db, [item.id])


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


@router.post(
    "/{item_id}/suggested-description",
    responses={422: unprocessable(_REFUSED_ITEM)},
)
def suggest_description_for(
    item_id: int, payload: ItemSuggestIn, db: DbSession, _admin: AdminUser
) -> SuggestedDescriptionOut:
    """A description of the item as the editor shows it, unsaved changes included.

    The editor sends what Save would send, and the errors its panel holds.
    `_dry_run_save` applies the edit as Save does, defaults included, inside
    this request's transaction; the item is described as that left it, and
    everything is rolled back. The rows it touched are locked only for those
    milliseconds; nothing is ever committed.
    """
    item = _get_item(db, item_id)
    data = payload.changes.model_dump(exclude_unset=True)
    attributes = data.pop("attributes", None)
    try:
        _dry_run_save(db, item, data)
        db.flush()
        # Defaults were written by SQL as well as through the ORM: read the
        # item back as the dry run left it.
        db.expire_all()
        item = _get_item(db, item_id)
        saved = saved_features(db, item)
        features = Features(
            attributes=(
                _attribute_labels(db, attributes)
                if attributes is not None
                else saved.attributes
            ),
            errors=(
                _error_labels(db, payload.errors)
                if payload.errors is not None
                else saved.errors
            ),
        )
        description = suggested_description(db, item, features)
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="These changes could not be saved as they stand, so there is "
            f"nothing to describe yet: {exc.orig}",
        ) from exc
    finally:
        # Nothing the dry run wrote survives: not the edit, not the defaults,
        # not the version it moved.
        db.rollback()
    return SuggestedDescriptionOut(description=description)


@router.post("/{item_id}/preview", responses={422: unprocessable(_REFUSED_ITEM)})
def preview_item(
    item_id: int, payload: ItemSuggestIn, db: DbSession, _admin: AdminUser
) -> ItemDetailOut:
    """The item as Save would leave it, for the editor to show before saving.

    Entering the facts fills what follows from them -- a note's class, seal
    and signatures from its series, its Bank from its serial, a coin's metal
    and weights from its composition, the design series -- but only a save
    runs those rules. This runs the save (`_dry_run_save`) inside the
    request's transaction, reads the item back, and rolls everything back,
    so the editor shows what the facts decide as they are typed, by the very
    rules Save will apply. `derived` names what a rule filled.

    Nothing is written, and the rows touched are locked only for those
    milliseconds. A change the dry run itself refuses -- an unknown code, no
    date beside a year, a range that ends before it starts, a constraint of
    the database's -- is refused here; `_dry_run_save` lists the refusals of
    Save's it does not make.
    """
    item = _get_item(db, item_id)
    data = payload.changes.model_dump(exclude_unset=True)
    # Attributes have a route and a history of their own; the editor shows
    # its own held set.
    data.pop("attributes", None)
    try:
        _dry_run_save(db, item, data)
        db.flush()
        # Defaults were written by SQL as well as through the ORM: read the
        # item back as the dry run left it.
        db.expire_all()
        return item_detail(db, _get_item(db, item_id))
    except IntegrityError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"These changes could not be saved as they stand: {exc.orig}",
        ) from exc
    finally:
        # Nothing the dry run wrote survives: not the edit, not the defaults,
        # not the version it moved.
        db.rollback()


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
    "rating",
    "year_start",
    "year_end",
    "fineness",
    "gross_weight_ozt",
    "fine_weight_ozt",
    "weight_note",
    "piece_count",
    "item_cost",
    "shipping_cost",
    "tax_rate",
    "tax_includes_shipping",
    "sellers_item_id",
    "listing_url",
)

#: EDITABLE_SCALARS refused by name when sent as null. Every one of these
#: columns is NOT NULL, and an explicit null would otherwise reach the
#: database as a constraint violation -- an unhandled 500 rather than a
#: message a caller can act on. The same reason REQUIRED_CLASSIFIERS exists.
REQUIRED_SCALARS: frozenset[str] = frozenset(
    {
        "source_title",
        "description",
        "piece_count",
        "item_cost",
        "shipping_cost",
        "tax_rate",
        "tax_includes_shipping",
    }
)

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
        "series_id",
        "fineness",
        "gross_weight_ozt",
        "fine_weight_ozt",
    }
)

#: Fields an entry form may fill from `/api/defaults` and
#: report back as suggestions the person left alone.
SUGGESTABLE_FIELDS: frozenset[str] = frozenset(
    {
        "note_type",
        "seal_color",
        "fed_district",
        "signature_combination",
        "metal",
        "series",
    }
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
    holds stays saveable, so an old record can be saved back as it reads
    (see `app.references`). A bulk edit passes none.
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
            changes[field] = _note_scalar_as_stored(field, data)
    face = changes.get("face_plate_number")
    if isinstance(face, str):
        changes["printing_facility"] = _facility(face, data.get("printing_facility"))
    return changes


def _note_scalar_as_stored(field: str, data: dict[str, Any]) -> object:
    """A note's plain field as sent in `data`, in the form it is stored in.

    A series letter is kept trimmed and in capitals, and one sent blank is
    none; a serial sent blank is none (`_serial_or_none`). Every other field
    is stored as sent.
    """
    value = data[field]
    if field == "series_letter" and isinstance(value, str):
        return value.strip().upper() or None
    if field == "serial_number":
        return _serial_or_none(value)
    return value


def _serial_or_none(serial: str | None) -> str | None:
    """A serial as stored: one sent blank is no serial, which is NULL.

    An empty string would read as a recorded serial to every check that asks
    `IS NULL`. What is printed on the note is otherwise kept as sent.
    """
    return serial if serial and serial.strip() else None


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

    The detail row is another table, so a note whose column actually changes
    has its item touched: that is what moves the item's version, which a form
    opened before this save, and an open editor watching for changes made
    elsewhere, both go by.
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
        detail = item.currency_detail
        moved = [c for c, value in changes.items() if getattr(detail, c) != value]
        for column in moved:
            setattr(detail, column, changes[column])
        if moved:
            item.updated_at = datetime.now(UTC)


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
        currency_id = item_kinds.currency_kind_id(db)
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
    currency_id = item_kinds.currency_kind_id(db)
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
        # A plain 422, as the attributes guard below uses.
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
    currency_id = item_kinds.currency_kind_id(db)
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


def _designation_of_the_other_kind(
    sent: object, sides: dict[str, AppliesTo], *, is_currency: bool
) -> str | None:
    """Why a designation does not fit an item of this kind, or None if it does.

    `sent` is the code the item holds once the edit is made. A blank clears
    it, one that fits any kind fits, and an unknown code is `code_to_id`'s
    422 to raise, not a mismatch.
    """
    code = sent if isinstance(sent, str) else None
    side = sides.get(code) if code else None
    if side is None or side == AppliesTo.any:
        return None
    if (side == AppliesTo.currency) == is_currency:
        return None
    owner = "banknotes" if side == AppliesTo.currency else "coins"
    return f"{code} belongs to {owner}"


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
    currency_id = item_kinds.currency_kind_id(db)
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
        mismatch = _designation_of_the_other_kind(
            sent, sides, is_currency=_effective_is_currency(data, item, currency_id)
        )
        if mismatch is not None:
            wrong.append(f"{item.item_code} ({mismatch})")
    if wrong:
        raise HTTPException(
            status_code=422,
            detail=(
                "designation of the other kind: "
                f"{', '.join(sorted(set(wrong)))}. Nothing was changed."
            ),
        )


def _carried_denomination_kinds(
    db: Session, items: Sequence[InventoryItem]
) -> dict[int, DenominationKind]:
    """The kind of every denomination one of these items already carries.

    Read once for the set, so a bare `item_kind` edit can be checked against
    what it would strand without a query per item.
    """
    carried_ids = {item.denomination_id for item in items if item.denomination_id}
    if not carried_ids:
        return {}
    carried = db.scalars(
        select(Denomination).where(Denomination.id.in_(carried_ids))
    ).all()
    return {d.id: d.kind for d in carried}


def _split_from_their_denomination(
    db: Session,
    data: dict[str, object],
    items: Sequence[InventoryItem],
    currency_id: int | None,
    sent_kind: DenominationKind | None,
) -> list[str]:
    """The codes of the items an edit leaves with the other kind's denomination.

    Each item is judged by the denomination it holds once the edit is made:
    the one sent (`sent_kind`; None when it is being cleared), else the one
    it already carries. An item with none conflicts with neither kind.
    """
    sending_denomination = "denomination" in data
    carried_kinds: dict[int, DenominationKind] = {}
    if not sending_denomination:
        carried_kinds = _carried_denomination_kinds(db, items)

    split: list[str] = []
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
        split.append(item.item_code)
    return split


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
    currency_id = item_kinds.currency_kind_id(db)

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

    split = _split_from_their_denomination(db, data, items, currency_id, sent_kind)
    if not split:
        return
    if sending_denomination:
        side = "banknotes" if sent_kind == DenominationKind.note else "coins"
        raise HTTPException(
            status_code=422,
            detail=(
                f"denomination {data['denomination']} belongs to {side}: "
                f"{', '.join(sorted(set(split)))} cannot take it. Nothing "
                "was changed."
            ),
        )
    target = f" to {data['item_kind']!r}" if "item_kind" in data else ""
    raise HTTPException(
        status_code=422,
        detail=(
            f"{', '.join(sorted(set(split)))} already carries a "
            f"denomination for the other kind; clear it before changing "
            f"item_kind{target}. Nothing was changed."
        ),
    )


#: Fields of `InventoryItemUpdate` only a single edit writes: the flag and
#: the coin's own fields are per piece, the certificates are a whole set per
#: item, and `base` belongs to the single edit's field-by-field merge.
_SINGLE_EDIT_ONLY: tuple[str, ...] = (
    "no_date",
    "mint",
    "variety",
    "cert_numbers",
    "base",
)

#: Fields an edit can move without being sent them: a year sent alone moves
#: a single year's end and clears `no_date`, becoming a note empties the
#: years and drops the coin's mint and variety with its detail row, a
#: compound grade sets the strike type, and a face plate names the printing
#: location. The change log is asked about these on every edit, so a value
#: that went is on record with the change that took it.
_MOVED_UNSENT: tuple[str, ...] = (
    "year_start",
    "year_end",
    "no_date",
    "strike_type",
    "printing_facility",
    "mint",
    "variety",
)


def _logged_fields(sent: Iterable[str]) -> list[str]:
    """The fields an edit's change log compares: those sent, then `_MOVED_UNSENT`."""
    fields = list(sent)
    return fields + [field for field in _MOVED_UNSENT if field not in fields]


def _refuse_fine_above_gross(
    db: Session, items: Sequence[InventoryItem], data: dict[str, Any]
) -> list[InventoryItem]:
    """Raise a 422 if the edit leaves an item with more fine weight than gross.

    A piece cannot hold more metal than it weighs, and the database refuses
    the row (`ck_inventory_item_fine_within_gross`). Judged on each item as
    the edit leaves it, so a fine weight sent alone is held against the
    gross weight already stored, and the reverse.

    Returns the items that are no refusal: a fine weight the weight rule
    worked out, which this edit does not send, comes down with a gross
    weight made smaller. The caller empties it (`_empty_worked_out_fine_weight`)
    so the rule works it out again from the new weight.
    """
    if "gross_weight_ozt" not in data and "fine_weight_ozt" not in data:
        return []
    refused: list[str] = []
    worked_out: list[InventoryItem] = []
    for item in items:
        gross = data.get("gross_weight_ozt", item.gross_weight_ozt)
        fine = data.get("fine_weight_ozt", item.fine_weight_ozt)
        if gross is None or fine is None or fine <= gross:
            continue
        rule = derived_fields(db, item.id).get("fine_weight_ozt")
        if "fine_weight_ozt" not in data and rule == WEIGHT:
            worked_out.append(item)
        else:
            refused.append(item.item_code)
    if refused:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=(
                "fine_weight_ozt cannot be more than gross_weight_ozt: "
                f"{', '.join(sorted(refused))}. Nothing was changed."
            ),
        )
    return worked_out


def _empty_worked_out_fine_weight(db: Session, items: Sequence[InventoryItem]) -> None:
    """Empty a rule's fine weight that the new gross weight has fallen below.

    Called once the edit's own values are set. `refresh_items` flushes before
    it works the fine weight out again, and the stale one, larger than the
    new gross weight, is a row the database refuses. Emptied, the rule fills
    it from the new weight; where the edit also took the fineness away there
    is nothing to work it out from, so its mark as the rule's goes too.
    """
    for item in items:
        item.fine_weight_ozt = None
        if item.fineness is None:
            forget(db, [item.id], ["fine_weight_ozt"])


def _refuse_single_edit_fields(data: dict[str, Any]) -> None:
    """Raise a 422 naming the fields sent that only a single edit can set."""
    if "attributes" in data:
        # A whole set, per item: the same set across many items would wipe
        # whatever each carried that the others do not.
        raise HTTPException(
            status_code=422,
            detail="attributes are set one item at a time. Nothing was changed.",
        )
    # Refused by name rather than dropped: nothing in a bulk edit writes any
    # of these, and a 200 would report a change that was never made.
    single_only = sorted(field for field in _SINGLE_EDIT_ONLY if field in data)
    if single_only:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"{', '.join(single_only)}: set one item at a time. "
            "Nothing was changed.",
        )


def _resolved_for_bulk(
    db: Session, data: dict[str, Any]
) -> tuple[dict[str, object], int | None]:
    """The item columns a bulk edit sets, by column, and the status it moves to.

    Every code is resolved here, before anything is set. Status is the one
    classifier with a history table behind it, so it is returned apart from
    the columns: it goes through `set_status` instead of a plain `setattr`,
    the same reason `PATCH /{item_id}` special-cases it. The years are not
    here either; they are worked out per item.
    """
    resolved: dict[str, object] = {}
    status_id: int | None = None
    for field, model in ITEM_CLASSIFIERS.items():
        if field not in data:
            continue
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
    return resolved, status_id


def _years_for_bulk(
    items: Sequence[InventoryItem], data: dict[str, Any]
) -> dict[int, tuple[int | None, int | None] | None]:
    """The years each item holds after a bulk edit, by item id; None if untouched.

    A note holds none. Raises a 422 naming every item the edit would leave
    with a range that ends before it starts, before any is changed.
    """
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
    return years


def _lock_offers_holding(
    db: Session, item_ids: list[int]
) -> offering_writes.LockedForSale | None:
    """Lock the offers holding these items, and the items; None if none is offered.

    For a caller about to change the standing of items that are on sale: the
    rows are taken in the canonical order (`offering_writes.lock_for_sale`)
    before its first write. The read of the offers here only chooses what to
    lock; `_end_offers_holding` reads them again under the locks.
    """
    held_offers = offering_writes.offers_holding(db, item_ids) if item_ids else []
    if not held_offers:
        return None
    return offering_writes.lock_for_sale(
        db,
        listing_ids=[live.id for live in held_offers],
        item_ids=item_ids,
        including_paused=True,
    )


def _set_resolved(items: Sequence[InventoryItem], resolved: dict[str, object]) -> None:
    """Set the same resolved columns on every item."""
    for item in items:
        for column, value in resolved.items():
            setattr(item, column, value)


def _set_years_and_status(
    db: Session,
    item: InventoryItem,
    data: dict[str, Any],
    pair: tuple[int | None, int | None] | None,
    status_id: int | None,
    user_id: int,
) -> None:
    """Give one item of a bulk edit its years, then its status.

    A year dates the piece, and a note never holds `no_date`: its year is
    its series year. The status goes through `set_status`, which keeps the
    move in its history.
    """
    if pair is not None:
        item.year_start, item.year_end = pair
        if pair != (None, None) or _is_note_after(item, data):
            item.no_date = False
    if status_id is not None:
        set_status(db, item, status_id, user_id=user_id)


def _move_if_elsewhere(
    db: Session, item: InventoryItem, to_location: int | None, user_id: int
) -> None:
    """Move an item to where an edit puts it, unless it is already there.

    Through `set_location`, one move in the item's location history. That
    history is another table, so the item is touched: that is what moves its
    version.
    """
    if item.storage_location_id != to_location:
        set_location(
            db,
            item,
            to_location,
            user_id=user_id,
            note="edited in the console",
        )
        item.updated_at = datetime.now(UTC)


def _log_bulk_changes(
    db: Session,
    items: Sequence[InventoryItem],
    before: dict[int, dict[str, Any]],
    sent: list[str],
    user_id: int,
) -> None:
    """Log, per item, each of `sent` whose value a bulk edit moved."""
    for item in items:
        field_changes.record(
            db,
            item.id,
            before[item.id],
            _sent_values(db, item, sent),
            sent,
            user_id=user_id,
        )


@router.post("/bulk", responses={422: unprocessable(_REFUSED_ITEM)})
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
    _refuse_single_edit_fields(data)
    # Where the items are kept moves through `set_location`, one move in
    # each item's location history; it is not a field the writes below
    # set, and like the single edit it is not what the sale warning is for.
    moves = "storage_location_id" in data
    to_location = data.pop("storage_location_id", None)
    if moves and to_location is not None:
        _require_location(db, to_location)
    refuse_null_required(data, REQUIRED_SCALARS)
    # The fields as sent, before `_split_grade` reshapes them: the change log
    # records these, in the editor's own terms, and the fields an edit moves
    # without being sent them.
    sent = _logged_fields(data)
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
    worked_out = _refuse_fine_above_gross(db, items, data)
    if data and not acknowledged:
        sale_state.guard(db, list(items), acknowledged=False)

    # Every code resolved before anything is set, so a typo in the last field
    # does not leave the first three applied.
    resolved, status_id = _resolved_for_bulk(db, data)
    note_changes = _note_changes(db, data)

    # Per item, not once for the set: the same Year moves a single year's end
    # with it and leaves a range's end alone. Checked across every item before
    # any is changed, the same all-or-nothing as the codes above.
    _refuse_note_year(items, data)
    years = _years_for_bulk(items, data)

    # As in `update_item`: a new status or disposition on an offered item
    # ends its offer, the guard above having had the caller acknowledge the
    # sale. Locked before the first write, in the canonical order.
    standing = {item.id: code for item in items if (code := _standing_code(item, data))}
    changing = sorted(standing)
    locked = _lock_offers_holding(db, changing)

    # No version is sent for a set of rows, so one that moves between this
    # request's read and its UPDATE is found only by the database: that is
    # the caller's conflict to retry, with nothing applied.
    with committing(db, _STALE_ITEMS):
        _set_resolved(items, resolved)
        _empty_worked_out_fine_weight(db, worked_out)
        _set_notes_and_detail(
            db, list(items), note_changes, kind_changed="item_kind" in data
        )
        for item in items:
            _set_years_and_status(db, item, data, years[item.id], status_id, admin.id)
            if moves:
                _move_if_elsewhere(db, item, to_location, admin.id)

        if locked is not None:
            _end_offers_holding(
                db,
                changing,
                locked,
                lambda live: _offer_standing_code(db, live, standing),
                "edited to",
            )

        # What a person sets is theirs from now on: no pass refreshes it.
        # What follows from the new facts is refreshed now.
        forget(db, found, [_column(field) for field in data])
        hold(db, found, _emptied(data))
        refresh_items(db, found)

        # Who changed what, per item, in the same transaction -- see
        # update_item (`refresh_items` above has flushed).
        _log_bulk_changes(db, items, before, sent, admin.id)

    return {"updated": len(items)}


def _refuse_null_set(
    payload: InventoryItemUpdate, field: str, value: list[str] | None
) -> None:
    """Raise a 422 if a whole-set field was sent as null; `[]` is what clears it."""
    if field in payload.model_fields_set and value is None:
        raise HTTPException(
            status_code=422,
            detail=f"{field} may not be null; send [] to clear them.",
        )


def _sent_by_the_caller(
    data: dict[str, Any], attributes: list[str] | None, certs: list[str] | None
) -> dict[str, Any]:
    """The fields an edit sent, in the editor's terms, with its two whole sets.

    A copy of `data` as it stands when this is called, so what the
    field-by-field merge and the change log compare is what the caller sent
    and not what later steps reshape.
    """
    sent = dict(data)
    if attributes is not None:
        sent["attributes"] = attributes
    if certs is not None:
        sent["cert_numbers"] = certs
    return sent


def _refuse_lost_update(
    db: Session,
    item: InventoryItem,
    before: dict[str, Any],
    sent: dict[str, Any],
    base: dict[str, Any] | None,
    expected: int | None,
) -> None:
    """Refuse a save that would overwrite a change made since the form opened. 409.

    This is what catches the ordinary lost-update case: two staff, each with
    a form loaded at a different time, and the second one saving over the
    first. The item is re-fetched fresh at the top of every request, so
    nothing later in the request -- including the database's own
    version_id_col check -- ever sees a token from an earlier request; only
    this comparison, against the value the caller actually sent, does.

    With a `base`, the save is merged field by field instead: a change made
    since only stops it where it touched a field this save changes, so two
    people editing different fields of one item both keep their work. The
    version is then not compared -- the base is the finer check -- and the
    version column still guards the narrow window between the request's read
    and its commit.
    """
    if base is not None:
        _refuse_field_conflicts(db, item, before, sent, base)
        return
    refuse_stale_version(
        expected,
        item.version,
        f"{item.item_code} was changed by someone else (you have "
        f"version {expected}, current is {item.version}). Reload and "
        f"reapply your changes.",
    )


def _years_after(
    item: InventoryItem, data: dict[str, Any], *, no_date: bool
) -> tuple[int | None, int | None] | None:
    """The years an item holds after an edit; None if the edit leaves them alone.

    A note holds none, and neither does a piece with no date. Raises a 422
    for a range that would end before it starts, before anything is set.
    """
    years = (
        (None, None)
        if _is_note_after(item, data) or no_date
        else resolve_years((item.year_start, item.year_end), data)
    )
    if years is not None:
        refuse_backwards(years, item.item_code)
    return years


def _set_classifiers(
    db: Session, item: InventoryItem, data: dict[str, Any], user_id: int
) -> None:
    """Resolve and set the item classifiers an edit sends.

    `keep`: a value this item already holds stays saveable after it is
    retired, so the form can save back what it loaded; only a new use of one
    is refused.

    Status is the one classifier with a history table behind it. Going
    through `set_status` is what keeps that table true; a plain setattr
    would leave no history row. Status is a REQUIRED_CLASSIFIER, so
    `require_code` has refused a null code and its id is never None.
    """
    for field, model in ITEM_CLASSIFIERS.items():
        if field not in data:
            continue
        value = data[field]
        resolved: int | None
        held = getattr(item, f"{field}_id")
        if field in REQUIRED_CLASSIFIERS:
            resolved = require_code(db, model, value, field, keep=held)
        else:
            resolved = code_to_id(db, model, value, field, keep=held)
        if field == "status":
            assert resolved is not None
            set_status(db, item, resolved, user_id=user_id)
        else:
            setattr(item, f"{field}_id", resolved)


def _set_scalars(item: InventoryItem, data: dict[str, Any]) -> None:
    """Set the plain fields an edit sends; the years are set apart."""
    for field in EDITABLE_SCALARS:
        if field in data and field not in YEAR_FIELDS:
            setattr(item, field, data[field])


def _set_attributes_sent(
    db: Session, item: InventoryItem, attributes: list[str] | None, user_id: int
) -> None:
    """Make the item's attributes the set an edit sent; none sent changes none.

    A code unknown or of the other kind refuses the edit (422). The links
    are another table. Touching the item is what moves its version, so a
    form opened before this save gets a 409 rather than putting the old set
    back.
    """
    if attributes is None:
        return
    try:
        changed = item_attributes.set_attributes(db, item, attributes, user_id=user_id)
    except item_attributes.AttributeRefused as exc:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if changed:
        item.updated_at = datetime.now(UTC)


@router.patch(
    "/{item_id}",
    response_model=InventoryItemOut,
    responses={422: unprocessable(_REFUSED_ITEM)},
)
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
    _refuse_null_set(payload, "attributes", attributes)
    certs = data.pop("cert_numbers", None)
    _refuse_null_set(payload, "cert_numbers", certs)
    # What the caller sent, before `_split_grade` below reshapes it: the
    # field-by-field merge compares these, in the editor's own terms.
    sent = _sent_by_the_caller(data, attributes, certs)
    # The item as it stands, in those same terms: the merge compares against
    # it, and the change log records it as each changed field's old value.
    before = field_values(db, item)
    # Where it is kept moves through `set_location`, which keeps the move in
    # its location history; it is not a field the generic writes below set.
    moves = "storage_location_id" in data
    to_location = data.pop("storage_location_id", None)
    if moves and to_location is not None:
        _require_location(db, to_location)
    # The coin's own fields live on its detail row, not on the item.
    coin_changes = {f: data.pop(f) for f in COIN_DETAIL_FIELDS if f in data}
    _refuse_coin_detail_on_a_note(data, item, coin_changes, db)
    refuse_null_required(data, REQUIRED_SCALARS)
    _refuse_coin_only_fields(data, [item], db)
    _refuse_mismatched_denomination(data, [item], db)
    _refuse_mismatched_designation(data, [item], db)
    worked_out = _refuse_fine_above_gross(db, [item], data)
    _split_grade(data)

    _refuse_lost_update(db, item, before, sent, base, expected)

    # A change to an item on offer, or in an order that has not shipped,
    # shows to a buyer at once: the caller must say it knows. A coin's own
    # fields were taken out of `data` above and count as much as any other.
    changes = data or coin_changes or attributes is not None or certs is not None
    if changes and not acknowledged:
        sale_state.guard(db, [item], acknowledged=False)

    # Before anything is set: a refused year leaves the item untouched. A
    # note holds none: its series year is its year.
    _refuse_note_year([item], data)
    no_date = _no_date_after(item, data)
    years = _years_after(item, data, no_date=no_date)

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

    _set_classifiers(db, item, data, admin.id)

    _set_notes_and_detail(db, [item], note_changes, kind_changed="item_kind" in data)
    if coin_changes:
        _set_coin_detail(db, item, coin_changes)

    _set_scalars(item, data)
    _empty_worked_out_fine_weight(db, worked_out)
    if years is not None:
        item.year_start, item.year_end = years
    item.no_date = no_date
    # After the classifiers: a kind changed in this request decides which
    # attributes fit.
    _set_attributes_sent(db, item, attributes, admin.id)
    # After the classifiers, so a new certificate takes the grading service
    # this same save sets; another table, so touched like the links above.
    if certs is not None and _set_certifications(db, item, certs):
        item.updated_at = datetime.now(UTC)
    if moves:
        _move_if_elsewhere(db, item, to_location, admin.id)

    try:
        # Inside the try: ending the offers and the refresh both flush, and a
        # version conflict found at either is the same 409 as one found at
        # commit.
        if standing is not None:
            assert locked is not None
            _end_offers_holding(
                db, [item.id], locked, lambda _live: standing, "edited to"
            )

        forget(db, [item.id], [_column(field) for field in data])
        hold(db, [item.id], _emptied(data))
        refresh_items(db, [item.id])
        # Who changed what, in the same transaction as the change: one row
        # per field whose value actually moved -- those sent, and those the
        # edit moves without being sent them (`_MOVED_UNSENT`). The "after"
        # read sees this edit's own writes (attribute links included) under
        # production's autoflush=False because `refresh_items` above has
        # flushed -- measured: an extra flush here changed nothing.
        # A move is kept in the location history already, not again here.
        field_changes.record(
            db,
            item.id,
            before,
            field_values(db, item),
            _logged_fields(f for f in sent if f != "storage_location_id"),
            user_id=admin.id,
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
    reported.** `sales_tax` is a generated column, `round((item_cost +
    shipping_cost where tax_includes_shipping) * tax_rate, 2)`, computed per
    row. The sum of several rounded taxes is not always the rounded tax of
    the sum -- splitting $100 three ways at 6.35%
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

    with committing(db, _STALE_ITEMS):
        try:
            children = split_item(db, parent, pieces, payload.mode)
        except SplitError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc

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


def _item_errors(db: Session, item_id: int) -> list[ItemErrorOut]:
    """Every error recorded against an item, as the API returns them."""
    rows = db.scalars(
        select(ItemError)
        .where(ItemError.inventory_item_id == item_id)
        .order_by(ItemError.error_type_id)
    ).all()
    return [
        ItemErrorOut(
            error_type=code_of(db, ErrorType, row.error_type_id) or "",
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
    after = [
        {"error_type": entry.error_type, "details": entry.details}
        for entry in payload.errors
    ]
    with committing(db, _STALE_ITEMS):
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
        changed = field_changes.record(
            db,
            item.id,
            {"errors": before},
            {"errors": field_changes.sorted_errors(after)},
            ["errors"],
            user_id=admin.id,
        )
        if changed:
            # The errors are another table. Touching the item is what moves
            # its version, which an open editor watches to notice a change
            # made elsewhere; the same set sent again moves nothing.
            item.updated_at = datetime.now(UTC)

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
    ended ones included, and nothing removes a listing row (no endpoint deletes one, and
    `offer_claim` references the row `ON DELETE RESTRICT` anyway). That is the
    intended rule -- once a coin has been offered, the offer is part of the
    sales history -- so the message says so rather than naming a step that
    cannot clear it.

    It asks `sale_state.ever_offered`, not a query of its own:
    `Listing.inventory_item_id == item.id` can never match a lot listing,
    whose `inventory_item_id` is null, so a coin offered only inside a lot
    would be deleted silently. The shared predicate also covers the next
    shape of offer: the claim half it reads is written one row per member.

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

    with committing(db, _STALE_ITEMS):
        item.deleted_at = datetime.now(UTC)


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
    with committing(db, _STALE_ITEMS):
        item.parent_item_id = None
    db.refresh(item)
    return item
