"""Catalogue endpoints. Reads are public; writes require an administrator.

A catalogue entry is a `listing` joined to the `inventory_item` behind it.
Keeping them separate in the database is what lets an item be listed, delisted
and relisted at different prices without rewriting its history -- and what lets
the public catalogue show a listing while the item's cost basis and storage
location stay private. The API presents the pair as one resource, because that
is how a shop is actually operated.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, selectinload

from ..deps import AdminUser, DbSession
from ..models import (
    Authenticity,
    BullionForm,
    Country,
    Currency,
    Denomination,
    Disposition,
    Grade,
    GradingService,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Listing,
    Metal,
    ProvenanceSource,
    SalesOrderItem,
    StorageForm,
    ValuationBasis,
)
from ..references import code_to_id, require_code
from ..schemas import (
    CatalogItemCreate,
    CatalogItemOut,
    CatalogItemUpdate,
    CatalogPage,
)

router = APIRouter(prefix="/catalog", tags=["catalog"])

#: Classifier fields a client may set, and the table each resolves against.
CLASSIFIERS: dict[str, type] = {
    "item_kind": ItemKind,
    "country": Country,
    "denomination": Denomination,
    "bullion_form": BullionForm,
    "grade": Grade,
    "grading_service": GradingService,
    "metal": Metal,
}

#: Columns on `inventory_item` a client may set directly.
ITEM_SCALARS = (
    "title",
    "description",
    "year_start",
    "year_end",
    "fineness",
    "gross_weight_ozt",
    "fine_weight_ozt",
    "storage_quantity",
)


def _eager(stmt):
    """Load the classifier rows the response needs in one round trip.

    Without this, rendering a page of 24 results costs a query per classifier
    per row -- roughly 170 extra queries for one page.
    """
    return stmt.options(
        selectinload(Listing.inventory_item).options(
            selectinload(InventoryItem.item_kind),
            selectinload(InventoryItem.country),
            selectinload(InventoryItem.denomination),
            selectinload(InventoryItem.bullion_form),
            selectinload(InventoryItem.grade),
            selectinload(InventoryItem.grading_service),
            selectinload(InventoryItem.metal),
        ),
        selectinload(Listing.currency),
    )


def to_catalog_item(listing: Listing) -> CatalogItemOut:
    """Project a listing and its item into the public shape.

    Built field by field on purpose. A `select *` here is how cost basis and
    storage location eventually leak into a customer-facing response.
    """
    item = listing.inventory_item
    code = lambda row: getattr(row, "code", None)  # noqa: E731

    return CatalogItemOut(
        id=listing.id,
        inventory_item_id=item.id,
        title=listing.title or item.title,
        description=listing.description or item.description,
        item_kind=code(item.item_kind),
        country=code(item.country),
        denomination=code(item.denomination),
        bullion_form=code(item.bullion_form),
        grade=code(item.grade),
        grading_service=code(item.grading_service),
        metal=code(item.metal),
        year_start=item.year_start,
        year_end=item.year_end,
        fineness=item.fineness,
        gross_weight_ozt=item.gross_weight_ozt,
        fine_weight_ozt=item.fine_weight_ozt,
        storage_quantity=item.storage_quantity,
        price=listing.price,
        currency=code(listing.currency) or "USD",
        quantity_available=listing.quantity_available,
        is_active=listing.is_active,
        created_at=listing.created_at,
        updated_at=listing.updated_at,
    )


def _resolve_classifiers(db: Session, payload: dict[str, Any]) -> dict[str, int | None]:
    """Turn the classifier codes in a payload into foreign key values."""
    resolved: dict[str, int | None] = {}
    for field, model in CLASSIFIERS.items():
        if field not in payload:
            continue
        code = payload[field]
        if field == "item_kind":
            resolved["item_kind_id"] = require_code(db, model, code, field)
        else:
            resolved[f"{field}_id"] = code_to_id(db, model, code, field)
    return resolved


@router.get("", response_model=CatalogPage)
def list_catalog(
    db: DbSession,
    q: Annotated[str | None, Query(description="Free text over title and description")] = None,
    kind: Annotated[str | None, Query(description="item_kind code")] = None,
    country: Annotated[str | None, Query(description="country code")] = None,
    metal: Annotated[str | None, Query(description="metal code")] = None,
    year_min: int | None = None,
    year_max: int | None = None,
    in_stock: Annotated[bool, Query(description="Only listings with stock")] = False,
    include_inactive: Annotated[
        bool, Query(description="Admin preview of withdrawn listings")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CatalogPage:
    """Browse the catalogue. Withdrawn listings are hidden by default."""
    filters = []
    if not include_inactive:
        filters.append(Listing.is_active.is_(True))
    if q:
        pattern = f"%{q}%"
        filters.append(
            or_(
                Listing.title.ilike(pattern),
                InventoryItem.title.ilike(pattern),
                InventoryItem.description.ilike(pattern),
            )
        )
    if kind:
        filters.append(
            InventoryItem.item_kind_id == code_to_id(db, ItemKind, kind, "kind")
        )
    if country:
        filters.append(
            InventoryItem.country_id == code_to_id(db, Country, country, "country")
        )
    if metal:
        filters.append(InventoryItem.metal_id == code_to_id(db, Metal, metal, "metal"))
    if year_min is not None:
        filters.append(InventoryItem.year_start >= year_min)
    if year_max is not None:
        filters.append(InventoryItem.year_start <= year_max)
    if in_stock:
        filters.append(Listing.quantity_available > 0)

    base = select(Listing).join(InventoryItem, Listing.inventory_item_id == InventoryItem.id)

    total = (
        db.scalar(
            select(func.count())
            .select_from(Listing)
            .join(InventoryItem, Listing.inventory_item_id == InventoryItem.id)
            .where(*filters)
        )
        or 0
    )
    rows = db.scalars(
        _eager(base.where(*filters).order_by(Listing.id.desc()).limit(limit).offset(offset))
    ).all()

    return CatalogPage(
        items=[to_catalog_item(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _get_listing(db: Session, listing_id: int) -> Listing:
    listing = db.scalar(_eager(select(Listing).where(Listing.id == listing_id)))
    if listing is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Catalogue item not found"
        )
    return listing


@router.get("/{listing_id}", response_model=CatalogItemOut)
def get_catalog_item(listing_id: int, db: DbSession) -> CatalogItemOut:
    return to_catalog_item(_get_listing(db, listing_id))


@router.post("", response_model=CatalogItemOut, status_code=status.HTTP_201_CREATED)
def create_catalog_item(
    payload: CatalogItemCreate, db: DbSession, _admin: AdminUser
) -> CatalogItemOut:
    """Create the inventory item and the listing that offers it, together."""
    data = payload.model_dump()
    classifiers = _resolve_classifiers(db, data)

    item = InventoryItem(
        **{field: data[field] for field in ITEM_SCALARS},
        **classifiers,
        # Sensible defaults for an item created through the shop: it is on
        # hand, unverified until someone says otherwise, and listed.
        storage_form_id=require_code(db, StorageForm, "single", "storage_form"),
        authenticity_id=require_code(db, Authenticity, "unverified", "authenticity"),
        status_id=require_code(db, ItemStatus, "received", "status"),
        disposition_id=require_code(db, Disposition, "listed", "disposition"),
        valuation_basis_id=require_code(
            db, ValuationBasis, "numismatic", "valuation_basis"
        ),
        source=ProvenanceSource.manual,
    )
    db.add(item)
    db.flush()

    listing = Listing(
        inventory_item_id=item.id,
        price=data["price"],
        currency_id=require_code(db, Currency, data["currency"], "currency"),
        quantity_available=data["quantity_available"],
        is_active=data["is_active"],
    )
    db.add(listing)
    db.commit()

    return to_catalog_item(_get_listing(db, listing.id))


@router.patch("/{listing_id}", response_model=CatalogItemOut)
def update_catalog_item(
    listing_id: int, payload: CatalogItemUpdate, db: DbSession, _admin: AdminUser
) -> CatalogItemOut:
    listing = _get_listing(db, listing_id)
    item = listing.inventory_item

    # exclude_unset so an omitted field is left alone rather than nulled.
    data = payload.model_dump(exclude_unset=True)

    for field, value in _resolve_classifiers(db, data).items():
        setattr(item, field, value)
    for field in ITEM_SCALARS:
        if field in data:
            setattr(item, field, data[field])

    for field in ("price", "quantity_available", "is_active"):
        if field in data:
            setattr(listing, field, data[field])

    # Withdrawing the last listing puts the item back to simply being held.
    if data.get("is_active") is False:
        item.disposition_id = require_code(db, Disposition, "held", "disposition")
    elif data.get("is_active") is True:
        item.disposition_id = require_code(db, Disposition, "listed", "disposition")

    db.commit()
    return to_catalog_item(_get_listing(db, listing_id))


@router.delete("/{listing_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_catalog_item(listing_id: int, db: DbSession, _admin: AdminUser) -> None:
    """Remove a listing, and the item behind it when nothing else refers to it.

    An item that has been ordered is never deleted: order history must keep
    resolving to what was actually bought.
    """
    listing = _get_listing(db, listing_id)

    ordered = db.scalar(
        select(func.count())
        .select_from(SalesOrderItem)
        .where(SalesOrderItem.listing_id == listing.id)
    )
    if ordered:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This listing appears in existing orders and cannot be deleted. "
                "Set is_active to false to withdraw it from sale."
            ),
        )

    item = listing.inventory_item
    db.delete(listing)
    db.flush()

    # The item outlives the listing when it is still offered elsewhere; an
    # item created through the shop and never ordered goes with it.
    remaining = db.scalar(
        select(func.count())
        .select_from(Listing)
        .where(Listing.inventory_item_id == item.id)
    )
    if not remaining:
        db.delete(item)

    db.commit()
