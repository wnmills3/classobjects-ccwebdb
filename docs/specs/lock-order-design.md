# Lock order for the selling writers

How every write that touches more than one kind of selling row takes its row
locks, and why. The code is `offering_writes.lock_for_sale` and the private
`_acquire` behind it; this note explains the rule and lists who follows it.

## The rule

**Lot rows, then items, then listings** -- each kind in *one* statement,
`FOR UPDATE`, ascending id. Rows that sit above a sale are taken before it:
an `auction` row (`auctions._lock_auction`) or a `sales_order` row
(`order_writes.revise_order`, `routers.orders.update_order_status`) comes
first, then the sale's rows in the order above.

The order lives in exactly one function, `offering_writes._acquire`, and
every writer that takes more than one kind of row reaches it through
`offering_writes.lock_for_sale`. The order is not a convention several
modules remember; an inversion is something you would have to write on
purpose.

One statement per kind matters as much as the order: N sorted statements are
not a sorted acquisition, and the listing half is where a lot makes that a
real hazard.

## Why this order

Two writers need the same rows for different reasons.

- **`offering_writes` must hold items before it reads claims.** Its listing
  set is *derived*: a listing holds an item only through an `offer_claim`,
  this module is the only writer of claims, and `_lock_items` holds those
  items' rows before the claims are read. Nothing can join the set between
  the read and the lock. Taking listings first would mean locking a set
  derived from an unlocked claim read. The one-live-claim-per-item guarantee
  rests on this order.
- **`order_writes` is handed listing ids** (`Line(listing_id=...)`), and no
  invariant of its own depends on which kind it takes first.

So the order that carries a guarantee is the canonical one. If the two
writers disagreed, a checkout and a concurrent offer of the same coin could
each hold what the other waits for, and Postgres would abort one of them: an
HTTP 500 on a money path instead of the clean refusal either path gives.
Every money-path request commits once, at the end, so an abort never leaves
anything half-written -- the cost is a bad error, not bad data.

## `lock_for_sale`

`lock_for_sale(db, *, listing_ids=(), item_ids=(), including_paused=False)`
returns a `LockedForSale` (`listings` by id, the confirmed `item_ids`, and
the `lot_ids` it holds). It writes nothing.

**Two entry points, one closure.** `offer` enters from items -- one item, or
a lot's members, which `_lot_members` froze under the lot's own row lock
before reading them. Checkout, revision, stock return, record-a-sale and
receiving enter from listings, and the function resolves listing → lot →
members itself. `including_paused=True` widens the derived item set from
`offered_items` (what a *sale* touches) to `_affected_items` (also the items
of the store listings an offer paused, which an *ending* must move).

**Read, lock, re-read.** Which items a listing offers has to be read before
anything can be locked. That is safe because an offered lot's membership is
frozen while the offer stands: `lot_writes._refuse_unless_assembling`
refuses a membership change on a lot that is not `assembling`, and
`_refuse_grouped` refuses offering a member of an offered lot on any venue.
The function re-reads the set under the locks to confirm it.

- A still-live listing whose set moved raises **`LockSetChanged`** -- a 500,
  unmapped in the routers, because it is an invariant violation no retry can
  fix. It is deliberately not an `OfferRefused` (409 means "something is in
  the way, try again", which would be false).
- **The offer's own ending is not a violation.** `offering_writes._end`
  releases every membership when a lot is sold or dissolved, so the loser of
  two checkouts racing one lot reads two members, waits, and finds none. A
  listing the lock found ended is left to each caller's own refusal:
  `place_order` "is not currently for sale", `revise_order` "has ended…",
  `record_sale` "is not on offer".

Locking more rows than a caller turns out to need is never unsafe; a caller
that refuses writes nothing to them.

## The one rule a caller has to remember

`lock_for_sale` also locks every live listing holding one of the items, and
one of those can be a **lot** listing the caller never named. Its lot row is
taken only if the function's own unlocked `offers_holding` read saw it, so a
lot offered between that read and the item lock is held without its lot row.
`LockedForSale.lot_ids` says which lot rows really are held.

**A caller that may call `end_offer` on a listing it did not name must first
pass that set through `refuse_if_lot_unheld`.** Otherwise `end_offer` would
take the lot row late, after items and listings -- the inversion one level
down. Exactly one caller does this: `routers.inventory.receive_items`, which
ends whatever `offers_holding` returns. Every other caller ends only
listings it named, whose lot rows are always taken first.

It is not enforced inside `lock_for_sale` because every narrower key turns
an ordinary lost race into a 500: keyed on the derived half it refuses
`offer` losing to a lot offer
(`test_offering_a_lot_races_offering_one_of_its_members`), keyed on
`listing_ids` it catches `place_order`, keyed on `including_paused` it
catches `end_offer`. The obligation is stated in `lock_for_sale`'s docstring,
in `refuse_if_lot_unheld`, and here.

## Who comes through it

| Writer | How it reaches `_acquire` |
|---|---|
| `offering_writes.offer` | `lock_for_sale(item_ids=...)`, after `_lot_members` has taken the lot row |
| `offering_writes.end_offer` | `lock_for_sale(listing_ids=..., including_paused=True)` |
| `order_writes.place_order` | `_lock_listings` → `lock_for_sale(listing_ids=...)` |
| `order_writes.revise_order` | `_lock_listings`, after its own `sales_order` row lock |
| `order_writes.return_stock` | `_lock_listings`, after the caller's `sales_order` row lock |
| `sales_writes.record_sale_lines` | `lock_for_sale(listing_ids=...)` |
| `routers.inventory.receive_items` | `lock_for_sale(listing_ids=..., item_ids=..., including_paused=True)` before its first write, then `refuse_if_lot_unheld` |
| `splitting.split_item` | `end_offer`, while holding the parent item row |
| `auctions.settle` | `_lock_auction`, then `lock_for_sale(item_ids=...)` over every coin in the auction |
| `auctions.add_lot`, `remove_lot`, `cancel` | `_lock_auction`, then `offer` / `end_offer` |

**Receiving** takes its locks before it writes any `inventory_item` row, and
its `offers_holding` read *above* the locks only chooses what to lock. The
authoritative read is a second `offers_holding` call after the writes, under
the locks. Acting on the first read could end a listing a concurrent
checkout had already ended, and for a lot listing that would rewrite
`sales_lot.status` from `sold` to `dissolved`
(`test_buying_a_lot_races_marking_one_of_its_coins_missing`).

**Splitting** is items → listings, the canonical direction, and takes no lot
row: its listing loop filters `Listing.inventory_item_id == parent.id`,
which is NULL on a lot listing, and `split_item` refuses outright a parent
that is an open member of an *offered* lot, so it never reaches a lot
listing through `end_offer`.

**Settlement** passes `item_ids` only, so the confirming re-read has nothing
to compare; that is safe because `add_lot` refuses any auction that is not
`draft` or `scheduled`, so a `closed` auction's lots cannot change under it.

## Every `with_for_update` in `backend/app`

| Site | Row kind | Role |
|---|---|---|
| `offering_writes._lock_lots` | `sales_lot` | acquisition, step 1 |
| `offering_writes._lock_items` | `inventory_item` | acquisition, step 2 |
| `offering_writes._lock_listing_rows` | `listing` | acquisition, step 3: named listings (any status) and derived ones (`ON_OFFER`) in one `or_` |
| `offering_writes._locked_offers` | `listing` | re-lock of rows already held, once per member |
| `offering_writes.end_offer` (`paused_by_it`) | `listing` | re-lock, to identify rows already held |
| `offering_writes._lot_members` | `sales_lot` | step 1 for `offer`, which must hold the row to read the membership |
| `lot_writes._refuse_unless_assembling` | `sales_lot` | single kind; `add_member`, `remove_member`, `edit_lot`, `delete_lot` |
| `order_writes.revise_order` | `sales_order` | taken before `_lock_listings` |
| `routers.orders.update_order_status` | `sales_order` | taken before `return_stock` |
| `auctions._lock_auction` | `auction` | outermost row for every auction transition |
| `splitting.split_item` | `inventory_item` | the parent, held across the `end_offer` call |

## Related rules

- **Ending is idempotent.** `offering_writes._end` returns early on a listing
  that is already `ended`, so a second `POST /api/listings/{id}/end` cannot
  rewrite a `sold` lot to `dissolved`
  (`test_ending_a_sold_lot_s_listing_again_leaves_it_sold`). It lives in
  `_end` because that is the single writer of `sales_lot.status`.
- **No false conflicts on items.** `_after_stock_change` writes
  `inventory_item.disposition`, which carries a version column; the items are
  locked and re-read first, so an unrelated concurrent edit to a coin does
  not refuse a revision or a cancellation. The `except StaleDataError`
  clauses in `revise_order` and `update_order_status` remain as defence in
  depth.

## Tests

- `test_a_checkout_takes_the_three_kinds_of_row_in_the_canonical_order`
  (`tests/test_offering_writes.py`) records the sequence of row kinds on one
  connection. It is what fails if `_acquire`'s lines are reordered.
- `test_buying_a_lot_races_offering_one_of_its_coins`
  (`tests/test_offer_races.py`) races a lot checkout against an offer of one
  of its coins on two real connections and asserts one winner, one clean
  refusal and no `OperationalError`. It fails if a caller **bypasses**
  `_acquire` (for example `_lock_listings` taking listings on its own).
  Reordering `_acquire` does not fail it: with one copy of the order, both
  writers move together and still agree. A deadlock needs disagreement, not
  a particular direction -- which is why the rule is "come through here".
- `tests/test_offer_races.py` also races two checkouts for one lot and a lot
  checkout against a receipt marking one of its coins missing;
  `tests/test_settlement_race.py` races settlement against settlement,
  cancellation and receiving, and consigning against cancelling.
