"""An item's listing web address and seller's item id, each filled from the other.

`app.listing_links`: at entry, and as a pass over the collection. See the
"Listing links" rules in docs/specs/entry-panels-design.md.
"""

from __future__ import annotations

import pytest
from app.listing_links import apply, ebay_listing_url, listing_id_from, plan
from app.models import InventoryItem, ItemFieldChange, PurchaseOrder, User, Vendor
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import ItemFactory, build_purchase_order

LOT = "https://hibid.com/lot/280623476/1986-2024-american-eagle--5-00-gold-coin"


@pytest.mark.parametrize(
    ("url", "found"),
    [
        ("https://www.ebay.com/itm/126845170680", "126845170680"),
        ("https://www.ebay.com/itm/1878-Morgan/235872202173?x=1", "235872202173"),
        (LOT, "280623476"),
        ("https://goldstandardauctions.hibid.com/lot/218761505/x", "218761505"),
        ("https://www.liveauctioneers.com/item/194045322_1880-s-morgan", "194045322"),
        ("https://www.proxibid.com/lotInformation/91896919/x#Top", "91896919"),
        ("https://www.whatnot.com/order/YLMRqwXPqcdsXx35UxxA2V", None),
        ("https://order.ebay.com/ord/show?orderId=1", None),
        (None, None),
    ],
)
def test_the_listing_id_a_web_address_carries(
    url: str | None, found: str | None
) -> None:
    assert listing_id_from(url) == found


def test_an_ebay_id_rebuilds_its_listing_address() -> None:
    assert ebay_listing_url("126845170680") == "https://www.ebay.com/itm/126845170680"


def _item(
    db: Session, make_item: ItemFactory, order: PurchaseOrder, **fields: object
) -> InventoryItem:
    return make_item(purchase_order_id=order.id, **fields)


def test_the_pass_fills_each_from_the_other(
    db: Session, make_item: ItemFactory
) -> None:
    ebay = build_purchase_order(db, vendor_name="ebay.com", order_number="E-1")
    lot_order = build_purchase_order(db, vendor_name="hibid.com", source_url=LOT)
    bare_order = build_purchase_order(db, vendor_name="liveauctioneers.com")
    whatnot = build_purchase_order(
        db, vendor_name="whatnot.com", source_url="https://www.whatnot.com/order/A"
    )

    by_id = _item(db, make_item, ebay, sellers_item_id="126845170680")
    by_url = _item(db, make_item, lot_order, listing_url=LOT)
    from_order = _item(db, make_item, lot_order)
    to_order = _item(
        db,
        make_item,
        bare_order,
        listing_url="https://www.liveauctioneers.com/item/194045322_x",
    )
    # Whatnot's link is an order page: it is no listing, and no lot.
    show = _item(db, make_item, whatnot)
    db.commit()

    todo = plan(db)

    # An address filled from the purchase gives its id in the same run.
    assert todo.listing_ids == {
        by_url.id: "280623476",
        to_order.id: "194045322",
        from_order.id: "280623476",
    }
    assert todo.listing_urls == {
        by_id.id: "https://www.ebay.com/itm/126845170680",
        from_order.id: LOT,
    }
    assert todo.order_urls == {
        bare_order.id: "https://www.liveauctioneers.com/item/194045322_x"
    }
    assert show.id not in todo.listing_urls


def test_a_shops_page_is_not_copied_onto_its_items(
    db: Session, make_item: ItemFactory
) -> None:
    # A pawn shop's location page names no lot: the pass leaves the items be.
    order = build_purchase_order(
        db,
        vendor_name="silasdeanepawn.com",
        source_url="https://silasdeanepawn.com/locations/manchester/",
    )
    item = _item(db, make_item, order)
    db.commit()

    assert item.id not in plan(db).listing_urls


def test_a_purchase_of_several_lots_takes_no_single_address(
    db: Session, make_item: ItemFactory
) -> None:
    order = build_purchase_order(db, vendor_name="hibid.com")
    _item(db, make_item, order, listing_url=LOT)
    _item(db, make_item, order, listing_url="https://hibid.com/lot/1/other")
    db.commit()

    assert order.id not in plan(db).order_urls


def test_a_value_already_there_is_never_replaced(
    db: Session, make_item: ItemFactory
) -> None:
    order = build_purchase_order(db, vendor_name="hibid.com", source_url=LOT)
    kept = _item(db, make_item, order, listing_url=LOT, sellers_item_id="MINE")
    db.commit()

    todo = plan(db)

    assert kept.id not in todo.listing_ids
    assert kept.id not in todo.listing_urls


def test_applying_logs_each_items_change_under_the_person(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    order = build_purchase_order(db, vendor_name="hibid.com", source_url=LOT)
    item = _item(db, make_item, order)
    db.commit()

    counts = apply(db, plan(db), admin_user.id)
    db.commit()

    db.refresh(item)
    assert (item.listing_url, item.sellers_item_id) == (LOT, "280623476")
    assert counts["listing_urls"] == 1
    changes = db.scalars(
        select(ItemFieldChange).where(ItemFieldChange.inventory_item_id == item.id)
    ).all()
    assert sorted((c.field_name, c.changed_by_id) for c in changes) == [
        ("listing_url", admin_user.id),
        ("sellers_item_id", admin_user.id),
    ]
    # Run again, nothing is left to do.
    again = plan(db)
    assert item.id not in again.listing_urls
    assert item.id not in again.listing_ids


LISTING = "https://www.ebay.com/itm/126845170680"


def _ebay_order(
    db: Session, source_url: str | None, order_number: str | None = None
) -> PurchaseOrder:
    """An eBay purchase; each gets an order number of its own unless given one."""
    vendor = db.scalar(select(Vendor).where(Vendor.name == "ebay.com"))
    if vendor is None:
        vendor = Vendor(name="ebay.com")
        db.add(vendor)
        db.flush()
    if order_number is None:
        held = len(db.scalars(select(PurchaseOrder.id)).all())
        order_number = f"02-15255-{15698 + held:05d}"
    order = PurchaseOrder(
        vendor_id=vendor.id, source_url=source_url, order_number=order_number
    )
    db.add(order)
    db.commit()
    db.refresh(order)
    return order


def _page(order: PurchaseOrder) -> str:
    return f"https://order.ebay.com/ord/show?orderId={order.order_number}"


def test_an_ebay_purchase_takes_its_order_page(
    db: Session, make_item: ItemFactory
) -> None:
    # The decoy shifts the ids, so an address matched to the wrong purchase
    # would land on the wrong key.
    _item(db, make_item, build_purchase_order(db, vendor_name="hibid.com"))
    bare = _ebay_order(db, None)
    _item(db, make_item, bare)
    db.commit()

    assert plan(db).order_pages == {
        bare.id: "https://order.ebay.com/ord/show?orderId=02-15255-15699"
    }


def test_a_listing_page_on_an_ebay_purchase_gives_way_once_an_item_carries_it(
    db: Session, make_item: ItemFactory
) -> None:
    by_address = _ebay_order(db, LISTING)
    _item(db, make_item, by_address, listing_url=LISTING)
    by_id = _ebay_order(db, "https://www.ebay.com/itm/Morgan/126845170680?hash=1")
    _item(db, make_item, by_id, sellers_item_id="126845170680")
    # No item carries this listing: replacing it would lose the only copy.
    only_copy = _ebay_order(db, LISTING)
    _item(db, make_item, only_copy, sellers_item_id="999999999999")
    db.commit()

    todo = plan(db)

    assert todo.order_pages == {
        by_address.id: _page(by_address),
        by_id.id: _page(by_id),
    }
    assert [order_id for order_id, _url in todo.kept] == [only_copy.id]


@pytest.mark.parametrize(
    ("source_url", "replaced"),
    [
        # eBay's own list of purchases names no order at all.
        ("https://www.ebay.com/mye/myebay/purchase", True),
        # A seller's page says something the order page does not.
        ("https://www.ebay.com/usr/dafie_1447", False),
        ("https://order.ebay.com/ord/show?orderId=02-15255-15698#/", False),
    ],
)
def test_which_other_addresses_an_ebay_purchase_gives_up(
    db: Session, make_item: ItemFactory, source_url: str, replaced: bool
) -> None:
    order = _ebay_order(db, source_url)
    _item(db, make_item, order)
    db.commit()

    assert (order.id in plan(db).order_pages) is replaced


def test_only_an_ebay_order_number_makes_an_order_page(
    db: Session, make_item: ItemFactory
) -> None:
    typed = _ebay_order(db, None, "Order-0001")
    _item(db, make_item, typed)
    whatnot = build_purchase_order(
        db, vendor_name="whatnot.com", order_number="02-15255-15698"
    )
    _item(db, make_item, whatnot)
    db.commit()

    assert plan(db).order_pages == {}


def test_applying_writes_the_order_page_and_leaves_nothing_to_do(
    db: Session, make_item: ItemFactory, admin_user: User
) -> None:
    order = _ebay_order(db, LISTING)
    item = _item(db, make_item, order, listing_url=LISTING)
    db.commit()

    counts = apply(db, plan(db), admin_user.id)
    db.commit()

    db.refresh(order)
    db.refresh(item)
    assert order.source_url == _page(order)
    assert item.listing_url == LISTING
    assert counts["order_pages"] == 1
    assert plan(db).order_pages == {}


def test_entering_an_item_gives_a_bare_lot_purchase_its_address(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = build_purchase_order(db, vendor_name="hibid.com")
    res = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": "coin",
            "source_title": "A gold eagle",
            "listing_url": LOT,
        },
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    db.refresh(order)
    assert order.source_url == LOT


def test_entering_an_ebay_item_leaves_the_order_address_alone(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    # An eBay order's page is not its listing's.
    order = build_purchase_order(db, vendor_name="ebay.com")
    res = client.post(
        "/api/inventory",
        json={
            "purchase_order_id": order.id,
            "item_kind": "coin",
            "source_title": "A dime",
            "listing_url": "https://www.ebay.com/itm/126845170680",
        },
        headers=admin_headers,
    )
    assert res.status_code == 201, res.text
    db.refresh(order)
    assert order.source_url is None
