"""An item's listing web address and seller's item id, each filled from the other.

Two identifiers name the listing an item was bought from: its web address
(`inventory_item.listing_url`) and the seller's own id for it
(`sellers_item_id`). Either can recover the other:

- the id is read from the address where the site puts it -- eBay's
  `/itm/<id>`, a HiBid, LiveAuctioneers or Proxibid lot;
- an eBay id rebuilds its address, `https://www.ebay.com/itm/<id>`.

At an auction house or a shop -- any vendor but eBay and Whatnot, whose
pages are orders of many listings -- a purchase is usually one lot, so the
lot's page is the purchase's web address too. An item with no address takes
its purchase's when that is recognisably a lot's page (it carries a lot id),
and a purchase with none takes the one address its items share. A purchase
whose items name several lots gives none and takes none.

An eBay purchase's own web address is its order page, built from its order
number: `https://order.ebay.com/ord/show?orderId=<number>`. eBay removes a
listing's page after a while and keeps the order's, so the purchase takes
the order page when it has no address, when its address is eBay's list of
purchases (which names no order), and when its address is a listing's page
that one of its items already carries -- that listing is still recorded, on
the item. Any other address an eBay purchase holds is kept and reported.

Otherwise nothing already recorded is replaced. `create_item` applies the
purchase rule as each item is entered; this module's pass fills what older
records lack, every item's change logged in its History under the person
named.

    python -m app.listing_links                          report, touching nothing
    python -m app.listing_links --commit --by EMAIL      fill the gaps

Rules: docs/specs/entry-panels-design.md, "Listing links".
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import Row, select
from sqlalchemy.orm import Session

from . import field_changes, pass_cli
from .database import SessionLocal
from .ebay_orders import ITEM_ID, ORDER_PAGE, is_ebay, vendor_key
from .models import InventoryItem, PurchaseOrder, Vendor

#: Where each site puts a listing's own id in its web address. An order page
#: (Whatnot's `/order/`, eBay's order.ebay.com) names an order, not a listing.
_LISTING_IDS = (
    ITEM_ID,
    re.compile(r"hibid\.com/lot/(\d+)", re.IGNORECASE),
    re.compile(r"liveauctioneers\.com/item/(\d+)", re.IGNORECASE),
    re.compile(r"proxibid\.com/lotinformation/(\d+)", re.IGNORECASE),
)
_EBAY_ID = re.compile(r"^\d{9,15}$")
_WEB_ADDRESS = re.compile(r"^https?://", re.IGNORECASE)
#: Whatnot's own host name: the whole label, so a site whose name only
#: contains the letters is not Whatnot.
_WHATNOT_HOST = re.compile(r"(?:^|\.)whatnot\.com$")
#: An order number as eBay writes one; only these build an order page.
_EBAY_ORDER_NUMBER = re.compile(r"^\d{2}-\d{5}-\d{5}$")
_EBAY_ORDER_PAGE = re.compile(r"order\.ebay\.[a-z.]+/ord/", re.IGNORECASE)
#: eBay's list of everything bought: an address that names no order.
_EBAY_PURCHASES = re.compile(r"ebay\.[a-z.]+/mye/myebay/purchase", re.IGNORECASE)


def listing_id_from(url: str | None) -> str | None:
    """The seller's id for the listing at this web address, or None."""
    for pattern in _LISTING_IDS:
        found = pattern.search(url or "")
        if found:
            return found.group(1)
    return None


def ebay_listing_url(item_id: str) -> str:
    """The web address of the eBay listing with this item number."""
    return f"https://www.ebay.com/itm/{item_id}"


def _is_whatnot(vendor: Vendor) -> bool:
    """Whether the vendor is Whatnot: called `whatnot`, or at its host."""
    key = vendor_key(vendor)
    return key == "whatnot" or _WHATNOT_HOST.search(key) is not None


def is_marketplace(vendor: Vendor) -> bool:
    """Whether the vendor's orders hold many listings: eBay and Whatnot.

    Each is known by its own host name (`ebay_orders.is_ebay`), never by
    the letters appearing somewhere in another vendor's.
    """
    return is_ebay(vendor) or _is_whatnot(vendor)


def _web_address(value: str | None) -> str | None:
    """The value when it is a web address, otherwise None."""
    return value if value and _WEB_ADDRESS.match(value) else None


@dataclass
class Plan:
    """What the pass would fill: by item id, and by purchase id."""

    listing_ids: dict[int, str] = field(default_factory=dict)
    listing_urls: dict[int, str] = field(default_factory=dict)
    order_urls: dict[int, str] = field(default_factory=dict)
    #: eBay purchases taking their order page, by purchase id.
    order_pages: dict[int, str] = field(default_factory=dict)
    #: eBay purchases whose other address is kept: (purchase id, address).
    kept: list[tuple[int, str]] = field(default_factory=list)


def plan(db: Session) -> Plan:
    """Every gap the other identifier, or the purchase, can fill. Writes nothing."""
    todo = Plan()
    rows = db.execute(
        select(InventoryItem, PurchaseOrder, Vendor)
        .join(PurchaseOrder, PurchaseOrder.id == InventoryItem.purchase_order_id)
        .join(Vendor, Vendor.id == PurchaseOrder.vendor_id)
        .where(InventoryItem.deleted_at.is_(None), InventoryItem.split_at.is_(None))
        .order_by(InventoryItem.id)
    ).all()

    # The listing addresses each purchase's items name, for the purchase rules.
    named: dict[int, set[str]] = {}
    for item, order, _vendor in rows:
        if item.listing_url:
            named.setdefault(order.id, set()).add(item.listing_url)

    for item, order, vendor in rows:
        _plan_item(todo, item, order, vendor, named.get(order.id, set()))

    for _item, order, vendor in rows:
        _plan_order_url(todo, order, vendor, named.get(order.id, set()))

    # The listings each purchase's items carry, as recorded or as planned
    # above: a listing page on an eBay purchase gives way only to these.
    carried = _carried_listings(todo, rows)
    seen: set[int] = set()
    for _item, order, vendor in rows:
        if order.id in seen:
            continue
        seen.add(order.id)
        _plan_order_page(todo, order, vendor, carried.get(order.id, set()))
    return todo


def _address_for(
    item: InventoryItem, order: PurchaseOrder, vendor: Vendor, lots: set[str]
) -> str | None:
    """The listing address an item with none can be given, or None.

    Its purchase's, where that is one lot's page; failing that, the address
    its eBay item number rebuilds. `lots` is the addresses the purchase's
    items already name.
    """
    own = _web_address(order.source_url)
    if (
        own is not None
        and not is_marketplace(vendor)
        # Only a page that is recognisably a lot's: a shop's location
        # page is the purchase's, but no listing.
        and listing_id_from(own) is not None
        and lots <= {own}  # one lot: no item names another
    ):
        return own
    if is_ebay(vendor) and _EBAY_ID.match(item.sellers_item_id or ""):
        return ebay_listing_url(item.sellers_item_id or "")
    return None


def _plan_item(
    todo: Plan,
    item: InventoryItem,
    order: PurchaseOrder,
    vendor: Vendor,
    lots: set[str],
) -> None:
    """Plan the listing address and the seller's item id one item lacks."""
    url = item.listing_url
    if url is None:
        url = _address_for(item, order, vendor, lots)
        if url is not None:
            todo.listing_urls[item.id] = url
    if item.sellers_item_id is None:
        found = listing_id_from(url)
        if found is not None:
            todo.listing_ids[item.id] = found


def _plan_order_url(
    todo: Plan, order: PurchaseOrder, vendor: Vendor, lots: set[str]
) -> None:
    """Plan a purchase with no web address taking the one its items share.

    Not a marketplace's, whose order holds many listings, and not where the
    items name several lots or none.
    """
    if order.source_url is None and not is_marketplace(vendor) and len(lots) == 1:
        (only,) = lots
        if _web_address(only):
            todo.order_urls[order.id] = only


def _carried_listings(
    todo: Plan, rows: Sequence[Row[tuple[InventoryItem, PurchaseOrder, Vendor]]]
) -> dict[int, set[str]]:
    """The listing ids each purchase's items hold, recorded or planned, by purchase."""
    carried: dict[int, set[str]] = {}
    for item, order, _vendor in rows:
        for listing in (
            item.sellers_item_id,
            todo.listing_ids.get(item.id),
            listing_id_from(item.listing_url),
            listing_id_from(todo.listing_urls.get(item.id)),
        ):
            if listing:
                carried.setdefault(order.id, set()).add(listing)
    return carried


def _plan_order_page(
    todo: Plan, order: PurchaseOrder, vendor: Vendor, carried: set[str]
) -> None:
    """Decide whether an eBay purchase takes its order page or keeps its address.

    Nothing is planned for another vendor's purchase, for one without an eBay
    order number, or for one already at its order page. `carried` is the
    listings the purchase's items hold.
    """
    if not is_ebay(vendor):
        return
    if not _EBAY_ORDER_NUMBER.match(order.order_number or ""):
        return
    current = (order.source_url or "").strip()
    if _EBAY_ORDER_PAGE.search(current):
        return
    listing = listing_id_from(current)
    if (
        not current
        or _EBAY_PURCHASES.search(current)
        or (listing is not None and listing in carried)
    ):
        todo.order_pages[order.id] = ORDER_PAGE.format(order.order_number)
    else:
        todo.kept.append((order.id, current))


def apply(db: Session, todo: Plan, user_id: int) -> dict[str, int]:
    """Fill the planned gaps and log each item's change; the caller commits."""
    now = datetime.now(UTC)
    for item_id in sorted(set(todo.listing_urls) | set(todo.listing_ids)):
        item = db.get_one(InventoryItem, item_id)
        before = {
            "listing_url": item.listing_url,
            "sellers_item_id": item.sellers_item_id,
        }
        if item_id in todo.listing_urls:
            item.listing_url = todo.listing_urls[item_id]
        if item_id in todo.listing_ids:
            item.sellers_item_id = todo.listing_ids[item_id]
        after = {
            "listing_url": item.listing_url,
            "sellers_item_id": item.sellers_item_id,
        }
        field_changes.record(
            db,
            item_id,
            before,
            after,
            ["listing_url", "sellers_item_id"],
            user_id=user_id,
            at=now,
            text_fields=["listing_url", "sellers_item_id"],
        )
    for order_id, url in todo.order_urls.items():
        db.get_one(PurchaseOrder, order_id).source_url = url
    for order_id, url in todo.order_pages.items():
        db.get_one(PurchaseOrder, order_id).source_url = url
    db.flush()
    return {
        "listing_ids": len(todo.listing_ids),
        "listing_urls": len(todo.listing_urls),
        "order_urls": len(todo.order_urls),
        "order_pages": len(todo.order_pages),
    }


def main(argv: Sequence[str] | None = None) -> int:
    """Report, or with --commit fill, the listing addresses and ids."""
    parser = argparse.ArgumentParser(prog="listing_links", description=__doc__)
    pass_cli.add_commit_arguments(parser, "write the changes")
    args = pass_cli.parse_args(parser, argv)

    with SessionLocal() as db:
        todo = plan(db)
        print(f"seller's item ids to read from addresses: {len(todo.listing_ids)}")
        print(f"listing addresses to fill: {len(todo.listing_urls)}")
        print(f"purchase web addresses to fill: {len(todo.order_urls)}")
        print(f"eBay purchases to take their order page: {len(todo.order_pages)}")
        for order_id, url in todo.kept:
            print(f"  kept, purchase #{order_id}: {url}")
        return pass_cli.commit_or_report(
            db, args, lambda user_id: apply(db, todo, user_id)
        )


if __name__ == "__main__":
    sys.exit(main())
