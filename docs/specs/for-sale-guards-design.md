# Warning before a change to an item that is for sale

An item a buyer is looking at, or has agreed to buy, must not change
underneath them without someone saying they know. Every write path that can
change such an item asks `sale_state.guard` first and refuses with **409**
until the request carries `acknowledge_for_sale`.

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
| `PATCH /api/inventory/{id}` | any field or attribute change; a new status or disposition, acknowledged, ends the item's offers | `acknowledge_for_sale` in the body |
| `POST /api/inventory/bulk` | any change; a new status or disposition ends each changed item's offers | body |
| `POST /api/inventory/receive` | outcomes `missing`, `returned`, `canceled` only | body (`ReceiveRequest`) |
| `POST /api/inventory/{id}/split` | listings only (`kinds={"listing"}`) | body (`SplitRequest`) |
| `PUT /api/inventory/{id}/errors` | always | body (`ItemErrorsRequest`) |
| `POST /api/images` | when `inventory_item_id` names an item | multipart `Form` field |
| `POST /api/images/{image_id}/links` | always | body |
| `DELETE /api/images/{id}` | on every item the image is linked to | query parameter |
| `PATCH /api/image-links/{link_id}` | always | body |
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
  image; attaching, detaching, re-roling, promoting and deleting all change
  what a buyer sees. An unattached upload is not guarded.
- **Merge** reports first. `reference_merge.plan()` carries `for_sale_count`
  and up to ten `for_sale` item codes on `ReferenceMergeOut`, so the
  `dry_run` preview names them before anything is written; the non-dry-run
  call is still guarded for a caller that skips the preview. One
  acknowledgement covers the whole merge.

## Where the guard lives, and where it does not

**In the routers, not the writer modules.** `lifecycle_writes.set_status`,
`splitting.split_item`, `image_links` and `reference_merge` are also called by
CLI passes (`app.classifier_defaults`, `app.vendor_cleanup`, `app.seed`,
`app.photo_import`) that have nobody to acknowledge a warning. A
batch pass that auto-acknowledges is worse than no guard, because it looks
safe. `app.photo_import` instead reports which linked items are for sale.

**Not a FastAPI dependency.** Item ids arrive differently on every endpoint
(path, body list, image join, vocabulary join), so a dependency would need
bespoke extraction each time and buys nothing over a plain call.

**API-only paths are guarded too.** No console component calls split, and
image deletion is not in `management/api.js`; both are guarded anyway, because a
guard that exists only where a button exists is the wrong invariant.

## The console

- **`ForSaleNotice`** -- an inline notice with an acknowledgement checkbox,
  shared by `ItemEditForm`, `BulkEditBar`, `ErrorsPanel`, `PhotosPanel` and
  the Photos page. In `ErrorsPanel`, which saves on every change, the
  acknowledgement is **per editing session, not per save**: re-asking on
  each change would train the operator to tick it blind.
- **`ForSaleConfirm`** -- a modal driven by the 409, used by `ReceiptPanel`,
  which holds no `sale_state` for what it receives. It names the items and
  says the listing will be ended; confirming resubmits with
  `acknowledge_for_sale: true`. A photograph upload after the receipt never
  rolls the receipt back: a refusal on the upload is reported against the
  item like any other upload failure.
- **Vocabularies** shows the merge preview's for-sale codes, and confirming
  the merge sends the acknowledgement. No extra dialog.

## Tests

`backend/tests/test_for_sale_guards.py` holds the policy in one file: each
path refuses when the item is for sale and nothing was acknowledged, and
succeeds when it was; receiving guards only the three outcomes and an
acknowledged `missing` ends the listing and releases its claim; split refuses
an order with no acknowledgement path; merge's `dry_run` reports the codes.
Receiving tests build their listing through `offering_writes.offer()`,
because `conftest.build_listing` makes claimless listings and the test has to
prove a claim was released. Each guard call site is mutation-checked: delete
the call and a named test must go red.

Frontend: `ErrorsPanel`, `ReceiptPanel`, `Vocabularies`, `ForSaleNotice` and
`ForSaleConfirm` have tests, rendered with `renderWithProviders(..., { strict:
true })` -- a sticky acknowledgement across auto-saves is the kind of state
that breaks between StrictMode and a non-strict harness.

## Not built

- **A split screen in the console.** Splitting is API-only.
- **Image deletion in the console.** `DELETE /api/images/{id}` is guarded but
  not exposed; the console detaches instead.
