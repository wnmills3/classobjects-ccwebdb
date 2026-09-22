"""Auction transitions: adding and removing lots, and the auction's own life.

Task 2 of the auctions phase. `app/models/auctions.py` (Task 1) built the
schema; this module is the sole writer of `auction` and `auction_lot`, the
same single-writer discipline `offering_writes.py` keeps for `listing.status`,
`offer_claim` and `sales_lot.status`, and `lifecycle_writes.py` keeps for
`inventory_item.storage_location_id`. **One deliberate exception**, confirmed
by the fix-round-1 review: `_consigned_location` also constructs a
`StorageLocation` row. The brief's Step 3 and the spec's *Consignment
custody* both require creating one on first use, and `auctions.py` is the
only place in `backend/app` that constructs a `StorageLocation` at all, so no
existing single writer of that table is being bypassed -- there is no other
writer to collide with.

Adding or removing a lot **is** an offer or an ending -- "Same refusals and
pausing as any offer -- it *is* an offer" (the Task 2 brief's own test
docstring) -- so this module calls `offering_writes.offer` and
`offering_writes.end_offer` for that half rather than writing
`listing.status`, `offer_claim` or `sales_lot.status` itself. Moving items
into consignment custody **is** a location change, so `consign` and the
return-from-consignment half of `remove_lot`/`cancel` call
`lifecycle_writes.set_location` rather than assigning
`inventory_item.storage_location_id` directly. Settlement (`settle`) is a
later task's writer and is deliberately not here, but `remove_lot` and
`cancel` already carry the `returned_to_location_id` contract it will share
(ruling R9, fix round 1) -- see `_return_from_consignment`, the one place
"move these items back" is implemented. Custody is tracked by
`auction.consigned_on is not None`, never by `auction.status is
AuctionStatus.consigned` (ruling R13, fix round 2): `close` accepts a
`consigned` auction without clearing the date, so status alone cannot tell
whether the house still holds something -- fix round 1's `status ==
consigned` check let `consign -> close -> cancel`, and `consign -> close ->
remove_lot`, walk straight past the return requirement fix round 1 had just
added. `consigned_on` is cleared in exactly one place, `cancel`, once every
one of the auction's lots has actually been returned -- never inside
`_remove_lot`, which only ever returns one lot's worth.

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
(`cancel`, which removes lots through `_remove_lot` one at a time) -- never a
listing reached through `lock_for_sale`'s *derived* half, the search that
finds a listing because it holds one of the caller's items without the
caller ever naming it (`routers.inventory.receive_items` is the one caller
today that reaches a listing that way). And `end_offer` itself, called here
with `sold=False` (withdrawal, never a sale), only ever ends the one listing
it is handed -- the `paused_by_it` listings it also touches are *resumed*,
not ended, on that path; only `sold=True` ends them, and this module never
passes it. So no call in this module ends a listing it did not name, and
`refuse_if_lot_unheld` has nothing to discharge here. Independently verified
in the fix-round-1 review, including that `lock_for_sale` seeds `lot_ids`
from the *named* listings before `_acquire` runs, so the lot row of the
listing `remove_lot` names is always taken first regardless.
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


#: Statuses in which a lot may still be withdrawn *individually*, through the
#: public `remove_lot`: before the sale has closed. `consigned` is included
#: -- an auction house may still need a lot pulled back after physical
#: custody moved, before the sale itself runs. `remove_lot` also accepts
#: `closed` on its own, narrower condition -- `auction.consigned_on is not
#: None` -- not listed here because that check needs the auction row, not
#: just its status; see `remove_lot`'s own docstring (ruling R13, fix round
#: 2). `cancel` has its own, wider status boundary (ruling R8) and reaches
#: lot removal through `_remove_lot` directly, bypassing this gate -- see
#: `cancel`'s own docstring.
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

    **Builds the row through the `auction`/`listing` relationships, not their
    foreign-key columns.** `AuctionLot(auction_id=..., listing_id=...)` fires
    no backref event -- SQLAlchemy synchronises a *loaded* collection only
    when a relationship attribute is assigned, never from a bare FK column --
    so a session that had already read `auction.lots` before this call would
    keep seeing the old, short list for the rest of the session. `consign`
    and `cancel` both iterate `auction.lots`; against the FK-only
    construction, either could silently skip the very lot this call just
    added. Fixed in fix round 1 (Important #1).
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
        auction=auction,
        listing=listing,
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


def remove_lot(
    db: Session,
    auction_lot: AuctionLot,
    *,
    returned_to_location_id: int | None = None,
) -> None:
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

    `returned_to_location_id` (ruling R9, fix round 1; keyed on custody, not
    status, since ruling R13, fix round 2): **required** whenever
    `auction.consigned_on is not None`, because its items physically left the
    premises and something has to say where they came back to before the lot
    can be considered withdrawn -- the spec's *Consignment custody* moves
    items "there, and back", and this is the "back" half for a withdrawal
    rather than a settlement. `consigned_on is not None` means the house
    still holds *something* of this auction's, whatever the auction's
    current *status* is -- in particular, `close` accepts a `consigned`
    auction and does not clear the date, so a lot can still need returning
    from a `closed` auction. Checking `status == consigned` instead was
    fix round 1's defect (Important #1, fix round 2): `consign -> close ->
    remove_lot` read `closed`, not `consigned`, and skipped the requirement
    and the move entirely, leaving the coin filed at the house with nothing
    saying so. Ignored otherwise; a caller may pass `None` (the default) or
    omit it entirely when `consigned_on` is already `None`.

    Raises `AuctionRefused` if the auction has already closed, settled or
    been cancelled -- editing a finished or abandoned auction's lot table
    makes no sense once results are being entered, or nothing is happening
    any more. Also raises `AuctionRefused`, naming the missing argument, if
    `auction.consigned_on is not None` and `returned_to_location_id` is not
    given.

    See this module's own docstring for why this does not need
    `offering_writes.refuse_if_lot_unheld`: `end_offer` is always called here
    on `auction_lot.listing`, a listing this function was handed by name.

    **`closed` is accepted too, but only when the house still holds
    something (`auction.consigned_on is not None`)** -- ruling R13, fix
    round 2, extended here beyond its letter to keep the interpretation
    coherent: refusing a `closed`-and-consigned auction would mean no public
    call could ever bring a single withdrawn lot's coins home once the
    auction closed, forcing a whole-auction `cancel` for what may be one
    disputed lot out of many. A `closed` auction that was **never**
    consigned still refuses, unchanged from fix round 1 -- there is nothing
    to return, and reshuffling a finished auction's lot table for no
    physical reason still makes no sense.
    """
    auction = auction_lot.auction
    closed_but_consigned = (
        auction.status is AuctionStatus.closed and auction.consigned_on is not None
    )
    if auction.status not in _LOTS_REMOVABLE and not closed_but_consigned:
        raise AuctionRefused(
            f"auction #{auction.id} is {auction.status.value}, "
            "so lots cannot be removed"
        )
    _remove_lot(db, auction_lot, returned_to_location_id=returned_to_location_id)


def _remove_lot(
    db: Session,
    auction_lot: AuctionLot,
    *,
    returned_to_location_id: int | None,
) -> None:
    """The removal itself, without the status gate the public `remove_lot` applies.

    Factored out so `cancel` -- whose own status boundary is wider than a
    single lot's removal (ruling R8: `cancel` accepts `closed`, `remove_lot`
    does not) -- can remove each of an auction's lots without `remove_lot`'s
    own `_LOTS_REMOVABLE` check refusing a `closed` auction that `cancel` has
    already decided, by its own check, is cancellable.

    If the house still holds something of this auction's
    (`auction.consigned_on is not None`, ruling R13 -- **not** `auction.status
    is AuctionStatus.consigned`, which a `closed` auction fails even though
    the coins never came home), the lot's items are moved back to
    `returned_to_location_id` (required; see `remove_lot`'s docstring)
    *before* the offer ends -- `_return_from_consignment` reads the lot's
    open membership through `offering_writes.offered_items`, which
    `end_offer` would otherwise have already released.

    Never clears `auction.consigned_on` -- that is an auction-level fact
    (ruling R13, fix round 2), owned by `cancel`, which clears it only once
    every one of the auction's lots has been returned. Removing a single lot
    out of several leaves the house still holding the rest.
    """
    auction = auction_lot.auction
    if auction.consigned_on is not None:
        if returned_to_location_id is None:
            raise AuctionRefused(
                f"auction #{auction.id} is consigned: returned_to_location_id "
                "is required to bring its items back before the lot can be "
                "removed"
            )
        _return_from_consignment(db, auction_lot, returned_to_location_id)
    offering_writes.end_offer(db, auction_lot.listing)
    db.delete(auction_lot)
    db.flush()


def _return_from_consignment(
    db: Session,
    auction_lot: AuctionLot,
    location_id: int,
    *,
    user_id: int | None = None,
) -> None:
    """Move one auction lot's items back from the house, through `lifecycle_writes`.

    The one implementation of "move these items back" (ruling R9, fix round
    1): `remove_lot` and `cancel` both call it today, through `_remove_lot`,
    and Task 3's `settle` is meant to call it too rather than growing a
    second copy for the "unsold at an auction house" case the spec's
    *Settle* row describes.

    Reads the lot's currently open members through
    `offering_writes.offered_items` -- the one answer in the codebase to
    "which items does this listing offer" -- so it must run before
    `end_offer` releases that membership, never after.
    """
    for item in offering_writes.offered_items(db, auction_lot.listing):
        lifecycle_writes.set_location(
            db,
            item,
            location_id,
            user_id=user_id,
            note=f"Returned from consignment, auction #{auction_lot.auction_id}",
        )


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


def consign(
    db: Session, auction: Auction, *, on_date: date, user_id: int | None = None
) -> None:
    """Move every member item into the house's consigned location.

    Only an `auction_house` platform ever does this -- a live or marketplace
    auction never leaves the premises (spec, *Consignment custody*). Every
    item moves through `lifecycle_writes.set_location`, never by assigning
    `inventory_item.storage_location_id` here, so the location history stays
    the single source of truth `lifecycle_writes.py`'s own docstring
    describes. The move is noted with the auction it belongs to (fix round 1,
    Minor #8), so the history says *why* the item went, not only where.

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
    note = f"Consigned to auction #{auction.id}"
    for auction_lot in auction.lots:
        for item in offering_writes.offered_items(db, auction_lot.listing):
            lifecycle_writes.set_location(
                db, item, location.id, user_id=user_id, note=note
            )
    auction.consigned_on = on_date
    auction.status = AuctionStatus.consigned
    db.flush()


def _consigned_location(db: Session, institution: str) -> StorageLocation:
    """The house's consigned location, created the first time it is needed.

    One row per auction-house platform, `institution` the platform's name,
    `identifier` always `NULL` -- the reference vocabulary's own note on the
    `consigned` code (`data/reference/operations.json`). The lookup filters
    on `identifier IS NULL` explicitly (fix round 1, Minor #5): the table's
    actual uniqueness key, `uq_storage_location_identity`, is
    `(kind_id, institution, identifier)`, and without this predicate a
    "Consigned: Heritage" row that also happened to carry an `identifier` --
    a crate or shelf the owner recorded by hand -- would match, and an
    unordered `Session.scalar` would return whichever of two such rows came
    back first.

    Found by a plain lookup rather than raced with an upsert against a
    unique index: Postgres treats two `NULL` `identifier` values as
    *distinct*, so `uq_storage_location_identity` is no backstop here, and
    two concurrent first-consignments of the *same* platform, from two
    *different* auctions, can each pass the `found is None` check and insert
    a duplicate location (fix round 1, Minor #6 -- confirmed as a real, if
    minor, gap: a duplicate row and items split across two "Consigned:
    Heritage" entries, not lost data). `auction.version`'s optimistic lock
    only serialises two consigns of the *same* auction and does not cover
    this. Not fixed here: the spec's *Concurrency* paragraph does not name
    this race, and closing it for real needs a partial unique index on
    `(storage_location_kind_id, institution) WHERE identifier IS NULL` --
    a migration, which this task does not add. This docstring is that
    decision's record, so the gap is a documented choice rather than an
    oversight the next reader has to rediscover.
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
            StorageLocation.identifier.is_(None),
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

    Raises `AuctionRefused` unless the auction is `scheduled` or `consigned`.

    **Not `draft` (ruling R8, fix round 1).** A `draft` auction never
    happened -- there is nothing to close, and accepting it made `close` a
    one-way door: `cancel` refused `closed` outright (before this same
    ruling widened it), so one mis-click on an unscheduled auction produced
    one that could never be cancelled, un-closed, or have its lots touched
    again, with `settle` -- a later task -- as the only exit for a sale that
    never ran. A `draft` auction that should not proceed is `cancel`led, not
    closed.
    """
    if auction.status not in (AuctionStatus.scheduled, AuctionStatus.consigned):
        raise AuctionRefused(
            f"auction #{auction.id} is {auction.status.value}, so it cannot be closed"
        )
    auction.status = AuctionStatus.closed
    db.flush()


def cancel(
    db: Session, auction: Auction, *, returned_to_location_id: int | None = None
) -> None:
    """Cancel the auction and remove every lot it still holds.

    Each lot is removed exactly as `remove_lot` removes one -- ending its
    offer, resuming any store listing it paused, dissolving its sales lot --
    so nothing is left claimed by a sale that is not happening. The removals
    run before the status moves to `cancelled`.

    **Accepts every status except `settled` and `cancelled` (ruling R8, fix
    round 1) -- including `closed`.** The spec's own line reads
    `draft -> scheduled -> [consigned] -> closed -> settled, **or
    cancelled**`, read here as making `cancelled` an alternative terminal
    state from anywhere before `settled`: a closed auction whose sale
    happened and whose settlement is being abandoned is a real case, not a
    contradiction. Lots are removed through `_remove_lot` directly, **not**
    through the public `remove_lot`, precisely so a `closed` auction --
    which `remove_lot`'s own `_LOTS_REMOVABLE` refuses individually -- can
    still be cancelled as a whole; `cancel`'s own check above is the gate
    that applies.

    Lots are removed in **ascending id order** (fix round 1, Minor #7):
    `auction.lots` carries no `order_by` of its own
    (`app/models/auctions.py`), and locking them one at a time in whatever
    order the collection happens to return would let two concurrent cancels
    of the same auction acquire their rows in different orders and deadlock,
    rather than one losing cleanly with a 409. Sorting first makes the order
    identical for both sessions; within a single pass the canonical
    lot -> items -> listings order inside `end_offer` is unaffected.

    `returned_to_location_id` (ruling R9, fix round 1; keyed on custody, not
    status, since ruling R13, fix round 2): the same contract `remove_lot`
    carries, and for the same reason -- **required** whenever
    `auction.consigned_on is not None`, since every one of its lots needs
    somewhere to return its items to before it can be withdrawn. **Not**
    `auction.status is AuctionStatus.consigned`: `close` accepts a
    `consigned` auction without clearing the date, so `consign -> close ->
    cancel` used to read `closed`, skip this requirement entirely, and cancel
    an auction whose coins were still sitting at the house with nothing
    saying so (Important #1, fix round 2 -- the defect fix round 1's own
    ruling R8 opened by letting `cancel` accept `closed`, and R9 did not
    anticipate). When the return happens, `consigned_on` is cleared along
    with the status move -- **only here**, once every lot has actually been
    returned, never inside `_remove_lot`: a single lot coming home out of
    five leaves the house still holding the other four, so the field is an
    auction-level fact, not a per-lot one.

    Checked here **as well as** inside `_remove_lot`'s own identical guard,
    and the duplication is deliberate, not an oversight: a consigned
    auction with **zero** lots (`consign` never requires at least one) never
    enters the removal loop below, so `_remove_lot`'s check would never run
    at all -- this is the only guard that covers that case. Mutation-verified
    apart from the per-lot check in fix round 1
    (`test_cancelling_a_consigned_auction_with_no_lots_requires_a_return_location`).

    Raises `AuctionRefused` if the auction has already been settled or
    cancelled, or if `auction.consigned_on is not None` and
    `returned_to_location_id` is missing.

    Needs no `offering_writes.refuse_if_lot_unheld` for the same reason
    `remove_lot` does not -- see this module's own docstring: every
    `end_offer` call this makes goes through `_remove_lot`, on a listing
    named by iterating `auction.lots`, never one reached through
    `lock_for_sale`'s derived half.
    """
    if auction.status in (AuctionStatus.settled, AuctionStatus.cancelled):
        raise AuctionRefused(f"auction #{auction.id} is already {auction.status.value}")
    if auction.consigned_on is not None and returned_to_location_id is None:
        raise AuctionRefused(
            f"auction #{auction.id} is consigned: returned_to_location_id is "
            "required to bring its items back before it can be cancelled"
        )
    still_consigned = auction.consigned_on is not None
    for auction_lot in sorted(auction.lots, key=lambda row: row.id):
        _remove_lot(db, auction_lot, returned_to_location_id=returned_to_location_id)
    if still_consigned:
        auction.consigned_on = None
    auction.status = AuctionStatus.cancelled
    db.flush()
