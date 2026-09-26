"""Offering items for sale, and the one home of the shop's rule.

This module has two jobs, and they belong together because the second one is
what the first one has to get right everywhere.

**It is the only writer of `listing.status`, `offer_claim`,
`sales_lot.status`, `sales_lot_item.released_at` and the
`inventory_item.disposition` changes they cause** -- the same pattern as
`lifecycle_writes.py`. An item is offered in one place at a time: offering it
elsewhere pauses the store listing that held it, ending that offer unsold
resumes the store listing, and ending it sold ends the store listing instead,
so a sold item never comes back into the shop. Offering is refused for the
same reason it is not undone: an item a buyer has already bought, or that an
unshipped order holds, is not the business's to offer again. The partial
unique index on `offer_claim` is the backstop if two requests still race.

A *lot* is offered the same way, and that is deliberate rather than a second
implementation: `offer` builds a list of member items -- one for an item, the
lot's open members for a lot -- and every refusal, lock, pause and claim below
runs per member. `app.lot_writes` assembles a lot and decides who may be in
one; the moment a lot is *offered*, frozen, sold or dissolved it belongs here,
because those are exactly the writes this module is the single writer of.

The writes here take the affected `inventory_item` rows `FOR UPDATE`, in id
order, and re-read them under the lock -- the lock alone would leave the
decision resting on values read before the wait. `offer` knows its items up
front and locks them first. `end_offer` cannot, and neither can a checkout:
which items an ending or a sale touches is itself a query, so `lock_for_sale`
reads the set, locks it, and reads again to confirm it has not moved. That
second read is what makes the "locked before the claims are read" rule true
rather than nearly true, and a set that did move is refused rather than
retried (`LockSetChanged`).

There are three kinds of row to lock, and they are taken in this order:
**lot row, then items (ascending id), then listings (ascending id)**. Each
kind is taken in *one* statement, which is the part that matters: N sorted
statements are not a sorted acquisition, and the listing half is where a lot
makes that a real hazard (`_lock_listing_rows`).

**That order lives in exactly one function, `_acquire`, and every writer that
takes more than one kind of row reaches it through `lock_for_sale`.** Eight
call sites call `lock_for_sale` itself: `offer` and `end_offer` here,
`order_writes._lock_listings` (for `place_order`, `revise_order` and
`return_stock`), `sales_writes.record_sale_lines`, `auctions.settle`, and
`routers.inventory`'s `receive_items`, `bulk_edit` and `update_item`. Others
reach it through `offer` or `end_offer` while holding a row of their own:
`splitting.split_item` holds the parent `inventory_item` row, the same
shape as `offer`'s lot lock, and `auctions.add_lot`, `remove_lot` and
`cancel` hold the `auction` row. `_acquire`'s own docstring carries the
rest, and `docs/specs/lock-order-design.md` lists every writer and every
`with_for_update` in the package. The order is this one because it is the
one that carries a guarantee: the listing set here is *derived* from claims
this module alone writes, and `_lock_listing_rows` says why that needs the
items held first. `place_order` is handed listing ids, and nothing of its
own rests on which kind it takes first.

**It owns the rule for what the shop may sell** -- our own store, fixed price,
active -- in both the Python form (`sellable_in_shop`) and the SQL form
(`shop_listing_filters`). Checkout, the catalog's detail endpoint and the
catalog's list query all ask here.

Functions flush and never commit; the caller's request owns the transaction.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import ColumnElement, Select, and_, or_, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    ClaimState,
    Currency,
    Disposition,
    InventoryItem,
    Listing,
    ListingFormat,
    ListingStatus,
    ListingStatusHistory,
    OfferClaim,
    SalesLot,
    SalesLotStatus,
    SalesVenue,
    utcnow,
)
from .references import require_code

__all__ = [
    "HELD_BY",
    "ON_OFFER",
    "LockSetChanged",
    "LockedForSale",
    "OfferRefused",
    "claims_for",
    "end_offer",
    "ever_claimed",
    "ever_named_any",
    "lock_for_sale",
    "offer",
    "offered_items",
    "offers_holding",
    "refuse_if_lot_unheld",
    "sellable_in_shop",
    "shop_listing_filters",
]

#: Claim states that hold an item: it is offered now, or set aside for an
#: offer made elsewhere. A released claim holds nothing.
HELD_BY = (ClaimState.active, ClaimState.paused)

#: The listing statuses that match them, one for one.
ON_OFFER = (ListingStatus.active, ListingStatus.paused)

#: The currency a new offer is priced in. One today; when a platform sells in
#: another, this becomes an argument to `offer` rather than a second copy of
#: the lookup.
OFFER_CURRENCY = "USD"


class OfferRefused(Exception):
    """One item cannot be offered; the batch it belongs to writes nothing."""

    def __init__(self, item_code: str, reason: str) -> None:
        """Name the item and why it cannot be offered."""
        self.item_code = item_code
        self.reason = reason
        super().__init__(f"{item_code}: {reason}")


class LockSetChanged(Exception):
    """A lock pass chose its rows from a read, and that read went stale.

    `lock_for_sale` reads which items a listing offers *before* it can lock
    them -- there is no other order available, because the item ids are what
    it needs in order to lock anything. It re-reads under the locks and
    raises this if the two answers differ.

    Deliberately **not** an `OfferRefused` and deliberately unmapped in the
    routers, which is the reasoning `sales_writes.ShareMissing` already
    carries: an `OfferRefused` is 409, which tells a caller "something is in
    the way, try again", and that is false here. While the offer stands its
    membership is frozen -- `lot_writes._refuse_unless_assembling` refuses a
    membership change on a lot that is not `assembling`, and
    `_refuse_grouped` refuses offering a member of an offered lot on every
    venue -- so a set that moved under this lock is an invariant violation
    inside this codebase that no retry can fix. It surfaces as a 500 naming
    both readings rather than as a silent proceed on a stale set.

    The one change that is *not* this, and is not raised: the offer's own
    ending, which releases every membership. `_refuse_if_changed` has that
    distinction and why it exists.

    Nothing is half-written when it is raised: it comes out of the lock pass,
    before the first write of whichever caller asked.

    **Covered by construction, not by reach.** Both places that raise it --
    `_refuse_if_changed` and `refuse_if_lot_unheld` -- have unit tests that
    prove the raise fires and that the message names the listing and the lot,
    and neither *condition* is reachable from a request: the freeze rules
    close one window and `lock_for_sale`'s own `offers_holding` read closes
    the other in every ordinary interleaving. So a coverage number on these
    lines says more than it knows; what is tested is the comparison, not the
    race that would reach it.
    """


# --------------------------------------------------------------------------
# The shop's rule, in its two forms
# --------------------------------------------------------------------------


def sellable_in_shop(listing: Listing, *, active_only: bool = True) -> bool:
    """Whether the shop may sell this listing: our store, fixed price, active.

    `active_only=False` asks only the first half -- whether the listing is
    this shop's at all, whatever state it is in. Two callers need that and
    say so: checkout, which refuses an inactive listing with its own message
    ("not currently for sale", which is not the same news as "not sold in
    this shop"), and the catalog's detail endpoint, which serves a
    withdrawn listing so a page someone bookmarked can say it has ended.
    """
    ours = (
        listing.sales_venue.is_own_store and listing.format is ListingFormat.fixed_price
    )
    if not active_only:
        return ours
    return ours and listing.status is ListingStatus.active


def shop_listing_filters(*, active_only: bool = True) -> list[ColumnElement[bool]]:
    """The same rule as SQL, for the catalog query.

    `active_only=False` is the catalog's admin preview of withdrawn
    listings (`include_inactive`), the SQL twin of `sellable_in_shop`'s.
    """
    filters: list[ColumnElement[bool]] = [
        Listing.sales_venue.has(SalesVenue.is_own_store.is_(True)),
        Listing.format == ListingFormat.fixed_price,
    ]
    if active_only:
        filters.append(Listing.is_active.is_(True))
    return filters


# --------------------------------------------------------------------------
# Claims
# --------------------------------------------------------------------------


def _holding_claims() -> Select[tuple[OfferClaim]]:
    """The one definition of a claim that actually holds its item.

    Two conditions, and both are needed. The claim is `active` or `paused`,
    never `released`. And the listing behind it is still on offer: a claim's
    state follows its listing's status (see `OfferClaim`), but this module was
    not always the only writer of that status -- the catalog API's retired
    `PATCH .../is_active` withdrew a listing without touching its claims -- so
    a claim left reading `paused` on a listing an older write ended holds
    nothing. Everything that asks whether a claim holds its item *now* asks
    through here, or the answers disagree and an item is either offered
    twice or never allowed back to `held`. The past-tense question -- has a
    claim ever named this item, released ones included -- is a different
    read and deliberately does not go through here; `ever_claimed` below is
    its one home, the same way `_claims_of` is for one listing.
    """
    return (
        select(OfferClaim)
        .join(Listing, Listing.id == OfferClaim.listing_id)
        .where(OfferClaim.state.in_(HELD_BY), Listing.status.in_(ON_OFFER))
    )


def claims_for(db: Session, item_ids: Collection[int]) -> dict[int, list[OfferClaim]]:
    """The claims holding each of `item_ids`: active and paused, not released.

    Paused counts because an item whose store listing was set aside for an
    offer elsewhere is still spoken for -- which is what the for-sale edit
    warning (`app.sale_state`) has to say.
    """
    ids = list(item_ids)
    found: dict[int, list[OfferClaim]] = {}
    if not ids:
        return found
    claims = db.scalars(
        _holding_claims()
        .where(OfferClaim.inventory_item_id.in_(ids))
        .order_by(OfferClaim.id)
    ).all()
    for claim in claims:
        found.setdefault(claim.inventory_item_id, []).append(claim)
    return found


def ever_claimed(db: Session, item_ids: Collection[int]) -> set[int]:
    """Which of `item_ids` a claim has ever named, released ones included.

    `claims_for` above answers "is this item spoken for now"; this is its
    released-inclusive twin, the same reach `_claims_of` gives for one
    listing but across items and states rather than one listing. `offer`
    writes one claim per member and a claim is released, never deleted, so
    this is also the lot-aware way to ask "has this item ever been
    offered" -- a lot listing itself names no item, but its members' claims
    do and outlive the lot's dissolution.

    `app.sale_state.ever_offered` is the one caller today, for a delete's
    permanent refusal: once a coin has been offered the offer is sales
    history, so that refusal must not clear just because the offer ended or
    the lot dissolved. Kept here, not there, because "is this item spoken
    for" -- present or past -- is this module's question to answer; a second
    hand-rolled read of `offer_claim` outside it is exactly the drift this
    function closes.
    """
    ids = list(item_ids)
    if not ids:
        return set()
    return set(
        db.scalars(
            select(OfferClaim.inventory_item_id).where(
                OfferClaim.inventory_item_id.in_(ids)
            )
        ).all()
    )


def _claims_of(db: Session, listing: Listing) -> list[OfferClaim]:
    """Every claim on one listing, released ones included."""
    return list(
        db.scalars(
            select(OfferClaim)
            .where(OfferClaim.listing_id == listing.id)
            .order_by(OfferClaim.id)
        ).all()
    )


def _move_claims(db: Session, listing: Listing, state: ClaimState) -> None:
    """Move every claim a listing holds to `state`, writing a missing one.

    A listing made before claims existed has none. When such a listing is
    paused or resumed here, the claim this module would have written is
    written now rather than leaving the hold recorded nowhere. Nothing is
    invented for a release: an ended listing holds nothing, and a released
    claim on a listing that never had one records no history worth keeping.

    Nothing is invented for a *lot* listing either, whatever the state. Its
    `inventory_item_id` is NULL, so the claim below would be built with a
    NULL `inventory_item_id` and fail NOT NULL -- and there is nothing to
    guess, because a lot's claims name its *members*, never the listing. A
    lot listing predating claims cannot exist: `offer` writes one claim per
    member, so a claimless lot listing is not the backfill case this branch
    is for.
    """
    claims = _claims_of(db, listing)
    if not claims:
        if listing.inventory_item_id is None:
            return
        if state is not ClaimState.released:
            db.add(
                OfferClaim(
                    inventory_item_id=listing.inventory_item_id,
                    listing_id=listing.id,
                    state=state,
                )
            )
        return
    for claim in claims:
        # Only a claim that still holds something moves. A released claim is
        # finished with: resuming a listing must not re-claim a member let go
        # earlier, which for a lot would be a second active claim on an item
        # the index has already given to someone else.
        if claim.state in HELD_BY:
            claim.state = state


# --------------------------------------------------------------------------
# Locking
# --------------------------------------------------------------------------


def _lock_items(db: Session, item_ids: Collection[int]) -> None:
    """Take the affected items' rows FOR UPDATE, in id order, and re-read them.

    In id order so two requests touching the same items can never each hold
    what the other needs.

    The whole entity rather than its id, with `populate_existing`, because the
    lock alone settles nothing: under READ COMMITTED a request that waited on
    it resumes holding the values it read *before* the wait, and would then
    refuse or allow an offer on the losing side of the race it just lost.
    Re-reading here overwrites what the session cached -- the same reason
    `_lock_listing_rows` passes `populate_existing` and
    `splitting.split_item` locks with `db.refresh(..., with_for_update=True)`.

    The re-read is also what stopped a *false* conflict on the money path.
    `order_writes._after_stock_change` writes `inventory_item.disposition`,
    which carries a version column and was not locked at all until these
    rows came to be taken here for a checkout too: an unrelated concurrent
    edit to a coin made a revision or a cancellation fail with
    `StaleDataError` and a person was told to reload
    (`tests/test_order_revision_race.py`).
    """
    ids = sorted(set(item_ids))
    if not ids:
        return
    db.scalars(
        select(InventoryItem)
        .where(InventoryItem.id.in_(ids))
        .order_by(InventoryItem.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()


def _names_any(
    item_ids: Collection[int], claim_states: Collection[ClaimState] | None
) -> ColumnElement[bool]:
    """The one definition of "this listing names one of these items".

    Both halves, always: the listings written *against* an item, and any
    listing written against something else whose claim names it -- a lot's,
    since phase 3. Asking only the first half silently ignores exactly the
    case the claim table exists for.

    `claim_states` is the only difference between the two tenses built on
    this, and it is a parameter rather than a second copy of the `or_`
    because a copy is what would drift. `HELD_BY` asks the present tense
    (`_holds_any`); `None` asks every state, the past tense
    (`ever_named_any`), the same released-inclusive reach `ever_claimed`
    gives -- a claim is released, never deleted, so a lot listing that has
    ended is still reachable from its members this way and no other.

    The listing's own status is not asked here -- each caller adds its own
    filter, which is the outer half of `_holding_claims`.
    """
    claimed = select(OfferClaim.listing_id).where(
        OfferClaim.inventory_item_id.in_(item_ids)
    )
    if claim_states is not None:
        claimed = claimed.where(OfferClaim.state.in_(claim_states))
    return or_(Listing.inventory_item_id.in_(item_ids), Listing.id.in_(claimed))


def _holds_any(item_ids: Collection[int]) -> ColumnElement[bool]:
    """Listings that hold one of these items **now**: claims that still hold.

    The three callers below (`offers_holding`, `_lock_listing_rows`,
    `_locked_offers`) must agree: a listing one of them can see and another
    cannot is an item this module would offer twice, or never let back to
    `held`.
    """
    return _names_any(item_ids, HELD_BY)


def ever_named_any(item_ids: Collection[int]) -> ColumnElement[bool]:
    """Listings that name one of these items **now or ever**, for a history read.

    The past-tense twin of `_holds_any`, and the same relation to it that
    `ever_claimed` has to `claims_for`: released claims count, because an
    offer that has ended is still where this coin has been offered.

    `routers.offers.list_listings` is the caller, for one item's offer
    history in the console's offers panel. It filtered on
    `Listing.inventory_item_id == item_id` alone until Task 14, which is
    NULL on a lot listing: a coin sold inside a lot got an empty list and
    the panel said "Not offered anywhere yet" about a coin that was on sale.

    Public, and here rather than in the router, for the reason `ever_claimed`
    gives: "is this item spoken for", present or past, is this module's
    question, and a fourth hand-rolled read of `offer_claim` outside it is
    the drift these functions exist to close. A `ColumnElement` rather than
    a list of listings so the router can keep composing its own venue,
    format and status filters onto one statement.
    """
    return _names_any(item_ids, None)


def offers_holding(db: Session, item_ids: Collection[int]) -> Sequence[Listing]:
    """Every live offer that holds any of these items, without locking.

    The same definition `_locked_offers` uses, for callers that are about to
    end offers on a batch of items rather than offer one: both the listings
    written *against* an item and any listing written against something else
    that claims it. A piece of a lot is offered by the lot's listing and has
    no listing of its own, so asking only the first half silently ignores
    exactly the case the claim table exists for.

    Public because `routers.inventory`'s `receive_items`, `bulk_edit` and
    `update_item` end whatever holds the items they change; asking only the
    first half would leave a lot holding a piece marked missing still on
    sale. One definition of "holds this item" or there are two, and the pair
    drift.
    """
    ids = set(item_ids)
    if not ids:
        return []
    return db.scalars(
        select(Listing)
        .where(Listing.status.in_(ON_OFFER), _holds_any(ids))
        .order_by(Listing.id)
    ).all()


def _lock_lots(db: Session, lot_ids: Collection[int]) -> None:
    """Take these lots' rows FOR UPDATE, in id order, and re-read them.

    The first of the three kinds, in one statement for the reason the other
    two are: N sorted statements are not a sorted acquisition.

    `offer` reaches this step through `_lot_members` rather than through
    here, and has to: it must hold the lot's row *before* it reads the
    membership, because that read is what gives it the item ids everything
    after depends on, so the lock cannot wait until those ids are known.
    Either way the lot row is taken first, which is the whole of the rule.

    `populate_existing` for the reason `_lock_items` gives, and one more
    here: `SalesLot` carries a `version_id_col`, and `_end` writes
    `sales_lot.status` through it at the end of a sale. Re-reading the row
    under its own lock is what keeps that write working from the version the
    lock just granted rather than from one a caller read earlier.
    """
    ids = sorted(set(lot_ids))
    if not ids:
        return
    db.scalars(
        select(SalesLot)
        .where(SalesLot.id.in_(ids))
        .order_by(SalesLot.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()


def _lock_listing_rows(
    db: Session, listing_ids: Collection[int], item_ids: Collection[int]
) -> Sequence[Listing]:
    """Take the listings FOR UPDATE, in id order, in one statement.

    Two ways in, and they are **one** statement because two would not be a
    sorted acquisition however each was sorted on its own. Named by id: what
    `order_writes` was handed, at any status, because a checkout has to hold
    a paused or ended listing in order to refuse it. Derived from the items:
    every live offer holding one of them, which is `_holds_any` -- the rule
    `offer` has always followed, since a piece of a lot is offered by the
    lot's listing and has no listing of its own.

    One statement matters for the derived half on its own terms too:
    `_locked_offers` below is called once per member, and a lot of two whose
    shop listings are #9 and #4 would take #9 then #4 that way, while a
    two-line `place_order` takes #4 then #9. Ascending id in one pass is what
    keeps that from being a deadlock of its own.

    Nothing can join the derived set between this pass and the per-member
    calls: a listing comes to hold an item only through a claim, this module
    is the only writer of claims, and `_acquire` has already taken these
    items' rows FOR UPDATE. So the per-member `_locked_offers` calls re-lock
    rows this transaction already holds, which neither blocks nor reorders
    anything -- which is why they can stay exactly as they are, asking their
    own question per member.

    `selectinload(Listing.sales_venue)` because every caller reads the venue
    off the rows it gets back -- `offer` to name where an item is already
    active, `order_writes` for `sellable_in_shop`, `sales_writes` to name the
    platform in a refusal. A lazy load would emit that SELECT while these
    rows are held FOR UPDATE, lengthening the lock for no reason, and a
    separate SELECT is what `selectinload` issues anyway, so it cannot widen
    the `FOR UPDATE` to `sales_venue` the way a join would.
    """
    named = sorted(set(listing_ids))
    items = set(item_ids)
    reachable: list[ColumnElement[bool]] = []
    if named:
        reachable.append(Listing.id.in_(named))
    if items:
        reachable.append(and_(Listing.status.in_(ON_OFFER), _holds_any(items)))
    if not reachable:
        return []
    return db.scalars(
        select(Listing)
        .where(or_(*reachable))
        .order_by(Listing.id)
        .with_for_update(of=Listing)
        .options(selectinload(Listing.sales_venue))
        .execution_options(populate_existing=True)
    ).all()


def _acquire(
    db: Session,
    *,
    lot_ids: Collection[int],
    item_ids: Collection[int],
    listing_ids: Collection[int],
) -> Sequence[Listing]:
    """**The** acquisition order, and the only place it is written down.

    Lot rows, then items, then listings -- each kind in one statement, each
    ascending id. Every writer that takes more than one kind of row comes
    through here, by way of `lock_for_sale`; this module's docstring lists
    them. A row that sits above a sale -- an `auction` or a `sales_order` --
    is taken before this pass, by the rule.

    **Two writers hold a row of this pass's own kinds taken outside it.**
    `offer` holds the lot row, which
    `_lot_members` must take before it can read the membership that produces
    the item ids -- so the lot is still first and the rule holds. And
    `split_item` holds the parent `inventory_item` row (`db.refresh(...,
    with_for_update=True)`) across the `end_offer` call at the end of it,
    which then takes listing rows through here. That is items-then-listings,
    the canonical direction, and it takes no lot row at all: its loop filters
    `Listing.inventory_item_id == parent.id`, which is NULL on a lot listing,
    and a parent that *is* an open member of an offered lot is refused
    outright by `split_item` before it gets that far. Both halves matter --
    without the refusal, `end_offer`'s pass would reach the lot listing
    through `offers_holding` and take a lot row after an item row.

    Items before listings because this module's listing set is *derived*
    from claims it is the only writer of, and the claim-uniqueness guarantee
    rests on holding the items first; nothing rests on the other direction
    (`docs/specs/lock-order-design.md`).

    **Two different mutations, and it is worth being exact about which does
    what, because the obvious guess is wrong.** Swapping two of the three
    lines below breaks the canonical order this module documents, and
    `test_a_checkout_takes_the_three_kinds_of_row_in_the_canonical_order`
    (`tests/test_offering_writes.py`) is what fails -- it measures the
    sequence of kinds on one connection. It does **not** bring the deadlock
    back: the cross-writer race test passes with these lines inverted,
    because there is only one copy of them and inverting it moves every
    writer at once. A deadlock needs *disagreement*, not a particular
    direction.

    What re-creates the deadlock is a caller that **bypasses this function**
    and takes a kind of row on its own -- `order_writes._lock_listings`
    locking listings itself, say -- and that is what
    `test_buying_a_lot_races_offering_one_of_its_coins`
    (`tests/test_offer_races.py`) fails on. That is the non-obvious half, and
    the reason the rule this function enforces is "come through here", not
    "prefer this direction".
    """
    _lock_lots(db, lot_ids)
    _lock_items(db, item_ids)
    return _lock_listing_rows(db, listing_ids, item_ids)


@dataclass(frozen=True)
class LockedForSale:
    """Everything one `lock_for_sale` pass took, for the caller that asked.

    `listings` is every listing row the pass holds, keyed by id: the ones the
    caller named and the ones derived from the items. A caller that named ids
    narrows it to those and reports the rest missing; `order_writes.
    _lock_listings` is the one that does.

    `item_ids` is the confirmed item set, ascending -- what `end_offer` walks
    when it decides which dispositions go back to `held`.

    `lot_ids` is the lot rows this pass holds, and it is reported rather than
    assumed because a caller can lock a lot listing without it: see
    `refuse_if_lot_unheld`, which is the one thing that reads it.
    """

    listings: dict[int, Listing]
    item_ids: tuple[int, ...]
    lot_ids: frozenset[int]


def _listings_by_id(db: Session, listing_ids: Collection[int]) -> list[Listing]:
    """The named listings, unlocked, for the read that chooses what to lock."""
    ids = sorted(set(listing_ids))
    if not ids:
        return []
    return list(
        db.scalars(
            select(Listing).where(Listing.id.in_(ids)).order_by(Listing.id)
        ).all()
    )


def _items_of(
    db: Session, listings: Sequence[Listing], *, including_paused: bool
) -> dict[int, tuple[int, ...]]:
    """Which items each listing brings into a lock pass, keyed by listing id.

    `offered_items` is the narrow, present-tense answer a *sale* needs: one
    item for an item listing, the lot's open members for a lot listing.
    `including_paused` asks `_affected_items` instead, which also reaches the
    items of the listings this one paused -- the wider set an *ending* has to
    move and a sale must not. The pair are not interchangeable, and the
    difference is one argument rather than two copies of this function
    because a copy is what would drift.
    """
    if including_paused:
        return {row.id: tuple(_affected_items(db, row)) for row in listings}
    return {
        row.id: tuple(item.id for item in offered_items(db, row)) for row in listings
    }


def _refuse_if_changed(
    listings: dict[int, Listing],
    before: dict[int, tuple[int, ...]],
    after: dict[int, tuple[int, ...]],
) -> None:
    """Refuse if a still-live listing's item set moved while it was being locked.

    Refuse, never loop: a retry would re-read a set that is not supposed to
    be able to move while the offer stands, so looping would hide the
    violation rather than resolve it. See `LockSetChanged` for why a 500 and
    not a 409.

    **One change is ordinary and is not refused: the offer ending.** An
    offered lot's membership is not frozen outright:
    `_refuse_unless_assembling` guards `lot_writes`, but `_end` in this
    module is a second writer of
    `sales_lot_item.released_at` and releases every open membership the
    moment the lot is sold or dissolved. The losing side of two checkouts
    racing one lot sees exactly that: it reads two members, waits, and finds
    none. So the set is only held to have been frozen while the listing is
    still `ON_OFFER`; a listing the lock found ended is one whose members
    were released by the ending itself, and each caller already has its own
    refusal for it -- `place_order` "is not currently for sale",
    `revise_order` "has ended, so the stock this order holds cannot be put
    back on sale", `record_sale` "is not on offer".

    The items locked from the stale read are still held, which is never
    unsafe: a lock pass that took *more* rows than it needed reorders
    nothing, and the caller writes nothing to them once it refuses.
    """
    for listing_id, chosen in before.items():
        if after.get(listing_id, ()) == chosen:
            continue
        row = listings.get(listing_id)
        if row is not None and row.status not in ON_OFFER:
            continue
        raise LockSetChanged(
            f"the items listing {listing_id} offers changed while its rows "
            f"were being locked: read {chosen}, locked {after.get(listing_id, ())}"
        )


def refuse_if_lot_unheld(listings: Iterable[Listing], lot_ids: Collection[int]) -> None:
    """Refuse to *end* a lot listing whose lot row this pass does not hold.

    The completeness half of the acquisition, and the twin of
    `_refuse_if_changed`: that one asks whether the *items* a listing offers
    moved between the read and the locks, this one whether a *lot* did.

    **Asked by the caller, not by `lock_for_sale`, and the difference is the
    whole design.** Locking a lot listing without its lot row is harmless in
    itself -- `offer` does it on every race it loses to a lot offer, then
    refuses cleanly with `OfferRefused` and never touches a lot row at all.
    It only becomes the inversion again if the caller goes on to call
    `end_offer` on that listing, whose own pass takes the lot row *late*,
    while items and listings are already held. So the refusal belongs at the
    sites that end derived listings: `routers.inventory`'s `receive_items`,
    `bulk_edit` and `update_item`, each of which ends whatever
    `offers_holding` returns. Making `lock_for_sale` refuse instead turns an
    ordinary lost race into a 500 -- measured, it reds
    `test_offering_a_lot_races_offering_one_of_its_members`.

    A lot row can only be taken first, so a lot listing that arrived in the
    derived half after `lock_for_sale`'s `offers_holding` read cannot be
    repaired by taking its lot row now: that would be lots after listings,
    the inversion this module exists to prevent. Refusing is the one answer
    that keeps the order true, and the rows are held, so it is deterministic
    rather than something to retry.

    Reachable only by a lot being offered in the window between that read and
    the item lock -- microseconds, and it needs a concurrent `offer` of a lot
    containing one of these items. `LockSetChanged` for the reason that
    exception gives: not a conflict a caller can act on, and a named 500
    beats either a deadlock or the quiet ending of a lot whose row nobody
    holds.
    """
    held = set(lot_ids)
    for listing in listings:
        if listing.sales_lot_id is not None and listing.sales_lot_id not in held:
            raise LockSetChanged(
                f"listing {listing.id} belongs to lot {listing.sales_lot_id}, "
                "whose row this pass did not take: it joined the set after the "
                "read that chose which lot rows to lock"
            )


def lock_for_sale(
    db: Session,
    *,
    listing_ids: Collection[int] = (),
    item_ids: Collection[int] = (),
    including_paused: bool = False,
) -> LockedForSale:
    """Take every row a sale or an offer touches, in the canonical order.

    The public door onto `_acquire`, and the reason the order has one owner
    rather than four call sites that agree by hand.

    **Two entry points, one closure.** `offer` enters from *items* -- one
    item, or a lot's members, already frozen under the lot's own row lock by
    `_lot_members` -- and passes them as `item_ids`; the listings are then
    derived. `order_writes` and
    `sales_writes` enter from *listings*, because `place_order` is handed
    `Line(listing_id=...)`; they pass `listing_ids` and this function resolves
    the closure the other way, listing to lot to member.

    **Why reading the members before locking anything is safe.** While the
    offer stands, its membership is frozen: `lot_writes.
    _refuse_unless_assembling` refuses a membership change on a lot that is
    not `assembling`, and `_refuse_grouped` refuses offering a member of an
    offered lot on every venue. A listing a checkout can reach is on offer,
    so its member set cannot move underneath this read -- until the offer
    itself ends, which `_end` does by releasing every membership, and which
    `_refuse_if_changed` treats as the ordinary outcome it is rather than a
    violation.

    The re-read below confirms that rather than trusting it, for the reason
    this module's docstring gives: it is what makes "locked before the
    claims are read" true rather than nearly true.

    `including_paused` widens the derived item set from `offered_items` to
    `_affected_items` (see `_items_of`). Four callers pass it, each because
    it ends offers: `end_offer`, and `routers.inventory`'s `receive_items`,
    `bulk_edit` and `update_item`.

    **The one obligation this function does not discharge, and the only rule
    in this design a caller has to remember.** The listing rows it hands back
    are not only the ones named: `_lock_listing_rows` also takes every live
    offer holding one of the items, and one of those can be a **lot** listing
    the caller never named. Their lot rows are taken too, but only as of this
    function's own `offers_holding` read, which is unlocked -- a lot offered
    between that read and the item lock is reached by the derived half with
    no lot row held. `lot_ids` on the result says which lot rows are actually
    held.

    That is harmless until a caller **ends** such a listing: `end_offer`
    takes its lot row *late*, while items and listings are already held,
    which is the inversion this whole module exists to prevent, one level
    down. So: **a caller that may call `end_offer` on a listing it did not
    name must first pass that set through `refuse_if_lot_unheld` with
    `lot_ids`.** Three do -- `routers.inventory`'s `receive_items`,
    `bulk_edit` and `update_item`, each of which ends whatever
    `offers_holding` returns rather than a list of ids. Every other caller
    ends only listings it named, whose lot rows are always held.

    It is not enforced here, and that is measured rather than lazy: refusing
    inside this function turns ordinary lost races into 500s -- keyed on the
    derived half it reds `test_offering_a_lot_races_offering_one_of_its_
    members`, keyed on `listing_ids` it would catch `place_order`, keyed on
    `including_paused` it would catch `end_offer`. The obligation is stated
    here, at the door, because the failure mode if a caller forgets it is a
    Postgres deadlock under concurrency -- a 500 on a money path, not
    reproducible from a single request.

    Raises `LockSetChanged` if a still-live listing's set disagrees. Writes
    nothing, on any path.
    """
    named = sorted(set(listing_ids))
    chosen = _listings_by_id(db, named)
    before = _items_of(db, chosen, including_paused=including_paused)

    items = set(item_ids)
    for reachable in before.values():
        items.update(reachable)

    # Lot rows for **both** halves of the listing set, not just the named
    # half. `_lock_listing_rows` also takes every live offer holding one of
    # these items, and one of those can be a lot listing the caller never
    # named -- a coin whose own store listing is paused while an offered lot
    # holds it, which `routers.inventory`'s writers reach by asking
    # `offers_holding` rather than by naming ids. Locking such a listing
    # without its lot row leaves the caller to take that lot row *later*,
    # through `end_offer`, while already holding items and listings: the
    # inversion, one level down, against a checkout that holds the lot row
    # and waits on a member.
    #
    # `offers_holding` rather than a predicate of its own, so "which live
    # offers hold these items" has the one definition `_lock_listing_rows`
    # derives from. It is **unlocked**, so a lot offered between this read
    # and the locks below would be reached by the derived half and have no
    # row held here.
    #
    # Nothing in this function covers that window. `refuse_if_lot_unheld`
    # does -- for the callers that ask it, the three `routers.inventory`
    # writers the docstring names. `lock_for_sale` reports the
    # lot rows it holds in `LockedForSale.lot_ids` and leaves the question
    # there, because asking it here refuses a real path: see that function.
    lot_ids = {row.sales_lot_id for row in chosen if row.sales_lot_id is not None}
    lot_ids |= {
        row.sales_lot_id
        for row in offers_holding(db, items)
        if row.sales_lot_id is not None
    }

    locked = {
        row.id: row
        for row in _acquire(db, lot_ids=lot_ids, item_ids=items, listing_ids=named)
    }
    after = _items_of(
        db,
        [locked[listing_id] for listing_id in named if listing_id in locked],
        including_paused=including_paused,
    )
    _refuse_if_changed(locked, before, after)
    return LockedForSale(
        listings=locked,
        item_ids=tuple(sorted(items)),
        lot_ids=frozenset(lot_ids),
    )


def _locked_offers(db: Session, item_id: int) -> Sequence[Listing]:
    """The listings that already hold this item, locked and re-read.

    Both the listings written against the item and any written against
    something else that claims it (a lot, in phase 3). `populate_existing`
    for the reason `_lock_listing_rows` gives: a row already in the
    session's identity map would otherwise come back locked but stale.

    Paused counts, exactly as it does in `_still_offered` and `claims_for`.
    One definition of "holds this item" or there are two, and the pair drift:
    a paused claim invisible here but visible there would be an item this
    module would happily offer again and would never let go back to `held`.
    The listing-status half of `_holding_claims` is the outer `where` below.

    Called once per member by `offer`, *after* `_lock_listing_rows` has
    taken the whole batch in one ascending statement -- see there for why
    that order matters and why re-locking here is free.
    """
    return db.scalars(
        select(Listing)
        .where(Listing.status.in_(ON_OFFER), _holds_any([item_id]))
        .order_by(Listing.id)
        .with_for_update(of=Listing)
        .options(selectinload(Listing.sales_venue))
        .execution_options(populate_existing=True)
    ).all()


# --------------------------------------------------------------------------
# Offering and ending
# --------------------------------------------------------------------------


#: Dispositions that say the item has been sold and is no longer ours to
#: offer. The mirror image of `end_offer`'s rule that only a `listed` item
#: goes back to `held`: `held` and `listed` are the two this module owns, and
#: a sale wrote the rest. `returned_by_buyer` is deliberately absent -- that
#: coin came back and offering it again is exactly what happens next, so
#: refusing it would be a dead end with no remedy.
SOLD_AWAY = frozenset({"sold", "shipped", "delivered"})


def _refuse_sold(db: Session, item: InventoryItem) -> None:
    """Refuse an item a buyer has already bought, naming what holds it.

    Two ways to be spoken for, and `app.sale_state` already knows both: the
    disposition a settled sale wrote, and an order that has not shipped. The
    order half is asked through `sale_state.for_sale` rather than queried
    again here, so "spoken for" has one definition -- the one the console
    warns on before an edit -- instead of two that can drift apart. Only its
    order half is consulted: a listing offering the item is not a refusal but
    the ordinary case `offer` pauses, and it has its own messages below.

    Imported here rather than at the top because `sale_state` imports this
    module: it is the reader built on top of this writer, and the dependency
    is meant to run that way round. A module-level import back would make the
    two load correctly only in one order, which is the same trap
    `reference_merge.plan` avoids the same way.
    """
    from . import sale_state

    disposition = item.disposition.code
    if disposition in SOLD_AWAY:
        raise OfferRefused(
            item.item_code, f"has already been sold (it is {disposition})"
        )
    held_by = [
        use
        for use in sale_state.for_sale(db, [item.id]).get(item.id, [])
        if use.kind == "order"
    ]
    if held_by:
        raise OfferRefused(item.item_code, f"is held by {held_by[0].text}")


def _refuse_unofferable(db: Session, item: InventoryItem, venue: SalesVenue) -> None:
    """Refuse an item that is not the business's to offer, naming which reason."""
    if item.deleted_at is not None:
        raise OfferRefused(item.item_code, "has been deleted")
    if item.split_at is not None:
        raise OfferRefused(
            item.item_code, "has been split into pieces; offer the pieces"
        )
    status = item.status.code
    if status != "received":
        raise OfferRefused(item.item_code, f"is not received (it is {status})")
    if not venue.is_active:
        raise OfferRefused(item.item_code, f"{venue.name} is retired")
    _refuse_sold(db, item)
    _refuse_grouped(db, item)


def _refuse_grouped(db: Session, item: InventoryItem) -> None:
    """Refuse an item that is an open member of a lot already offered.

    "Once offered it is frozen: the buyer is looking at that exact group"
    (spec, *`sales_lot` and `sales_lot_item`*). A coin leaving the group
    changes what the group *is*, so this is not the shop-to-eBay case where a
    coin moves in one step -- that rule is about an item's own store listing,
    not about a group someone is being shown. The owner ends or dissolves the
    lot and then offers the coin: one deliberate act rather than a silent
    cascade.

    The cascade this prevents is concrete. A lot offered **in the shop** has
    an own-store listing whose claims hold its members, so offering a member
    on eBay would find that listing under `ours` and *pause* it -- pausing
    every other member's claim with it. Settling the eBay sale then reaches
    the paused lot listing through `_end`, dissolving the lot, while
    `end_offer` returns before the disposition loop because the sale was
    `sold=True`: every other member is left at `listed` with nothing offering
    it. That is also why `_end` can still say a paused listing is an item
    listing -- this refusal is what makes it true.

    `OfferRefused`, not `LotRefused`: this is `offer` turning down one item,
    and `routers/offers.py` reads `item_code` off it to build what a person
    sees. Only `offered` refuses -- an `assembling` lot has been shown to
    nobody, and a `sold` or `dissolved` lot has released its members, so
    `lot_holding` cannot return one.

    `lot_writes.lot_holding` rather than a query of this module's own, so
    "already in a lot" has one definition; `uq_sales_lot_item_open` is what
    makes it a single row. Private, and with the one caller above:
    `routers.inventory.delete_item` needs the same fact but not this
    refusal -- a delete has its own message and its own pair of questions
    (`sale_state.ever_offered`, then `lot_writes.lot_holding`), and it asks
    `lot_holding` directly, which is the shared definition. Nothing outside
    this module calls this one.
    """
    from . import lot_writes

    lot = lot_writes.lot_holding(db, item.id)
    if lot is not None and lot.status is SalesLotStatus.offered:
        raise OfferRefused(
            item.item_code,
            f"is in lot #{lot.id}, which is offered: "
            "end or dissolve the lot before offering it on its own",
        )


def _lot_members(db: Session, lot: SalesLot) -> list[InventoryItem]:
    """Freeze one assembling lot's membership, and hand back the items in it.

    The lot's row is taken FOR UPDATE *before* its members are read, and that
    order is the whole point. Nothing bumps `sales_lot.version` when a
    membership row is written -- `add_member` inserts into `sales_lot_item`
    and never updates `sales_lot` -- so the optimistic lock `version_id_col`
    gives this table cannot see a concurrent membership change at all. It only
    protects the status write at the end of `offer` from another *status*
    writer. Reading the membership under the row lock, and
    `lot_writes._refuse_unless_assembling` taking the same lock before it
    reads `status`, are what make the group this function freezes the same
    group nothing can add to afterwards: whichever of the two arrives second
    waits, re-reads, and finds the answer the first one wrote.

    `lot_writes` imported inside the function, not at the top, for the same
    reason `_refuse_sold` imports `sale_state` that way: `lot_writes` imports
    this module at module level, so a module-level import back would make the
    pair load correctly in only one order -- and `tests/conftest.py` imports
    `app.lot_writes` first, which is the order that would fail.
    """
    from . import lot_writes

    # Flushed first, because `Session.refresh` expires the instance *before*
    # it autoflushes: a pending change to this lot would be discarded rather
    # than written. No caller can do that today, and this is the line that
    # keeps it that way once Task 5 gives lots a router.
    db.flush()
    db.refresh(lot, with_for_update=True)
    if lot.status is not SalesLotStatus.assembling:
        raise lot_writes.LotRefused(
            f"lot #{lot.id} is {lot.status.value}, so it cannot be offered"
        )
    rows = lot_writes.open_members(db, lot)
    if not rows:
        # `EmptyLot`, a `LotRefused` subclass, so the router answers 422 for
        # bad input rather than 409 for a conflict (spec, *API*).
        raise lot_writes.EmptyLot(f"lot #{lot.id} has no members to offer")
    # `open_members` orders by `inventory_item_id`; every reader downstream
    # relies on that one sequence, so it is never re-sorted here.
    return [row.item for row in rows]


def offer(
    db: Session,
    *,
    item: InventoryItem | None = None,
    lot: SalesLot | None = None,
    venue: SalesVenue,
    listing_format: ListingFormat,
    price: Decimal,
    title: str,
    description: str,
    external_id: str | None,
    quantity: int = 1,
) -> Listing:
    """Offer one item, or one lot, on one platform. Raises OfferRefused.

    Exactly one of `item` and `lot`. Passing both or neither is a
    `ValueError`, not an `OfferRefused`: `OfferRefused` names an item code a
    person can act on and there is none to give, the request schema makes it
    unreachable from outside, and a caller that did it has a bug.

    Every refusal is decided before anything is written, so a refused offer
    leaves the items exactly as they were -- which is what lets a batch of
    offers, and a lot with one unofferable member, be all or nothing.

    A lot is a list of members and the single-item case is a list of one, so
    there is one implementation of the rules rather than two that can drift.
    """
    if item is not None and lot is not None:
        raise ValueError("offer() takes exactly one of item= and lot=, not both")
    if lot is not None:
        members = _lot_members(db, lot)
    elif item is not None:
        members = [item]
    else:
        raise ValueError("offer() takes exactly one of item= and lot=, not neither")

    member_ids = [member.id for member in members]
    # Lot, then items, then listings -- each in one ascending statement, and
    # through the one function that owns that order (`lock_for_sale`), not a
    # sequence written out again here. The lot row is already held: for a lot,
    # `_lot_members` above took it before it read the membership, because that
    # read is what produced `member_ids` at all. The per-member
    # `_locked_offers` calls below re-lock what this already holds.
    lock_for_sale(db, item_ids=member_ids)

    # Every condition for every member, before the first write below.
    to_pause: dict[int, Listing] = {}
    for member in members:
        _refuse_unofferable(db, member, venue)
        holding = _locked_offers(db, member.id)
        # Elsewhere first, whatever the listing ids happen to be: an item held
        # on another platform is the refusal a person has to act on, and
        # saying "it is already in the shop" instead would send them to the
        # wrong screen.
        elsewhere = [held for held in holding if not held.sales_venue.is_own_store]
        if elsewhere:
            raise OfferRefused(
                member.item_code,
                f"is active on {elsewhere[0].sales_venue.name}, "
                f"listing #{elsewhere[0].id}: end it first",
            )
        ours = [held for held in holding if held.sales_venue.is_own_store]
        # Offering an *item* on the store while it is already active on the
        # store is a duplicate listing of the same thing, and the spec simply
        # refuses it. A lot is not a duplicate of its member's listing: it is
        # the coin moving from being sold on its own to being sold as part of
        # a group, which is the same one-step transition shop -> eBay already
        # is, so the member's store listing is *paused* instead. Both exits
        # then come out right -- if the lot sells the member is sold and
        # nothing resumes; if the lot is dissolved the member's store listing
        # comes back, which is what the owner wants after an unsold grouping.
        if ours and venue.is_own_store and lot is None:
            raise OfferRefused(
                member.item_code,
                f"is already offered in the shop, listing #{ours[0].id}",
            )
        # By listing id, defensively: `_refuse_unofferable` (above, via
        # `_refuse_grouped`) has already refused any member that is an open
        # part of an already-offered lot, so within one call `held` can only
        # ever be a plain item listing and `ours` cannot repeat a listing id
        # across members. Keyed regardless, so the loop stays safe rather
        # than relying on that invariant to hold forever.
        to_pause.update({held.id: held for held in ours})

    listing = Listing(
        inventory_item_id=item.id if item is not None else None,
        sales_lot_id=lot.id if lot is not None else None,
        price=price,
        currency_id=require_code(db, Currency, OFFER_CURRENCY, "currency"),
        # A lot is one thing however many coins are in it, which is also what
        # `ck_listing_lot_quantity_one` enforces.
        quantity_available=1 if lot is not None else quantity,
        sales_venue_id=venue.id,
        format=listing_format,
        status=ListingStatus.active,
        title=title,
        description=description,
        external_id=external_id,
    )
    db.add(listing)
    db.flush()
    db.add(
        ListingStatusHistory(
            listing_id=listing.id,
            from_status=None,
            to_status=ListingStatus.active,
            note="offered",
        )
    )

    # Before the new claims, not after: the store listing's claim is active
    # until it is paused, and two active claims on one item is exactly what
    # the partial unique index refuses.
    for paused in to_pause.values():
        _set_status(
            db, paused, ListingStatus.paused, f"paused for listing #{listing.id}"
        )
        paused.paused_by_listing_id = listing.id
        _move_claims(db, paused, ClaimState.paused)
    db.flush()

    listed = require_code(db, Disposition, "listed", "disposition")
    for member in members:
        db.add(
            OfferClaim(
                inventory_item_id=member.id,
                listing_id=listing.id,
                state=ClaimState.active,
            )
        )
        member.disposition_id = listed
    if lot is not None:
        lot.status = SalesLotStatus.offered
    db.flush()
    return listing


def offered_items(db: Session, listing: Listing) -> list[InventoryItem]:
    """The items this listing itself offers, in item id order.

    One for an item listing; a lot listing's open members for a lot. The
    one answer in the codebase to "which items": `lock_for_sale` (through
    `_items_of`), `order_writes._after_stock_change` and `_sync_shares`,
    `sales_writes._shared_items`, `sale_snapshot.take`, and `auctions`'
    `consign`, `settle` and `_return_from_consignment` all ask here rather
    than each deciding for themselves, because several answers to one
    question are several places for a lot's members to be silently skipped.

    Deliberately *narrower* than `_affected_items`, which also carries the
    items of listings this one paused: moving a paused listing's item is
    an ending's job, never a sale's.

    "Open" members, so a caller must ask before `end_offer` releases them --
    which is the order a sale already runs in: the snapshot and the shares
    are built from the listing, and only then is the listing ended.
    """
    if listing.sales_lot_id is None:
        return [] if listing.inventory_item is None else [listing.inventory_item]

    from . import lot_writes

    lot = listing.sales_lot
    if lot is None:  # pragma: no cover - the check constraint forbids it
        return []
    return [row.item for row in lot_writes.open_members(db, lot)]


def _affected_items(db: Session, listing: Listing) -> list[int]:
    """Every item an ending touches: this listing's, and those it paused.

    Only claims that still hold something. An item whose claim on this
    listing was released earlier has already moved on -- it may be offered
    somewhere else entirely -- and re-deciding its disposition here would
    reach into a sale this ending has nothing to do with.

    Wider than `offered_items` on purpose, and the pair are not
    interchangeable: this one also reaches the items of the listings this one
    paused, which an *ending* must move and a *sale* must not. For "which
    items does this listing offer", ask `offered_items`.

    The NULL filter is a lot listing: its own `inventory_item_id` is NULL, so
    the first query yields `None`, and `sorted({None, 12, 13})` raises
    `TypeError` -- `end_offer` on a lot listing would crash rather than skip.
    A lot's members reach this set through the claim half below, which already
    finds them because `offer` writes one claim per member.
    """
    touched = select(Listing.id).where(
        or_(Listing.id == listing.id, Listing.paused_by_listing_id == listing.id)
    )
    ids = {
        item_id
        for item_id in db.scalars(
            select(Listing.inventory_item_id).where(Listing.id.in_(touched))
        ).all()
        if item_id is not None
    }
    ids |= set(
        db.scalars(
            select(OfferClaim.inventory_item_id).where(
                OfferClaim.listing_id.in_(touched),
                OfferClaim.state.in_(HELD_BY),
            )
        ).all()
    )
    return sorted(ids)


def _still_offered(db: Session, item_id: int) -> bool:
    """Whether anything still offers this item, so its disposition stands.

    A claim or a listing: a listing written outside this module has no claim,
    and an item whose shop listing is still up must not be filed as merely
    held because an offer elsewhere ended. The claim half is `_holding_claims`,
    the same question `claims_for` and the refusal path ask.
    """
    claimed = db.scalars(
        _holding_claims().where(OfferClaim.inventory_item_id == item_id).limit(1)
    ).first()
    if claimed is not None:
        return True
    listed = db.scalar(
        select(Listing.id)
        .where(
            Listing.inventory_item_id == item_id,
            Listing.status.in_(ON_OFFER),
        )
        .limit(1)
    )
    return listed is not None


def _set_status(
    db: Session, listing: Listing, status: ListingStatus, note: str
) -> None:
    """Change a listing's status and record the change, in one place.

    Every status change after the opening one goes through here, so
    `ListingStatusHistory` cannot fall behind `listing.status`: a writer
    that set the column directly would be a second path, and the history
    would silently stop being true -- the same reason `lifecycle_writes`
    owns an item's status.
    """
    db.add(
        ListingStatusHistory(
            listing_id=listing.id,
            from_status=listing.status,
            to_status=status,
            note=note,
        )
    )
    listing.status = status


def _end(
    db: Session, listing: Listing, *, sold: bool = False, note: str | None = None
) -> None:
    """End one listing, release what it held, and settle its lot if it has one.

    `note` is what the status history records for the ending; by default
    "sold" or "withdrawn" from `sold`.

    A lot listing's lot ends with it -- `sold` when the sale path ended this
    offer, `dissolved` otherwise -- and every open membership is released.
    This is the only place `sales_lot.status` moves past `offered` and the
    only place `sales_lot_item.released_at` is ever written: `lot_writes`
    *deletes* a membership dropped during assembly rather than releasing it,
    precisely so a released row always means a lot that was really offered
    and then sold or dissolved.

    `sold` is keyword-only with a `False` default so the resume path below,
    which ends the store listings a sold offer paused, reads unchanged. Those
    listings are item listings -- but only because `_refuse_grouped` makes it
    so, not because a lot listing could not otherwise be paused. A lot offered
    in the shop *would* be paused by an offer of one of its members, and
    ending that offer sold would dissolve the lot here while `end_offer`
    skipped the disposition loop, stranding every other member at `listed`.
    That refusal is the reason this branch does not fire for a paused
    listing; if it is ever relaxed, this function is where the damage lands.

    A dissolved lot never comes back: re-offering the same coins starts a new
    lot, which is why nothing here moves a lot back to `assembling`.

    **An already-ended listing is left exactly as it is**, and that guard is
    here rather than in `end_offer` or in any caller. It is not defensive
    tidying: `routers.offers.end_listing`
    (`POST /api/listings/{id}/end`) loads its listing with a plain id lookup
    and calls `end_offer(sold=False)` unconditionally, so a lot bought in the
    shop -- settled as `sold`, its listing `ended` -- was one admin API call
    from being rewritten to `dissolved` while its members stayed `sold`,
    because `end_offer` skips the disposition loop for a sale. `sold` and
    `dissolved` are different histories and never collapse into one (spec,
    *`sales_lot` and `sales_lot_item`*), and no concurrency is needed to
    collapse them -- a stale console tab is enough
    (`test_ending_a_sold_lot_s_listing_again_leaves_it_sold`).

    **Why here and not in `end_offer`.** This module is the single writer of
    `sales_lot.status`, so an invariant each caller has to remember is the
    "four call sites that agree by hand" shape this branch exists to remove.
    A guard in `end_offer` would also miss the `paused_by_it` loop there,
    which calls this function directly.

    **An early return, not a refusal**, for two reasons. Ending an offer
    that has already ended asks for a state it is already in, so returning
    makes the endpoint idempotent rather than punishing a double-click or a
    stale tab. And the harm being prevented is a **rewrite** -- of
    `lot.status`, and of `ended_at`, which a second ending would move off the
    moment the offer really ended -- not an action anyone needs to be told
    about. The cost is that this cannot tell a caller its request did
    nothing; nothing today wants to know.

    The `paused_by_it` loop never reaches this guard: it filters
    `Listing.status == ListingStatus.paused`.
    """
    if listing.status is ListingStatus.ended:
        return
    _set_status(
        db,
        listing,
        ListingStatus.ended,
        note if note is not None else ("sold" if sold else "withdrawn"),
    )
    listing.ended_at = utcnow()
    listing.paused_by_listing_id = None
    _move_claims(db, listing, ClaimState.released)
    if listing.sales_lot_id is None:
        return

    from . import lot_writes

    lot = listing.sales_lot
    if lot is None:  # pragma: no cover - the check constraint forbids it
        return
    released = utcnow()
    for member in lot_writes.open_members(db, lot):
        member.released_at = released
    # No explicit `FOR UPDATE` here, and two separate reasons why that is
    # safe. The first: every caller reaches `_end` through `end_offer`, whose
    # `lock_for_sale` pass takes the lot of the listing being ended as its
    # first statement, so by the time the UPDATE below runs this transaction
    # is holding that row already and the path's order is the canonical
    # lot -> items -> listings. That reason is new, and it is the one that
    # generalises -- but it rests on the caller's pass having named *this*
    # lot, which is true of `end_offer` and is worth checking rather than
    # assuming if `_end` ever gains a second entry point.
    #
    # The second, which held before that and still does independently: the
    # lot reached here always has a listing, so it is never a lot a
    # concurrent `offer` could be holding -- that lot is still `assembling`
    # and has no listing. Every `lot_writes` path that *waits* on a lot row
    # takes it first and holds nothing else: `add_member`, `remove_member`,
    # `edit_lot` and `delete_lot`, all through
    # `_refuse_unless_assembling`, plus `offer` through `_lot_members`.
    #
    # "Holds nothing else" is **not** true of every waiter in the codebase,
    # and saying so absolutely would be wrong: `order_writes.revise_order`
    # and `routers.orders.update_order_status` both lock the `sales_order`
    # row and only then reach a lot row, through `_lock_listings`. There is
    # no cycle all the same, and the reason is the acquisition order rather
    # than a claim about who writes that table: both of them take the
    # `sales_order` row *before* any lot row and nothing ever takes the two
    # the other way, because this module -- the only writer of
    # `sales_lot.status` -- never touches `sales_order` at all. (The table
    # itself has two writers, not one: `order_writes`, and
    # `update_order_status`, which sets `sales_order_status_id` directly.
    # Neither holds a lot row when it does.)
    # `version_id_col` then covers this write against a stale in-session lot,
    # and `_lock_lots` re-read the row so that version is the current one.
    lot.status = SalesLotStatus.sold if sold else SalesLotStatus.dissolved


def end_offer(
    db: Session, listing: Listing, *, sold: bool = False, note: str | None = None
) -> None:
    """End an offer, resuming or ending the store listings it paused.

    `sold=False` is a withdrawal: a store listing set aside for this offer
    comes back at the price it had, and an item nothing offers any more goes
    back to `held`.

    `sold=True` is the settlement of a sale made on this listing -- phase 2's
    record-a-sale and phase 4's auction settlement both end their listing this
    way. A store listing paused for it is **ended** rather than resumed, so a
    sold item cannot reappear in the shop, and the items are left alone: the
    sale path owns the move to `sold`.

    A **lot** listing ends its lot with it (`_end`): `dissolved` when
    withdrawn, `sold` when settled, and its members released either way. The
    two are different histories and never collapse into one.

    `note` is what `ListingStatusHistory` records for the ending, when the
    caller knows more than "sold" or "withdrawn" -- an auction lot that came
    back unsold, a coin marked missing, a lot split into pieces. Without one,
    `_end` records "sold" or "withdrawn" from `sold`.
    """
    # Lot row, then items, then listings, through the one owner of that order
    # (`lock_for_sale`). `including_paused=True` because an ending's item set
    # is the wider `_affected_items`: the items of the listings this one
    # paused have to move too, and a sale's must not (`_items_of`).
    #
    # The listing comes back locked and re-read for the same reason
    # `_locked_offers` re-reads one before `offer` touches it: `listing` is
    # whatever the caller already had in hand, and a concurrent `offer`
    # elsewhere may have paused it -- bumping its optimistic-lock version --
    # between when the caller loaded it and when the item locks were granted.
    # Without that, `_end` below writes through the caller's stale version and
    # raises `StaleDataError` even though nothing is actually wrong: the item
    # lock already serialized the two writers, and this listing's *current*
    # row is exactly what this function is entitled to act on.
    locked = lock_for_sale(db, listing_ids=[listing.id], including_paused=True)
    item_ids = list(locked.item_ids)
    listing = locked.listings[listing.id]

    # Still paused, not merely pointing here. A listing ended while it was
    # paused keeps the pointer -- the catalog API's retired
    # `PATCH .../is_active` ended one without clearing it -- and resuming
    # that would put a listing an administrator deliberately withdrew back
    # into the public shop, re-claiming the item with it.
    #
    # This re-locks rows the pass above already holds and so reorders
    # nothing -- **while each paused listing still has a `HELD_BY` claim**,
    # which is the condition and not an aside. `_affected_items` reaches a
    # paused listing's items through its claims, and `_lock_listing_rows`
    # takes every live offer holding those items, so a paused listing whose
    # claim were already `released` would fall outside `_holds_any` and be
    # acquired for the first time *here*, after the listings above -- out of
    # order. Nothing produces that state: `_move_claims` releases a listing's
    # claims only in `_end`, which sets its status to `ended` in the same
    # breath, and the `status == paused` filter below then excludes it. So
    # this statement is here to *identify* rows, not to acquire them, the
    # same way `_locked_offers` asks its own question per member.
    paused_by_it = db.scalars(
        select(Listing)
        .where(
            Listing.paused_by_listing_id == listing.id,
            Listing.status == ListingStatus.paused,
        )
        .order_by(Listing.id)
        .with_for_update(of=Listing)
        .execution_options(populate_existing=True)
    ).all()

    _end(db, listing, sold=sold, note=note)
    # Flushed before anything is resumed, not with it: this listing's claim
    # must be released in the database before the claim it paused goes back to
    # active, or the two are briefly active on one item and the partial unique
    # index refuses the pair. A single flush would leave that order to the
    # unit of work, which sorts by primary key and inserts before it updates.
    db.flush()
    for other in paused_by_it:
        if sold:
            _end(db, other, note=f"ended: sold through listing #{listing.id}")
        else:
            _set_status(
                db,
                other,
                ListingStatus.active,
                f"resumed: listing #{listing.id} ended",
            )
            other.paused_by_listing_id = None
            _move_claims(db, other, ClaimState.active)
    db.flush()

    if sold:
        return
    held = require_code(db, Disposition, "held", "disposition")
    listed = require_code(db, Disposition, "listed", "disposition")
    for item_id in item_ids:
        if _still_offered(db, item_id):
            continue
        item = db.get(InventoryItem, item_id)
        # Only a listed item goes back to held, because `listed` is the only
        # disposition this module writes. An item bought through the shop is
        # `sold` while its listing stays active at zero stock
        # (`order_writes._after_stock_change`); ending that listing afterwards
        # must not report the item as merely held again, and `shipped`,
        # `delivered` and `returned_by_buyer` are no more this function's to
        # undo.
        if item is not None and item.disposition_id == listed:
            # This cleanup is scoped to the `listed` branch only, not to
            # every item `_still_offered` excluded. `_still_offered`
            # returning False means every `HELD_BY` claim on this item, if
            # any remain, is on a listing it did not count -- one an older
            # write ended without releasing its claim (the retired catalog
            # `PATCH .../is_active` is the one still-live example; see
            # `_holding_claims` and
            # `test_a_withdrawn_store_listing_is_not_resurrected`). Nothing
            # legitimately holds a `listed` item once this decides it is
            # `held` again, so a stray claim does not either -- leaving it
            # `active`/`paused` forever is exactly the shape
            # `check_disposition_invariant` (`tests/conftest.py`) exists to
            # catch, not a state this module should go on producing. The
            # identical stray-claim shape on a `SOLD_AWAY` item is left as
            # is by this guard; `check_claim_invariant` still catches that
            # one separately, so this is a narrower cleanup, not a hole.
            stray_claims = db.scalars(
                select(OfferClaim).where(
                    OfferClaim.inventory_item_id == item_id,
                    OfferClaim.state.in_(HELD_BY),
                )
            ).all()
            for stray in stray_claims:
                stray.state = ClaimState.released
            item.disposition_id = held
    db.flush()
