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
decision resting on values read before the wait. `offer` knows its item up
front and locks it first. `end_offer` cannot: which items an ending touches is
itself a query, so it reads the set, locks it, and reads again to confirm none
joined in between (`_lock_affected_items`). That second read is what makes the
"locked before the claims are read" rule true rather than nearly true.

**It owns the rule for what the shop may sell** -- our own store, fixed price,
active -- in both the Python form (`sellable_in_shop`) and the SQL form
(`shop_listing_filters`). Phase 1 left that rule written out in three places:
checkout, the catalogue's detail endpoint and the catalogue's list query. They
now all ask here.

Functions flush and never commit; the caller's request owns the transaction.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from decimal import Decimal

from sqlalchemy import ColumnElement, Select, or_, select
from sqlalchemy.orm import Session, selectinload

from .models import (
    ClaimState,
    Currency,
    Disposition,
    InventoryItem,
    Listing,
    ListingFormat,
    ListingStatus,
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
    "OfferRefused",
    "claims_for",
    "end_offer",
    "offer",
    "offered_items",
    "offers_holding",
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


# --------------------------------------------------------------------------
# The shop's rule, in its two forms
# --------------------------------------------------------------------------


def sellable_in_shop(listing: Listing, *, active_only: bool = True) -> bool:
    """Whether the shop may sell this listing: our store, fixed price, active.

    `active_only=False` asks only the first half -- whether the listing is
    this shop's at all, whatever state it is in. Two callers need that and
    say so: checkout, which refuses an inactive listing with its own message
    ("not currently for sale", which is not the same news as "not sold in
    this shop"), and the catalogue's detail endpoint, which serves a
    withdrawn listing so a page someone bookmarked can say it has ended.
    """
    ours = (
        listing.sales_venue.is_own_store and listing.format is ListingFormat.fixed_price
    )
    if not active_only:
        return ours
    return ours and listing.status is ListingStatus.active


def shop_listing_filters(*, active_only: bool = True) -> list[ColumnElement[bool]]:
    """The same rule as SQL, for the catalogue query.

    `active_only=False` is the catalogue's admin preview of withdrawn
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
    not always the only writer of that status -- the catalogue API's retired
    `PATCH .../is_active` withdrew a listing without touching its claims -- so
    a claim left reading `paused` on a listing an older write ended holds
    nothing. Everything that asks "is this item spoken for" asks through here,
    or the answers disagree and an item is either offered twice or never
    allowed back to `held`.
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
        # earlier, which for a lot (phase 3) would be a second active claim on
        # an item the index has already given to someone else.
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
    `order_writes._lock_listings` passes `populate_existing` and
    `splitting.split_item` locks with `db.refresh(..., with_for_update=True)`.
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


def offers_holding(db: Session, item_ids: Collection[int]) -> Sequence[Listing]:
    """Every live offer that holds any of these items, without locking.

    The same definition `_locked_offers` uses, for callers that are about to
    end offers on a batch of items rather than offer one: both the listings
    written *against* an item and any listing written against something else
    that claims it. A piece of a lot is offered by the lot's listing and has
    no listing of its own, so asking only the first half silently ignores
    exactly the case the claim table exists for.

    Public because `routers.inventory.receive_items` needs it and asked the
    first half only -- marking a piece missing left the lot holding it still
    on sale. One definition of "holds this item" or there are two, and the
    pair drift.
    """
    ids = set(item_ids)
    if not ids:
        return []
    claimed = select(OfferClaim.listing_id).where(
        OfferClaim.inventory_item_id.in_(ids),
        OfferClaim.state.in_(HELD_BY),
    )
    return db.scalars(
        select(Listing)
        .where(
            Listing.status.in_(ON_OFFER),
            or_(Listing.inventory_item_id.in_(ids), Listing.id.in_(claimed)),
        )
        .order_by(Listing.id)
    ).all()


def _locked_offers(db: Session, item_id: int) -> Sequence[Listing]:
    """The listings that already hold this item, locked and re-read.

    Both the listings written against the item and any written against
    something else that claims it (a lot, in phase 3). `populate_existing`
    for the reason `order_writes._lock_listings` gives: a row already in the
    session's identity map would otherwise come back locked but stale.

    Paused counts, exactly as it does in `_still_offered` and `claims_for`.
    One definition of "holds this item" or there are two, and the pair drift:
    a paused claim invisible here but visible there would be an item this
    module would happily offer again and would never let go back to `held`.
    The listing-status half of `_holding_claims` is the outer `where` below.
    """
    claimed = select(OfferClaim.listing_id).where(
        OfferClaim.inventory_item_id == item_id,
        OfferClaim.state.in_(HELD_BY),
    )
    return db.scalars(
        select(Listing)
        .where(
            Listing.status.in_(ON_OFFER),
            or_(Listing.inventory_item_id == item_id, Listing.id.in_(claimed)),
        )
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

    db.refresh(lot, with_for_update=True)
    if lot.status is not SalesLotStatus.assembling:
        raise lot_writes.LotRefused(
            f"lot #{lot.id} is {lot.status.value}, so it cannot be offered"
        )
    rows = lot_writes.open_members(db, lot)
    if not rows:
        # `EmptyLot`, a `LotRefused` subclass, so the router answers 422 for
        # bad input rather than 409 for a conflict (spec, *Errors*).
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

    _lock_items(db, [member.id for member in members])

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
        # By listing id, because one store listing can hold more than one
        # member and must not be paused (and have its claims moved) twice.
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

    # Before the new claims, not after: the store listing's claim is active
    # until it is paused, and two active claims on one item is exactly what
    # the partial unique index refuses.
    for paused in to_pause.values():
        paused.status = ListingStatus.paused
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
    one answer in the codebase to "which items": `_affected_items` below,
    `order_writes._after_stock_change`, `sales_writes._shared_items` and
    `sale_snapshot.take` all ask here rather than each deciding for
    themselves, because four answers to one question is four places for
    a lot's members to be silently skipped.

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


def _lock_affected_items(db: Session, listing: Listing) -> list[int]:
    """Lock every item the ending touches, then confirm none joined the set.

    The set has to be read before it can be locked, which leaves a window: a
    claim written in it names an item this call would never lock and never
    reconsider. So it is read again afterwards, and the second read decides.
    That second read is enough rather than a loop, because every writer of a
    claim -- this module is the only one -- holds the item's row before it
    writes: once these rows are held, no new claim naming them can appear.
    """
    ids = _affected_items(db, listing)
    _lock_items(db, ids)
    joined = _affected_items(db, listing)
    if joined != ids:
        _lock_items(db, joined)
    return joined


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


def _end(db: Session, listing: Listing, *, sold: bool = False) -> None:
    """End one listing, release what it held, and settle its lot if it has one.

    A lot listing's lot ends with it -- `sold` when the sale path ended this
    offer, `dissolved` otherwise -- and every open membership is released.
    This is the only place `sales_lot.status` moves past `offered` and the
    only place `sales_lot_item.released_at` is ever written: `lot_writes`
    *deletes* a membership dropped during assembly rather than releasing it,
    precisely so a released row always means a lot that was really offered
    and then sold or dissolved.

    `sold` is keyword-only with a `False` default so the resume path below,
    which ends the *store* listings a sold offer paused, reads unchanged --
    those are item listings, so the lot branch simply does not fire for them.
    A dissolved lot never comes back: re-offering the same coins starts a new
    lot, which is why nothing here moves a lot back to `assembling`.
    """
    listing.status = ListingStatus.ended
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
    # No row lock taken here, unlike `_lot_members`: `sales_lot.version` does
    # protect a status write against another status writer, this module is the
    # only one, and the listing above is already locked FOR UPDATE -- so the
    # two ways into this line (ending an offer, settling a sale) are already
    # serialised on the listing they both end.
    lot.status = SalesLotStatus.sold if sold else SalesLotStatus.dissolved


def end_offer(db: Session, listing: Listing, *, sold: bool = False) -> None:
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
    """
    item_ids = _lock_affected_items(db, listing)

    # Locked and re-read for the same reason `_locked_offers` re-reads a
    # listing before `offer` touches it: `listing` is whatever the caller
    # already had in hand, and a concurrent `offer` elsewhere may have paused
    # it -- bumping its optimistic-lock version -- between when the caller
    # loaded it and when the item lock above was granted. Without this,
    # `_end` below writes through the caller's stale version and raises
    # `StaleDataError` even though nothing is actually wrong: the item lock
    # already serialised the two writers, and this listing's *current* row is
    # exactly what this function is entitled to act on.
    listing = db.execute(
        select(Listing)
        .where(Listing.id == listing.id)
        .with_for_update(of=Listing)
        .execution_options(populate_existing=True)
    ).scalar_one()

    # Still paused, not merely pointing here. A listing ended while it was
    # paused keeps the pointer -- the catalogue API's retired
    # `PATCH .../is_active` ended one without clearing it -- and resuming
    # that would put a listing an administrator deliberately withdrew back
    # into the public shop, re-claiming the item with it.
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

    _end(db, listing, sold=sold)
    # Flushed before anything is resumed, not with it: this listing's claim
    # must be released in the database before the claim it paused goes back to
    # active, or the two are briefly active on one item and the partial unique
    # index refuses the pair. A single flush would leave that order to the
    # unit of work, which sorts by primary key and inserts before it updates.
    db.flush()
    for other in paused_by_it:
        if sold:
            _end(db, other)
        else:
            other.status = ListingStatus.active
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
            item.disposition_id = held
    db.flush()
