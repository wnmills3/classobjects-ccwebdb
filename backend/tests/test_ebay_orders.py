"""Order numbers and listing ids from eBay's purchase history (`app.ebay_orders`).

The history is a workbook of lines -- OrderNumber, OrderDate, ItemID, ... --
built here in miniature. The purchases are made the way the collection's
were: one per eBay listing, linking it, some with a number and most without.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from app import ebay_orders
from app.models import InventoryItem, ItemFieldChange, PurchaseOrder, User, Vendor
from fastapi.testclient import TestClient
from openpyxl import Workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.conftest import build_item

HEADER = [
    "OrderNumber",
    "OrderDate",
    "ItemID",
    "Seller",
    "ItemName",
    "ItemPrice",
    "Currency",
    "Quantity",
    "OrderTotal",
]


def _history(tmp_path: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    """A purchase-history workbook: (order, date, item id, name) per line."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(HEADER)
    for order, day, item_id, name in rows:
        sheet.append(
            [order, day, item_id, "seller", name, "10", "USD", "1", "US $10.00"]
        )
    path = tmp_path / "Ebay_Purchase_History_2025.xlsx"
    book.save(path)
    return path


def _ebay(db: Session) -> Vendor:
    vendor = db.scalar(select(Vendor).where(Vendor.name == ebay_orders.EBAY))
    if vendor is None:
        vendor = Vendor(name=ebay_orders.EBAY)
        db.add(vendor)
        db.flush()
    return vendor


def _purchase(
    db: Session, item_id: str, on: date, number: str | None = None, pieces: int = 1
) -> PurchaseOrder:
    """A purchase of one listing, linking it, with `pieces` items from it."""
    url = f"https://www.ebay.com/itm/{item_id}"
    order = PurchaseOrder(
        vendor_id=_ebay(db).id, order_number=number, ordered_on=on, source_url=url
    )
    db.add(order)
    db.flush()
    for _ in range(pieces):
        item = build_item(db, purchase_order_id=order.id)
        item.listing_url = url
    db.flush()
    return order


@pytest.fixture(autouse=True)
def _someone_to_log_under(admin_user: User) -> None:
    """Every change is logged under a person: the pass needs an account."""


def _admin(db: Session) -> int:
    user_id = db.scalar(select(User.id).order_by(User.id).limit(1))
    assert user_id is not None
    return user_id


def _run(db: Session, path: Path) -> ebay_orders.Plan:
    todo = ebay_orders.plan(db, ebay_orders.read_history([path]))
    ebay_orders.apply(db, todo, _admin(db))
    db.flush()
    db.expire_all()
    return todo


DAY = date(2025, 3, 4)


def test_an_unnumbered_purchase_takes_its_listings_order(
    db: Session, tmp_path: Path
) -> None:
    order = _purchase(db, "111111111111", DAY, pieces=2)
    path = _history(
        tmp_path, [("11-11111-11111", "Mar 04, 2025", "111111111111", "a lot")]
    )
    _run(db, path)

    refreshed = db.get(PurchaseOrder, order.id)
    assert refreshed is not None and refreshed.order_number == "11-11111-11111"
    items = db.scalars(
        select(InventoryItem).where(InventoryItem.purchase_order_id == order.id)
    ).all()
    assert [i.sellers_item_id for i in items] == ["111111111111"] * 2
    # Both the number and the listing id are in each item's History.
    logged = db.scalars(
        select(ItemFieldChange.field_name).where(
            ItemFieldChange.inventory_item_id.in_([i.id for i in items])
        )
    ).all()
    assert sorted(logged) == ["order_number"] * 2 + ["sellers_item_id"] * 2


def test_an_order_of_several_listings_becomes_one_purchase(
    db: Session, tmp_path: Path
) -> None:
    first = _purchase(db, "222222222221", DAY)
    second = _purchase(db, "222222222222", DAY, pieces=2)
    # Loaded, as any caller that looked at the purchase would have it: deleting
    # a parent whose loaded list still holds the moved items nulls their
    # purchase, which the pass guards against (`db.expire` before the delete).
    assert len(second.items) == 2
    path = _history(
        tmp_path,
        [
            ("22-22222-22222", "Mar 04, 2025", "222222222221", "one"),
            ("22-22222-22222", "Mar 04, 2025", "222222222222", "two"),
        ],
    )
    todo = _run(db, path)
    assert todo.orders["22-22222-22222"] == (first.id, [second.id])

    survivor = db.get(PurchaseOrder, first.id)
    assert survivor is not None and survivor.order_number == "22-22222-22222"
    assert survivor.source_url == ebay_orders.ORDER_PAGE.format("22-22222-22222")
    assert db.get(PurchaseOrder, second.id) is None
    items = db.scalars(
        select(InventoryItem)
        .where(InventoryItem.purchase_order_id == first.id)
        .order_by(InventoryItem.id)
    ).all()
    # Every item moved, none orphaned, each keeping its own listing's id.
    assert [i.sellers_item_id for i in items] == [
        "222222222221",
        "222222222222",
        "222222222222",
    ]
    orphans = db.scalar(
        select(InventoryItem.id).where(InventoryItem.purchase_order_id.is_(None))
    )
    assert orphans is None


def test_a_listing_of_an_order_already_recorded_joins_that_purchase(
    db: Session, tmp_path: Path
) -> None:
    held = _purchase(db, "333333333331", DAY, number="33-33333-33333")
    loose = _purchase(db, "333333333332", DAY)
    path = _history(
        tmp_path,
        [
            ("33-33333-33333", "Mar 04, 2025", "333333333331", "one"),
            ("33-33333-33333", "Mar 04, 2025", "333333333332", "two"),
        ],
    )
    _run(db, path)
    assert db.get(PurchaseOrder, loose.id) is None
    moved = db.scalars(
        select(InventoryItem.purchase_order_id).where(
            InventoryItem.sellers_item_id == "333333333332"
        )
    ).all()
    assert moved == [held.id]


def test_what_cannot_be_decided_is_left_and_listed(db: Session, tmp_path: Path) -> None:
    twice = _purchase(db, "444444444441", DAY)
    absent = _purchase(db, "444444444449", DAY)
    wrong = _purchase(db, "444444444442", DAY, number="44-00000-00000")
    path = _history(
        tmp_path,
        [
            # The same listing bought twice on one day: which order is ours?
            ("44-44444-44441", "Mar 04, 2025", "444444444441", "x"),
            ("44-44444-44442", "Mar 04, 2025", "444444444441", "x"),
            ("44-44444-44443", "Mar 04, 2025", "444444444442", "y"),
        ],
    )
    todo = _run(db, path)
    left = dict(todo.unmatched)
    assert left[twice.id].startswith("in several orders")
    assert left[absent.id] == "listing not in the purchase history"
    # A stored number eBay contradicts is reported, never changed.
    assert (wrong.id, "44-00000-00000", "44-44444-44443") in todo.disagreements
    for purchase in (twice, absent):
        kept = db.get(PurchaseOrder, purchase.id)
        assert kept is not None and kept.order_number is None
    kept = db.get(PurchaseOrder, wrong.id)
    assert kept is not None and kept.order_number == "44-00000-00000"


def test_a_plan_writes_nothing_and_a_second_run_finds_nothing(
    db: Session, tmp_path: Path
) -> None:
    order = _purchase(db, "555555555555", DAY)
    path = _history(tmp_path, [("55-55555-55555", "Mar 04, 2025", "555555555555", "z")])
    lines = ebay_orders.read_history([path])
    ebay_orders.plan(db, lines)
    db.expire_all()
    fresh = db.get(PurchaseOrder, order.id)
    assert fresh is not None and fresh.order_number is None

    _run(db, path)
    again = ebay_orders.plan(db, lines)
    assert (again.listing_ids, again.orders) == ({}, {})


def test_an_item_shows_and_is_found_by_its_listing_id(
    client: TestClient, admin_headers: dict[str, str], db: Session, tmp_path: Path
) -> None:
    order = _purchase(db, "666666666666", DAY)
    path = _history(tmp_path, [("66-66666-66666", "Mar 04, 2025", "666666666666", "w")])
    _run(db, path)
    db.commit()
    item_id = db.scalar(
        select(InventoryItem.id).where(InventoryItem.purchase_order_id == order.id)
    )
    detail = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()
    assert (detail["sellers_item_id"], detail["order_number"]) == (
        "666666666666",
        "66-66666-66666",
    )
    found = client.get(
        "/api/inventory/coins/search",
        params={"sellers_item_id": "666666666666"},
        headers=admin_headers,
    )
    assert found.status_code == 200, found.text
    assert [row["id"] for row in found.json()["rows"]] == [item_id]


def test_a_listing_id_is_typed_in_and_changed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """For items the pass could not fill, a person types it (owner, 2026-09-25)."""
    order = PurchaseOrder(vendor_id=_ebay(db).id)
    db.add(order)
    db.commit()
    created = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "source_title": "t",
            "item_kind": "coin",
            "sellers_item_id": " 375454001960 ",
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["sellers_item_id"] == "375454001960"
    item_id = created.json()["id"]
    cleared = client.patch(
        f"/api/inventory/{item_id}",
        json={"sellers_item_id": "  "},
        headers=admin_headers,
    )
    assert cleared.status_code == 200, cleared.text
    detail = client.get(f"/api/inventory/{item_id}", headers=admin_headers).json()
    assert detail["sellers_item_id"] is None
    history = client.get(f"/api/inventory/{item_id}/history", headers=admin_headers)
    assert any(e.get("field") == "sellers_item_id" for e in history.json())


def test_a_set_form_is_entered_and_changed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Set form on entry and in the editor -- "Mixed Sets" is one (2026-09-25).

    Kept beside the purchase tests: the owner met it entering a purchase.
    """
    order = PurchaseOrder(vendor_id=_ebay(db).id)
    db.add(order)
    db.commit()
    created = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "source_title": "9 set lot",
            "item_kind": "set",
            "set_form": "mint_set",
        },
        headers=admin_headers,
    )
    assert created.status_code == 201, created.text
    assert created.json()["set_form"] == "mint_set"
    changed = client.patch(
        f"/api/inventory/{created.json()['id']}",
        json={"set_form": "proof_set"},
        headers=admin_headers,
    )
    assert changed.status_code == 200, changed.text
    detail = client.get(f"/api/inventory/{created.json()['id']}", headers=admin_headers)
    assert detail.json()["set_form"] == "proof_set"


def test_a_workbook_without_the_columns_is_refused(tmp_path: Path) -> None:
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.append(["Order", "Date"])
    path = tmp_path / "wrong.xlsx"
    book.save(path)
    with pytest.raises(ValueError, match="no column OrderNumber"):
        ebay_orders.read_history([path])


@pytest.mark.parametrize(
    ("url", "item_id"),
    [
        ("https://www.ebay.com/itm/315248803796", "315248803796"),
        ("https://www.ebay.com/itm/Some-Title/315248803796?hash=x", "315248803796"),
        ("https://order.ebay.com/ord/show?orderId=10-12524-31600", None),
        ("Gift", None),
        (None, None),
    ],
)
def test_the_item_id_is_read_from_a_listing_link(
    url: str | None, item_id: str | None
) -> None:
    assert ebay_orders.item_id_of(url) == item_id
