"""The acquisition side: purchase orders and where items physically sit.

This is what a receiving page reads: which orders still have items on the
way, what is on each one, and where a received item could be put.

Admin-only throughout. What was paid a vendor, and the safe-deposit box an
item sits in, are neither a customer's business.
"""

from __future__ import annotations

import re

from fastapi import APIRouter, HTTPException, Response, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..deps import AdminUser, DbSession
from ..item_history import location_label
from ..live import OUTSTANDING_STATUSES, live_item
from ..models import (
    InventoryItem,
    ItemStatus,
    LocationHistory,
    PurchaseOrder,
    SalesVenue,
    Seller,
    StorageLocation,
    StorageLocationKind,
    Vendor,
    VendorKind,
)
from ..purchases import GENERATED, WEB_ADDRESS
from ..references import code_to_id, require_code
from ..schemas import (
    PurchaseOrderCreate,
    PurchaseOrderDetailOut,
    PurchaseOrderLineOut,
    PurchaseOrderOut,
    PurchaseOrderUpdate,
    SellerCreate,
    SellerOut,
    SellerUpdate,
    StorageLocationCreate,
    StorageLocationOut,
    StorageLocationUpdate,
    VendorCreate,
    VendorOut,
    VendorUpdate,
)
from ._resolve import found_or_404, get_or_404, refuse_future

vendors_router = APIRouter(prefix="/vendors", tags=["acquisitions"])
sellers_router = APIRouter(prefix="/sellers", tags=["acquisitions"])
purchase_orders_router = APIRouter(prefix="/purchase-orders", tags=["acquisitions"])
storage_locations_router = APIRouter(prefix="/storage-locations", tags=["acquisitions"])

_ORDER_NOT_FOUND = "Purchase order not found"

#: A generated order number: `Order-0001`, `Order-0002`, ... (owner,
#: 2026-09-24). A purchase with no number of its own could not be found by
#: one; this gives it one, above the highest already issued. The pattern
#: itself (`GENERATED`) lives in `app.purchases`, alongside `WEB_ADDRESS`,
#: so `dq_purchases` can read the same one rather than restating it.
_GENERATED_PREFIX = "Order-"
#: Held for the rest of the transaction while a number is issued, so two
#: purchases created at once cannot both take the same next number.
_NUMBERING_LOCK = 2026092401


def next_order_number(db: Session) -> str:
    """The next generated order number, `Order-0001` and up."""
    db.execute(select(func.pg_advisory_xact_lock(_NUMBERING_LOCK)))
    issued = db.scalars(
        select(PurchaseOrder.order_number).where(
            PurchaseOrder.order_number.op("~")(GENERATED.pattern)
        )
    ).all()
    highest = max(
        (int(m.group(1)) for n in issued if n and (m := GENERATED.match(n))),
        default=0,
    )
    return f"{_GENERATED_PREFIX}{highest + 1:04d}"


#: The rule for turning a vendor's web address into the plain hostname
#: stored in `Vendor.host`, so every vendor with the same URL has the same
#: host.
_HOST = re.compile(r"https?://([^/]+)")


def _host_of(url: str | None) -> str | None:
    """The lowercase hostname of `url`, or `None` when there isn't one."""
    if not url:
        return None
    match = _HOST.search(url)
    return match.group(1).lower() if match else None


def _counted(count: int, noun: str) -> str:
    """`1 purchase`, `3 purchases`: the count a refusal names."""
    return f"{count} {noun}" if count == 1 else f"{count} {noun}s"


def _vendor_use(db: Session, vendor_id: int) -> int:
    """Purchases and sales platforms that name this vendor."""
    orders = db.scalar(select(func.count()).where(PurchaseOrder.vendor_id == vendor_id))
    venues = db.scalar(select(func.count()).where(SalesVenue.vendor_id == vendor_id))
    return (orders or 0) + (venues or 0)


def _vendor_out(db: Session, vendor: Vendor) -> VendorOut:
    """A vendor row, with its kind resolved back to a code for the wire."""
    kind = db.get(VendorKind, vendor.vendor_kind_id) if vendor.vendor_kind_id else None
    return VendorOut(
        id=vendor.id,
        name=vendor.name,
        url=vendor.url,
        vendor_kind=kind.code if kind is not None else None,
        order_count=_vendor_use(db, vendor.id),
    )


@vendors_router.get("")
def list_vendors(db: DbSession, _admin: AdminUser) -> list[VendorOut]:
    """Every vendor, ordered by name, for picking on the entry panels."""
    vendors = db.scalars(select(Vendor).order_by(Vendor.name)).all()
    return [_vendor_out(db, vendor) for vendor in vendors]


@vendors_router.post("", status_code=status.HTTP_201_CREATED)
def create_vendor(payload: VendorCreate, db: DbSession, _admin: AdminUser) -> VendorOut:
    """Add a vendor inline, while entering a purchase.

    Names are unique -- checked here case-insensitively, so a caller typing
    "eBay.com" against an existing "ebay.com" gets a 409 echoing the name as
    typed ("A vendor named eBay.com already exists"), rather than a second
    row the database's own constraint (which is case-sensitive) would
    happily allow.
    """
    duplicate = db.scalar(
        select(Vendor).where(func.lower(Vendor.name) == payload.name.casefold())
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A vendor named {payload.name} already exists",
        )

    vendor_kind_id = (
        code_to_id(db, VendorKind, payload.vendor_kind, "vendor_kind")
        if payload.vendor_kind is not None
        else require_code(db, VendorKind, "unknown", "vendor_kind")
    )

    vendor = Vendor(
        name=payload.name,
        url=payload.url,
        host=_host_of(payload.url),
        vendor_kind_id=vendor_kind_id,
    )
    db.add(vendor)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two inline "+ Add a vendor..." submissions of the same name at
        # once both pass the case-insensitive check above; `uq_vendor_name`
        # (case-sensitive) stops the second at the database, and it should
        # read as the same 409 rather than an unhandled 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A vendor named {payload.name} already exists",
        ) from exc
    db.refresh(vendor)
    return _vendor_out(db, vendor)


@vendors_router.patch("/{vendor_id}")
def update_vendor(
    vendor_id: int, payload: VendorUpdate, db: DbSession, _admin: AdminUser
) -> VendorOut:
    """Correct a vendor's name, link or kind; only the fields sent change.

    The name stays unique case aside, as `create_vendor` keeps it.
    """
    vendor = get_or_404(db, Vendor, vendor_id, "Vendor not found")
    sent = payload.model_fields_set
    if "name" in sent and payload.name is not None:
        clash = db.scalar(
            select(Vendor.id).where(
                func.lower(Vendor.name) == payload.name.casefold(),
                Vendor.id != vendor.id,
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"A vendor named {payload.name} already exists",
            )
        vendor.name = payload.name
    if "url" in sent:
        vendor.url = payload.url
        vendor.host = _host_of(payload.url)
    if "vendor_kind" in sent:
        vendor.vendor_kind_id = (
            code_to_id(db, VendorKind, payload.vendor_kind, "vendor_kind")
            if payload.vendor_kind is not None
            else require_code(db, VendorKind, "unknown", "vendor_kind")
        )
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A vendor named {vendor.name} already exists",
        ) from exc
    db.refresh(vendor)
    return _vendor_out(db, vendor)


@vendors_router.delete("/{vendor_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_vendor(vendor_id: int, db: DbSession, _admin: AdminUser) -> Response:
    """Delete a vendor nothing names -- a slip made while entering a purchase."""
    vendor = get_or_404(db, Vendor, vendor_id, "Vendor not found")
    used = _vendor_use(db, vendor.id)
    if used:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{vendor.name} is named on "
            f"{_counted(used, 'purchase or platform')}",
        )
    db.delete(vendor)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@purchase_orders_router.get("")
def list_purchase_orders(db: DbSession, _admin: AdminUser) -> list[PurchaseOrderOut]:
    """Every purchase order, with how many of its lines have not yet arrived.

    "Not yet arrived" is `ordered` or `missing` -- a line written off as
    missing is paid for, not cancelled, and sometimes turns up later, so it
    is exactly as outstanding as one still `ordered`. Counting only `ordered`
    would make an order whose sole receivable line is `missing` report
    `outstanding=0` and disappear from `OrderPicker` entirely, with no route
    back to it.

    The counts are computed in SQL, one grouped query for every order, rather
    than by loading each order's items and counting in Python -- an order can
    carry hundreds of lines and this view needs none of them.

    A soft-deleted item and a split lot's parent row are excluded from both
    counts -- `live_item()` sits in the join's `ON` clause rather than a
    `WHERE` on the assembled query, so an order whose only lines are
    non-live still appears (with `outstanding=0, total=0`) instead of being
    dropped by the aggregation entirely.
    """
    outstanding = func.count(
        case((ItemStatus.code.in_(OUTSTANDING_STATUSES), InventoryItem.id))
    )
    total = func.count(InventoryItem.id)

    rows = db.execute(
        select(
            PurchaseOrder.id,
            PurchaseOrder.order_number,
            PurchaseOrder.ordered_on,
            Vendor.name,
            outstanding.label("outstanding"),
            total.label("total"),
        )
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .outerjoin(
            InventoryItem,
            and_(InventoryItem.purchase_order_id == PurchaseOrder.id, live_item()),
        )
        .outerjoin(ItemStatus, ItemStatus.id == InventoryItem.status_id)
        .group_by(
            PurchaseOrder.id,
            PurchaseOrder.order_number,
            PurchaseOrder.ordered_on,
            Vendor.name,
        )
        .order_by(PurchaseOrder.id.desc())
    ).all()

    return [
        PurchaseOrderOut(
            id=row.id,
            order_number=row.order_number,
            vendor=row.name,
            ordered_on=row.ordered_on,
            outstanding=row.outstanding,
            total=row.total,
        )
        for row in rows
    ]


@purchase_orders_router.get("/{order_id}")
def get_purchase_order(
    order_id: int, db: DbSession, _admin: AdminUser
) -> PurchaseOrderDetailOut:
    """One purchase order and every line on it, each with its own status.

    Status is per item, not per order, since split shipments are normal and
    partial receipt must leave the remainder `ordered` -- so the detail view
    lists lines individually rather than folding them into one order status.

    Lines are fetched with their own query rather than through
    `PurchaseOrder.items`, so `live_item()` can be applied in SQL: that
    relationship is unfiltered and shared with other code, so it is not
    touched here. A soft-deleted item and a split lot's superseded parent
    are excluded; a split lot's children are not -- they are what a
    receiving clerk should see and receive instead.
    """
    order = db.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == order_id)
        .options(selectinload(PurchaseOrder.vendor))
    )
    order = found_or_404(order, _ORDER_NOT_FOUND)

    items = db.scalars(
        select(InventoryItem)
        .where(InventoryItem.purchase_order_id == order_id, live_item())
        .options(
            selectinload(InventoryItem.status), selectinload(InventoryItem.item_kind)
        )
        .order_by(InventoryItem.id)
    ).all()

    return PurchaseOrderDetailOut(
        id=order.id,
        order_number=order.order_number,
        vendor=order.vendor.name,
        ordered_on=order.ordered_on,
        source_url=(
            order.source_url
            if order.source_url and WEB_ADDRESS.match(order.source_url)
            else None
        ),
        source_text=order.source_url,
        seller_id=order.seller_id,
        seller=order.seller.name if order.seller is not None else None,
        seller_url=order.seller.store_url if order.seller is not None else None,
        notes=order.notes,
        lines=[
            PurchaseOrderLineOut(
                id=item.id,
                item_code=item.item_code,
                source_title=item.source_title,
                description=item.description,
                item_kind=item.item_kind.code,
                item_cost=item.item_cost,
                status=item.status.code,
            )
            for item in items
        ],
    )


def _seller_id(db: Session, seller_id: int | None) -> int | None:
    """The seller a purchase names, or None; 404 for one that does not exist."""
    if seller_id is None:
        return None
    return get_or_404(db, Seller, seller_id, f"Unknown seller_id: {seller_id}").id


def _refuse_seller_name(db: Session, name: str, keep: int | None) -> None:
    """409 when another seller has this name, whatever its case."""
    clash = db.scalar(
        select(Seller.id).where(
            func.lower(Seller.name) == name.casefold(),
            Seller.id != (keep if keep is not None else -1),
        )
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A seller named {name} already exists",
        )


def _commit_seller(db: Session, name: str) -> None:
    """Commit, reading a racing duplicate name as the same 409."""
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A seller named {name} already exists",
        ) from exc


def _seller_orders(db: Session, seller_id: int) -> int:
    """Purchases that name this seller."""
    return (
        db.scalar(select(func.count()).where(PurchaseOrder.seller_id == seller_id)) or 0
    )


def _seller_out(db: Session, seller: Seller) -> SellerOut:
    """A seller for the wire, with how many purchases name them."""
    return SellerOut(
        id=seller.id,
        name=seller.name,
        store_url=seller.store_url,
        order_count=_seller_orders(db, seller.id),
    )


@sellers_router.get("")
def list_sellers(db: DbSession, _admin: AdminUser) -> list[SellerOut]:
    """Every seller, ordered by name (case aside), with their purchases."""
    counts = dict(
        db.execute(
            select(PurchaseOrder.seller_id, func.count())
            .where(PurchaseOrder.seller_id.is_not(None))
            .group_by(PurchaseOrder.seller_id)
        )
        .tuples()
        .all()
    )
    sellers = db.scalars(select(Seller).order_by(func.lower(Seller.name))).all()
    return [
        SellerOut(
            id=seller.id,
            name=seller.name,
            store_url=seller.store_url,
            order_count=counts.get(seller.id, 0),
        )
        for seller in sellers
    ]


@sellers_router.post("", status_code=status.HTTP_201_CREATED)
def create_seller(payload: SellerCreate, db: DbSession, _admin: AdminUser) -> SellerOut:
    """Add a seller inline, while entering a purchase."""
    _refuse_seller_name(db, payload.name, None)
    seller = Seller(name=payload.name, store_url=payload.store_url)
    db.add(seller)
    _commit_seller(db, payload.name)
    db.refresh(seller)
    return _seller_out(db, seller)


@sellers_router.patch("/{seller_id}")
def update_seller(
    seller_id: int, payload: SellerUpdate, db: DbSession, _admin: AdminUser
) -> SellerOut:
    """Rename a seller or change their store; only the fields sent change."""
    seller = get_or_404(db, Seller, seller_id, "Seller not found")
    sent = payload.model_fields_set
    if "name" in sent and payload.name is not None:
        _refuse_seller_name(db, payload.name, seller.id)
        seller.name = payload.name
    if "store_url" in sent:
        seller.store_url = payload.store_url
    _commit_seller(db, seller.name)
    db.refresh(seller)
    return _seller_out(db, seller)


@sellers_router.delete("/{seller_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_seller(seller_id: int, db: DbSession, _admin: AdminUser) -> Response:
    """Delete a seller no purchase names."""
    seller = get_or_404(db, Seller, seller_id, "Seller not found")
    used = _seller_orders(db, seller.id)
    if used:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{seller.name} is named on {_counted(used, 'purchase')}",
        )
    db.delete(seller)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@purchase_orders_router.post("", status_code=status.HTTP_201_CREATED)
def create_purchase_order(
    payload: PurchaseOrderCreate, db: DbSession, admin: AdminUser
) -> PurchaseOrderDetailOut:
    """Start a new purchase: a vendor, and everything else optional.

    Returns the same shape `GET /api/purchase-orders/{id}` does -- built by
    calling that route's own function, so the two can never drift into
    describing a freshly created order differently from an existing one.
    """
    vendor = get_or_404(
        db, Vendor, payload.vendor_id, f"Unknown vendor_id: {payload.vendor_id}"
    )

    refuse_future("ordered_on", payload.ordered_on)
    number = payload.order_number or next_order_number(db)
    _refuse_duplicate(db, vendor, number, None)

    order = PurchaseOrder(
        vendor_id=vendor.id,
        order_number=number,
        ordered_on=payload.ordered_on,
        source_url=payload.source_url,
        seller_id=_seller_id(db, payload.seller_id),
        notes=payload.notes,
    )
    db.add(order)
    _commit_order(db, vendor, number)
    return get_purchase_order(order.id, db, admin)


@purchase_orders_router.patch("/{order_id}")
def update_purchase_order(
    order_id: int, payload: PurchaseOrderUpdate, db: DbSession, admin: AdminUser
) -> PurchaseOrderDetailOut:
    """Change a purchase's number, date, web address, seller or notes.

    Only the fields sent change. An order number sent blank is given the next
    generated one: a purchase is never left without a number to find it by.
    """
    order = db.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == order_id)
        .options(selectinload(PurchaseOrder.vendor))
    )
    order = found_or_404(order, _ORDER_NOT_FOUND)
    sent = payload.model_fields_set
    if "ordered_on" in sent:
        refuse_future("ordered_on", payload.ordered_on)
        order.ordered_on = payload.ordered_on
    if "order_number" in sent:
        number = payload.order_number or next_order_number(db)
        _refuse_duplicate(db, order.vendor, number, order.id)
        order.order_number = number
    if "source_url" in sent:
        order.source_url = payload.source_url
    if "seller_id" in sent:
        order.seller_id = _seller_id(db, payload.seller_id)
    if "notes" in sent:
        order.notes = payload.notes
    _commit_order(db, order.vendor, order.order_number)
    return get_purchase_order(order.id, db, admin)


def _refuse_duplicate(
    db: Session, vendor: Vendor, number: str, own_id: int | None
) -> None:
    """409 when this vendor already has another purchase with this number."""
    duplicate = db.scalar(
        select(PurchaseOrder.id).where(
            PurchaseOrder.vendor_id == vendor.id,
            PurchaseOrder.order_number == number,
            PurchaseOrder.id != (own_id if own_id is not None else -1),
        )
    )
    if duplicate is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{vendor.name} order {number} is already recorded",
        )


def _commit_order(db: Session, vendor: Vendor, number: str | None) -> None:
    """Commit; the unique index's refusal reads as the same 409.

    Two saves of the same vendor + number at once both pass the pre-check;
    `uq_purchase_order_vendor_number` stops the second at the database, and
    it should read as a 409 rather than an unhandled 500.
    """
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{vendor.name} order {number} is already recorded",
        ) from exc


#: Location kinds made by other code: a consignment by the auction code,
#: a sold item's by the sale. Not added from a picker.
_MADE_ELSEWHERE = frozenset({"consigned", "sold"})


@storage_locations_router.post("", status_code=status.HTTP_201_CREATED)
def create_storage_location(
    payload: StorageLocationCreate, db: DbSession, _admin: AdminUser
) -> StorageLocationOut:
    """Add a place items are kept: a bank box, a safe, home.

    409 for a location that already exists -- the same kind, institution and
    identifier, case aside -- so one box is never two rows items split
    between.
    """
    if payload.kind in _MADE_ELSEWHERE:
        raise HTTPException(
            status_code=422,
            detail=f"A {payload.kind} location is made by the auction or sale code",
        )
    kind_id = require_code(db, StorageLocationKind, payload.kind, "kind")
    same = func.lower(func.coalesce(StorageLocation.institution, "")) == (
        (payload.institution or "").casefold()
    )
    same_box = func.lower(func.coalesce(StorageLocation.identifier, "")) == (
        (payload.identifier or "").casefold()
    )
    exists = db.scalar(
        select(StorageLocation.id).where(
            StorageLocation.storage_location_kind_id == kind_id, same, same_box
        )
    )
    if exists is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That storage location already exists",
        )
    location = StorageLocation(
        storage_location_kind_id=kind_id,
        institution=payload.institution,
        identifier=payload.identifier,
        notes=payload.notes,
    )
    db.add(location)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That storage location already exists",
        ) from exc
    db.refresh(location)
    return _location_out(db, location)


def _location_use(db: Session, location_id: int) -> int:
    """Items kept there now, and moves recorded to or from it before."""
    items = db.scalar(
        select(func.count()).where(InventoryItem.storage_location_id == location_id)
    )
    history = db.scalar(
        select(func.count()).where(LocationHistory.storage_location_id == location_id)
    )
    return (items or 0) + (history or 0)


def _location_out(
    db: Session, location: StorageLocation, item_count: int | None = None
) -> StorageLocationOut:
    """A location for the wire: its label, its parts, and how much it holds."""
    return StorageLocationOut(
        id=location.id,
        label=location_label(location),
        kind=location.kind.code,
        institution=location.institution,
        identifier=location.identifier,
        notes=location.notes,
        item_count=_location_use(db, location.id) if item_count is None else item_count,
    )


def _refuse_same_location(
    db: Session,
    kind_id: int,
    institution: str | None,
    identifier: str | None,
    keep: int,
) -> None:
    """409 when another location has this kind, institution and identifier."""
    clash = db.scalar(
        select(StorageLocation.id).where(
            StorageLocation.storage_location_kind_id == kind_id,
            func.lower(func.coalesce(StorageLocation.institution, ""))
            == (institution or "").casefold(),
            func.lower(func.coalesce(StorageLocation.identifier, ""))
            == (identifier or "").casefold(),
            StorageLocation.id != keep,
        )
    )
    if clash is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That storage location already exists",
        )


@storage_locations_router.patch("/{location_id}")
def update_storage_location(
    location_id: int, payload: StorageLocationUpdate, db: DbSession, _admin: AdminUser
) -> StorageLocationOut:
    """Correct a location's kind, institution, identifier or notes.

    Only what is sent changes. A location the auction or sale code made
    (`consigned`, `sold`) is that code's, and is not edited by hand.
    """
    location = get_or_404(
        db, StorageLocation, location_id, "Storage location not found"
    )
    if location.kind.code in _MADE_ELSEWHERE or payload.kind in _MADE_ELSEWHERE:
        raise HTTPException(
            status_code=422,
            detail="A consigned or sold location is made by the auction or sale code",
        )
    sent = payload.model_fields_set
    kind_id = (
        require_code(db, StorageLocationKind, payload.kind, "kind")
        if "kind" in sent and payload.kind is not None
        else location.storage_location_kind_id
    )
    institution = payload.institution if "institution" in sent else location.institution
    identifier = payload.identifier if "identifier" in sent else location.identifier
    _refuse_same_location(db, kind_id, institution, identifier, location.id)
    location.storage_location_kind_id = kind_id
    location.institution = institution
    location.identifier = identifier
    if "notes" in sent:
        location.notes = payload.notes
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That storage location already exists",
        ) from exc
    db.refresh(location)
    return _location_out(db, location)


@storage_locations_router.delete(
    "/{location_id}", status_code=status.HTTP_204_NO_CONTENT
)
def delete_storage_location(
    location_id: int, db: DbSession, _admin: AdminUser
) -> Response:
    """Delete a location nothing is, or ever was, kept in.

    One an item has been in is part of that item's history, so it stays.
    """
    location = get_or_404(
        db, StorageLocation, location_id, "Storage location not found"
    )
    used = _location_use(db, location.id)
    if used:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{location_label(location)} holds or held "
            f"{_counted(used, 'item record')}",
        )
    db.delete(location)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@storage_locations_router.get("")
def list_storage_locations(
    db: DbSession, _admin: AdminUser
) -> list[StorageLocationOut]:
    """Every storage location, for choosing where a received item goes.

    Admin-only. `storage_location` is an authorization boundary, not a
    convention -- a public listing that leaked the safe-deposit box holding
    an item would be a security failure, not a cosmetic one.
    """
    locations = db.scalars(
        select(StorageLocation).options(selectinload(StorageLocation.kind))
    ).all()

    return [_location_out(db, location) for location in locations]
