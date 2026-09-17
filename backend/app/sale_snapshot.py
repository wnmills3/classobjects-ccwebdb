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
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import CoinDetail, ItemCertification, Listing, Mint, utcnow

__all__ = ["SNAPSHOT_VERSION", "take"]

#: Bumped when the shape changes, so a reader can tell old copies apart.
SNAPSHOT_VERSION = 1

#: Fields of the editor's view that describe the editing, not the item.
_EDITING_ONLY = frozenset(
    {"lot_claims", "reviewed", "derived", "default_tax_rate", "sale_state", "version"}
)


def take(db: Session, listing: Listing) -> dict[str, Any]:
    """A JSON-ready copy of the listing's item and the listing, as of now."""
    from .routers.inventory import item_detail

    item = listing.inventory_item
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
        "snapshot_version": SNAPSHOT_VERSION,
        "taken_at": utcnow().isoformat(),
        "item": {
            **detail,
            "mint": mint.code if mint else None,
            "variety": coin.variety if coin else None,
            "certificates": certificates,
        },
        "listing": {
            "id": listing.id,
            "title": listing.title,
            "description": listing.description,
            "price": str(listing.price),
            "currency": listing.currency.code if listing.currency else None,
        },
    }
