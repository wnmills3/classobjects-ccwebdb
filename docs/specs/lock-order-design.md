# Lock order between `order_writes` and `offering_writes`

**Status:** **built**, 2026-09-21, on `fix/lock-order`. The owner chose the
recommendation below: **`order_writes` moves**, and the canonical order is
**lot rows → items → listings**. It has one owner,
`offering_writes.lock_for_sale` over the private `_acquire`, and every
writer that takes more than one kind of row reaches it through that -- seven
of them across four modules, enumerated rather than asserted below. See *What was actually
built* at the end, which also records the **two** claims in this note that
turned out to be wrong: that an offered lot's membership is frozen outright,
and that inverting the shared helper would be the mutation that proves the
fix.
**Written:** 2026-09-21, after phase 3 (sales lots) merged at `2c864d9`.
**Defect it addresses:** *Known defect: the lock order between
`order_writes` and `offering_writes`* in `selling-design.md` — now removed
from that document, which records the fix in its place.

## The defect

Two modules take the same two kinds of row in opposite orders.

| Module | Order | Where |
|---|---|---|
| `offering_writes.offer` / `end_offer` | lot row → **items** → **listings** | module docstring; `_lock_items` then `_lock_offers` |
| `order_writes.place_order` / `revise_order` / `return_stock` | **listings** → **items** | `_lock_listings`, then the item writes in `_after_stock_change` |

A shopper checking out while the owner offers the same coin elsewhere can
each hold what the other waits for. Postgres detects it and aborts one
transaction: the victim sees **HTTP 500** instead of the clean refusal the
code otherwise produces, and which party loses is not deterministic.

**It is not new.** At `main` before phase 3, `_lock_listings` already ran
before `_after_stock_change`'s item write, against `offer`'s items-then-
listings. Buying the last unit of an ordinary item listing while offering
that item elsewhere deadlocked then too. Sales lots widen the exposure —
a lot sale always crosses zero, and touches N member rows instead of one —
without creating the inversion.

**Nothing is left half-written.** Every money-path request commits exactly
once, at the end (`routers/orders.py:210`, `:439`, `:475`,
`routers/offers.py:688`, `routers/lots.py:189`, `:267`, `:303`); no path
commits and then does more locking work. A deadlock abort discards the whole
transaction, so a retry is safe and the database is never inconsistent. That
is why this is a bad error rather than a data defect, and why it was left
out of phase 3 rather than rushed into it.

## Why the two orders exist

Neither is arbitrary, but only one of them is *load-bearing*.

**`offering_writes` must hold items before it reads claims.** Its listing set
is derived, not given: a listing holds an item only through a claim, and
`_lock_offers`' own docstring states the invariant that makes the derivation
safe —

> Nothing can join the set between this pass and the per-member calls: a
> listing comes to hold an item only through a claim, this module is the only
> writer of claims, and `_lock_items` holds these items' rows FOR UPDATE
> already.

Take the listings first and that sentence stops being true. The module would
have to lock a listing set it derived from an unlocked claim read, then
re-derive and re-check. The claim-uniqueness guarantee — one live claim per
item, the spine of the whole selling design — rests on this ordering.

**`order_writes` locks listings first because that is what it was handed.**
`place_order` receives `Line(listing_id=…)`. Listings come first for no
stated reason beyond being the input; the item writes happen later simply
because `_after_stock_change` runs after the decrement. No invariant in that
module depends on the order, and no docstring claims one does.

**So the incidental order is the one that should move.** Preserve the
ordering that carries a guarantee; change the one that is an accident of the
call signature.

## Can `order_writes` know its items before locking?

Yes, and safely — because an offered lot's membership is frozen.

`_refuse_unless_assembling` refuses `remove_member` on any lot that is not
`assembling`, and `_refuse_grouped` refuses offering a member of an offered
lot on every venue. So for a listing that is on offer — the only kind a
checkout can reach — the member set cannot change underneath a reader.

The sequence becomes: read `offered_items` for the listing ids (unlocked) →
lock those items ascending → lock the listings ascending → re-read the member
set and confirm it is unchanged. That final re-read is not new machinery; it
is exactly what `offering_writes._lock_affected_items` already does, and its
module docstring explains why: *"it reads the set, locks it, and reads again
to confirm none joined in between. That second read is what makes the
'locked before the claims are read' rule true rather than nearly true."*

## Recommendation: one owner for the acquisition order

Do not fix this by editing four call sites to take rows in a new order by
hand. Four hand-written sequences are four chances to drift, and this
codebase's strongest pattern is the opposite of that — `offering_writes` is
the single writer of listing status and claims, `order_writes` the single
writer of orders and shares, `offered_items` the single present-tense member
list. Every one of those exists because a second hand-rolled answer drifted
or would have.

**Give the acquisition order a single owner too.** A public
`offering_writes.lock_for_sale(db, listing_ids)` that takes lot rows, then
items, then listings, in the canonical order and in one statement per kind,
with the confirming re-read. `order_writes._lock_listings` calls it and keeps
its own `populate_existing` and `selectinload` behaviour for the listing
rows it returns.

Then the order is not a convention four modules remember. It is one function,
and an inversion becomes something you would have to write on purpose.

The four sites that change: `order_writes.place_order` (`:372`),
`revise_order` (`:559`), `return_stock` (`:790`), and `sales_writes`'
`FOR UPDATE OF Listing` read (`:221`). `offering_writes.offer` and
`end_offer` keep their current order and their documented rule; they would
call the same helper so that the rule has one implementation rather than two
agreeing copies.

## What this costs, and what it does not

- **Checkout gains one read before it locks.** `offered_items` for the lines'
  listings, which for a single-item listing is one row.
- **The re-read is a cheap confirmation**, not a retry loop: if the member
  set changed — which the freeze rules say it cannot — the request refuses
  rather than proceeding on a stale set.
- **No schema change, no migration.** This is ordering only.
- **The missing test becomes writable.** No test currently races
  `order_writes` against `offering_writes` on a lot, because the only
  reachable cross-writer shape is this deadlock and such a test would be
  permanently red. Once the order is single-owned, that race can be written
  and mutation-proven by inverting the helper — which is a far better test
  than any of the four call sites could have carried.

## The decision the owner owns

**Which module's order becomes the canonical one.** The recommendation above
is *items before listings*, i.e. `order_writes` moves, on the reasoning that
`offering_writes`' order protects the claim-uniqueness guarantee while
`order_writes`' order protects nothing.

The alternative — listings before items, `offering_writes` moves — is
defensible only if checkout's lock sequence is considered too hot to touch.
It costs more: the module that owns claims would have to derive its listing
set from an unlocked claim read, and the invariant quoted above would need
replacing rather than preserving.

## What was actually built

**The owner chose *items before listings*** — `order_writes` moves — and it
was built on `fix/lock-order` on 2026-09-21.

- **`offering_writes._acquire`** is the one place the order is written down:
  `_lock_lots`, then `_lock_items`, then `_lock_listing_rows`, each one
  statement, each ascending id.
- **`offering_writes.lock_for_sale`** is the public door onto it, and takes
  either entry point. `offer` passes `item_ids` (its members, already frozen
  under the lot's row lock, which `_lot_members` must hold in order to *read*
  them at all); `order_writes._lock_listings` and `sales_writes.record_sale`
  pass `listing_ids` and it resolves listing → lot → members itself.
  `end_offer` passes `listing_ids` with `including_paused=True`, which widens
  the derived set from `offered_items` to `_affected_items`.
- `_lock_offers` is gone, folded into `_lock_listing_rows`, which takes the
  named listings (any status — a checkout must hold an ended listing to
  refuse it) and the derived ones (`ON_OFFER` only) in a single `or_`. That
  fold also removed a second unsorted listing acquisition inside
  `end_offer`, which used to take its listings in two statements.
- **No schema change and no migration**, as this note predicted.

### Which writers come through it

Enumerated rather than asserted, because "every writer" was stated here
absolutely in the first draft and there was an exception at the time:

| Writer | Reaches `_acquire` via |
|---|---|
| `offering_writes.offer` | `lock_for_sale(item_ids=...)`, after `_lot_members` has taken the lot row |
| `offering_writes.end_offer` | `lock_for_sale(listing_ids=..., including_paused=True)` |
| `order_writes.place_order` | `_lock_listings` → `lock_for_sale(listing_ids=...)` |
| `order_writes.revise_order` | `_lock_listings`, after its own `sales_order` row lock |
| `order_writes.return_stock` | `_lock_listings` |
| `sales_writes.record_sale` | `lock_for_sale(listing_ids=...)` |
| `routers.inventory.receive_items` | `lock_for_sale(listing_ids=..., item_ids=..., including_paused=True)`, **added in fix round 1** — see below |
| `splitting.split_item` | `end_offer` → `lock_for_sale`, while holding the parent item row it took itself — **added in fix round 2**, see below |

### Every `with_for_update` in `backend/app`, enumerated

"Every other one takes a single kind of row" was written here in round 1 and
was **wrong about `split_item`**, which takes two. An enumeration that is
wrong is worse than the absolute it replaced, because it looks checked. So
the whole list, ten sites:

| Site | Row kind | Role |
|---|---|---|
| `offering_writes._lock_lots` | `sales_lot` | the acquisition, step 1 |
| `offering_writes._lock_items` | `inventory_item` | the acquisition, step 2 |
| `offering_writes._lock_listing_rows` | `listing` | the acquisition, step 3 |
| `offering_writes._locked_offers` | `listing` | re-lock, once per member |
| `offering_writes.end_offer`'s `paused_by_it` | `listing` | re-lock, to *identify* rows already held |
| `offering_writes._lot_members` | `sales_lot` | step 1 for `offer`, which must hold the row to read the membership that produces the item ids |
| `lot_writes._refuse_unless_assembling` | `sales_lot` | single kind; `add_member`, `remove_member`, `edit_lot`, `delete_lot` |
| `order_writes.revise_order` | `sales_order` | single kind, taken before `_lock_listings` |
| `routers.orders.update_order_status` | `sales_order` | single kind, taken before `return_stock` |
| `splitting.split_item` | `inventory_item` | **two kinds**: this row is held across the `end_offer` call at the end of the function, which takes listing rows |

`split_item` is safe and is now safe *by construction* rather than by
accident. Its order is items → listings, the canonical direction, and it
takes no lot row: its listing loop filters `Listing.inventory_item_id ==
parent.id`, which is NULL on a lot listing. What made that an accident was
that a parent which *is* an open member of an offered lot would have reached
the lot listing through `end_offer`'s own `offers_holding` derivation and
taken a lot row after an item row. Round 2 refuses that split outright, for
reasons that have nothing to do with locking — see *Splitting a coin offered
inside a lot* below.

### `receive_items` was the one remaining inversion, and is now closed

Found in review after the first commit, and **not** a regression — the same
pair deadlocked before the fix, on the listing instead of the lot.
`routers.inventory.receive_items` wrote its `inventory_item` status rows and
flushed them (taking their exclusive row locks) and only *then* called
`end_offer`, whose pass waits on the lot row. So receiving acquired items
before lots. The reachable pair: an administrator marking a lot's member
`missing` while a shopper checks that lot out — `order_writes._lock_listings`
takes the lot row as its first statement and then waits on the member row
receiving holds.

Closed rather than documented, because closing was contained: one
`lock_for_sale` call added before the first write, with an `offers_holding`
read above it to choose what to lock. No flush reordering and no
restructuring of receiving.

The read above the locks **only chooses what to lock**; the authoritative
read stays where it was, after the writes and under the locks. Acting on the
earlier one was the first version of this fix and it was wrong: a checkout
that committed in between left receiving ending a listing already ended, and
`_end` with `sold=False` rewrote `sales_lot.status` from `sold` to
`dissolved` -- two histories this design says never collapse into one, on a
lot somebody had just bought. Caught by
`test_buying_a_lot_races_marking_one_of_its_coins_missing`
(`AssertionError: dissolved`, eight runs of eight), which is the read-lock-
re-read discipline `lock_for_sale` applies to a member set, applied here to
a listing set.

**Measured, and the observed loss was not the deadlock.** With the pass
removed -- either moved back below the flush or absent entirely, which is the
exact pre-fix arrangement -- that race test fails `['bought', 'stale']`,
eight runs of eight each way. The checkout reaches the member row first, the
receipt's unlocked UPDATE queues behind it and then writes through a version
that has moved, so the loss lands as `StaleDataError`. The Postgres abort is
the other possible loss of the same inversion -- the receipt holding the
member row while waiting on the lot -- and it was **not observed** in
sixteen mutated runs. Both are a 500 for an operator and the pass removes
both: it locks and re-reads the member row before writing it.

### The `_end` defect, found in review and fixed in round 2

Round 1 reported this as latent on the strength of a claim that was **false**:
that no caller reaches `end_offer` on an already-ended listing because
"`routers.offers` checks the status". It does not.
`POST /api/listings/{id}/end` loads its listing with a plain id lookup and
calls `end_offer(sold=False)` unconditionally.

So it was **one admin API call**, with no concurrency at all: a lot bought in
the shop settles as `end_offer(sold=True)` — lot `sold`, listing `ended` —
and a second `POST .../end` on the same listing rewrote the lot to
`dissolved` while its members stayed `sold`, because `end_offer` skips the
disposition loop for a sale. Measured through that route before the fix:
`AssertionError: dissolved`. A stale console tab is enough.

Fixed with an early return in `offering_writes._end` when the listing is
already `ended` — in `_end`, not `end_offer` or the router, because this
module is the single writer of `sales_lot.status` and because `end_offer`'s
own `paused_by_it` loop calls `_end` directly. An early return rather than a
refusal: it makes the endpoint idempotent, the recursion ends a *batch* where
a refusal would abort the whole ending over one member, and the harm being
prevented is a rewrite (of `lot.status`, and of `ended_at`) rather than an
action anyone needs to be told about. Covered by
`test_ending_a_sold_lot_s_listing_again_leaves_it_sold`.

`receive_items`' second `offers_holding` read is now defence rather than the
only defence.

### Splitting a coin offered inside a lot

Adjacent, found in the same review, and **measured end to end through
`POST /api/inventory/{id}/split` rather than reasoned about** — which is
what caught that the story was half right.

`splitting.split_item` refused a parent sold on its own and (since `7ea6eb0`)
one sold inside a lot, but nothing refused a parent that was a live member of
an *offered* lot. Its listing loop filters `Listing.inventory_item_id ==
parent.id`, which is NULL on a lot listing, so the lot's offer was never
ended either.

Measured: unacknowledged gave **409** with the for-sale warning —
`sale_state._offering` reads claims, so the warning was always lot-aware —
and acknowledged gave **200**. The coin was split and the lot went on
offering it: listing `active`, lot `offered`, membership open, claim
`active`, `offered_items` still naming a parent with `split_at` set. A buyer
looking at a group containing a coin that no longer exists as a whole item,
while `offering_writes._refuse_unofferable` refuses to *offer* a split item
and `lot_writes._refuse_unofferable` refuses to put one *into* a lot.

Fixed in `split_item` with `lot_writes.lot_holding`, the same predicate
`_refuse_grouped` and `routers.inventory.delete_item` use. Unconditional,
not an `acknowledge_for_sale` warning: that flag is for changes a buyer
should be told about, not for ones that leave the offer describing something
untrue. Only `offered` refuses, exactly as `_refuse_grouped` scopes it — an
`assembling` lot has been shown to nobody, and offering it later refuses the
split member by name.

It also makes `split_item`'s place in the lock table safe by construction
rather than by accident: with the refusal, a parent in an offered lot never
reaches `end_offer`, so no lot row is ever taken after its item row.

### Two claims in this note were wrong

#### 1. An offered lot's membership is not frozen outright

> for a listing that is on offer — the only kind a checkout can reach — the
> member set cannot change underneath a reader.

**It can, and the very first run of the new test proved it.**
`_refuse_unless_assembling` guards `lot_writes`, but `offering_writes._end`
is a *second* writer of `sales_lot_item.released_at` and releases every open
membership the moment the lot is sold or dissolved. The losing side of
`test_two_checkouts_race_for_one_lot` reads two members, waits on the
winner's locks, and finds none — which a flat "refuse on any change" rule
turned into a 500 for the ordinary case of arriving second.

So the confirming re-read holds the set frozen **only while the listing is
still `ON_OFFER`**. A listing the lock found `ended` is one whose members
were released by that ending, and every caller already has its own refusal
for it: `place_order` "is not currently for sale", `revise_order` "has
ended, so the stock this order holds cannot be put back on sale",
`record_sale` "is not on offer". Anything else is `LockSetChanged` — a 500,
unmapped in the routers, on the reasoning `sales_writes.ShareMissing`
already carries.

#### 2. Inverting the shared helper is not the mutation that proves the fix

This note assumed inverting `_acquire` would bring the deadlock back. It
does not, and the reason is the property the fix is for: inverting the one
shared function moves **both** sides at once, so the two writers still
agree and there is still no cycle. Measured — the race test passes eight of
eight with `_acquire` inverted.

What reproduces it is **breaking the single ownership**: restoring
`_lock_listings`' own `select(...).with_for_update()` so `order_writes`
takes listings first while `offering_writes` still takes items first. That
fails eight of eight with `['bought', 'deadlock']`. The inversion is still
caught, by
`test_a_checkout_takes_the_three_kinds_of_row_in_the_canonical_order`, which
measures the sequence of kinds on one connection.
