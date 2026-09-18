# Warning before a change to an item that is for sale

Design. Status: **agreed with the owner 2026-09-18**; not built.

## The problem

An item that a buyer is looking at should not change underneath them without
someone saying they know. That rule exists, it is written down in
`backend/app/sale_state.py`, and it is enforced on exactly **two** of the
write paths that can change such an item:

- `PATCH /api/inventory/{id}` (`routers/inventory.py`, the item editor)
- `POST /api/inventory/bulk` (`routers/inventory.py`, the bulk edit bar)

Both call a private helper, `_refuse_unacknowledged_sale`, which asks
`sale_state.for_sale()` and raises 409 with `sale_state.refusal()` unless the
request carried `acknowledge_for_sale`.

Five other paths change the same items and ask nothing:

- **`POST /api/inventory/receive`** refuses to *re-receive* an item that is
  already `received`, but that check only runs when the outcome is
  `received`. The other three outcomes -- `missing`, `returned`, `canceled`
  (`schemas.RECEIVE_OUTCOMES`) -- pass straight through to
  `lifecycle_writes.set_status`. A live, listed coin can be marked missing
  with no warning, and its listing stays active, still offering it.
- **`POST /api/inventory/{id}/split`** already has opinions about this, and
  they are not the naive gap the backlog recorded. `splitting.split_item`
  **hard-refuses** a parent that appears in an order, and **ends** every
  non-ended listing on the parent through `offering_writes.end_offer`. The
  ending is deliberate and commented. What is missing is that it happens
  without being announced, and ending is effectively permanent for the
  record: relisting clears `ended_at` and there is no `listing_status_history`
  table, so the previous ending's timestamp is lost.
- **`PUT /api/inventory/{id}/errors`** replaces the whole error set with no
  check at all.
- **`POST /api/images`** attaches a photograph to an item, and
  **`DELETE /api/images/{id}`** removes one. The shop serves an item's
  **primary image** (`routers/catalog.py`, via `images.image_urls`), so this
  is the one gap whose effect is visible on a shop page immediately.
  `delete_image` is the worst of the five: it deletes by image id and never
  joins `ItemImage`, so it does not know what the image is attached to, let
  alone whether that item is for sale.
- **`POST /api/reference/{table}/{code}/merge`** rewrites a vocabulary value
  across every record that describes an item -- `reference_merge` moves
  `inventory_item`, `coin_detail`, `currency_detail`, `item_attribute_link`,
  `item_error`, `item_certification` and `item_image` -- and moves each item's
  `version`. Some of those items can be for sale. Nothing says so.

The consequence is not theoretical. The rule `sale_state` states -- "a change
shows to buyers at once" -- is true of all seven paths, and enforced on two.

## Decisions

Made by the owner during design, 2026-09-18.

1. **A verdict per path, not one rule for all five.** The paths have three
   different relationships to a buyer: images change what the buyer *sees now*,
   receiving changes what the seller *can still deliver*, and errors and merge
   change what the *record* says about a coin someone is buying. Treating them
   alike would mean treating deletion of a live shop photograph as equivalent
   to editing a note the shop never displays.
2. **Receiving warns, and then also ends the listing.** When a for-sale item
   is marked `missing`, `returned` or `canceled`, acknowledging the warning
   sets the status *and* ends the live listing through `offering_writes`. A
   coin that cannot be delivered must not stay offered; leaving the listing up
   strands a buyer.
3. **Split warns before it ends a listing.** The ending itself is unchanged --
   it stays in `splitting.split_item` -- but it is announced first, because
   nothing brings the ended listing back.
4. **Item errors warn.** Justified by the sale snapshot rather than the shop
   page: `sale_snapshot.take()` copies the item as it was sold, so the error
   set is part of what a sale records.
5. **Merge warns in aggregate, and reports before it refuses.** The existing
   `dry_run` preview names how many affected items are for sale, and which,
   before anything is written.
6. **The API-only paths are guarded anyway.** Splitting has no console caller
   and image deletion is not even in `owner/api.js`; both are guarded
   regardless. A guard that exists only where a button exists is the wrong
   invariant, and the owner drives these endpoints directly.

### Rejected

- **One uniform rule for all five paths.** Rejected in decision 1. It would
  have produced an acknowledgement on split that does not actually let the
  caller through, because `split_item`'s order refusal is not negotiable --
  the worst kind of confirmation, since it teaches the operator to click past
  warnings that mean something.
- **Guards inside the writer modules** (`lifecycle_writes.set_status`,
  `splitting.split_item`, `imaging`, `reference_merge`) rather than the
  routers. Attractive because any future caller would inherit them. Rejected
  because `app.rating_pass`, `app.classifier_defaults`, `app.vendor_cleanup`
  and `app.seed` call those same writers and have no concept of an
  acknowledgement: the flag would have to be threaded through every pass, and
  a batch pass that auto-acknowledges is worse than no guard, because it looks
  safe.
- **A FastAPI dependency.** The item ids arrive differently in every endpoint
  -- a path parameter for split and errors, a body list for receive, an
  `ItemImage` join for images, a vocabulary-to-items join for merge -- so each
  would need bespoke extraction regardless. The wrapper buys nothing over a
  plain call.
- **Narrowing scope to what the shop displays** (images and receiving only).
  Rejected with decisions 4 and 5: the sale snapshot and the merge's reach
  across seven record tables are consequences a shop page does not show.

## What "for sale" already means

Unchanged by this design, and defined in `sale_state`: an item is for sale
while a listing offers it (`active` or `paused`, with stock) or an order that
has not shipped holds it (`pending`, `paid`, `packed`). A **paused** store
listing counts. `sale_state.for_sale()` takes a collection of item ids and
returns the reasons per item as `SaleUse` records; `sale_state.refusal()`
formats the message a save gets.

`sale_state._offering` deliberately reads **both** claims and listings,
because a listing can exist without a claim -- `app.seed` makes one, and so
did every listing written before `offer_claim` existed. This design does not
change that and relies on it.

## The guard

`sale_state` gains one public function:

```python
def guard(
    db: Session,
    items: Collection[InventoryItem],
    *,
    acknowledged: bool,
    kinds: Collection[str] | None = None,
) -> None:
    """409 unless the caller has said it knows these items are for sale."""
```

It calls `for_sale()`, filters to `kinds` when given, and raises
`HTTPException(409, detail=refusal(...))` when anything is left and
`acknowledged` is false. `routers/inventory.py`'s private
`_refuse_unacknowledged_sale` is **deleted**, and its two existing call sites
call `guard` instead, so exactly one place decides what the refusal says.

It takes loaded items rather than ids because the refusal names items by
`item_code`. The two callers that start from something else -- image deletion,
which starts from an image, and merge, which starts from a vocabulary value --
select the items they found and pass those.

Raising `HTTPException` from a non-router module is the established
convention here, not a new one: `references.py`, `order_writes.py` and
`years.py` all do it. No domain-error type is introduced.

**`kinds` exists for one reason.** Split must not let a caller acknowledge
past an order. `splitting.split_item` refuses outright when the parent appears
in an order, and that refusal is not negotiable, so split calls
`guard(..., kinds={"listing"})` and leaves the order case to the existing hard
refusal. Without the filter the operator would tick a box and be refused
anyway.

## The five paths, six endpoints

Images contributes two endpoints -- upload and delete -- so five paths become
six guarded endpoints, which with the two existing call sites makes **eight**
in total.

| Path | Guard | Also does | How the acknowledgement travels |
|---|---|---|---|
| `POST /inventory/receive` | outcomes `missing`, `returned`, `canceled` only | ends the live listing via `offering_writes.end_offer` | `acknowledge_for_sale` on `ReceiveRequest` |
| `POST /inventory/{id}/split` | `kinds={"listing"}` | nothing new; `split_item` already ends the listing | `acknowledge_for_sale` on `SplitRequest` |
| `PUT /inventory/{id}/errors` | full | -- | `acknowledge_for_sale` on `ItemErrorsRequest` |
| `POST /api/images` | when attaching to a for-sale item | -- | a multipart `Form` field |
| `DELETE /api/images/{id}` | joins `ItemImage` to its items | -- | a query parameter |
| `POST /reference/{table}/{code}/merge` | full, aggregate | `dry_run` reports it | `acknowledge_for_sale` on `ReferenceMergeIn` |

### Receiving

The guard is **exactly three outcomes wide**, and `received` needs no guard at
all. `offering_writes.offer()` refuses an item whose status is not `received`,
and an order can only hold a listing, so an item that has not been received
cannot be for sale. The existing "already received" 409 covers the rest.

On acknowledgement the endpoint sets the status and then ends every live
listing on those items through `offering_writes.end_offer` -- never by
assigning listing status directly, because `offering_writes` is the only
writer of listing status and claims.

### Split

Only the announcement is new. `split_item` keeps both of its existing
behaviours: refuse for an order, end the listings for the parent.

### Item errors

`PUT /inventory/{id}/errors` already loads the item, so this is the same two
lines as `PATCH`.

### Images

`POST /api/images` guards only when `inventory_item_id` names an item that is
for sale; an unattached upload is untouched, since photographs exist before
anyone has decided what they depict.

`DELETE /api/images/{id}` must first learn what it is deleting: it joins
`ItemImage` to find the items the image is attached to, and guards on those.

**The transport asymmetry is an HTTP constraint, not a style choice.** Four
endpoints take a JSON body and carry the flag exactly as `InventoryItemUpdate`
does. `POST /api/images` is `multipart/form-data`, so the flag is a `Form`
field beside `inventory_item_id` and `is_primary`. `DELETE /api/images/{id}`
has no body: request bodies on DELETE are permitted by specification but
unreliable through intermediaries and awkward from `fetch`, so the flag is a
query parameter. This is recorded here so it is not re-litigated during
implementation.

### Vocabulary merge

`reference_merge.plan()` already computes the set of affected item ids, and
already throws it away -- `MergePlan.items` is a count. `MergePlan` gains a
`for_sale: list[str]` of item codes -- **at most ten**, alongside
`for_sale_count`, the true total -- and `ReferenceMergeOut` carries both. The existing `dry_run` preview shows it before
anything is written; the 409 still guards the non-dry-run call, for a caller
that skips the preview.

One acknowledgement covers the whole merge. Naming two hundred item codes in a
refusal message helps nobody, so the message gives the count and names the
first few.

## The console

Three surfaces, not five: splitting has no component that calls
`api.splitItem`, and `deleteImage` is not in `owner/api.js` at all. Those two
guards protect the API only, by decision 6.

**`ErrorsPanel` -- an inline notice with a sticky acknowledgement.** The panel
takes `{ itemId, kind, value, onChange }`, does not know `sale_state`, and
**auto-saves on every row added, removed or edited** -- there is no Save button
to gate. The parent, which already has `item.sale_state`, passes it down; the
panel renders the notice once, and while the box is ticked every save carries
`acknowledge_for_sale`. The acknowledgement is **per editing session, not per
save**: a panel that re-asked on each change would train the operator to tick
it blind.

**`ReceiptPanel` -- a modal confirm, driven by the 409.** Receiving is a batch
and the panel holds no `sale_state` for its items, so the refusal is the
trigger. `submit(outcome)` catches the 409, reads the refusal off
`ApiError.body` (the field added in selling phase 2 so a structured error
survives to the caller), and shows a `ForSaleConfirm` naming the items and
saying the listing will be ended. Confirming resubmits with
`acknowledge_for_sale: true`.

The photograph upload inside the same `submit()` follows the rule already
written into that function: **a failed upload must never roll the receipt
back.** A for-sale refusal on an upload is reported like any other upload
failure -- against the item, by name, retryable, receipt intact -- and is not
promoted into the receipt's own confirmation.

**`Vocabularies` -- the preview that already exists.** The page already runs a
`dry_run` merge and shows the result before committing. The for-sale codes
arrive on `ReferenceMergeOut`, the preview gains a line naming them, and
confirming the merge sends the acknowledgement. No new dialog.

**Extraction.** `ItemEditForm` and `BulkEditBar` each hand-roll the same
notice-and-checkbox. `ErrorsPanel` would be the third copy, so the three
collapse into one shared `ForSaleNotice`. `ForSaleConfirm` joins
`EndOfferConfirm` as the modal for the batch case.

## Testing

**One new `backend/tests/test_for_sale_guards.py`.** The repository already
has cross-cutting test files -- `test_sale_snapshots.py` is one, and it is
where the existing acknowledgement flow is covered -- and the five verdicts are
one policy, so reading them together is how the policy is checked for
coherence. It follows that file's sentence-style test names.

One test per decision:

- each of the five paths refuses with 409 when the item is for sale and
  nothing was acknowledged
- each succeeds when `acknowledge_for_sale` is set
- receiving refuses only for `missing`, `returned` and `canceled`; a plain
  `received` outcome is untouched by the guard
- receiving an acknowledged `missing` **ends the listing and releases its
  claim**
- split refuses on an order with **no** acknowledgement path -- the
  `kinds={"listing"}` filter. A test that only checked "refuses, then allows"
  would still pass with the filter removed, and the bug would surface as a
  confirmation dialog that lies.
- merge's `dry_run` reports the for-sale codes before anything is written

**One subtlety the existing fixtures would hide.** `conftest.build_listing`
creates **claimless** listings. That is fine for *detecting* a sale, because
`sale_state._offering` reads claims and listings both, but it is wrong for the
receiving test, which has to prove the claim was released. Those tests build
their listing through `offering_writes.offer()` so that there is a claim to
release.

**Mutation pass.** Each guard is a guarantee, so each gets the treatment:
delete the `sale_state.guard(...)` call, confirm a named test goes red, restore.
**Eight call sites, eight confirmed kills** -- the six new ones and the two
existing ones, which are refactored onto the shared guard and must still be
killed by the tests already in `test_sale_snapshots.py`. Otherwise a guard that
is never reached passes review looking correct.

**Frontend.** `ErrorsPanel.test.jsx`, `ReceiptPanel.test.jsx` and
`Vocabularies.test.jsx` exist and are extended; `ForSaleNotice` gets its own.
New and changed tests render with `renderWithProviders(..., { strict: true })`.
`ErrorsPanel` is the component that already lost a guard to the
StrictMode-versus-test-harness mismatch, and a sticky acknowledgement across
auto-saves is the same class of state that broke then.

## What this does not do

- **No migration.** Nothing in this design changes the schema.
- **No `listing_status_history` table.** Split's warning makes the ending
  visible; it does not preserve the previous `ended_at` across a relist. That
  remains the open follow-up it already was.
- **No split UI.** Splitting stays API-only; this design guards it rather than
  building a console surface for it.
- **No image-linking UI.** `DELETE /api/images/{id}` is guarded, not exposed.
- **No change to what "for sale" means**, to `offering_writes`, or to the
  existing behaviour of `PATCH /api/inventory/{id}` and
  `POST /api/inventory/bulk` beyond calling the shared guard.

## Consequences worth knowing

- Eight call sites of one function is the whole enforcement surface. A write
  path added later is guarded by adding another call, and the mutation pass is
  the check that each one is actually reached.
- The guard makes `sale_state` the single module that knows both what "for
  sale" means and what happens when someone ignores it. That is a deliberate
  concentration: the alternative measured during design put the same knowledge
  into four writer modules and every CLI pass that calls them.
