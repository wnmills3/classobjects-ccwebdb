"""Auction transitions: adding and removing lots, settling, and an auction's life.

This module is the sole writer of every auction transition -- an
`auction`'s status and custody, and which `auction_lot` rows exist -- the
same single-writer discipline `offering_writes.py` keeps for
`listing.status`, `offer_claim` and `sales_lot.status`, and
`lifecycle_writes.py` keeps for `inventory_item.storage_location_id`.
`routers.auctions` creates a `draft` auction and edits its wording and
dates, and a lot's number and reserve, directly: fields with no consequence
for this module to own. It also constructs `StorageLocation`
rows: `_consigned_location` creates a house's consigned location on first
use (spec, *Consignment custody*), and nothing else in `backend/app`
constructs one, so no other writer of that table is bypassed.

Adding or removing a lot **is** an offer or an ending, so this module calls
`offering_writes.offer` and `offering_writes.end_offer` for that half rather
than writing `listing.status`, `offer_claim` or `sales_lot.status` itself.
Moving items into consignment custody **is** a location change, so `consign`
and the return-from-consignment half of `remove_lot`, `cancel` and `settle`
call `lifecycle_writes.set_location` rather than assigning
`inventory_item.storage_location_id` directly. And settling a lot **is** a
sale, so `settle` calls `sales_writes.record_sale_lines` for the money rather
than writing `sales_order`, `sales_order_fee` or `sales_order_item_share`
here (spec, *Where record-a-sale lives*); what it needs beyond recording one
sale is several listings on one order, because a house bills per buyer.

`remove_lot`, `cancel` and `settle` share the `returned_to_location_id`
contract through `_return_from_consignment`, the one place "move these items
back" is implemented, and record who brought them back. Custody is tracked
by `auction.consigned_on is not None`, never by `auction.status is
AuctionStatus.consigned`: `close` accepts a `consigned` auction without
clearing the date, so the status alone cannot tell whether the house still
holds something. `consigned_on` is cleared in exactly two places, `cancel`
and `settle`, each once every lot that had to come back has -- never inside
`_remove_lot`, which returns one lot's worth. The public `remove_lot` refuses
a `closed` auction outright, so only `cancel`, acting on the whole auction,
touches a closed auction's lots, through `_remove_lot`.

Every transition validates the auction's current status (and, for `consign`,
the platform's kind) before writing anything, and refuses with a message
naming what is in the way -- the same discipline `offering_writes.offer` and
`lot_writes.add_member` follow, and the one exception type this module
produces, `AuctionRefused`, is deliberately singular for the same reason
`LotRefused` is: a caller has one thing to catch.

**`offering_writes.lock_for_sale`'s caller obligation does not reach this
module.** Its docstring states the rule: a caller that may call `end_offer`
on a listing it did not name must first pass that set through
`refuse_if_lot_unheld`, because `end_offer` takes its lot row late and
forgetting it is a Postgres deadlock under concurrency, not a single-request
bug. `remove_lot` and `cancel` call `end_offer` only on `auction_lot.listing`,
a listing named by the `AuctionLot` row itself (`remove_lot`) or by this
auction's own lot rows read through `_lots_of` (`cancel`, through
`_remove_lot`) -- never one reached through `lock_for_sale`'s *derived* half,
the search that finds a listing because it holds one of the caller's items.
`end_offer(sold=False)` ends only the listing it is handed; the
`paused_by_it` listings it also touches are resumed, not ended. And
`lock_for_sale` seeds `lot_ids` from the named listings before `_acquire`
runs, so the named listing's lot row is always taken first.

**`settle` takes the `sold=True` branch, and still has nothing to
discharge.** A sale ends the store listings its offer paused instead of
resuming them (spec, *Record a sale*), and those are listings settlement
never named. The obligation would bite only if one were a **lot** listing,
and none can be. `paused_by_listing_id` is written in one statement --
`offering_writes.offer`'s `to_pause` loop -- over the own-store listings that
already hold a member being offered elsewhere, and each member first passes
`_refuse_unofferable` -> `_refuse_grouped`, which refuses any coin that is an
open member of an **offered** lot. A lot listing holds its coins only
through the claims `offer` wrote (`_holds_any`), and `_end` releases those
claims and the lot's memberships together, so "a lot listing holds this
coin" and "this coin is in an offered lot" are one fact. So no lot listing
carries `paused_by_listing_id`, and `end_offer(sold=True)`'s second `_end`
ends only item listings, which return before touching a lot row.
`test_a_lot_listing_can_never_be_paused_by_another_offer` and
`test_settlement_ends_only_item_listings_it_did_not_name`
(`tests/test_auction_settlement.py`) fail if `_refuse_grouped` is relaxed.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from . import buyers, lifecycle_writes, lot_writes, offering_writes, sales_writes
from .errors import ReferenceDataMissing
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
    "AuctionRefusal",
    "AuctionRefused",
    "SettlementInputInvalid",
    "SettlementLine",
    "add_lot",
    "cancel",
    "close",
    "consign",
    "refuse_unless_lot_editable",
    "remove_lot",
    "schedule",
    "settle",
]


@dataclass(frozen=True)
class AuctionRefusal:
    """One problem an `AuctionRefused` named, structured for a caller to read.

    Ruling R21 (Task 5 follow-up): `str(exc)` -- the message every existing
    `except AuctionRefused` clause, log line and test already reads -- stays
    exactly what it always was; this is *additional* structure carried
    alongside it, not a replacement, so nothing that reads the message
    changes. `lot_number` is set when the problem names one lot in
    particular, which is most of them, and `None` for a problem that does
    not -- a buyer's fees, or the auction as a whole. A caller serving this
    over HTTP (`routers.auctions`) reads this instead of re-parsing the
    message text, so the console can mark a settlement grid's offending rows
    without depending on `"; "` never appearing inside one problem's own
    words, which the text it replaces could not promise.
    """

    reason: str
    lot_number: str | None = None


class AuctionRefused(Exception):
    """One auction transition cannot happen now, with the reason for a person.

    `refusals` (ruling R21) is the same information `str(exc)` carries,
    structured: one `AuctionRefusal` per problem, in the order `settle`
    found them. Every raise site but `settle`'s own grid refusal names
    exactly one problem, so passing nothing here defaults `refusals` to that
    single message wrapped as one entry -- every caller sees a uniform list
    of at least one, regardless of which raise produced it.
    """

    def __init__(
        self, message: str, *, refusals: Sequence[AuctionRefusal] | None = None
    ) -> None:
        """Set `str(exc)` to `message`, unchanged, and `self.refusals` beside it."""
        super().__init__(message)
        self.refusals = (
            list(refusals) if refusals is not None else [AuctionRefusal(message)]
        )


def _custody_away(auction: Auction, purpose: str = "") -> str:
    """The refusal when a consigned auction's coins must come home first.

    Said the same way by `remove_lot`, `cancel` and `settle`: the house still
    holds the coins, so the caller has to name where they return to.
    `purpose` finishes the sentence with what they are coming back for.
    """
    return (
        f"auction #{auction.id}: custody has not returned from the auction "
        f"house, so returned_to_location_id is required{purpose}"
    )


def _refuse_unless(
    auction: Auction, allowed: Collection[AuctionStatus], action: str
) -> None:
    """Refuse unless the auction's status is one of `allowed`, naming it.

    Every status refusal in this module says it the same way: "auction #7
    is closed, so lots cannot be added". `action` is what follows "so".
    """
    if auction.status not in allowed:
        raise AuctionRefused(
            f"auction #{auction.id} is {auction.status.value}, so {action}"
        )


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

    **The API answers this with 422 and plain `AuctionRefused` with 409**, so
    the same bad number refuses the same way whether it was typed into the
    settlement grid or into the Listings page's Record sale, which reaches
    `sales_writes.SaleInputInvalid` -- the class this one mirrors. A
    subclass, not a field, so every `except AuctionRefused` and
    `pytest.raises(AuctionRefused)` catches it too. `app.main` registers a
    handler for each class, and Starlette picks the handler by walking the
    raised exception's MRO, so the narrower class's handler wins whatever
    order the two are registered in; no `except` ordering decides the
    status. `test_settlement_input_invalid_is_a_422_not_a_409`
    (`tests/test_auctions_api.py`) checks it at the API.

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
#: module or by a migration: vocabulary is seed data, loaded and kept
#: current by `app.seeding`, so an INSERT here would be the wrong fix.
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
    keep seeing the old, short list for the rest of the session, and a
    caller iterating it could silently skip the very lot this call just
    added. Fixed in fix round 1 (Important #1). `consign`, `cancel` and
    `settle` now all read `_lots_of` instead, which is the stronger of the
    two answers -- the relationship fix keeps a loaded collection honest, a
    fresh read never asks it to be.

    Takes the `auction` row first (`_lock_auction`), like every transition
    that reaches an auction's coins -- see that function for why.
    """
    auction = _lock_auction(db, auction)
    _refuse_unless(
        auction, (AuctionStatus.draft, AuctionStatus.scheduled), "lots cannot be added"
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
    user_id: int | None = None,
) -> None:
    """Take a lot out of its auction: end its offer, as an ordinary End.

    Ends the listing exactly the way withdrawing any offer does --
    `offering_writes.end_offer`, `sold=False` -- so a store listing it paused
    resumes, and a lot listing's `sales_lot` dissolves and releases its
    members back to `held` (`offering_writes._end`).

    The `auction_lot` row itself is deleted, not left behind ended.
    `sales_lot` stays once offered, because it is the permanent record of a
    group that really was shown to a buyer; `auction_lot` names a numbered
    slot in a sale that may still be reshuffled, and once its listing is no
    longer offered here there is nothing left for the row to describe.
    Deleting it also frees `lot_number` for reuse, which
    `uq_auction_lot_auction_lot_number` would otherwise hold for a lot that
    never sold.

    `returned_to_location_id` is **required** whenever `auction.consigned_on
    is not None`: the items physically left the premises, and something has
    to say where they came back to before the lot is withdrawn -- the "back"
    half of the spec's *Consignment custody* for a withdrawal rather than a
    settlement. Ignored otherwise. `user_id` is who brought them back, for
    each item's location history.

    Raises `AuctionRefused` if the auction has closed, settled or been
    cancelled. **`closed` refuses whether or not the auction was consigned**:
    once closed, the sale has happened and the only ways out are `settle`
    and `cancel`; a lot that did not sell is `AuctionLotResult.withdrawn`, a
    settlement result, not a removal with no record of what became of it.
    Also raises `AuctionRefused`, naming the missing argument, if custody is
    still at the house and `returned_to_location_id` is not given.

    Needs no `offering_writes.refuse_if_lot_unheld` (see this module's
    docstring): `end_offer` is called here only on `auction_lot.listing`, a
    listing this function was handed by name.

    Takes the `auction` row first (`_lock_auction`), like every transition
    that reaches an auction's coins -- see that function for why.
    """
    auction = _lock_auction(db, auction_lot.auction)
    _refuse_unless(auction, _LOTS_REMOVABLE, "lots cannot be removed")
    _remove_lot(
        db,
        auction_lot,
        returned_to_location_id=returned_to_location_id,
        user_id=user_id,
    )


def _remove_lot(
    db: Session,
    auction_lot: AuctionLot,
    *,
    returned_to_location_id: int | None,
    user_id: int | None,
) -> None:
    """The removal itself, without the status gate the public `remove_lot` applies.

    Separate so `cancel`, whose status boundary is wider (it accepts
    `closed`, `remove_lot` does not), can remove each of an auction's lots
    without `_LOTS_REMOVABLE` refusing an auction `cancel` has already
    judged cancellable.

    If the house still holds something of this auction's
    (`auction.consigned_on is not None` -- **not** `auction.status is
    AuctionStatus.consigned`, which a `closed` auction fails even though the
    coins never came home), the lot's items are moved back to
    `returned_to_location_id`, recorded as moved by `user_id`, *before* the
    offer ends: `_return_from_consignment` reads the lot's open membership
    through `offering_writes.offered_items`, which `end_offer` releases.

    Never clears `auction.consigned_on` -- that is an auction-level fact,
    owned by `cancel`, which clears it once every lot has been returned.
    Removing a single lot out of several leaves the house holding the rest.
    """
    auction = auction_lot.auction
    if auction.consigned_on is not None:
        if returned_to_location_id is None:
            raise AuctionRefused(
                _custody_away(
                    auction, " to bring its items back before the lot can be removed"
                )
            )
        _return_from_consignment(
            db, auction_lot, returned_to_location_id, user_id=user_id
        )
    offering_writes.end_offer(
        db,
        auction_lot.listing,
        note=f"removed from auction #{auction_lot.auction_id}",
    )
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

    The one implementation of "move these items back": `remove_lot` and
    `cancel` call it through `_remove_lot`, and `settle` for the "unsold at
    an auction house" case the spec's *Settle* row describes. `user_id` is
    recorded on each item's location history as who moved it.

    Reads the lot's currently open members through
    `offering_writes.offered_items` -- the one answer in the codebase to
    "which items does this listing offer" -- so it must run before
    `end_offer` releases that membership, never after.
    """
    _move_lot_items(
        db,
        auction_lot,
        location_id,
        user_id=user_id,
        note=f"Returned from consignment, auction #{auction_lot.auction_id}",
    )


def _move_lot_items(
    db: Session,
    auction_lot: AuctionLot,
    location_id: int,
    *,
    user_id: int | None,
    note: str,
) -> None:
    """Move every coin the lot's listing offers to `location_id`, noting why.

    Both directions of consignment custody: `consign` out to the house and
    `_return_from_consignment` back. Through `lifecycle_writes.set_location`,
    the one writer of an item's location, and over
    `offering_writes.offered_items`, so it must run before `end_offer`
    releases the lot's membership.
    """
    for item in offering_writes.offered_items(db, auction_lot.listing):
        lifecycle_writes.set_location(db, item, location_id, user_id=user_id, note=note)


def refuse_unless_lot_editable(db: Session, auction_lot: AuctionLot) -> None:
    """Raise `AuctionRefused` unless this lot's number or reserve may still change.

    Ruling R22 (Task 5 follow-up): nothing owned this decision before --
    `routers.auctions.update_auction_lot` wrote `lot_number` and `reserve`
    with a plain `setattr` and no gate at all, the same way it still writes
    them, just unconditionally until now. The boundary is `_LOTS_REMOVABLE`,
    the identical tuple `remove_lot` uses: `draft`, `scheduled` or
    `consigned`, never `closed`, `settled` or `cancelled`. A lot number and a
    reserve are both things set *before* the sale runs -- once the auction is
    `closed`, the lot numbers are part of the record the house's statement is
    reconciled against, and a reserve is meaningless once a result exists to
    read instead. This matches the shape `remove_lot` already has: a
    `withdrawn` lot after close is a **settlement** result to be entered, not
    a table edit to be made.

    Raises naming the lot and the auction's status, the same words every
    other refusal in this module uses. Callers still perform the write
    themselves -- `lot_number` and `reserve` carry no consequence for
    `offer_claim`, `sales_lot.status` or a location the way this module's
    other single-writer columns do, so there is nothing beyond the gate
    itself for this module to own (see `routers.auctions.update_auction_lot`'s
    own docstring).

    **Takes `(db, auction_lot)`, the module's own convention** (Minor #9,
    Task 5 fix round 1), and reads the auction's status through `db` and
    `auction_lot.auction_id` -- a plain column, always present -- rather
    than through the `auction_lot.auction` relationship: that attribute is a
    lazy load, which emits a `SELECT` of its own on a cold instance and
    raises `DetachedInstanceError` on an expired one, so a "pure check" that
    reads it is not actually free of the session. Asking `db` directly for
    just the one column this function needs is both the honest signature and
    the cheaper query.
    """
    # `get_one`, not `scalar(select(...))`: `auction_id` is `ondelete="RESTRICT"`,
    # so the row is guaranteed to exist, and this reads that guarantee's
    # type as well as its data -- `AuctionStatus`, never `AuctionStatus | None`.
    _refuse_unless(
        db.get_one(Auction, auction_lot.auction_id),
        _LOTS_REMOVABLE,
        f"lot {auction_lot.lot_number} cannot be renumbered or have its "
        "reserve changed",
    )


def schedule(db: Session, auction: Auction) -> None:
    """Move a draft auction to scheduled.

    Raises `AuctionRefused` unless the auction is `draft`.
    """
    _refuse_unless(auction, (AuctionStatus.draft,), "it cannot be scheduled")
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
    the auction is not `scheduled`. Raises `errors.ReferenceDataMissing` (a
    narrow `RuntimeError`, ruling R24, Task 5 fix round 1) if the `consigned`
    storage-location kind is not seeded: a database that has been migrated
    but not loaded with `python -m app.seeding load` is a real, expected
    state, and this is the message that tells the owner what to do. Nothing
    here creates the kind on the fly or falls back to a different one -- the
    same shape as `app.sales_venues.ensure_store_venue`'s `ReferenceDataMissing` for a
    missing `own_store` platform kind.

    Takes the `auction` row first (`_lock_auction`), like every transition
    that reaches an auction's coins: its item moves used to flush *before*
    its `UPDATE auction`, the inverse of `cancel`, so the two could deadlock
    on a scheduled auction. Reads its lots through `_lots_of`, not
    `auction.lots`, for the reason that function gives.
    """
    auction = _lock_auction(db, auction)
    venue = auction.sales_venue
    if venue.kind.code != _AUCTION_HOUSE_KIND_CODE:
        raise AuctionRefused(
            f"{venue.name} is not an auction house: nothing leaves the "
            f"premises for a {venue.kind.code} auction"
        )
    _refuse_unless(auction, (AuctionStatus.scheduled,), "it cannot be marked consigned")
    location = _consigned_location(db, venue.name)
    note = f"Consigned to auction #{auction.id}"
    for auction_lot in _lots_of(db, auction):
        _move_lot_items(db, auction_lot, location.id, user_id=user_id, note=note)
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

    **Created with `INSERT ... ON CONFLICT DO NOTHING`, then read back.**
    Postgres treats two `NULL` `identifier` values as distinct, so
    `uq_storage_location_identity` never covered this row, and two
    concurrent first consignments to one house, from two different
    auctions, could each find nothing and insert a duplicate location --
    coins split across two "Consigned: Heritage" entries (auctions fix round
    1, Minor #6). `uq_storage_location_identity_no_identifier` closes that
    for the `identifier IS NULL` case, and the
    upsert is what turns the loser of that race into a reader of the
    winner's row instead of an `IntegrityError` and a 500: it waits for the
    first insert to commit, does nothing, and the `SELECT` below finds the
    one row.
    """
    kind_id = db.scalar(
        select(StorageLocationKind.id).where(
            StorageLocationKind.code == _CONSIGNED_KIND_CODE
        )
    )
    if kind_id is None:
        raise ReferenceDataMissing(
            f"storage_location_kind {_CONSIGNED_KIND_CODE!r} is not seeded: "
            "run `python -m app.seeding load`"
        )
    db.execute(
        pg_insert(StorageLocation)
        .values(storage_location_kind_id=kind_id, institution=institution)
        .on_conflict_do_nothing(
            index_elements=["storage_location_kind_id", "institution"],
            index_where=text("identifier IS NULL"),
        )
    )
    return db.scalars(
        select(StorageLocation).where(
            StorageLocation.storage_location_kind_id == kind_id,
            StorageLocation.institution == institution,
            StorageLocation.identifier.is_(None),
        )
    ).one()


def close(db: Session, auction: Auction) -> None:
    """Close the auction: lot results may now be entered, through `settle`.

    Raises `AuctionRefused` unless the auction is `scheduled` or `consigned`.

    **Not `draft` (ruling R8, fix round 1).** A `draft` auction never
    happened -- there is nothing to close, and accepting it made `close` a
    one-way door: `cancel` refused `closed` outright (before this same
    ruling widened it), so one mis-click on an unscheduled auction produced
    one that could never be cancelled, un-closed, or have its lots touched
    again, with `settle` as the only exit for a sale that never ran. A
    `draft` auction that should not proceed is `cancel`led, not closed.
    """
    _refuse_unless(
        auction,
        (AuctionStatus.scheduled, AuctionStatus.consigned),
        "it cannot be closed",
    )
    auction.status = AuctionStatus.closed
    db.flush()


def cancel(
    db: Session,
    auction: Auction,
    *,
    returned_to_location_id: int | None = None,
    user_id: int | None = None,
) -> None:
    """Cancel the auction and remove every lot it still holds.

    Each lot is removed exactly as `remove_lot` removes one -- ending its
    offer, resuming any store listing it paused, dissolving its sales lot --
    so nothing is left claimed by a sale that is not happening. The removals
    run before the status moves to `cancelled`.

    **Accepts every status except `settled` and `cancelled`, `closed`
    included.** The spec's line `draft -> scheduled -> [consigned] -> closed
    -> settled, or cancelled` makes `cancelled` an alternative terminal state
    from anywhere before `settled`: a closed auction whose settlement is
    being abandoned is a real case. Lots are removed through `_remove_lot`,
    not the public `remove_lot`, so a `closed` auction -- which
    `_LOTS_REMOVABLE` refuses lot by lot -- can still be cancelled whole.

    **Takes the `auction` row FOR UPDATE first, as `settle` does**
    (`_lock_auction`). Both are legal on a `closed` auction, and a `cancel`
    that reached the coins first and wrote the auction row last would take
    lots -> items -> listings -> auction, the inverse of `settle`: a Postgres
    deadlock, an HTTP 500 on a money path.
    `test_cancelling_an_auction_races_settling_it`
    (`tests/test_settlement_race.py`) is the proof. `add_lot`, `remove_lot`
    and `consign` take the auction row first for the same reason: `cancel`
    is legal on their statuses too. The status and custody checks below
    therefore read a locked, re-read row, so cancel-versus-cancel is refused
    with an `AuctionRefused` naming the status; the version column still
    guards a `settle` that commits without ever contending.

    Lots are removed in **ascending id order**, read fresh through
    `_lots_of` rather than `auction.lots` (which has no `order_by`), so two
    concurrent cancels of one auction take their rows in the same order.
    Within each removal, `end_offer` keeps the canonical lot -> items ->
    listings order.

    `returned_to_location_id` is **required** whenever `auction.consigned_on
    is not None` -- keyed on custody, not on `status is consigned`, because
    `close` accepts a `consigned` auction without clearing the date -- since
    every lot needs somewhere to return its items to. `user_id` is recorded
    as who brought them back. `consigned_on` is cleared here, with the
    status move, once every lot has been returned -- never inside
    `_remove_lot`, since one lot coming home leaves the house holding the
    rest. The requirement is checked here as well as in `_remove_lot`,
    because a consigned auction with **no** lots never enters the removal
    loop:
    `test_cancelling_a_consigned_auction_with_no_lots_requires_a_return_location`.

    Raises `AuctionRefused` if the auction has already been settled or
    cancelled, or if custody is still at the house and
    `returned_to_location_id` is missing.

    Needs no `offering_writes.refuse_if_lot_unheld`, as for `remove_lot`
    (see this module's docstring): every `end_offer` call goes through
    `_remove_lot`, on a listing named by this auction's own lot rows.
    """
    auction = _lock_auction(db, auction)
    if auction.status in (AuctionStatus.settled, AuctionStatus.cancelled):
        raise AuctionRefused(f"auction #{auction.id} is already {auction.status.value}")
    if auction.consigned_on is not None and returned_to_location_id is None:
        raise AuctionRefused(
            _custody_away(
                auction, " to bring its items back before it can be cancelled"
            )
        )
    still_consigned = auction.consigned_on is not None
    for auction_lot in _lots_of(db, auction):
        _remove_lot(
            db,
            auction_lot,
            returned_to_location_id=returned_to_location_id,
            user_id=user_id,
        )
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


def _buyer_key(username: str | None) -> str | None:
    """The grouping key for a buyer: `buyers.buyer_name`, casefolded.

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
    name = buyers.buyer_name(username)
    return None if name is None else name.casefold()


def _lock_auction(db: Session, auction: Auction) -> Auction:
    """Take the auction row FOR UPDATE and re-read it. The outermost lock.

    **A new, outermost level above `offering_writes`' canonical order**
    (`docs/specs/lock-order-design.md`), and it must be taken strictly
    *before* `lock_for_sale`, never after or between. Nothing else in the
    codebase ever takes an `auction` row, so this level is contended only by
    other auction transitions and cannot invert against lots, items or
    listings -- which is what makes adding a level here safe at all.

    **Taken first by every transition that reaches an auction's coins** --
    `add_lot`, `remove_lot`, `consign`, `cancel` and `settle` -- and by all
    of them or by none. One taking this level and another not is the "caller
    that bypasses the owner" shape, one level up: `settle` alone took it
    until the whole-branch review measured a real `DeadlockDetected` and an
    HTTP 500 against `cancel` (Critical #1, proven by
    `test_cancelling_an_auction_races_settling_it`), and the review of that
    fix found the same inversion between the newly-locking `cancel` and
    `consign`, whose item moves flushed before its `UPDATE auction`. Any
    future transition that reaches a coin must take this first as well.
    `schedule` and `close` touch only the auction row and rely on its
    `version` column.

    Why it is needed: `settle` decides what to write from the auction's
    status and its lot table, and then writes both. Two settlements of one
    auction that each read `closed` would each go on to record every lot's
    sale, and `lock_for_sale` cannot serialize them -- it takes the *items*,
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
    transaction that then marks the auction `settled`; `cancel` removes every
    one of them, so a stale collection there is a lot left live under an
    auction that reads `cancelled`. A fresh read closes that only together
    with the auction lock: `add_lot` takes `_lock_auction` first, so it
    cannot insert a lot between another transition's read and its commit.

    Ascending id, one order for every pass, so two concurrent passes over the
    same auction cannot take the same rows two ways. Every caller shares
    this function rather than sorting for itself.
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

    `lot_number` (ruling R21) is this problem's own lot, when it has exactly
    one -- most problems do -- and `None` for one that spans several lots or
    none at all (a buyer's fees, or the whole auction). It is what
    `settle` copies onto the `AuctionRefusal` it raises with, alongside
    `text`; nothing here is thrown away the way the old semicolon-joined
    message alone would have.
    """

    text: str
    bad_input: bool = False
    lot_number: str | None = None


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
        # No `lot_number`: the id names no lot in this auction, so there is
        # no lot_number to give -- it may not even exist anywhere.
        _Problem(f"lot id {line.auction_lot_id} is not in auction #{auction.id}")
        for line in unplaced
    ]
    problems += [
        _Problem(f"lot {number} was given two results", lot_number=number)
        for number in duplicated
    ]
    names_buyers = auction.sales_venue.kind.code != _AUCTION_HOUSE_KIND_CODE
    for row in lots:
        line = by_lot.get(row.id)
        if line is None:
            problems.append(
                _Problem(
                    f"lot {row.lot_number} has no result", lot_number=row.lot_number
                )
            )
            continue
        if line.result is not AuctionLotResult.sold:
            if line.hammer_price is not None:
                problems.append(
                    _Problem(
                        f"lot {row.lot_number} is {line.result.value}, so it cannot "
                        "have a hammer price",
                        lot_number=row.lot_number,
                    )
                )
            continue
        if line.hammer_price is None:
            problems.append(
                _Problem(
                    f"lot {row.lot_number} sold but has no hammer price",
                    lot_number=row.lot_number,
                )
            )
        else:
            money = sales_writes.money_problem(
                line.hammer_price, f"lot {row.lot_number}'s hammer price"
            )
            if money is not None:
                problems.append(
                    _Problem(money, bad_input=True, lot_number=row.lot_number)
                )
        if names_buyers and _buyer_key(line.buyer_username) is None:
            problems.append(
                _Problem(
                    f"lot {row.lot_number} sold but names no buyer",
                    lot_number=row.lot_number,
                )
            )

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
                    _custody_away(
                        auction, f" to bring back lot(s) {', '.join(coming_home)}"
                    )
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
                key=key, username=buyers.buyer_name(line.buyer_username), lots=[]
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
       which step 1 already serialized; this call is what serializes them
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
    _refuse_unless(auction, (AuctionStatus.closed,), "it cannot be settled")
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
            + "; ".join(problem.text for problem in problems),
            refusals=[
                AuctionRefusal(reason=problem.text, lot_number=problem.lot_number)
                for problem in problems
            ],
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
    #
    # `item_ids` only, no `listing_ids` -- so `_refuse_if_changed`'s own
    # confirming re-read is structurally vacuous here: `named` comes back
    # empty, `before` has nothing keyed in it, and the loop that would compare
    # a listing's item set before and after locking never has an entry to
    # compare. That is safe, not accidental. `add_lot` refuses everything but
    # a `draft` or `scheduled` auction, so by the time `settle` requires
    # `closed` (above) no lot can be added to or reshuffled under the `lots`
    # this function already read; `offering_writes._end` is the only writer
    # that ever moves a lot listing's item set, and it does so by ending the
    # listing in the same statement, which is exactly the "ordinary, not a
    # violation" case `_refuse_if_changed`'s own docstring carves out; and
    # `record_sale_lines`, called below, trusts the rows this call already
    # holds locked rather than re-deriving membership of its own.
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
                raise AuctionRefused(_custody_away(auction))
            # Every return before any ending, never interleaved:
            # `_return_from_consignment` reads a lot's coins through
            # `offered_items`, which `end_offer` releases.
            for row in coming_home:
                _return_from_consignment(
                    db, row, returned_to_location_id, user_id=settled_by.id
                )
        for row in coming_home:
            # Said as what it was: unsold, or withdrawn from the sale -- not
            # the bare "withdrawn" an End would record. This is the fact
            # `ListingStatusHistory` exists to keep for settlement.
            result = by_lot[row.id].result.value
            offering_writes.end_offer(
                db, row.listing, note=f"{result} at auction #{auction.id}"
            )

        # Cleared here and only after the loop above: the house is holding
        # nothing of this auction's any more, whether because everything sold
        # or because everything that did not has just come back. An auction
        # that was never consigned leaves this alone.
        if auction.consigned_on is not None:
            auction.consigned_on = None
        auction.status = AuctionStatus.settled
        db.flush()
    return orders
