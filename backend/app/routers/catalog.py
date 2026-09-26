"""Catalog endpoints: public reads only.

A catalog entry is a `listing` joined to the `inventory_item` behind it.
Keeping them separate in the database is what lets an item be listed, delisted
and relisted at different prices without rewriting its history -- and what lets
the public catalog show a listing while the item's cost basis and storage
location stay private. The API presents the pair as one resource, because that
is how a shop is actually operated.

A **lot** listing has no `inventory_item` at all: it offers a group of coins
reached through `sales_lot_item`, and the shop shows it as one thing for sale
whose `members` describe what is in it. The join to `inventory_item` is an
outer one for that reason -- it was an inner join, which dropped every lot
listing out of the shop -- and `to_catalog_item` has a branch for each shape.

Writing a listing -- offering an item, changing its price or wording, ending
it -- is `app.routers.offers`. This module used to also create, update and
delete catalog entries; that path let "Manage" create an item and a listing
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
from ..lot_writes import members_held
from ..models import (
    Country,
    CurrencyDetail,
    Grade,
    InventoryItem,
    ItemImage,
    ItemKind,
    Listing,
    Metal,
    SalesLot,
    SalesLotItem,
)
from ..references import code_to_id
from ..schemas import CatalogItemOut, CatalogMemberOut, CatalogPage
from ._resolve import found_or_404
from .images import image_urls

router = APIRouter(prefix="/catalog", tags=["catalog"])


def _item_loads() -> tuple[Any, ...]:
    """The classifier chain one inventory item's public shape reads.

    A function rather than a constant because the same chain hangs off two
    different paths -- the listing's own item, and a lot's members -- and
    each call wants its own loader objects rather than a shared one attached
    twice.
    """
    return (
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
        # the image itself. Without this the catalog would issue one
        # extra query per photographed item.
        selectinload(InventoryItem.images).selectinload(ItemImage.image),
        # A note's year is its series year, read from its currency detail
        # (`_years`): one query for the page rather than one per item.
        selectinload(InventoryItem.currency_detail),
    )


def _eager(stmt: Select[Any]) -> Select[Any]:
    """Load the classifier rows the response needs in one round trip.

    Without this, rendering a page of 24 results costs a query per classifier
    per row -- roughly 170 extra queries for one page.

    A lot listing has no item of its own and reaches its coins through
    `sales_lot_item`, so the same chain hangs off that path too: without it a
    page holding lots costs a query per member per classifier, which is the
    same arithmetic one row further down.
    """
    return stmt.options(
        selectinload(Listing.inventory_item).options(*_item_loads()),
        selectinload(Listing.sales_lot)
        .selectinload(SalesLot.members)
        .selectinload(SalesLotItem.item)
        .options(*_item_loads()),
        selectinload(Listing.currency),
    )


def _code(row: object) -> str | None:
    """A classifier row's code, or None when there is no row."""
    return getattr(row, "code", None)


def version_token(listing: Listing, item: InventoryItem | None) -> str:
    """One token for a resource that is two rows.

    A catalog entry is a listing plus its inventory item, each with its own
    version counter. The client should not have to know that, and checking
    only one of them lets an edit to the other through unnoticed.

    A **lot** listing has no item, and the token is `"<listing.version>.0"`.
    The lot's own version is deliberately not in it: what a buyer is looking
    at is the listing plus its members, and the members cannot change while
    the lot is offered -- `lot_writes` refuses every membership change once a
    lot leaves `assembling`. A second counter that can never move would only
    suggest it might.
    """
    return f"{listing.version}.{item.version if item is not None else 0}"


def _photograph(item: InventoryItem) -> dict[str, str]:
    """The renditions of an item's primary photograph, if one has been chosen.

    Most of a real collection is unphotographed, so this is routinely empty
    and the response says so with nulls rather than a placeholder URL that
    404s.
    """
    primary = next((link for link in item.images if link.is_primary), None)
    return image_urls(primary.image.sha256) if primary else {}


def _years(item: InventoryItem) -> tuple[int | None, int | None]:
    """The years a buyer is shown: a note's are its series year.

    A note holds no year of its own (owner, 2026-09-25) -- its series year is
    its year -- so it is read from the note's detail here, not stored twice.
    """
    detail = item.currency_detail
    if detail is not None and detail.series_year is not None:
        return detail.series_year, detail.series_year
    return item.year_start, item.year_end


def to_catalog_member(item: InventoryItem) -> CatalogMemberOut:
    """Project one coin of a lot into the public shape.

    Field by field, for the reason `to_catalog_item` is: this function is the
    authorization boundary for everything a lot's members put in front of a
    buyer, and the fields it does not name are the ones that stay private.
    """
    urls = _photograph(item)
    return CatalogMemberOut(
        inventory_item_id=item.id,
        item_code=item.item_code,
        title=item.source_title,
        description=item.description,
        item_kind=_code(item.item_kind),
        country=_code(item.country),
        denomination=_code(item.denomination),
        bullion_form=_code(item.bullion_form),
        grade=_code(item.grade),
        strike_type=_code(item.strike_type),
        grade_display=grades.display_item(item),
        grading_service=_code(item.grading_service),
        metal=_code(item.metal),
        year_start=_years(item)[0],
        year_end=_years(item)[1],
        fineness=item.fineness,
        gross_weight_ozt=item.gross_weight_ozt,
        fine_weight_ozt=item.fine_weight_ozt,
        piece_count=item.piece_count,
        thumbnail_url=urls.get("thumbnail_url"),
        image_url=urls.get("image_url"),
    )


def _lot_entry(listing: Listing) -> CatalogItemOut:
    """Project a lot listing into the public shape: one thing, many coins.

    Most item-describing fields keep their defaults, because no single kind,
    grade, metal or year describes a group -- the same reason the list
    query's item filters cannot match a lot. What a buyer gets instead is
    `members`.

    `piece_count` is the **exception**, and is summed rather than left at 1.
    The column means how many objects the row represents (`models/core.py`),
    so 1 on a three-coin lot is a wrong fact rather than a missing one, and
    is indistinguishable from a genuine single-piece entry. It is summed, not
    counted: a member may itself be a multi-piece row -- a roll, a mint set
    -- so `len(members)` would be a second wrong number.

    The members come from `lot_writes.members_held`, which answers "which
    coins was this group made of" -- the past tense on purpose. The live
    question, `offering_writes.offered_items`, answers "none" for a lot that
    has **sold**, because ending a lot releases every membership; a page a
    buyer bookmarked would then show a group with nothing in it. The two
    answers are identical while a lot is on offer, since a membership is
    released only when the lot ends. It also costs nothing: `_eager` has
    already loaded these rows and their classifiers, and `members_held` reads
    that collection rather than querying again.
    """
    lot = listing.sales_lot
    members = (
        []
        if lot is None
        else [to_catalog_member(row.item) for row in members_held(lot)]
    )
    return CatalogItemOut(
        id=listing.id,
        inventory_item_id=None,
        version=version_token(listing, None),
        item_code=None,
        piece_count=sum(member.piece_count for member in members),
        # The offer's own wording first, exactly as for an item, with the
        # lot's own title behind it. `lot` is never None in practice --
        # `ck_listing_item_xor_lot` means a listing with no item has one --
        # but the column is nullable, so this reads it defensively rather
        # than crashing the shop if that ever stops being true.
        title=listing.title or (lot.title if lot is not None else ""),
        description=listing.description or (lot.description if lot is not None else ""),
        price=listing.price,
        currency=_code(listing.currency) or "USD",
        quantity_available=listing.quantity_available,
        is_active=listing.is_active,
        created_at=listing.created_at,
        updated_at=listing.updated_at,
        members=members,
    )


def to_catalog_item(listing: Listing) -> CatalogItemOut:
    """Project a listing and what it offers into the public shape.

    Built field by field on purpose. A `select *` here is how cost basis and
    storage location eventually leak into a customer-facing response -- the
    `public_catalog` view describes the same boundary, but nothing reads it,
    so this function and the tests over it are the boundary.

    A lot listing has no `inventory_item` at all and goes to `_lot_entry`.
    """
    item = listing.inventory_item
    if item is None:
        return _lot_entry(listing)

    urls = _photograph(item)
    return CatalogItemOut(
        id=listing.id,
        inventory_item_id=item.id,
        version=version_token(listing, item),
        item_code=item.item_code,
        thumbnail_url=urls.get("thumbnail_url"),
        image_url=urls.get("image_url"),
        title=listing.title or item.source_title,
        description=listing.description or item.description,
        item_kind=_code(item.item_kind),
        country=_code(item.country),
        denomination=_code(item.denomination),
        bullion_form=_code(item.bullion_form),
        grade=_code(item.grade),
        strike_type=_code(item.strike_type),
        grade_display=grades.display_item(item),
        grading_service=_code(item.grading_service),
        metal=_code(item.metal),
        year_start=_years(item)[0],
        year_end=_years(item)[1],
        fineness=item.fineness,
        gross_weight_ozt=item.gross_weight_ozt,
        fine_weight_ozt=item.fine_weight_ozt,
        piece_count=item.piece_count,
        price=listing.price,
        currency=_code(listing.currency) or "USD",
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
    """Browse the catalog. Withdrawn listings are for administrators only.

    The endpoint itself is public -- the shop has to answer a signed-out
    browser -- but `include_inactive` is not. A withdrawn listing is stock
    the owner took off sale, often because it sold somewhere else, and the
    full set of them is a history of the collection that no buyer is owed.
    """
    if include_inactive and not is_admin(caller):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager privileges required to see withdrawn listings",
        )

    # or_() returns a ColumnElement, which is wider than the
    # BinaryExpression the first append would otherwise pin this to.
    # The shop's catalog is the web store's active fixed-price listings, and
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
    # A note's year is its series year (see `_years`): read from its
    # detail row, through a subquery so the joins below stay as they are.
    year = func.coalesce(
        InventoryItem.year_start,
        select(CurrencyDetail.series_year)
        .where(CurrencyDetail.inventory_item_id == InventoryItem.id)
        .scalar_subquery(),
    )
    if year_min is not None:
        filters.append(year >= year_min)
    if year_max is not None:
        filters.append(year <= year_max)
    if in_stock:
        filters.append(Listing.quantity_available > 0)

    # An **outer** join, because a lot listing's `inventory_item_id` is NULL
    # and an inner join dropped every one of them from the shop outright.
    # The consequence is deliberate rather than tolerated: each of the item
    # filters above (`kind`, `country`, `metal`, `year_min`, `year_max`)
    # compares a column of the missing row, so a lot listing matches none of
    # them and a page filtered by any of them holds no lots. That is the
    # right answer -- a filter on grade or year cannot describe a group of
    # coins that may have several of each. `q` is the exception: it is an
    # `or_` that also matches `Listing.title`, the lot's own wording, so a
    # search still finds one.
    base = select(Listing).outerjoin(
        InventoryItem, Listing.inventory_item_id == InventoryItem.id
    )

    total = (
        db.scalar(
            select(func.count())
            .select_from(Listing)
            .outerjoin(InventoryItem, Listing.inventory_item_id == InventoryItem.id)
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
    return found_or_404(
        db.scalar(_eager(select(Listing).where(Listing.id == listing_id))),
        "Catalog item not found",
    )


@router.get("/{listing_id}")
def get_catalog_item(listing_id: int, db: DbSession) -> CatalogItemOut:
    """One catalog entry. Public: it carries no cost basis or location.

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
            status_code=status.HTTP_404_NOT_FOUND, detail="Catalog item not found"
        )
    return to_catalog_item(listing)
