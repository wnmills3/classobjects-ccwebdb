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
from ..inventory_search import (
    VIEWS,
    UnknownIssue,
    count_facets,
    count_issues,
    plain,
    search,
)
from ..lifecycle_writes import set_location, set_status
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
    StorageLocation,
)
from ..references import code_to_id, require_code
from ..schemas import (
    RECEIVE_OUTCOMES,
    BulkEditRequest,
    InventoryItemOut,
    InventoryItemUpdate,
    InventoryPageOut,
    ItemDetailOut,
    ItemReviewOut,
    ReceiveRequest,
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


@router.post("/receive")
def receive_items(
    payload: ReceiveRequest, db: DbSession, admin: AdminUser
) -> dict[str, int]:
    """Record what arrived, for one item or a whole box of them.

    All or nothing, in one transaction, the same as `POST /bulk` and for the
    same reason: a partial receipt across twenty coins leaves a state nobody
    can describe, and "which of the twenty applied?" is not a question the UI
    should have to answer. Every id is resolved and every code checked before
    anything is written.
    """
    if payload.outcome not in RECEIVE_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown outcome {payload.outcome!r}. "
            f"Known: {sorted(RECEIVE_OUTCOMES)}",
        )

    items = db.scalars(
        select(InventoryItem).where(InventoryItem.id.in_(payload.item_ids))
    ).all()
    missing = sorted(set(payload.item_ids) - {i.id for i in items})
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown item ids: {missing}")

    received_id = require_code(db, ItemStatus, "received", "status")
    already = [i.item_code for i in items if i.status_id == received_id]
    if already and payload.outcome == "received":
        raise HTTPException(
            status_code=409,
            detail=f"Already received: {sorted(already)}. "
            "Use PATCH to correct a receipt rather than repeating it.",
        )

    if payload.storage_location_id is not None:
        exists = db.get(StorageLocation, payload.storage_location_id)
        if exists is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown storage_location_id: {payload.storage_location_id}",
            )

    to_status = require_code(db, ItemStatus, payload.outcome, "status")
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

    db.commit()
    return {"received": len(items)}


@router.get("/{item_id}")
def get_item(item_id: int, db: DbSession, _admin: AdminUser) -> ItemDetailOut:
    """One item, with what its lot claimed and what has been confirmed.

    Both in one response because the edit form needs both on every field, and
    three round trips per coin is three per coin across 7,591 of them.

    Carries every field EDITABLE_SCALARS and ITEM_CLASSIFIERS accept, not a
    hand-picked subset -- see test_the_detail_payload_covers_every_editable_field.
    A field the client can set but this endpoint never returns renders blank
    in the form regardless of what is stored, which is how a coin already
    holding MS65 shows an empty Grade box and gets overwritten with nothing.
    """
    item = _get_item(db, item_id)

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
                "piece_count",
                "item_cost",
                "shipping_cost",
                "sales_tax",
                "total_cost",
                "parent_item_id",
                "split_at",
            )
        },
        **classifiers,
        parent_item_code=parent_code,
        lot_claims=claims,
        reviewed=_reviewed_fields(db, item.id),
    )


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

#: The subset of ITEM_CLASSIFIERS whose column is NOT NULL.
#:
#: `code_to_id` returns None for a null or empty code, and setting a NOT NULL
#: foreign key to None is an IntegrityError from the database -- an unhandled
#: 500, not a message a caller can act on. `catalog.py` already guards
#: `item_kind` this way for the same reason; this mirrors it for every
#: required classifier on this router, not only that one.
REQUIRED_CLASSIFIERS: frozenset[str] = frozenset(
    {"item_kind", "storage_form", "authenticity", "status", "disposition"}
)

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
        if field in data:
            resolved[field] = data[field]

    for item in items:
        for column, value in resolved.items():
            setattr(item, column, value)
        if status_id is not None:
            set_status(db, item, status_id, user_id=admin.id)

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
            resolved: int | None
            if field in REQUIRED_CLASSIFIERS:
                resolved = require_code(db, model, value, field)
            else:
                resolved = code_to_id(db, model, value, field)
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


@router.delete("/{item_id}/parent", response_model=InventoryItemOut)
def detach_item(item_id: int, db: DbSession, _admin: AdminUser) -> InventoryItem:
    """Set an item's `parent_item_id` back to null.

    An item with no parent is complete, not orphaned -- 7,591 of 7,591 have
    none. So this moves nothing and repairs nothing: the piece keeps the cost
    it was allocated, and simply stops recording where it came from.

    Idempotent, because the end state is exactly what was asked for.
    """
    item = _get_item(db, item_id)
    item.parent_item_id = None
    db.commit()
    db.refresh(item)
    return item
