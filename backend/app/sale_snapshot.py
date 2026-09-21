"""The item as it was sold.

An order line keeps a copy of the item and its listing, taken when the line
is made: at checkout, when an administrator places an order, or when a
revision adds a line. The item may be corrected afterwards, returned, and
sold again; each sale's line keeps what that sale was. A quantity or price
change to an existing line keeps its snapshot -- it is the same sale.

The copy is the console's own view of the item (`routers.inventory`,
everything the editor shows) less what describes the editing rather than the
item, plus what that view leaves out -- mint, variety, certificates -- and
the listing: title, description, price, currency. It holds costs, so only the
console ever sees it.

A **lot** listing offers several items, so its copy carries `items` -- the
same per-item detail, one entry per member, in item id order -- and `lot`
(id, title, description) in place of `item`. That is the whole record of
which coins the group held: `offering_writes._end` releases every membership
the moment the lot sells, so a reader that went back to `sales_lot_item`
would find nothing.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CoinDetail, InventoryItem, ItemCertification, Listing, Mint, utcnow
from .offering_writes import offered_items

__all__ = ["SNAPSHOT_VERSION", "take"]

#: Bumped when the shape changes, so a reader can tell old copies apart.
#:
#: 2 is where lots arrive: a snapshot may now carry `items` and `lot` instead
#: of `item`, so the *set* of shapes a reader may meet changed even though no
#: single-item key moved. A reader has to be able to tell which one it holds.
SNAPSHOT_VERSION = 2

#: Fields of the editor's view that describe the editing, not the item.
_EDITING_ONLY = frozenset(
    {"lot_claims", "reviewed", "derived", "default_tax_rate", "sale_state", "version"}
)


def _detail(db: Session, item: InventoryItem) -> dict[str, Any]:
    """One item's copy: the editor's view, less the editing, plus what it omits."""
    from .routers.inventory import item_detail

    detail = item_detail(db, item).model_dump(mode="json", exclude=set(_EDITING_ONLY))
    coin = db.scalar(select(CoinDetail).where(CoinDetail.inventory_item_id == item.id))
    mint = db.get(Mint, coin.mint_id) if coin and coin.mint_id else None
    certificates = list(
        db.scalars(
            select(ItemCertification.cert_number)
            .where(ItemCertification.inventory_item_id == item.id)
            .order_by(ItemCertification.id)
        )
    )
    return {
        **detail,
        "mint": mint.code if mint else None,
        "variety": coin.variety if coin else None,
        "certificates": certificates,
    }


def take(db: Session, listing: Listing) -> dict[str, Any]:
    """A JSON-ready copy of what the listing offers, and the listing, as of now.

    An item listing keeps the shape every existing snapshot has: `item` and
    `listing`. A lot listing carries `items` and `lot` instead -- the members
    come from `offering_writes.offered_items`, the single answer in the
    codebase to "which items does this listing offer", so a lot's snapshot,
    its shares and its dispositions can never be built from three different
    member lists.
    """
    items = offered_items(db, listing)
    snapshot: dict[str, Any] = {
        "snapshot_version": SNAPSHOT_VERSION,
        "taken_at": utcnow().isoformat(),
    }
    lot = listing.sales_lot
    if lot is None:
        # `ck_listing_item_xor_lot`: no lot means an item, and
        # `inventory_item_id` is then NOT NULL, so there is exactly one.
        snapshot["item"] = _detail(db, items[0])
    else:
        snapshot["items"] = [_detail(db, item) for item in items]
        snapshot["lot"] = {
            "id": lot.id,
            "title": lot.title,
            "description": lot.description,
        }
    snapshot["listing"] = {
        "id": listing.id,
        "title": listing.title,
        "description": listing.description,
        "price": str(listing.price),
        "currency": listing.currency.code if listing.currency else None,
    }
    return snapshot
