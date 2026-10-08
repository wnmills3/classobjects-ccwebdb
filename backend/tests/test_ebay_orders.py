"""Order numbers and listing ids from eBay's purchase history (`app.ebay_orders`).

The history is a workbook of lines -- OrderNumber, OrderDate, ItemID, ... --
built here in miniature. The purchases are made the way the collection's
were: one per eBay listing, linking it, some with a number and most without.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from app import ebay_orders
from app.models import (
    InventoryItem,
    ItemFieldChange,
    PurchaseOrder,
    Seller,
    User,
    Vendor,
)
from app.models.base import utcnow
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook
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
    """The vendor the pass treats as eBay, created when the database has none."""
    vendor = db.scalar(select(Vendor).where(Vendor.name == ebay_orders.EBAY))
    if vendor is None:
        vendor = Vendor(name=ebay_orders.EBAY)
        db.add(vendor)
        db.flush()
    return vendor


def _purchase(
    db: Session,
    item_id: str,
    on: date | None,
    number: str | None = None,
    pieces: int = 1,
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
    """The id of the first user, in whose name the pass logs its changes."""
    user_id = db.scalar(select(User.id).order_by(User.id).limit(1))
    assert user_id is not None
    return user_id


def _run(db: Session, path: Path) -> ebay_orders.Plan:
    """Plan from one history file and apply it, in the test's session."""
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


def _two_listings_of_one_order(tmp_path: Path, number: str, stem: str) -> Path:
    """A history of one order holding the listings `<stem>1` and `<stem>2`."""
    return _history(
        tmp_path,
        [
            (number, "Mar 04, 2025", f"{stem}1", "one"),
            (number, "Mar 04, 2025", f"{stem}2", "two"),
        ],
    )


def test_a_merged_purchases_seller_and_notes_go_to_the_one_kept(
    db: Session, tmp_path: Path
) -> None:
    """Deleting a purchase must not delete what only it recorded."""
    seller = Seller(name="drh9989")
    db.add(seller)
    db.flush()
    first = _purchase(db, "232323232321", DAY)
    first.notes = "paid by card"
    second = _purchase(db, "232323232322", DAY)
    second.seller_id = seller.id
    second.notes = "one coin short, refunded 4.00"
    db.flush()

    _run(db, _two_listings_of_one_order(tmp_path, "23-23232-32323", "23232323232"))

    assert db.get(PurchaseOrder, second.id) is None
    survivor = db.get(PurchaseOrder, first.id)
    assert survivor is not None
    assert survivor.seller_id == seller.id
    assert "paid by card" in (survivor.notes or "")
    assert "one coin short, refunded 4.00" in (survivor.notes or "")


def test_the_kept_purchases_own_seller_stays_and_the_review_names_the_other(
    db: Session, tmp_path: Path
) -> None:
    """One purchase has one seller: a second one is for a person to settle."""
    kept_seller = Seller(name="coind0g")
    other_seller = Seller(name="summer_the_cockapoo")
    db.add_all([kept_seller, other_seller])
    db.flush()
    first = _purchase(db, "242424242421", DAY)
    first.seller_id = kept_seller.id
    second = _purchase(db, "242424242422", DAY)
    second.seller_id = other_seller.id
    second.notes = "came in a second parcel"
    db.flush()
    path = _two_listings_of_one_order(tmp_path, "24-24242-42424", "24242424242")
    lines = ebay_orders.read_history([path])
    todo = ebay_orders.plan(db, lines)

    review = tmp_path / "review.xlsx"
    ebay_orders.write_review(db, todo, lines, review)
    book = load_workbook(review)
    try:
        numbered = " | ".join(
            str(cell)
            for row in book["Numbered"].iter_rows(values_only=True)
            for cell in row
            if cell is not None
        )
    finally:
        book.close()
    # What the merge cannot carry, and what it appends, is there to be read.
    assert "summer_the_cockapoo" in numbered
    assert "came in a second parcel" in numbered

    ebay_orders.apply(db, todo, _admin(db))
    db.flush()
    db.expire_all()
    survivor = db.get(PurchaseOrder, first.id)
    assert survivor is not None
    assert survivor.seller_id == kept_seller.id
    assert "came in a second parcel" in (survivor.notes or "")


def test_a_listing_bought_twice_goes_to_the_order_of_the_purchases_date(
    db: Session, tmp_path: Path
) -> None:
    order = _purchase(db, "252525252525", date(2025, 4, 9))
    path = _history(
        tmp_path,
        [
            ("25-25252-00001", "Mar 04, 2025", "252525252525", "x"),
            ("25-25252-00002", "Apr 09, 2025", "252525252525", "x"),
        ],
    )
    todo = _run(db, path)

    assert todo.unmatched == []
    refreshed = db.get(PurchaseOrder, order.id)
    assert refreshed is not None and refreshed.order_number == "25-25252-00002"


def test_an_empty_order_date_is_filled_from_the_history_and_a_set_one_kept(
    db: Session, tmp_path: Path
) -> None:
    undated = _purchase(db, "262626262621", None)
    dated = _purchase(db, "262626262622", date(2025, 3, 1))
    path = _history(
        tmp_path,
        [
            ("26-26262-00001", "Mar 04, 2025", "262626262621", "x"),
            ("26-26262-00002", "Mar 04, 2025", "262626262622", "y"),
        ],
    )
    _run(db, path)

    filled = db.get(PurchaseOrder, undated.id)
    assert filled is not None
    assert (filled.order_number, filled.ordered_on) == ("26-26262-00001", DAY)
    kept = db.get(PurchaseOrder, dated.id)
    assert kept is not None
    assert (kept.order_number, kept.ordered_on) == (
        "26-26262-00002",
        date(2025, 3, 1),
    )


def test_a_deleted_item_and_a_split_lot_say_nothing_about_the_order(
    db: Session, tmp_path: Path
) -> None:
    """Only live items are read, as every count of the collection reads them.

    A row deleted as a mistake, or a lot replaced by its pieces, may link a
    listing of another order: read, it would leave the purchase "in several
    orders" and write a listing id and a History row on a row nobody sees.
    """
    order = _purchase(db, "272727272721", DAY)
    gone = build_item(db, purchase_order_id=order.id)
    gone.listing_url = "https://www.ebay.com/itm/272727272722"
    gone.deleted_at = utcnow()
    lot = build_item(db, purchase_order_id=order.id)
    lot.listing_url = "https://www.ebay.com/itm/272727272723"
    lot.split_at = utcnow()
    db.flush()
    path = _history(
        tmp_path,
        [
            ("27-27272-00001", "Mar 04, 2025", "272727272721", "ours"),
            ("27-27272-00002", "Mar 04, 2025", "272727272722", "deleted"),
            ("27-27272-00003", "Mar 04, 2025", "272727272723", "split"),
        ],
    )
    todo = _run(db, path)

    assert todo.unmatched == []
    refreshed = db.get(PurchaseOrder, order.id)
    assert refreshed is not None and refreshed.order_number == "27-27272-00001"
    for item in (gone, lot):
        assert item.id not in todo.listing_ids
        assert db.get_one(InventoryItem, item.id).sellers_item_id is None
    logged = db.scalars(
        select(ItemFieldChange.inventory_item_id).where(
            ItemFieldChange.inventory_item_id.in_([gone.id, lot.id])
        )
    ).all()
    assert logged == []


def test_ebay_is_known_by_its_host_name_whatever_the_vendor_is_called(
    db: Session, tmp_path: Path
) -> None:
    """A vendor renamed on the Lists page is still eBay to the pass."""
    for renamed in db.scalars(select(Vendor).where(Vendor.name == ebay_orders.EBAY)):
        renamed.name = "eBay"
        renamed.host = "www.ebay.com"
    vendor = db.scalar(select(Vendor).where(Vendor.name == "eBay"))
    if vendor is None:
        vendor = Vendor(name="eBay", host="www.ebay.com")
        db.add(vendor)
    db.flush()
    url = "https://www.ebay.com/itm/282828282828"
    order = PurchaseOrder(vendor_id=vendor.id, ordered_on=DAY, source_url=url)
    db.add(order)
    db.flush()
    build_item(db, purchase_order_id=order.id).listing_url = url
    db.flush()
    path = _history(tmp_path, [("28-28282-82828", "Mar 04, 2025", "282828282828", "z")])

    _run(db, path)

    refreshed = db.get(PurchaseOrder, order.id)
    assert refreshed is not None and refreshed.order_number == "28-28282-82828"


def test_with_no_ebay_vendor_the_pass_refuses_rather_than_reporting_nothing(
    db: Session, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Zero changes must mean nothing is left to do, not that eBay was not found.

    `main` is given the test's own `db`: left to itself it opens the
    application's `SessionLocal`.
    """
    for vendor in db.scalars(select(Vendor)):
        assert not ebay_orders.is_ebay(vendor)
    path = _history(tmp_path, [("29-29292-92929", "Mar 04, 2025", "292929292929", "z")])

    assert ebay_orders.main([str(path)], db=db) == 1

    captured = capsys.readouterr()
    assert "no eBay vendor" in captured.err
    assert "listing ids to set" not in captured.out


def test_the_person_named_is_found_whatever_the_capitals(
    db: Session, tmp_path: Path, admin_user: User
) -> None:
    """`--by` reads an email address as every other pass does: case aside."""
    order = _purchase(db, "303030303030", DAY)
    db.commit()
    path = _history(tmp_path, [("30-30303-03030", "Mar 04, 2025", "303030303030", "z")])

    assert (
        ebay_orders.main([str(path), "--commit", "--by", "nobody@example.com"], db=db)
        == 2
    )
    db.expire_all()
    assert db.get_one(PurchaseOrder, order.id).order_number is None

    by = admin_user.email.upper()
    assert ebay_orders.main([str(path), "--commit", "--by", by], db=db) == 0
    db.expire_all()
    assert db.get_one(PurchaseOrder, order.id).order_number == "30-30303-03030"


def test_the_review_workbook_lists_what_was_numbered_and_what_is_left(
    db: Session, tmp_path: Path
) -> None:
    numbered = _purchase(db, "313131313131", DAY)
    absent = _purchase(db, "313131313139", DAY)
    wrong = _purchase(db, "313131313132", DAY, number="31-00000-00000")
    path = _history(
        tmp_path,
        [
            ("31-31313-00001", "Mar 04, 2025", "313131313131", "a dime"),
            ("31-31313-00002", "Mar 04, 2025", "313131313132", "a cent"),
        ],
    )
    lines = ebay_orders.read_history([path])
    review = tmp_path / "out" / "review.xlsx"

    ebay_orders.write_review(db, ebay_orders.plan(db, lines), lines, review)

    book = load_workbook(review)
    try:
        assert book.sheetnames == ["Numbered", "Needs you", "Numbers that disagree"]
        sheets = {
            name: [list(row) for row in book[name].iter_rows(values_only=True)][1:]
            for name in book.sheetnames
        }
    finally:
        book.close()
    assert [(row[0], row[1]) for row in sheets["Numbered"]] == [
        ("31-31313-00001", numbered.id)
    ]
    assert "a dime" in sheets["Numbered"][0]
    assert [(row[0], row[-1]) for row in sheets["Needs you"]] == [
        (absent.id, "listing not in the purchase history")
    ]
    (differs,) = sheets["Numbers that disagree"]
    assert differs[0] == wrong.id
    assert differs[-4:] == [
        "31-00000-00000",
        "31-31313-00002",
        "a cent",
        "not in the history (a typo?)",
    ]


def test_a_title_that_begins_like_a_formula_is_text_on_every_review_sheet(
    db: Session, tmp_path: Path
) -> None:
    """A seller wrote the title; the person reviewing opens it in a spreadsheet.

    The history's own names and the titles stored on the items both begin
    with `=`, so each of the three sheets has one to write. Read back
    without evaluating anything, every such cell is of the text type.
    """
    formula = '=HYPERLINK("https://example.test","a dime")'
    numbered = _purchase(db, "323232323231", DAY)
    absent = _purchase(db, "323232323239", DAY)
    wrong = _purchase(db, "323232323232", DAY, number="32-00000-00000")
    for purchase in (numbered, absent, wrong):
        for item in db.scalars(
            select(InventoryItem).where(InventoryItem.purchase_order_id == purchase.id)
        ):
            item.source_title = formula
    db.flush()
    path = _history(
        tmp_path,
        [
            ("32-32323-00001", "Mar 04, 2025", "323232323231", "a dime"),
            ("32-32323-00002", "Mar 04, 2025", "323232323232", "a cent"),
        ],
    )
    # The name as the seller wrote it: put on the lines read, since a cell
    # of the history typed in here would itself be read as a formula.
    lines = [replace(line, name=formula) for line in ebay_orders.read_history([path])]
    review = tmp_path / "review.xlsx"

    ebay_orders.write_review(db, ebay_orders.plan(db, lines), lines, review)

    book = load_workbook(review)
    try:
        for name in ("Numbered", "Needs you", "Numbers that disagree"):
            cells = [cell for row in book[name].iter_rows() for cell in row]
            written = [cell for cell in cells if cell.value == formula]
            assert written, f"{name} holds no such title: the fixture misses it"
            kinds = {c.data_type for c in cells if isinstance(c.value, str)}
            assert kinds == {"s"}, name
    finally:
        book.close()


def test_an_order_number_is_compared_as_text(db: Session) -> None:
    """`0123` and `123` are two order numbers, so the change is in the History."""
    order = _purchase(db, "444444444441", DAY, number="0123")
    ebay_orders.apply(db, ebay_orders.Plan(orders={"123": (order.id, [])}), _admin(db))
    db.flush()
    item_id = db.scalar(
        select(InventoryItem.id).where(InventoryItem.purchase_order_id == order.id)
    )
    logged = db.execute(
        select(ItemFieldChange.old_value, ItemFieldChange.new_value).where(
            ItemFieldChange.inventory_item_id == item_id,
            ItemFieldChange.field_name == "order_number",
        )
    ).all()
    assert [tuple(r) for r in logged] == [("0123", "123")]


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
    """For items the pass could not fill, a person types it."""
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
    """Set form on entry and in the editor -- "Mixed Sets" is one.

    Kept beside the purchase tests: it is chosen while entering a purchase.
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
