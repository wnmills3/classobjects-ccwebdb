"""The acquisition side: purchase orders and where items physically sit.

Until now this side of the schema was import-only -- the importer wrote
`purchase_order` and `storage_location` rows, and nothing read them back.
This is what a receiving page reads: which orders still have items on the
way, what is on each one, and where a received item could be put.

Admin-only throughout. What was paid a vendor, and the safe-deposit box an
item sits in, are neither a customer's business.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import and_, case, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from ..deps import AdminUser, DbSession
from ..models import (
    InventoryItem,
    ItemStatus,
    PurchaseOrder,
    StorageLocation,
    Vendor,
    VendorKind,
)
from ..references import code_to_id, require_code
from ..schemas import (
    PurchaseOrderCreate,
    PurchaseOrderDetailOut,
    PurchaseOrderLineOut,
    PurchaseOrderOut,
    StorageLocationOut,
    VendorCreate,
    VendorOut,
)

vendors_router = APIRouter(prefix="/vendors", tags=["acquisitions"])
purchase_orders_router = APIRouter(prefix="/purchase-orders", tags=["acquisitions"])
storage_locations_router = APIRouter(prefix="/storage-locations", tags=["acquisitions"])

_ORDER_NOT_FOUND = "Purchase order not found"

#: A line that should never count towards what is outstanding, what a
#: vendor sent, or what a receiving clerk sees: a soft-deleted row, which
#: never should have existed, and a split parent, which has been replaced by
#: its own children and would otherwise be received twice alongside them.
_ITEM_IS_LIVE = and_(
    InventoryItem.deleted_at.is_(None), InventoryItem.split_at.is_(None)
)

#: `purchase_order.source_url` is imported spreadsheet text, not necessarily
#: a URL -- an eBay listing page, an eBay order page, or the literal word
#: "Gift". Only a value that looks like a web address is ever offered as a
#: link; anything else, `javascript:` included, is withheld.
_WEB_ADDRESS = re.compile(r"^https?://", re.IGNORECASE)


def _vendor_out(db: Session, vendor: Vendor) -> VendorOut:
    """A vendor row, with its kind resolved back to a code for the wire."""
    kind = db.get(VendorKind, vendor.vendor_kind_id) if vendor.vendor_kind_id else None
    return VendorOut(
        id=vendor.id,
        name=vendor.name,
        url=vendor.url,
        vendor_kind=kind.code if kind is not None else None,
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
    "eBay.com" against an existing "ebay.com" gets a 409 naming the vendor
    they meant, rather than a second row the database's own constraint (which
    is case-sensitive) would happily allow.
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

    vendor = Vendor(name=payload.name, url=payload.url, vendor_kind_id=vendor_kind_id)
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
    counts -- `_ITEM_IS_LIVE` sits in the join's `ON` clause rather than a
    `WHERE` on the assembled query, so an order whose only lines are
    non-live still appears (with `outstanding=0, total=0`) instead of being
    dropped by the aggregation entirely.
    """
    outstanding = func.count(
        case((ItemStatus.code.in_(["ordered", "missing"]), InventoryItem.id))
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
            and_(InventoryItem.purchase_order_id == PurchaseOrder.id, _ITEM_IS_LIVE),
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
    `PurchaseOrder.items`, so `_ITEM_IS_LIVE` can be applied in SQL: that
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
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND
        )

    items = db.scalars(
        select(InventoryItem)
        .where(InventoryItem.purchase_order_id == order_id, _ITEM_IS_LIVE)
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
            if order.source_url and _WEB_ADDRESS.match(order.source_url)
            else None
        ),
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


@purchase_orders_router.post("", status_code=status.HTTP_201_CREATED)
def create_purchase_order(
    payload: PurchaseOrderCreate, db: DbSession, admin: AdminUser
) -> PurchaseOrderDetailOut:
    """Start a new purchase: a vendor, and everything else optional.

    Returns the same shape `GET /api/purchase-orders/{id}` does -- built by
    calling that route's own function, so the two can never drift into
    describing a freshly created order differently from an existing one.
    """
    vendor = db.get(Vendor, payload.vendor_id)
    if vendor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown vendor_id: {payload.vendor_id}",
        )

    # A future ordered_on is a data-entry error, not a fact -- the same
    # reasoning `POST /api/inventory/receive` applies to arrived_on, widened
    # by a day so a caller's honest "today" is never refused just because it
    # is ahead of UTC's. See that endpoint's comment for the full argument.
    limit: date = datetime.now(UTC).date() + timedelta(days=1)
    if payload.ordered_on is not None and payload.ordered_on > limit:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"ordered_on {payload.ordered_on.isoformat()} is too far "
            f"in the future. Latest accepted: {limit.isoformat()}.",
        )

    if payload.order_number is not None:
        duplicate = db.scalar(
            select(PurchaseOrder.id).where(
                PurchaseOrder.vendor_id == vendor.id,
                PurchaseOrder.order_number == payload.order_number,
            )
        )
        if duplicate is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"{vendor.name} order {payload.order_number} is "
                f"already recorded",
            )

    order = PurchaseOrder(
        vendor_id=vendor.id,
        order_number=payload.order_number,
        ordered_on=payload.ordered_on,
        source_url=payload.source_url,
        notes=payload.notes,
    )
    db.add(order)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two creations of the same vendor + order number at once both pass
        # the pre-check above; `uq_purchase_order_vendor_number` stops the
        # second at the database, and it should read as the same 409 rather
        # than an unhandled 500.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{vendor.name} order {payload.order_number} is already recorded",
        ) from exc

    return get_purchase_order(order.id, db, admin)


def _location_label(location: StorageLocation) -> str:
    """A human-readable identity for a storage location.

    `institution` and `identifier` are the only free-text fields that tell
    one location apart from another of the same kind; a location with
    neither set falls back to naming its kind.
    """
    parts = [part for part in (location.institution, location.identifier) if part]
    return " ".join(parts) if parts else location.kind.label


@storage_locations_router.get("")
def list_storage_locations(
    db: DbSession, _admin: AdminUser
) -> list[StorageLocationOut]:
    """Every storage location, for choosing where a received item goes.

    Admin-only. `storage_location` is an authorisation boundary, not a
    convention -- a public listing that leaked the safe-deposit box holding
    an item would be a security failure, not a cosmetic one.
    """
    locations = db.scalars(
        select(StorageLocation).options(selectinload(StorageLocation.kind))
    ).all()

    return [
        StorageLocationOut(
            id=location.id,
            label=_location_label(location),
            kind=location.kind.code,
        )
        for location in locations
    ]
