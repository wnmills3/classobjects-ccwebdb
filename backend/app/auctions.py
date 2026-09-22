"""Auction transitions: adding and removing lots, and the auction's own life.

Task 2 of the auctions phase. `app/models/auctions.py` (Task 1) built the
schema; this module is the sole writer of `auction` and `auction_lot`, the
same single-writer discipline `offering_writes.py` keeps for `listing.status`,
`offer_claim` and `sales_lot.status`, and `lifecycle_writes.py` keeps for
`inventory_item.storage_location_id`.

Adding or removing a lot **is** an offer or an ending -- "Same refusals and
pausing as any offer -- it *is* an offer" (the Task 2 brief's own test
docstring) -- so this module calls `offering_writes.offer` and
`offering_writes.end_offer` for that half rather than writing
`listing.status`, `offer_claim` or `sales_lot.status` itself. Moving items
into consignment custody **is** a location change, so `consign` calls
`lifecycle_writes.set_location` rather than assigning
`inventory_item.storage_location_id` directly. Settlement (`settle`) is a
later task's writer and is deliberately not here.

Every transition validates the auction's current status (and, for `consign`,
the platform's kind) before writing anything, and refuses with a message
naming what is in the way -- the same discipline `offering_writes.offer` and
`lot_writes.add_member` follow, and the one exception type this module
produces, `AuctionRefused`, is deliberately singular for the same reason
`LotRefused` is: a caller has one thing to catch.

**On `offering_writes.lock_for_sale`'s one undischarged obligation.** Its
docstring states the rule: a caller that may call `end_offer` on a listing it
did not name must first pass that set through `refuse_if_lot_unheld`, because
`end_offer` takes its lot row late and the failure mode for forgetting is a
Postgres deadlock under concurrency, not a single-request bug. `remove_lot`
and `cancel` both call `end_offer`, and neither needs it: both always call it
on `auction_lot.listing`, a listing this module named by holding the
`AuctionLot` row itself (`remove_lot`) or by iterating `auction.lots`
(`cancel`, which removes lots through `remove_lot` one at a time) -- never a
listing reached through `lock_for_sale`'s *derived* half, the search that
finds a listing because it holds one of the caller's items without the
caller ever naming it (`routers.inventory.receive_items` is the one caller
today that reaches a listing that way). And `end_offer` itself, called here
with `sold=False` (withdrawal, never a sale), only ever ends the one listing
it is handed -- the `paused_by_it` listings it also touches are *resumed*,
not ended, on that path; only `sold=True` ends them, and this module never
passes it. So no call in this module ends a listing it did not name, and
`refuse_if_lot_unheld` has nothing to discharge here.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import lifecycle_writes, lot_writes, offering_writes
from .models import (
    Auction,
    AuctionLot,
    AuctionStatus,
    InventoryItem,
    ListingFormat,
    SalesLot,
    StorageLocation,
    StorageLocationKind,
)

__all__ = [
    "AuctionRefused",
    "add_lot",
    "cancel",
    "close",
    "consign",
    "remove_lot",
    "schedule",
]


class AuctionRefused(Exception):
    """One auction transition cannot happen now, with the reason for a person."""


#: Statuses in which a lot may still be withdrawn: before the sale has
#: closed. `consigned` is included -- an auction house may still need a lot
#: pulled back after physical custody moved, before the sale itself runs.
_LOTS_REMOVABLE = (
    AuctionStatus.draft,
    AuctionStatus.scheduled,
    AuctionStatus.consigned,
)

#: The vocabulary code `consign` needs. Seeded from
#: `data/reference/operations.json` by `app.seeding.seed_all`, not by this
#: module or by any migration -- see the migration `e267ec3aedc1`'s
#: docstring for why an INSERT here would be the wrong fix.
_CONSIGNED_KIND_CODE = "consigned"

#: The one platform kind `consign` accepts. A live or marketplace auction
#: never leaves the premises (spec, *Consignment custody*).
_AUCTION_HOUSE_KIND_CODE = "auction_house"


def add_lot(
    db: Session,
    auction: Auction,
    lot: SalesLot | InventoryItem,
    *,
    lot_number: str,
    reserve: Decimal | None,
    price: Decimal | None,
    title: str | None = None,
    description: str | None = None,
    external_id: str | None = None,
) -> AuctionLot:
    """Add a lot, or a single item as a lot of one, to a draft or scheduled auction.

    This **is** an offer -- `offering_writes.offer(listing_format=auction)`
    -- so it carries the same refusals and store-listing pausing as any other
    offer (spec, *Add to an auction*). `price` is the starting bid, 0 when
    none is given, which is `listing.price`'s own meaning for an auction
    listing.

    `lot` is a `SalesLot` already being assembled, or a single
    `InventoryItem`, which this wraps in a fresh lot of one -- "a single item
    offered in an auction is a sales lot of one" (spec, *Three kinds of
    lot*). `offering_writes.offer` requires `title`, `description` and
    `external_id`, which the brief's original signature omits and gives no
    defaults for; they default here to the lot's own title and description
    (the item's own, for a lot of one) and to `None`, and a caller may
    override any of them.

    Raises `AuctionRefused` if the auction is not `draft` or `scheduled`. The
    refusals `offer` itself raises -- `offering_writes.OfferRefused`,
    `lot_writes.EmptyLot` -- are left to propagate unchanged, so a batch of
    adds stays all-or-nothing the way `offer` already guarantees.
    """
    if auction.status not in (AuctionStatus.draft, AuctionStatus.scheduled):
        raise AuctionRefused(
            f"auction #{auction.id} is {auction.status.value}, so lots cannot be added"
        )
    if isinstance(lot, InventoryItem):
        lot = _lot_of_one(db, lot)

    listing = offering_writes.offer(
        db,
        lot=lot,
        venue=auction.sales_venue,
        listing_format=ListingFormat.auction,
        price=price if price is not None else Decimal("0"),
        title=title if title is not None else lot.title,
        description=description if description is not None else lot.description,
        external_id=external_id,
    )
    auction_lot = AuctionLot(
        auction_id=auction.id,
        listing_id=listing.id,
        lot_number=lot_number,
        reserve=reserve,
    )
    db.add(auction_lot)
    db.flush()
    return auction_lot


def _lot_of_one(db: Session, item: InventoryItem) -> SalesLot:
    """Wrap a single item in a new assembling lot, through `lot_writes`.

    Through the writer, not a bare `SalesLot(...)`, for the reason every
    other fixture and caller in this codebase builds a lot that way: one
    assembled around `lot_writes.add_member`'s rules -- not split, not
    already sold, not already in another open lot -- is a lot the rules have
    accepted, rather than one that merely looks like one.
    """
    wrapped = lot_writes.create_lot(
        db, title=item.source_title, description=item.description
    )
    lot_writes.add_member(db, wrapped, item)
    return wrapped


def remove_lot(db: Session, auction_lot: AuctionLot) -> None:
    """Take a lot out of its auction: end its offer, as an ordinary End.

    Ends the listing exactly the way withdrawing any offer does --
    `offering_writes.end_offer`, `sold=False` -- so a store listing it paused
    resumes, and a lot listing's `sales_lot` dissolves and releases its
    members back to `held` (`offering_writes._end`).

    The `auction_lot` row itself is deleted, not left behind ended.
    `sales_lot` stays forever once offered, because it is the permanent
    record of a group that really was shown to a buyer; `auction_lot` is not
    that record -- it names a numbered slot in a sale that may still be
    reshuffled, and once its listing is no longer offered here there is
    nothing left for the row to describe. Deleting it also frees
    `lot_number` for reuse, which `uq_auction_lot_auction_lot_number` would
    otherwise hold onto forever for a lot that never sold.

    Raises `AuctionRefused` if the auction has already closed, settled or
    been cancelled -- editing a finished or abandoned auction's lot table
    makes no sense once results are being entered, or nothing is happening
    any more.

    See this module's own docstring for why this does not need
    `offering_writes.refuse_if_lot_unheld`: `end_offer` is always called here
    on `auction_lot.listing`, a listing this function was handed by name.
    """
    auction = auction_lot.auction
    if auction.status not in _LOTS_REMOVABLE:
        raise AuctionRefused(
            f"auction #{auction.id} is {auction.status.value}, "
            "so lots cannot be removed"
        )
    offering_writes.end_offer(db, auction_lot.listing)
    db.delete(auction_lot)
    db.flush()


def schedule(db: Session, auction: Auction) -> None:
    """Move a draft auction to scheduled.

    Raises `AuctionRefused` unless the auction is `draft`.
    """
    if auction.status is not AuctionStatus.draft:
        raise AuctionRefused(
            f"auction #{auction.id} is {auction.status.value}, "
            "so it cannot be scheduled"
        )
    auction.status = AuctionStatus.scheduled
    db.flush()


def consign(db: Session, auction: Auction, *, on_date: date) -> None:
    """Move every member item into the house's consigned location.

    Only an `auction_house` platform ever does this -- a live or marketplace
    auction never leaves the premises (spec, *Consignment custody*). Every
    item moves through `lifecycle_writes.set_location`, never by assigning
    `inventory_item.storage_location_id` here, so the location history stays
    the single source of truth `lifecycle_writes.py`'s own docstring
    describes.

    Raises `AuctionRefused` if the platform is not an auction house, or if
    the auction is not `scheduled`. Raises `RuntimeError` if the `consigned`
    storage-location kind is not seeded: a live database that has run the
    migration but not `python -m app.seeding load` is a real, expected state
    (migration `e267ec3aedc1`'s own docstring), and this is the message that
    tells the owner what to do. Nothing here creates the kind on the fly or
    falls back to a different one -- the same shape as
    `app.sales_venues.ensure_store_venue`'s `RuntimeError` for a missing
    `own_store` platform kind.
    """
    venue = auction.sales_venue
    if venue.kind.code != _AUCTION_HOUSE_KIND_CODE:
        raise AuctionRefused(
            f"{venue.name} is not an auction house: nothing leaves the "
            f"premises for a {venue.kind.code} auction"
        )
    if auction.status is not AuctionStatus.scheduled:
        raise AuctionRefused(
            f"auction #{auction.id} is {auction.status.value}, "
            "so it cannot be marked consigned"
        )
    location = _consigned_location(db, venue.name)
    for auction_lot in auction.lots:
        for item in offering_writes.offered_items(db, auction_lot.listing):
            lifecycle_writes.set_location(db, item, location.id)
    auction.consigned_on = on_date
    auction.status = AuctionStatus.consigned
    db.flush()


def _consigned_location(db: Session, institution: str) -> StorageLocation:
    """The house's consigned location, created the first time it is needed.

    One row per auction-house platform, `institution` the platform's name --
    the reference vocabulary's own note on the `consigned` code (`data/
    reference/operations.json`). Found by a plain lookup rather than raced
    with an upsert: two concurrent first-consignments of the same platform
    are not among the races this phase's spec calls out (*Concurrency*), and
    `auction.version`'s optimistic lock already serialises two consigns of
    the same auction.
    """
    kind_id = db.scalar(
        select(StorageLocationKind.id).where(
            StorageLocationKind.code == _CONSIGNED_KIND_CODE
        )
    )
    if kind_id is None:
        raise RuntimeError(
            f"storage_location_kind {_CONSIGNED_KIND_CODE!r} is not seeded: "
            "run `python -m app.seeding load`"
        )
    found = db.scalar(
        select(StorageLocation).where(
            StorageLocation.storage_location_kind_id == kind_id,
            StorageLocation.institution == institution,
        )
    )
    if found is not None:
        return found
    location = StorageLocation(
        storage_location_kind_id=kind_id, institution=institution
    )
    db.add(location)
    db.flush()
    return location


def close(db: Session, auction: Auction) -> None:
    """Close the auction: lot results may now be entered (a later task's job).

    Raises `AuctionRefused` if the auction has already closed, settled or
    been cancelled.
    """
    if auction.status not in (
        AuctionStatus.draft,
        AuctionStatus.scheduled,
        AuctionStatus.consigned,
    ):
        raise AuctionRefused(
            f"auction #{auction.id} is {auction.status.value}, so it cannot be closed"
        )
    auction.status = AuctionStatus.closed
    db.flush()


def cancel(db: Session, auction: Auction) -> None:
    """Cancel the auction and remove every lot it still holds.

    Each lot is removed exactly as `remove_lot` removes one -- ending its
    offer, resuming any store listing it paused, dissolving its sales lot --
    so nothing is left claimed by a sale that is not happening. The removals
    run before the status moves to `cancelled`, so `remove_lot`'s own status
    check still sees the auction in whichever editable status it is being
    cancelled from, rather than the terminal one this function is about to
    set.

    Raises `AuctionRefused` if the auction has already closed, settled or
    been cancelled.

    Needs no `offering_writes.refuse_if_lot_unheld` for the same reason
    `remove_lot` does not -- see this module's own docstring: every
    `end_offer` call this makes goes through `remove_lot`, on a listing named
    by iterating `auction.lots`, never one reached through `lock_for_sale`'s
    derived half.
    """
    if auction.status in (
        AuctionStatus.closed,
        AuctionStatus.settled,
        AuctionStatus.cancelled,
    ):
        raise AuctionRefused(f"auction #{auction.id} is already {auction.status.value}")
    for auction_lot in list(auction.lots):
        remove_lot(db, auction_lot)
    auction.status = AuctionStatus.cancelled
    db.flush()
