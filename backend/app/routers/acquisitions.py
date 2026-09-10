"""The acquisition side: purchase orders and where items physically sit.

Until now this side of the schema was import-only -- the importer wrote
`purchase_order` and `storage_location` rows, and nothing read them back.
This is what a receiving page reads: which orders still have items on the
way, what is on each one, and where a received item could be put.

Admin-only throughout. What was paid a vendor, and the safe-deposit box an
item sits in, are neither a customer's business.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import case, func, select
from sqlalchemy.orm import selectinload

from ..deps import AdminUser, DbSession
from ..models import InventoryItem, ItemStatus, PurchaseOrder, StorageLocation, Vendor
from ..schemas import (
    PurchaseOrderDetailOut,
    PurchaseOrderLineOut,
    PurchaseOrderOut,
    StorageLocationOut,
)

purchase_orders_router = APIRouter(prefix="/purchase-orders", tags=["acquisitions"])
storage_locations_router = APIRouter(prefix="/storage-locations", tags=["acquisitions"])

_ORDER_NOT_FOUND = "Purchase order not found"


@purchase_orders_router.get("")
def list_purchase_orders(db: DbSession, _admin: AdminUser) -> list[PurchaseOrderOut]:
    """Every purchase order, with how many of its lines are still outstanding.

    The counts are computed in SQL, one grouped query for every order, rather
    than by loading each order's items and counting in Python -- an order can
    carry hundreds of lines and this view needs none of them.
    """
    outstanding = func.count(case((ItemStatus.code == "ordered", InventoryItem.id)))
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
        .outerjoin(InventoryItem, InventoryItem.purchase_order_id == PurchaseOrder.id)
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
    """
    order = db.scalar(
        select(PurchaseOrder)
        .where(PurchaseOrder.id == order_id)
        .options(
            selectinload(PurchaseOrder.vendor),
            selectinload(PurchaseOrder.items).selectinload(InventoryItem.status),
        )
    )
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND
        )

    return PurchaseOrderDetailOut(
        id=order.id,
        order_number=order.order_number,
        vendor=order.vendor.name,
        ordered_on=order.ordered_on,
        lines=[
            PurchaseOrderLineOut(
                id=item.id,
                item_code=item.item_code,
                description=item.description,
                item_cost=item.item_cost,
                status=item.status.code,
            )
            for item in order.items
        ],
    )


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
