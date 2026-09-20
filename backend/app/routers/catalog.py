"""Catalogue endpoints: public reads only.

A catalogue entry is a `listing` joined to the `inventory_item` behind it.
Keeping them separate in the database is what lets an item be listed, delisted
and relisted at different prices without rewriting its history -- and what lets
the public catalogue show a listing while the item's cost basis and storage
location stay private. The API presents the pair as one resource, because that
is how a shop is actually operated.

Writing a listing -- offering an item, changing its price or wording, ending
it -- is `app.routers.offers`. This module used to also create, update and
delete catalogue entries; that path let "Manage" create an item and a listing
together, which contradicted entering nothing outside a purchase, and it could
never offer an item the business already owned. `app.offering_writes` is now
the only writer of `listing.status` and the claims that go with it.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.orm import Session, selectinload

from .. import grades, offering_writes
from ..deps import DbSession, OptionalUser, is_admin
from ..models import (
    Country,
    Grade,
    InventoryItem,
    ItemImage,
    ItemKind,
    Listing,
    Metal,
)
from ..references import code_to_id
from ..schemas import CatalogItemOut, CatalogPage
from .images import image_urls

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _eager(stmt: Select[Any]) -> Select[Any]:
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
            selectinload(InventoryItem.strike_type),
            selectinload(InventoryItem.grade).selectinload(Grade.grade_scale),
            selectinload(InventoryItem.grading_service),
            selectinload(InventoryItem.metal),
            # `.image` as well as the link: the public URL is keyed by the
            # image's content hash, so projecting a row now reads through to
            # the image itself. Without this the catalogue would issue one
            # extra query per photographed item.
            selectinload(InventoryItem.images).selectinload(ItemImage.image),
        ),
        selectinload(Listing.currency),
    )


def version_token(listing: Listing, item: InventoryItem) -> str:
    """One token for a resource that is two rows.

    A catalogue entry is a listing plus its inventory item, each with its own
    version counter. The client should not have to know that, and checking
    only one of them lets an edit to the other through unnoticed.
    """
    return f"{listing.version}.{item.version}"


def to_catalog_item(listing: Listing) -> CatalogItemOut:
    """Project a listing and its item into the public shape.

    Built field by field on purpose. A `select *` here is how cost basis and
    storage location eventually leak into a customer-facing response.
    """
    item = listing.inventory_item
    code = lambda row: getattr(row, "code", None)  # noqa: E731

    # The primary photograph, if one has been chosen. Most of a real
    # collection is unphotographed, so this is routinely absent and the
    # response says so with nulls rather than a placeholder URL that 404s.
    primary = next((link for link in item.images if link.is_primary), None)
    urls = image_urls(primary.image.sha256) if primary else {}

    return CatalogItemOut(
        id=listing.id,
        inventory_item_id=item.id,
        version=version_token(listing, item),
        item_code=item.item_code,
        thumbnail_url=urls.get("thumbnail_url"),
        image_url=urls.get("image_url"),
        title=listing.title or item.source_title,
        description=listing.description or item.description,
        item_kind=code(item.item_kind),
        country=code(item.country),
        denomination=code(item.denomination),
        bullion_form=code(item.bullion_form),
        grade=code(item.grade),
        strike_type=code(item.strike_type),
        grade_display=grades.display_item(item),
        grading_service=code(item.grading_service),
        metal=code(item.metal),
        year_start=item.year_start,
        year_end=item.year_end,
        fineness=item.fineness,
        gross_weight_ozt=item.gross_weight_ozt,
        fine_weight_ozt=item.fine_weight_ozt,
        piece_count=item.piece_count,
        price=listing.price,
        currency=code(listing.currency) or "USD",
        quantity_available=listing.quantity_available,
        is_active=listing.is_active,
        created_at=listing.created_at,
        updated_at=listing.updated_at,
    )


@router.get("")
def list_catalog(
    db: DbSession,
    caller: OptionalUser,
    q: Annotated[
        str | None, Query(description="Free text over title and description")
    ] = None,
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
    """Browse the catalogue. Withdrawn listings are for administrators only.

    The endpoint itself is public -- the shop has to answer a signed-out
    browser -- but `include_inactive` is not. A withdrawn listing is stock
    the owner took off sale, often because it sold somewhere else, and the
    full set of them is a history of the collection that no buyer is owed.
    """
    if include_inactive and not is_admin(caller):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required to see withdrawn listings",
        )

    # or_() returns a ColumnElement, which is wider than the
    # BinaryExpression the first append would otherwise pin this to.
    # The shop's catalogue is the web store's active fixed-price listings, and
    # `app.offering_writes` is where that rule lives -- in this SQL form and
    # in the Python one checkout asks. `include_inactive` is the admin preview
    # of withdrawn listings, which drops only the "active" half.
    filters: list[ColumnElement[bool]] = offering_writes.shop_listing_filters(
        active_only=not include_inactive
    )
    if q:
        pattern = f"%{q}%"
        filters.append(
            or_(
                Listing.title.ilike(pattern),
                InventoryItem.source_title.ilike(pattern),
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

    base = select(Listing).join(
        InventoryItem, Listing.inventory_item_id == InventoryItem.id
    )

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
        _eager(
            base.where(*filters).order_by(Listing.id.desc()).limit(limit).offset(offset)
        )
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


@router.get("/{listing_id}")
def get_catalog_item(listing_id: int, db: DbSession) -> CatalogItemOut:
    """One catalogue entry. Public: it carries no cost basis or location.

    A listing on another platform or sold at auction is not this shop's to
    show -- treated as unknown, the same 404 an unknown id gets, so a caller
    cannot tell "wrong platform" from "does not exist".

    `active_only=False`: a withdrawn listing of the shop's own is still this
    shop's, and is served so a page someone bookmarked can say it has ended
    rather than that it never existed.
    """
    listing = _get_listing(db, listing_id)
    if not offering_writes.sellable_in_shop(listing, active_only=False):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Catalogue item not found"
        )
    return to_catalog_item(listing)
