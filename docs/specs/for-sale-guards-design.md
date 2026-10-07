# Warning before a change to an item that is for sale

An item a buyer is looking at in the shop or on another platform, or has
already agreed to buy, must not change underneath them without someone
saying they know: its description, grade, errors and photographs are what
the buyer is being shown, and a coin marked missing cannot be delivered. So
every write that can change such an item is refused until the person making
it acknowledges the item is for sale.

Managers meet it in the management console wherever an item is edited: the
item editor, the inventory pages' bulk edit bar, splitting, the errors panel,
receiving, the Photos page and the Vocabularies merge. Each shows the
listings and orders involved and asks for a tick or a confirmation. On the
server, every such endpoint asks `sale_state.guard` first and answers
**409** until the request carries `acknowledge_for_sale`.

## What "for sale" means

Defined in `backend/app/sale_state.py`. An item is for sale while:

- **a listing offers it** -- `active` or `paused`, with stock. A paused store
  listing counts: the item is out of the shop only because it is offered
  somewhere else. The listing half reads **claims and listings both**
  (`sale_state._offering`): a claim is the only way a lot listing reaches
  its members, and a listing written directly against an item may have no
  claim; or
- **an order that has not shipped holds it** -- `pending`, `paid` or `packed`
  (`OPEN_ORDER_STATUSES`). `delivered` is excluded, an auction-house sale
  included: once an order ships, its lines keep a snapshot of the item as
  sold, and editing the live record is ordinary again. An order reaches its
  items through `sales_order_item_share`, which names every item on every
  line, a lot's members included.

`for_sale(db, item_ids)` returns the reasons per item as `SaleUse` records
(`kind` is `"listing"` or `"order"`); `refusal()` formats the message, which
starts "For sale".

## The guard

```python
def guard(db, items, *, acknowledged: bool, kinds: Collection[str] | None = None) -> None
```

Calls `for_sale()`, narrows to `kinds` when given, and raises
`HTTPException(409, detail=refusal(...))` when anything is left and
`acknowledged` is false. It is the one place that decides what the refusal
says. It takes loaded items because the refusal names items by `item_code`;
callers that start from an image or a vocabulary value select the items
first.

**`kinds` exists for split.** `splitting.split_item` refuses outright a
parent that appears in an order, and that refusal is not negotiable, so the
split endpoint guards with `kinds={"listing"}`. Without the filter the
operator would tick a box and then be refused anyway.

## Guarded endpoints

| Endpoint | When it guards | Acknowledgement travels as |
|---|---|---|
| `PATCH /api/inventory/{id}` | any field, attribute or certificate-number change, except where the item is kept (`storage_location_id`: a move does not show to a buyer); a new status or disposition, acknowledged, ends the item's offers | `acknowledge_for_sale` in the body |
| `POST /api/inventory/bulk` | any change; a new status or disposition ends each changed item's offers | body |
| `POST /api/inventory/receive` | outcomes `missing`, `returned`, `canceled` only | body (`ReceiveRequest`) |
| `POST /api/inventory/{id}/split` | listings only (`kinds={"listing"}`) | body (`SplitRequest`) |
| `PUT /api/inventory/{id}/errors` | always | body (`ItemErrorsRequest`) |
| `POST /api/images` | when `inventory_item_id` names an item | multipart `Form` field |
| `POST /api/images/from-url` | always (the item it is filed on) | body |
| `POST /api/images/{image_id}/links` | always | body |
| `DELETE /api/images/{id}` | on every item the image is linked to | query parameter |
| `PATCH /api/image-links/{link_id}` | always (role, primary) | body |
| `POST /api/image-links/{link_id}/move` | both the item it leaves and the item it joins, one acknowledgement | body |
| `DELETE /api/image-links/{link_id}` | detach | query parameter |
| `POST /api/reference/{table}/{code}/merge` | any affected item for sale | body (`ReferenceMergeIn`) |

The transport differs because HTTP does: an upload is `multipart/form-data`,
and a DELETE has no reliable body, so its flag is a query parameter.

**Per path:**

- **Receiving** guards only the three outcomes that say a coin will not be
  delivered. `received` needs no guard: `offering_writes.offer` refuses an
  item that is not `received`, so an unreceived item cannot be for sale.
  Once acknowledged, the endpoint sets the status and then ends every live
  offer holding the items through `offering_writes.end_offer` -- a coin that
  cannot be delivered must not stay offered. An auction lot's listing is
  refused instead (`_refuse_auction_lots`): it is ended only through its
  auction.
- **Split** also ends the parent's own listings (`split_item`), and refuses
  unconditionally a parent in an order or an open member of an offered lot.
- **Item errors** are guarded because the sale snapshot copies the error set:
  it is part of what a sale records.
- **Photographs** are guarded because the shop serves an item's primary
  image; attaching, fetching from a URL, moving, detaching, re-roling,
  promoting and deleting all change what a buyer sees. An unattached upload
  is not guarded. The guard runs before any bytes are stored, so a refused
  upload leaves no file behind.
- **Merge** reports first. `reference_merge.plan()` carries `for_sale_count`
  and up to ten `for_sale` item codes on `ReferenceMergeOut`, so the
  `dry_run` preview names them before anything is written; the non-dry-run
  call is still guarded for a caller that skips the preview. One
  acknowledgement covers the whole merge.

## Where the guard lives, and where it does not

**In the routers, not the writer modules.** The writers are also called
where nobody is present to acknowledge a warning: `image_links` by the
`app.photo_import` command-line pass, and `lifecycle_writes` and
`offering_writes.end_offer` by other writers (auction consignment and
settlement, splitting). A batch pass that auto-acknowledges is worse than no
guard, because it looks safe. `app.photo_import` instead reports which
linked items are for sale.

**Not a FastAPI dependency.** Item ids arrive differently on every endpoint
(path, body list, image join, vocabulary join), so a dependency would need
bespoke extraction each time and buys nothing over a plain call.

**API-only paths are guarded too.** Image deletion is not in
`management/api.js`, but it is guarded anyway, because a guard that exists only
where a button exists is the wrong invariant.

## The console

Two shapes, chosen by whether the page already knows the item's sale state.

- **`ForSaleNotice`** -- an inline notice with an acknowledgement checkbox,
  for forms that load the item's `sale_state` up front: `ItemEditForm`,
  `BulkEditBar`, `ErrorsPanel` and `SplitDialog`. In `ErrorsPanel` in
  Receiving, which saves on every change, the acknowledgement is **per
  editing session, not per save**: re-asking on each change would train the
  operator to tick it blind. In the item editor neither the errors nor the
  photographs panel shows a notice of its own; the form's one
  acknowledgement covers everything its Save applies.
- **Driven by the 409**, for pages that hold no sale state for the item:
  - `ReceiptPanel` opens **`ForSaleConfirm`**, a modal that shows the
    server's message and says the listing will be ended ("Record it
    anyway" / "Leave it on sale"); confirming resubmits with
    `acknowledge_for_sale: true`. Photographs uploaded after the receipt
    carry the same acknowledgement.
  - The **Photos** page attempts the link unacknowledged and, on a "For
    sale" 409, shows the message inline in that row with **Link anyway** and
    **Leave it unlinked** -- inline rather than modal, because the operator
    works down a grid of rows.
- **Vocabularies** shows the merge preview's for-sale codes, and confirming
  the merge sends the acknowledgement. No extra dialog.

The console tells the refusal apart from other 409s by the message's
opening words, "For sale".

## Tests

`backend/tests/test_for_sale_guards.py` holds the policy in one file: each
path refuses when the item is for sale and nothing was acknowledged, and
succeeds when it was; receiving guards only the three outcomes and an
acknowledged `missing` ends the listing and releases its claim; split refuses
an order with no acknowledgement path; merge's `dry_run` reports the codes.
Receiving tests build their listing through `offering_writes.offer()`,
because `conftest.build_listing` makes claimless listings and the test has to
prove a claim was released. Lot cases are covered too: an offered lot's
member warns like a listed item, and a sold lot's member warns while its
order is open. Each guard call site has a test that fails when the call is
removed.

Frontend: `ErrorsPanel`, `ReceiptPanel`, `Vocabularies`, `ForSaleNotice`,
`ForSaleConfirm` and the Photos page have tests. `ErrorsPanel`'s are
rendered with `renderWithProviders(..., { strict: true })`: a sticky
acknowledgement across auto-saves is the kind of state that breaks between
StrictMode and a non-strict harness.

## Limits

- **Image deletion is not in the console.** `DELETE /api/images/{id}` is
  guarded but not exposed; the console detaches instead.
