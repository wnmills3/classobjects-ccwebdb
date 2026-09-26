"""The item as it was sold.

An order line keeps a copy of the item and its listing, taken when the line
is made: at checkout, when an administrator places an order, or when a
revision adds a line. The item may be corrected afterwards, returned, and
sold again; each sale's line keeps what that sale was. A quantity or price
change to an existing line keeps its snapshot -- it is the same sale.

The copy is the console's own view of the item (`routers.inventory`,
everything the editor shows, mint, variety and certificates included) less
what describes the editing rather than the item (`_EDITING_ONLY`: the lot's
claims, review marks, derived defaults, the default tax rate, the sale
warning, the row version, and who last changed each field), plus the
listing: title, description, price, currency. The certificates are
kept under `certificates` as well as the view's own `cert_numbers`. It holds
costs, so only the console ever sees it.

A **lot** listing offers several items, so its copy carries `items` -- the
same per-item detail, one entry per member, in item id order -- and `lot`
(id, title, description) in place of `item`. That is the whole record of
which coins the group held: `offering_writes._end` releases every membership
the moment the lot sells, so a reader that went back to `sales_lot_item`
would find nothing.

**Reading an older copy.** `snapshot_version` says which shape is in hand,
and both shapes stay readable for ever -- a snapshot is never rewritten.

- **Version 1** always has `item`, and never `lot` or `items`. Every
  snapshot taken before sales lots existed is one of these.
- **Version 2** has *either* `item` (an item listing, byte for byte the
  version-1 shape) *or* `lot` **and** `items` (a lot listing). Never both.

So a reader asks for `lot` and branches on whether it is there; it must not
assume `item` is present, which is what every version-1 reader did and what
the bump exists to announce. `listing` is in every version.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import InventoryItem, Listing, utcnow
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
    {
        "lot_claims",
        "reviewed",
        "derived",
        "default_tax_rate",
        "sale_state",
        "version",
        "last_changes",
    }
)


def _detail(db: Session, item: InventoryItem) -> dict[str, Any]:
    """One item's copy: the editor's view, less the editing."""
    from .routers.inventory import item_detail

    detail = item_detail(db, item).model_dump(mode="json", exclude=set(_EDITING_ONLY))
    return {**detail, "certificates": detail["cert_numbers"]}


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
