"""Item-level operations, on what you own rather than on what is for sale.

Separate from `/catalog`, which is listing-centric. An item exists before it is
listed and after it is sold, and operations like splitting a lot apply to the
object, not to the offer.

Staff-only throughout: everything here exposes cost basis.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from ..deps import AdminUser, DbSession
from ..inventory_search import VIEWS, count_facets, search
from ..models import (
    Authenticity,
    BullionForm,
    Country,
    Denomination,
    Disposition,
    Grade,
    GradeDesignation,
    GradingService,
    InventoryItem,
    ItemFieldReview,
    ItemKind,
    ItemStatus,
    Listing,
    Metal,
    Series,
    StorageForm,
)
from ..references import code_to_id
from ..schemas import (
    InventoryItemOut,
    InventoryItemUpdate,
    InventoryPageOut,
    ItemReviewOut,
    ReviewRequest,
    SplitPieceIn,
    SplitRequest,
    SplitResultOut,
)
from ..splitting import SplitError, SplitPiece, split_item

router = APIRouter(prefix="/inventory", tags=["inventory"])

#: Per-piece classifier overrides a caller may supply, and where each resolves.
PIECE_CLASSIFIERS: dict[str, type] = {
    "denomination": Denomination,
    "grade": Grade,
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
    for field, model in PIECE_CLASSIFIERS.items():
        value = getattr(spec, field)
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


@router.get("/{view}/search", response_model=InventoryPageOut)
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
            status_code=422,
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
    except KeyError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown filter {exc.args[0]!r} for {view}. Available: "
            f"{sorted(spec.filters)}",
        ) from exc
    except ValueError as exc:
        # Two different failures reach here: an unsortable column, and an
        # unrecognised `deleted` mode. Appending the sortable list to both
        # sends the wrong person looking in the wrong place.
        hint = f" Sortable: {sorted(spec.sortable)}" if "sort" in str(exc) else ""
        raise HTTPException(status_code=422, detail=f"{exc}{hint}") from exc

    return InventoryPageOut(
        view=view,
        rows=rows,
        total=total,
        limit=limit,
        offset=offset,
        sort=sort or spec.default_sort,
        descending=desc,
        facets=count_facets(db, spec, params=params, query=q) if facets else {},
    )


@router.get("/{item_id}", response_model=InventoryItemOut)
def get_item(item_id: int, db: DbSession, _admin: AdminUser) -> InventoryItem:
    """One inventory item, cost basis included. Staff only."""
    return _get_item(db, item_id)


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

#: Plain columns a client may set. Named identically on the wire and in the
#: database, unlike the catalogue router's `ITEM_SCALARS` -- that one is a
#: *mapping*, because the shop says `title` and `price` where the item says
#: `source_title` and `item_cost`. This surface speaks the item's own
#: vocabulary throughout, the same names the Excel round trip uses, so no
#: translation is needed and a tuple is enough. Deliberately not called
#: ITEM_SCALARS: two things with one name in two routers is how the wrong one
#: gets imported.
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
)


@router.patch("/{item_id}", response_model=InventoryItemOut)
def update_item(
    item_id: int,
    payload: InventoryItemUpdate,
    db: DbSession,
    _admin: AdminUser,
) -> InventoryItem:
    """Correct an item. Send `version` to be told about conflicts.

    This is the editing path for the collection. `PATCH /api/catalog/{id}`
    needs a listing, and an item is owned long before it is offered and after
    it is sold -- most of this collection will never have a listing at all.
    """
    item = _get_item(db, item_id)

    # exclude_unset so an omitted field is left alone rather than nulled.
    data = payload.model_dump(exclude_unset=True)
    expected = data.pop("version", None)

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

    for field, model in ITEM_CLASSIFIERS.items():
        if field in data:
            value = data[field]
            setattr(item, f"{field}_id", code_to_id(db, model, value, field))

    for field in EDITABLE_SCALARS:
        if field in data:
            setattr(item, field, data[field])

    try:
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


@router.post("/{item_id}/split", response_model=SplitResultOut)
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
        "grade_id",
        "grade_designation_id",
        "grading_service_id",
        "denomination_id",
        "country_id",
        "metal_id",
        "series_id",
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


@router.get("/{item_id}/reviewed", response_model=ItemReviewOut)
def get_item_review(item_id: int, db: DbSession, _admin: AdminUser) -> ItemReviewOut:
    """Which of this item's fields a person has confirmed."""
    item = _get_item(db, item_id)
    return ItemReviewOut(
        inventory_item_id=item.id, reviewed=_reviewed_fields(db, item.id)
    )


@router.post("/{item_id}/reviewed", response_model=ItemReviewOut)
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
            status_code=422,
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


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: DbSession, _admin: AdminUser) -> None:
    """Soft delete: this row should never have existed.

    Guarded twice, because both failures are silent. A lot with pieces holds
    the cost basis they were allocated from, and deleting it would leave four
    coins descended from nothing. An item that has been listed or sold is
    referenced by order history, which would then point at a row the reports
    exclude.

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

    listed = db.scalar(
        select(Listing.id).where(Listing.inventory_item_id == item.id).limit(1)
    )
    if listed is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{item.item_code} has a listing and cannot be deleted. "
                f"Withdraw the listing first."
            ),
        )

    item.deleted_at = datetime.now(UTC)
    db.commit()
