"""Item-level operations, on what you own rather than on what is for sale.

Separate from `/catalog`, which is listing-centric. An item exists before it is
listed and after it is sold, and operations like splitting a lot apply to the
object, not to the offer.

Staff-only throughout: everything here exposes cost basis.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import AdminUser, DbSession
from ..inventory_search import VIEWS, count_facets, search
from ..models import Denomination, Grade, InventoryItem, Metal, StorageForm
from ..references import code_to_id
from ..schemas import (
    InventoryItemOut,
    InventoryPageOut,
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
        raise HTTPException(
            status_code=422,
            detail=f"{exc} Sortable: {sorted(spec.sortable)}",
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
    )


@router.get("/{item_id}", response_model=InventoryItemOut)
def get_item(item_id: int, db: DbSession, _admin: AdminUser) -> InventoryItem:
    """One inventory item, cost basis included. Staff only."""
    return _get_item(db, item_id)


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
