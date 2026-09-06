"""Item-level operations, on what you own rather than on what is for sale.

Separate from `/catalog`, which is listing-centric. An item exists before it is
listed and after it is sold, and operations like splitting a lot apply to the
object, not to the offer.

Staff-only throughout: everything here exposes cost basis.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..deps import AdminUser, DbSession
from ..models import Denomination, Grade, InventoryItem, Metal, StorageForm
from ..references import code_to_id
from ..schemas import (
    InventoryItemOut,
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
        title=spec.title,
        storage_quantity=spec.storage_quantity,
        relative_value=spec.relative_value,
        overrides=overrides,
    )


@router.get("/{item_id}", response_model=InventoryItemOut)
def get_item(item_id: int, db: DbSession, _admin: AdminUser) -> InventoryItem:
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

    **`price` and `shipping` always reconcile to the penny**, by construction:
    the allocation floors every share and hands the remainder out one cent at a
    time to the parts cut hardest.

    **`total_cost` may differ by a cent or two, and the difference is
    reported.** `taxes` is a generated column, `round((price + shipping) *
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

    allocated_price = sum((c.price for c in children), Decimal("0.00"))
    allocated_shipping = sum((c.shipping for c in children), Decimal("0.00"))
    allocated_total = sum((c.total_cost for c in children), Decimal("0.00"))

    return SplitResultOut(
        parent_item_code=parent.item_code,
        mode=payload.mode,
        parent_price=parent.price,
        allocated_price=allocated_price,
        parent_shipping=parent.shipping,
        allocated_shipping=allocated_shipping,
        parent_total_cost=parent.total_cost,
        allocated_total_cost=allocated_total,
        total_cost_difference=allocated_total - parent.total_cost,
        pieces=[InventoryItemOut.model_validate(c) for c in children],
    )
