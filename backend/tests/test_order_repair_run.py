"""Characterisation tests for `order_repair.run`.

`identify` was already covered; `run` -- the function that actually rewrites
the purchase orders -- was not, which left the riskiest code in the module
unprotected. These pin its behaviour so it can be refactored safely.

Written against the code as it stands, deliberately: their job is to detect a
change, not to assert what the behaviour ought to be.
"""

from __future__ import annotations

from app.importers.models import ImportBatch, ImportRow
from app.models import InventoryItem, PurchaseOrder, Vendor
from app.order_repair import run
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item

# Links whose identifiers test_order_repair already pins.
HIBID = "https://hibid.com/lot/226778844/1886-morgan"
PROXIBID = "https://www.proxibid.com/lotinformation/91896928/1928p-gold"
UNKNOWN = "https://example.com/some/page"


def _vendor(db: Session, name: str) -> Vendor:
    vendor = Vendor(name=name)
    db.add(vendor)
    db.flush()
    return vendor


def _fabricated(db: Session, vendor: Vendor) -> PurchaseOrder:
    """An order with no number -- the shape this module exists to remove."""
    order = PurchaseOrder(vendor_id=vendor.id, order_number=None)
    db.add(order)
    db.flush()
    return order


def _batch(db: Session) -> ImportBatch:
    batch = ImportBatch(
        source_path="fixture.xlsx",
        source_kind="xlsx",
        sha256="0" * 64,
        profile_name="collection_v1",
        mode="commit",
    )
    db.add(batch)
    db.flush()
    return batch


def _item(
    db: Session, order: PurchaseOrder, batch: ImportBatch, link: str | None, n: int
) -> InventoryItem:
    item = build_item(db, purchase_order_id=order.id)
    db.add(
        ImportRow(
            batch_id=batch.id,
            row_number=n,
            raw={"Link": link} if link is not None else {},
            inventory_item_id=item.id,
        )
    )
    db.flush()
    return item


def test_nothing_to_do_when_no_order_was_fabricated(db: Session) -> None:
    vendor = _vendor(db, "Real Vendor")
    order = PurchaseOrder(vendor_id=vendor.id, order_number="REAL-1")
    db.add(order)
    db.flush()
    assert run(db, commit=False) == {}


def test_a_dry_run_counts_without_writing(db: Session) -> None:
    vendor = _vendor(db, "Auction House")
    bad = _fabricated(db, vendor)
    batch = _batch(db)
    item = _item(db, bad, batch, HIBID, 1)

    stats = run(db, commit=False)

    assert stats["order_number"] == 1
    assert stats["orders_created"] == 1
    # Nothing moved and nothing was created: the item still points at the
    # fabricated order, which still exists.
    db.refresh(item)
    assert item.purchase_order_id == bad.id
    assert db.get(PurchaseOrder, bad.id) is not None


def test_a_commit_moves_the_item_and_removes_the_empty_order(db: Session) -> None:
    vendor = _vendor(db, "Auction House")
    bad = _fabricated(db, vendor)
    batch = _batch(db)
    item = _item(db, bad, batch, HIBID, 1)

    stats = run(db, commit=True)

    assert stats["orders_created"] == 1
    assert stats["fabricated_removed"] == 1
    db.refresh(item)
    assert item.purchase_order_id != bad.id
    assert db.get(PurchaseOrder, bad.id) is None

    moved_to = db.get(PurchaseOrder, item.purchase_order_id)
    assert moved_to.order_number == "226778844"
    assert moved_to.source_url == HIBID


def test_two_items_from_one_transaction_share_one_order(db: Session) -> None:
    vendor = _vendor(db, "Auction House")
    bad = _fabricated(db, vendor)
    batch = _batch(db)
    first = _item(db, bad, batch, HIBID, 1)
    second = _item(db, bad, batch, HIBID, 2)

    stats = run(db, commit=True)

    assert stats["orders_created"] == 1
    db.refresh(first)
    db.refresh(second)
    assert first.purchase_order_id == second.purchase_order_id


def test_the_same_identifier_from_two_vendors_stays_two_orders(db: Session) -> None:
    # The key is (vendor, identifier): two houses can issue the same lot
    # number without it being one purchase.
    batch = _batch(db)
    a = _fabricated(db, _vendor(db, "House A"))
    b = _fabricated(db, _vendor(db, "House B"))
    first = _item(db, a, batch, HIBID, 1)
    second = _item(db, b, batch, HIBID, 2)

    stats = run(db, commit=True)

    assert stats["orders_created"] == 2
    db.refresh(first)
    db.refresh(second)
    assert first.purchase_order_id != second.purchase_order_id


def test_a_row_with_no_identifier_is_left_without_an_order(db: Session) -> None:
    vendor = _vendor(db, "Auction House")
    bad = _fabricated(db, vendor)
    batch = _batch(db)
    item = _item(db, bad, batch, UNKNOWN, 1)

    stats = run(db, commit=True)

    assert stats["no_identifier"] == 1
    assert stats["orders_created"] == 0
    db.refresh(item)
    # No order at all beats an invented one.
    assert item.purchase_order_id is None


def test_a_listing_id_is_counted_apart_from_an_order_number(db: Session) -> None:
    vendor = _vendor(db, "Auction House")
    bad = _fabricated(db, vendor)
    batch = _batch(db)
    _item(db, bad, batch, PROXIBID, 1)

    stats = run(db, commit=True)

    assert stats["order_number"] == 1
    created = db.scalars(
        select(PurchaseOrder).where(PurchaseOrder.order_number == "91896928")
    ).all()
    assert len(created) == 1


def test_a_fabricated_order_still_holding_items_is_kept(db: Session) -> None:
    vendor = _vendor(db, "Auction House")
    bad = _fabricated(db, vendor)
    batch = _batch(db)
    _item(db, bad, batch, HIBID, 1)
    # A second item on the same fabricated order with no ImportRow: run() never
    # sees it, so the order must not be deleted out from under it.
    orphan = build_item(db, purchase_order_id=bad.id)

    run(db, commit=True)

    assert db.get(PurchaseOrder, bad.id) is not None
    db.refresh(orphan)
    assert orphan.purchase_order_id == bad.id
    remaining = db.scalars(
        select(InventoryItem.id).where(InventoryItem.purchase_order_id == bad.id)
    ).all()
    assert remaining == [orphan.id]
