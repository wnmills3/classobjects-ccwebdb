"""Which listings are lots of an auction.

Three guards ask this before ending or selling a listing: Record sale
(`sales_writes._refuse_manual_auction_sale`), End on the Listings page
(`routers.offers._refuse_auction_lot`) and the item writes that end offers
(`routers.inventory._refuse_auction_lots`). `app.auctions` is the sole writer
of `auction_lot`, so an auction lot is ended or sold only through its
auction, and each guard refuses the listings this names.

A module of its own because `app.auctions` imports `sales_writes`, so the
lookup cannot live in `app.auctions` without a cycle.
"""

from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import AuctionLot, Listing, ListingFormat


def auction_ids_by_listing(db: Session, listings: Iterable[Listing]) -> dict[int, int]:
    """`{listing id: auction id}` for each of `listings` that is a lot of an auction.

    **Keyed on the `auction_lot` row, not on `format` alone.** An
    auction-format listing need not belong to an auction: the Offer dialog
    offers a coin directly on eBay by auction, and `auctions.remove_lot`
    deletes the row while the listing keeps `format = auction`. Neither has
    an auction to go through, so neither is named here. Only auction-format
    listings are looked up, since only they can have an `auction_lot` row; a
    listing has at most one (`uq_auction_lot_listing_id`).
    """
    ids = [
        listing.id for listing in listings if listing.format is ListingFormat.auction
    ]
    if not ids:
        return {}
    return dict(
        db.execute(
            select(AuctionLot.listing_id, AuctionLot.auction_id).where(
                AuctionLot.listing_id.in_(ids)
            )
        )
        .tuples()
        .all()
    )
