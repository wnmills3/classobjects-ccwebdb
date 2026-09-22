"""Auction transitions: adding and removing lots, settling, and an auction's life.

Tasks 2 and 3 of the auctions phase. `app/models/auctions.py` (Task 1) built the
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
`inventory_item.storage_location_id` directly. And settling a lot **is** a
sale, so `settle` (Task 3) calls `sales_writes.record_sale_lines` for the
money rather than writing `sales_order`, `sales_order_fee` or
`sales_order_item_share` here -- it is the second caller
`sales_writes`' single entry point was built for (spec, *Where
record-a-sale lives*), and the only thing it needed that recording one sale
did not is several listings on one order, because a house bills per buyer.
`remove_lot`, `cancel` and `settle` share the `returned_to_location_id`
contract (ruling R9, fix round 1) through `_return_from_consignment`, the
one place "move these items back" is implemented. Custody is tracked by
`auction.consigned_on is not None`, never by `auction.status is
AuctionStatus.consigned` (ruling R13, fix round 2): `close` accepts a
`consigned` auction without clearing the date, so status alone cannot tell
whether the house still holds something -- fix round 1's `status ==
consigned` check let `consign -> close -> cancel`, and `consign -> close ->
_remove_lot` (reached through `cancel`), walk straight past the return
requirement fix round 1 had just added. `consigned_on` is cleared in exactly
two places, `cancel` and `settle`, each once every one of the auction's lots
that had to come back actually has -- never inside `_remove_lot`, which only
ever returns one lot's worth. The **public** `remove_lot` never sees this shape
at all: it refuses a `closed` auction unconditionally (ruling R14, fix
round 3), so `consign -> close -> remove_lot` is not a reachable call
sequence -- only `cancel`, acting on the whole auction, can touch a closed
auction's lots, through `_remove_lot` directly.

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
not ended, on that path. Independently verified in the fix-round-1 review,
including that `lock_for_sale` seeds `lot_ids` from the *named* listings
before `_acquire` runs, so the lot row of the listing `remove_lot` names is
always taken first regardless.

**`settle` does take the `sold=True` branch, and the answer is still no --
but for a different reason, which had to be established rather than
inherited.** A sale ends the store listings its offer paused instead of
resuming them (spec, *Record a sale*), and those are listings settlement
never named. The obligation covers exactly one case: one of them being a
**lot** listing, whose lot row `_end` would then rewrite. It cannot happen.
`paused_by_listing_id` is written in exactly one statement in the whole
codebase -- `offering_writes.offer`'s `to_pause` loop -- over the own-store
listings that already hold a member being offered elsewhere; and each member
passes `_refuse_unofferable` -> `_refuse_grouped` first, in the same loop
iteration, which refuses any coin that is an open member of an **offered**
lot. A lot listing holds its coins only through the claims `offer` wrote
(`_holds_any`), and `_end` releases those claims and the lot's memberships in
the same breath, so "a lot listing holds this coin" and "this coin is in an
offered lot" are one fact. So no lot listing can ever carry
`paused_by_listing_id`, `end_offer(sold=True)`'s second `_end` only ever ends
item listings -- which return before touching a lot row -- and
`refuse_if_lot_unheld` still has nothing to discharge in this module.
`offering_writes._end`'s own docstring already said this is what
`_refuse_grouped` makes true; Task 3 measured it rather than reading it, in
`test_a_lot_listing_can_never_be_paused_by_another_offer` and
`test_settlement_ends_only_item_listings_it_did_not_name`
(`tests/test_auction_settlement.py`). Those two are the tripwire if
`_refuse_grouped` is ever relaxed.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import lifecycle_writes, lot_writes, offering_writes, sales_writes
from .models import (
    Auction,
    AuctionLot,
    AuctionLotResult,
    AuctionStatus,
    InventoryItem,
    ListingFormat,
    SalesLot,
    SalesOrder,
    StorageLocation,
    StorageLocationKind,
    User,
)

__all__ = [
    "AuctionRefused",
    "SettlementInputInvalid",
    "SettlementLine",
    "add_lot",
    "cancel",
    "close",
    "consign",
    "remove_lot",
    "schedule",
    "settle",
]


class AuctionRefused(Exception):
    """One auction transition cannot happen now, with the reason for a person."""


class SettlementInputInvalid(AuctionRefused):
    """The settlement grid itself is malformed, not in conflict with the state.

    A hammer price or a fee that is negative, or given to less than the cent
    -- bad input, the spec's *Errors* split between "bad input" and "conflicts
    with other work ... or a stale version". Everything else `settle` refuses
    stays plain `AuctionRefused`: the auction is not `closed`, a lot has no
    result or two, a line names a lot from another sale, a sold lot outside
    an auction house names no buyer, fees are given for someone who bought
    nothing, or coins have nowhere to come back to. Each of those is a real
    conflict between what the grid says and what the auction is, which the
    owner could not have known from the form alone.

    **Task 5 maps this to 422 and plain `AuctionRefused` to 409**, so the
    same bad number refuses the same way whether it was typed into the
    settlement grid or into the Listings page's Record sale -- that one
    reaches `sales_writes.SaleInputInvalid`, which this deliberately mirrors
    (ruling R15). A subclass, not a field, for exactly the reasons
    `SaleInputInvalid`'s own docstring gives: every existing
    `except AuctionRefused` and `pytest.raises(AuctionRefused)` keeps
    catching this unchanged, and an HTTP layer dispatches by `except` clause
    order rather than by matching on a message string.

    **The same warning applies here as there: mypy does not check that
    ordering.** A reversed `except AuctionRefused` before
    `except SettlementInputInvalid` still type-checks cleanly, and every 422
    silently becomes a 409. `test_bad_money_and_a_conflict_are_different_refusals`
    in `test_auction_settlement.py` is what stands between that reversal and
    a silent regression until Task 5 adds its own router-level twin.

    **A grid with both kinds of problem refuses as the wider one.** `settle`
    reports every problem in one message, and a message that contains a real
    conflict is not merely bad input -- so `SettlementInputInvalid` is raised
    only when *every* problem in it is a money problem. 409 is the safer
    answer when the two are mixed: it tells the owner something about the
    auction is in the way, which is true, rather than that the form alone was
    wrong, which is not.
    """


#: Statuses in which a lot may still be withdrawn *individually*, through the
#: public `remove_lot`: before the sale has closed. `consigned` is included
#: -- an auction house may still need a lot pulled back after physical
#: custody moved, before the sale itself runs. `closed` is deliberately
#: **not** included, and stays that way even when the house still holds the
#: lot's coins (`auction.consigned_on is not None`) -- ruling R14, fix round
#: 3, reverting fix round 2's own widening. Once closed, the sale has
#: happened; the only ways out are `settle` and `cancel`, and a lot that did
#: not sell is `AuctionLotResult.withdrawn`, a **settlement** result Task 3's
#: `settle` will record, not a removal with no record at all. `cancel` has
#: its own, wider status boundary (ruling R8) and reaches lot removal
#: through `_remove_lot` directly, bypassing this gate entirely -- see
#: `cancel`'s own docstring; `_remove_lot` itself still keys its
#: custody-return logic on `auction.consigned_on`, never on this tuple (see
#: its own docstring).
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
    `auction.consigned_on is not None` at the time of removal, because its
    items physically left the premises and something has to say where they
    came back to before the lot can be considered withdrawn -- the spec's
    *Consignment custody* moves items "there, and back", and this is the
    "back" half for a withdrawal rather than a settlement. This function's
    own gate below never lets it see a `closed`-and-still-consigned auction
    (ruling R14, fix round 3 -- see that paragraph), so in practice this
    check only ever fires here for a `consigned` auction; the predicate
    still reads `consigned_on`, not the status, because `_remove_lot` is the
    one place that logic actually lives and `cancel` reaches the identical
    check through `_remove_lot` directly, on a `closed`-but-consigned
    auction, which is exactly the shape this function itself now refuses.
    Ignored otherwise; a caller may pass `None` (the default) or omit it
    entirely when `consigned_on` is already `None`.

    Raises `AuctionRefused` if the auction has already closed, settled or
    been cancelled -- editing a finished or abandoned auction's lot table
    makes no sense once results are being entered, or nothing is happening
    any more.

    **`closed` refuses unconditionally, whether or not the auction was ever
    consigned** (ruling R14, fix round 3, reverting a widening fix round 2
    added here). Once closed, the sale has happened and the only ways out
    are `settle` and `cancel`: a lot that did not sell is
    `AuctionLotResult.withdrawn`, a **settlement** result Task 3's `settle`
    will record -- returning its items the same way `_return_from_consignment`
    does here -- not a removal with no record of what became of it. Pulling
    a single lot out of a closed, still-consigned auction through this
    function would take coins out of the sale with nothing in the schema
    saying so.

    Also raises `AuctionRefused`, naming the missing argument, if the
    auction is `consigned` and `returned_to_location_id` is not given.

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


@dataclass(frozen=True)
class SettlementLine:
    """One lot's outcome, as the settlement grid collected it.

    `hammer_price` and `buyer_username` are meaningful only for a `sold`
    lot; an `unsold` or `withdrawn` one leaves both at their defaults, and
    `settle` refuses a price given alongside either -- money entered against
    a lot that did not sell is a mis-filled grid, not a fact.

    `buyer_username` of `None` means two different things depending on the
    platform, which is why `settle` reads it against the venue's kind rather
    than on its own: at an auction house it is the standing **undisclosed
    buyer** the spec's *Decisions* table gives every house that does not name
    its buyers, and anywhere else it is a blank the owner still has to fill
    in.
    """

    auction_lot_id: int
    result: AuctionLotResult
    hammer_price: Decimal | None = None
    buyer_username: str | None = None


def _buyer_name(username: str | None) -> str | None:
    """The username as the owner typed it, minus surrounding whitespace.

    What goes **into the customer record**, through `buyers.venue_buyer`.
    Empty and whitespace-only both become `None`, which is that function's
    own rule for the undisclosed buyer -- an untouched optional form field
    sends `""`, not a missing field, and `venue_username = ''` is a distinct
    non-null value the partial unique index cannot catch.

    Deliberately **not** casefolded: a customer row must carry the name the
    owner actually typed, `CoinFan88`, not a flattening of it. The folding
    belongs to `_buyer_key` below, which decides only how the grid is
    grouped.
    """
    stripped = username.strip() if username else None
    return stripped or None


def _buyer_key(username: str | None) -> str | None:
    """The grouping key for a buyer: `_buyer_name`, casefolded.

    This is what decides how many *orders* a settlement writes, so it has to
    answer the same question `buyers.venue_buyer` answers when it decides how
    many *customers* exist -- and that one matches on
    `func.lower(venue_username) == stored.lower()`, case-insensitively, with
    a case-insensitive partial unique index (`uq_customer_venue_username`)
    behind it. Grouping case-sensitively while the customer lookup folds is
    how a grid spelling one buyer `CoinFan88` on one row and `coinfan88` on
    the next wrote **two orders against one customer** (ruling R17): the
    orders reconcile against the house's statement one short, and nothing in
    the schema says they belong together.

    `casefold`, not `lower`: it is the operation defined for caseless
    matching rather than for display, it handles the cases `lower` does not,
    and it costs nothing here. That makes this very slightly wider than
    `venue_buyer`'s SQL `lower()`, which is the safe direction -- two
    spellings this groups together still resolve to one customer, whereas two
    it grouped apart could not be put back together afterwards.

    The key never leaves this module. `_BuyerGroup` carries the first
    spelling the grid used alongside it, and that is what reaches
    `venue_buyer`.
    """
    name = _buyer_name(username)
    return None if name is None else name.casefold()


def _lock_auction(db: Session, auction: Auction) -> Auction:
    """Take the auction row FOR UPDATE and re-read it. The outermost lock.

    **A new, outermost level above `offering_writes`' canonical order**
    (`docs/specs/lock-order-design.md`), and it must be taken strictly
    *before* `lock_for_sale`, never after or between. Nothing else in the
    codebase ever takes an `auction` row, so this level is contended only by
    other auction transitions and cannot invert against lots, items or
    listings -- which is what makes adding a level here safe at all.

    Why it is needed: `settle` decides what to write from the auction's
    status and its lot table, and then writes both. Two settlements of one
    auction that each read `closed` would each go on to record every lot's
    sale, and `lock_for_sale` cannot serialise them -- it takes the *items*,
    and the second settlement would simply wait for them and then do its
    work on a lot table the first had already settled. The auction row is the
    only thing both passes are guaranteed to want. The loser waits here,
    re-reads `settled`, and is refused by `settle`'s own status check with a
    message rather than by a unique index or a half-written second order.

    `db.flush()` first, because `Session.refresh` expires an instance
    *before* it reloads: a pending change to this auction would be discarded
    rather than written. `populate_existing` for the reason
    `offering_writes._lock_listing_rows` gives -- a row already in the
    identity map comes back locked but stale without it.

    Deliberately one statement, and deliberately not folded into `settle`:
    Task 4's race test mutates exactly this, and a lock spread across three
    lines of another function is one a mutation can silently half-remove.
    """
    db.flush()
    return db.scalars(
        select(Auction)
        .where(Auction.id == auction.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one()


def _lots_of(db: Session, auction: Auction) -> list[AuctionLot]:
    """This auction's lots, read fresh and in ascending id order.

    Never `auction.lots`: that collection carries no `order_by` of its own
    (`app/models/auctions.py`), and a session that read it before a lot was
    added keeps the short list for the rest of the session -- the defect fix
    round 1 found in `add_lot`. `settle` writes a result to every one of
    these rows, so a stale collection here is a lot left unsettled inside a
    transaction that then marks the auction `settled`.

    Ascending id for the reason `cancel` sorts: one order for every pass, so
    two concurrent settlements cannot take the same rows two ways.
    """
    return list(
        db.scalars(
            select(AuctionLot)
            .where(AuctionLot.auction_id == auction.id)
            .order_by(AuctionLot.id)
        ).all()
    )


@dataclass(frozen=True)
class _Problem:
    """One thing wrong with a settlement grid, and which refusal it belongs to.

    `bad_input` marks the `SettlementInputInvalid` half -- a number that is
    not money -- as against a genuine conflict with the auction's state
    (ruling R15). Carried per problem rather than decided at the end from the
    message text, because `settle` reports every problem in one message and
    matching on a string to pick an HTTP status is exactly the
    "default silently to the wrong status" shape `sales_writes.SaleInputInvalid`
    was made a subclass to avoid.
    """

    text: str
    bad_input: bool = False


@dataclass(frozen=True)
class _BuyerGroup:
    """One buyer's sold lots, keyed for grouping and spelled for the record.

    `key` is the casefolded form (`_buyer_key`) that every lookup uses --
    the grouping itself, and the `fees` mapping -- so one buyer spelled two
    ways in the grid is one order (ruling R17).

    `username` is the **first spelling the grid used** for that key, passed
    to `buyers.venue_buyer` unchanged: a customer record carries the name the
    owner typed, never a casefolded flattening of it. First rather than last
    only because something has to win and the earliest lot is the one the
    owner entered first; `venue_buyer` matches case-insensitively either way,
    so both spellings resolve to the same customer regardless of which is
    stored.
    """

    key: str | None
    username: str | None
    lots: list[tuple[AuctionLot, Decimal]]


def _grid_problems(
    auction: Auction,
    lots: Sequence[AuctionLot],
    by_lot: Mapping[int, SettlementLine],
    unplaced: Sequence[SettlementLine],
    duplicated: Sequence[str],
    fees: Mapping[str | None, Sequence[sales_writes.FeeLine]],
    returned_to_location_id: int | None,
) -> list[_Problem]:
    """Everything wrong with this settlement grid, not merely the first thing.

    A list rather than a raise, because the console shows a grid and fixing
    one problem per round trip is miserable -- the owner wants to see every
    lot that needs attention at once. `settle` turns whatever comes back
    into a single refusal, whose class it picks from the `bad_input` flags.

    Every money check asks `sales_writes.money_problem`, the same predicate
    `record_sale_lines` raises on, and every one of them is flagged
    `bad_input=True`. Two answers to "is this a real amount of money" is how
    a hammer price this function waved through becomes one
    `record_sale_lines` refuses part way down a settlement that has already
    written an order.
    """
    problems: list[_Problem] = [
        _Problem(f"lot id {line.auction_lot_id} is not in auction #{auction.id}")
        for line in unplaced
    ]
    problems += [
        _Problem(f"lot {number} was given two results") for number in duplicated
    ]
    names_buyers = auction.sales_venue.kind.code != _AUCTION_HOUSE_KIND_CODE
    for row in lots:
        line = by_lot.get(row.id)
        if line is None:
            problems.append(_Problem(f"lot {row.lot_number} has no result"))
            continue
        if line.result is not AuctionLotResult.sold:
            if line.hammer_price is not None:
                problems.append(
                    _Problem(
                        f"lot {row.lot_number} is {line.result.value}, so it cannot "
                        "have a hammer price"
                    )
                )
            continue
        if line.hammer_price is None:
            problems.append(
                _Problem(f"lot {row.lot_number} sold but has no hammer price")
            )
        else:
            money = sales_writes.money_problem(
                line.hammer_price, f"lot {row.lot_number}'s hammer price"
            )
            if money is not None:
                problems.append(_Problem(money, bad_input=True))
        if names_buyers and _buyer_key(line.buyer_username) is None:
            problems.append(_Problem(f"lot {row.lot_number} sold but names no buyer"))

    bought: set[str | None] = {
        _buyer_key(line.buyer_username)
        for line in by_lot.values()
        if line.result is AuctionLotResult.sold
    }
    seen: set[str | None] = set()
    for given, fee_lines in fees.items():
        key = _buyer_key(given)
        label = given if given is not None else "the undisclosed buyer"
        if key in seen:
            problems.append(_Problem(f"fees for {label} are given twice"))
            continue
        seen.add(key)
        if key not in bought:
            problems.append(_Problem(f"fees are given for {label}, who bought nothing"))
        problems += [
            _Problem(money, bad_input=True)
            for money in (
                sales_writes.money_problem(fee.amount, f"a fee for {label}")
                for fee in fee_lines
            )
            if money is not None
        ]

    if auction.consigned_on is not None and returned_to_location_id is None:
        coming_home = [
            row.lot_number
            for row in lots
            if row.id in by_lot and by_lot[row.id].result is not AuctionLotResult.sold
        ]
        if coming_home:
            problems.append(
                _Problem(
                    f"auction #{auction.id} is consigned: returned_to_location_id "
                    f"is required to bring back lot(s) {', '.join(coming_home)}"
                )
            )
    return problems


def _sold_by_buyer(
    lots: Sequence[AuctionLot], by_lot: Mapping[int, SettlementLine]
) -> list[_BuyerGroup]:
    """The sold lots grouped by buyer, both orders fixed and reproducible.

    One group is one order: "one order per buyer holding their lots" (spec,
    *Settle*). `lots` arrives in ascending id order and a dict keeps
    insertion order, so the groups come out in the order their first lot
    appears and the lots within a group in id order -- the same sequence
    every time, which is what lets a settlement be compared against the
    house's statement line by line.

    Grouped on the casefolded `_buyer_key` and reported with the first
    spelling seen -- see `_BuyerGroup`.
    """
    grouped: dict[str | None, _BuyerGroup] = {}
    for row in lots:
        line = by_lot[row.id]
        if line.result is not AuctionLotResult.sold:
            continue
        if line.hammer_price is None:  # pragma: no cover - refused by _grid_problems
            raise AuctionRefused(f"lot {row.lot_number} sold but has no hammer price")
        key = _buyer_key(line.buyer_username)
        group = grouped.get(key)
        if group is None:
            group = _BuyerGroup(
                key=key, username=_buyer_name(line.buyer_username), lots=[]
            )
            grouped[key] = group
        group.lots.append((row, line.hammer_price))
    return list(grouped.values())


def settle(
    db: Session,
    auction: Auction,
    lines: Sequence[SettlementLine],
    fees: Mapping[str | None, Sequence[sales_writes.FeeLine]],
    *,
    settled_by: User,
    returned_to_location_id: int | None = None,
) -> list[SalesOrder]:
    """Apply a whole settlement grid to a closed auction. One transaction.

    Every lot's result, every sold lot's money and every unsold lot's coins,
    or none of it (spec, *Settle*). Returns one `SalesOrder` per buyer, in
    the order their first lot appears in the sale.

    `fees` is keyed by buyer username -- `None` for an auction house's
    undisclosed buyer -- because a house bills per buyer **order**, not per
    lot. That is the one thing settlement needed that recording a single
    sale did not, and it is why `sales_writes` grew `record_sale_lines`
    rather than this module growing a loop over `record_sale`: two lots to
    one buyer are one order with two lines, and the order's fee divides
    across the coins of both.

    **Order of operations, and each one matters.**

    1. Take the `auction` row FOR UPDATE (`_lock_auction`), the outermost
       lock level and strictly before anything else. See that function.
    2. Refuse. Every problem in the grid is collected and reported together
       (`_grid_problems`), and nothing is written until the list comes back
       empty.
    3. Take every coin in the **whole auction** in one
       `offering_writes.lock_for_sale` call, never one per lot. That function
       is the single owner of the acquisition order -- lot rows, then items,
       then listings -- and reaching past it, or reaching it a lot at a time,
       is what reproduces the deadlock `docs/specs/lock-order-design.md`
       exists to prevent. Two lots sharing no coins still share this auction,
       which step 1 already serialised; this call is what serialises them
       against a checkout, an offer or a sale elsewhere.
    4. Sold lots, grouped by buyer: one `sales_writes.record_sale_lines` per
       buyer, which ends each listing as **sold** -- the lot `sold`, its
       members' paused store listings **ended** rather than resumed, the
       coins `sold`.
    5. Unsold and withdrawn lots: their coins come home first (only if the
       house still holds them), then `offering_writes.end_offer` with no
       sale, so the lot dissolves, paused store listings resume **at their
       old price**, and coins nothing else offers go back to `held`.
    6. `consigned_on` cleared, status `settled`.

    **Custody is keyed on `auction.consigned_on is not None`** (ruling R13),
    never on the status: `close` accepts a `consigned` auction without
    clearing the date, so a settled sale's coins may still be sitting at the
    house while the status reads `closed`. This is one of the two places the
    date is cleared -- `cancel` is the other -- and it clears it once every
    unsold and withdrawn lot has actually been returned. A sold lot's coins
    stay where they are: they left with the buyer, and moving them home
    would be a lie in the location history.

    `returned_to_location_id` is required only when the house still holds
    coins **and** at least one lot is coming back; an auction where
    everything sold needs nowhere to return to.

    A `withdrawn` lot is returned exactly as an `unsold` one is.
    `AuctionLotResult.withdrawn` is what "was in a closed auction and did not
    sell" means, which is why the public `remove_lot` refuses a closed
    auction outright (ruling R14): pulling a lot out after the sale is a
    settlement result, not a removal with no record of what became of it.
    The `auction_lot` rows are **kept**, unlike `remove_lot`, which deletes
    them -- `result`, `hammer_price` and `buyer_customer_id` are exactly what
    those columns were added for.

    **The whole of steps 4 to 6 runs inside one savepoint**
    (`db.begin_nested`), so a failure against the second buyer takes the
    first buyer's order, fees, shares and endings back out with it rather
    than leaving an auction half settled. The caller still commits. That
    boundary is not decoration: it is mutation-verified by
    `test_a_failure_part_way_through_leaves_nothing_written`, whose docstring
    records what removing it looks like.

    **One buyer spelled two ways is still one buyer** (ruling R17). Lots are
    grouped on a casefolded username, the same question
    `buyers.venue_buyer` answers when it decides how many *customers* exist,
    so a grid saying `CoinFan88` on one row and `coinfan88` on the next
    writes one order rather than two against a single customer. The customer
    record keeps the first spelling the grid used, never the fold. The `fees`
    mapping is keyed the same way.

    Raises `AuctionRefused` -- listing **every** problem, not the first -- if
    the auction is not `closed`, any lot lacks a result, any lot has two, any
    line names a lot from another auction, a sold lot lacks a hammer price
    or (outside an auction house) a buyer, an unsold lot carries a price,
    fees are given twice for one buyer or for someone who bought nothing, or
    coins have nowhere to come back to.

    Raises the narrower `SettlementInputInvalid` -- still an
    `AuctionRefused`, so nothing catching the wider one changes -- when every
    problem found is a **number that is not money**: a negative or sub-cent
    hammer price or fee. Task 5 maps that to 422 and the wider one to 409, so
    the same bad figure refuses the same way here as it does through the
    Listings page's Record sale (ruling R15). A grid holding both kinds
    refuses as the wider one; see `SettlementInputInvalid`.

    The refusals `record_sale_lines` raises -- `sales_writes.SaleRefused` and
    its narrower `SaleInputInvalid` -- are left to propagate unchanged;
    reaching one means a conflict arrived after this function's own checks
    passed, and the savepoint above has already undone whatever had been
    written.
    """
    auction = _lock_auction(db, auction)
    if auction.status is not AuctionStatus.closed:
        raise AuctionRefused(
            f"auction #{auction.id} is {auction.status.value}, so it cannot be settled"
        )
    lots = _lots_of(db, auction)

    by_lot: dict[int, SettlementLine] = {}
    unplaced: list[SettlementLine] = []
    duplicated: list[str] = []
    known = {row.id: row.lot_number for row in lots}
    for line in lines:
        if line.auction_lot_id not in known:
            unplaced.append(line)
        elif line.auction_lot_id in by_lot:
            duplicated.append(known[line.auction_lot_id])
        else:
            by_lot[line.auction_lot_id] = line

    problems = _grid_problems(
        auction, lots, by_lot, unplaced, duplicated, fees, returned_to_location_id
    )
    if problems:
        # The narrower class only when *every* problem is bad input (ruling
        # R15). A message that also names a real conflict is not merely a
        # badly filled form, and 409 is the safer of the two answers to give
        # about a mixture -- see `SettlementInputInvalid`.
        refusal = (
            SettlementInputInvalid
            if all(problem.bad_input for problem in problems)
            else AuctionRefused
        )
        raise refusal(
            f"auction #{auction.id} cannot be settled: "
            + "; ".join(problem.text for problem in problems)
        )

    # Keyed the same casefolded way the lots are grouped (ruling R17), so a
    # fee entered against `COINFAN88` reaches the order built from lots that
    # said `coinfan88`. `_grid_problems` has already refused two keys that
    # casefold to one, so nothing is silently overwritten here.
    fee_lines = {_buyer_key(given): given_fees for given, given_fees in fees.items()}
    # Every coin in the auction, in one pass, through the single owner of the
    # acquisition order. Read before anything is ended, because
    # `offered_items` answers from the *open* memberships that `end_offer`
    # releases -- asking again after step 5 would find a lot's coins gone.
    offering_writes.lock_for_sale(
        db,
        item_ids=sorted(
            item.id
            for row in lots
            for item in offering_writes.offered_items(db, row.listing)
        ),
    )

    orders: list[SalesOrder] = []
    with db.begin_nested():
        for row in lots:
            line = by_lot[row.id]
            row.result = line.result
            row.hammer_price = line.hammer_price
        for group in _sold_by_buyer(lots, by_lot):
            order = sales_writes.record_sale_lines(
                db,
                [
                    sales_writes.SaleLine(listing_id=row.listing_id, price=price)
                    for row, price in group.lots
                ],
                # The first spelling the grid used, not the casefolded key:
                # a customer record carries the name the owner typed
                # (ruling R17, and `_BuyerGroup`).
                buyer_username=group.username,
                # The sale number, and deliberately the same one on every
                # buyer's order (ruling R18): it identifies the **sale**,
                # which is what the owner reconciles an auction house's
                # statement against, and the house's statement names the sale
                # rather than one order per buyer inside it. Nothing
                # constrains this column to be unique, so the repetition is
                # the reconciliation key rather than a collision. If per-buyer
                # invoice numbers are ever wanted they are a field on the
                # settlement grid, entered from the statement -- never a value
                # derived here, because nothing in this transaction knows
                # them.
                external_order_id=auction.external_id,
                fees=fee_lines.get(group.key, ()),
                recorded_by=settled_by,
            )
            for row, _ in group.lots:
                row.buyer_customer_id = order.customer_id
            orders.append(order)

        coming_home = [
            row for row in lots if by_lot[row.id].result is not AuctionLotResult.sold
        ]
        if coming_home and auction.consigned_on is not None:
            if returned_to_location_id is None:  # pragma: no cover - refused above
                raise AuctionRefused(
                    f"auction #{auction.id} is consigned: "
                    "returned_to_location_id is required"
                )
            # Every return before any ending, never interleaved:
            # `_return_from_consignment` reads a lot's coins through
            # `offered_items`, which `end_offer` releases.
            for row in coming_home:
                _return_from_consignment(
                    db, row, returned_to_location_id, user_id=settled_by.id
                )
        for row in coming_home:
            offering_writes.end_offer(db, row.listing)

        # Cleared here and only after the loop above: the house is holding
        # nothing of this auction's any more, whether because everything sold
        # or because everything that did not has just come back. An auction
        # that was never consigned leaves this alone.
        if auction.consigned_on is not None:
            auction.consigned_on = None
        auction.status = AuctionStatus.settled
        db.flush()
    return orders
