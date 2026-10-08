# Receiving purchases: logging what actually arrived

A package arrives. Somebody opens it, checks what is inside against what was
bought, puts the objects somewhere, and records that this happened. Until
then a purchase is only a promise: an item cannot be offered for sale until
it is received, and a parcel that never comes has to be closed out as
missing, returned or cancelled rather than left outstanding forever.

The owner or a manager does this on the **Receiving** page
(`/management/receiving`, **Receive** in the console menu), usually by
following **Receive these** from a purchase or the Order link on an
inventory row. The page finds the outstanding items, and a dialog per item
records the outcome, the date it arrived, where it was put, and optionally
photographs, corrections and errors, all through
`POST /api/inventory/receive` and the item endpoints.

## What receiving is here

Receiving does not create inventory. An item already exists from the moment
it was bought -- entered on Purchases (`POST /api/inventory` defaults
`status` to `ordered`) -- and an item
awaiting arrival has `status = ordered`. Receiving is a **state transition on
a row that already exists**, so the design problem is *finding the right
row*, not typing anything in.

It is also the first moment a person holds the object, so the receipt dialog
opens the item's fields for correction.

## Status and location have one door each

`lifecycle_writes` is the only writer of `inventory_item.status_id` and
`storage_location_id`, and each write records history in the same breath:

- `set_status(session, item, to_status_id, *, user_id, note, arrived_on)`
  writes an `item_status_history` row with `from_status_id`, `changed_at`,
  `changed_by_id`, `note` and `arrived_on`. It is a no-op when the status is
  unchanged.
- `set_location(...)` writes `location_history`.
- `record_initial_status(...)` writes an item's opening row
  (`from_status_id` NULL); every path that creates an item calls it.

`PATCH /api/inventory/{id}`, `POST /api/inventory/bulk` and the receive
endpoint all change status through `set_status`, so a correction is recorded
too and "when did this actually arrive" survives it. Both helpers flush an
unpersisted item first so its history row gets a real id. `set_status`
assigns without flushing, so a caller that next calls a writer which
re-reads the row with `populate_existing` must flush in between;
`receive_items` does.

## `POST /api/inventory/receive`

```
{ "item_ids": [412, 413],
  "outcome": "received" | "missing" | "returned" | "canceled",
  "arrived_on": "2026-09-09",          // optional
  "storage_location_id": 3,            // optional; used only for received
  "note": "edge knock not in the listing photos",   // optional
  "acknowledge_for_sale": false }
```

- **All or nothing, in one transaction.** Every id is resolved and every code
  checked before anything is written.
- **`arrived_on` is a date, distinct from `changed_at`.** When a box sat
  unopened over a weekend, the day it arrived and the moment someone recorded
  it are different facts. It is written only for `received`. Omitted, it is
  stored as NULL -- "we don't know when" is a real answer, different from
  "today" -- although the console always sends one.
- **A future date is refused (422) beyond `utc_today + 1 day`.** The server
  only knows UTC while "today" is local, and a caller's local date can lead
  UTC's by up to a day. The extra day accepts every timezone's honest today
  and still refuses a date two or more days out; a typo exactly one day ahead
  is not caught.
- **The four outcomes are the four codes.** A page that can only record
  success cannot close out a line that never showed up; `missing` means "paid
  for, not cancelled, never arrived".
- **`received` twice is refused (409)**, naming the item and the date it
  arrived, so an operator can tell a double submission from the wrong row.
  Correcting a receipt is `PATCH`, which records the correction. `ordered`
  and `missing` are both receivable: a parcel written off as missing
  sometimes turns up.
- **Location** is set through `set_location` only for `received` with a
  `storage_location_id`; an unknown id is 422 whatever the outcome.
- **Unknown item id**: 404 naming the ids. **Unknown outcome**: 422 naming
  the four.
- **A for-sale item** (`missing`, `returned`, `canceled` only): refused with
  409 until `acknowledge_for_sale`, then every live offer holding the items
  is ended through `offering_writes.end_offer` -- a coin that cannot be
  delivered must not stay offered. An auction lot's listing is refused
  rather than ended; it ends only through its auction. See
  `for-sale-guards-design.md`, and `lock-order-design.md` for the row locks
  this takes before its first write.
- Returns `{"outcome": ..., "items": n}`.

## Other endpoints the page uses

All admin-only.

| Endpoint | Purpose |
|---|---|
| `GET /api/inventory/{view}/search` | the search; `view` is `coins` or `currency` |
| `GET /api/purchase-orders/{id}` | the purchase a `?order=` link names: order number, vendor, date, vendor page, seller |
| `GET /api/storage-locations`, `POST /api/storage-locations` | the *Storage location* choices, and adding one inline |
| `GET /api/inventory/{id}` | the item being received (kind, sale state) |
| `PATCH /api/inventory/{id}`, `PUT /api/inventory/{id}/errors` | save Identify, confirm or correct fields, record errors |
| `GET /api/defaults/note`, `GET /api/defaults/coin` | what the facts typed in Identify decide, shown beside them |
| `GET /api/friedberg`, `GET /api/friedberg/signatures`, `POST /api/friedberg`, `PATCH /api/friedberg/{id}`, `POST /api/inventory/{id}/friedberg` | a banknote's catalog-number lookup |
| `POST /api/images` | photographs of the item being received |

The search filters Receiving uses: `order_number` (partial, case-insensitive
`ILIKE`, for a number typed off a packing slip), `status`, `denomination`;
for coins `year_min`/`year_max` and `mint` (the mint mark); for currency
`serial_number` (partial) and `series_year`. Rows carry `order_number`,
`vendor` and `purchase_order_id`.

**Storage locations are never customer-visible.** `storage_location` is an
authorization boundary: a public listing that leaked the safe-deposit box
holding an item would be a security failure. `routers/catalog.py`
(`to_catalog_item`) builds every public response field by field, and
`test_catalogue_never_exposes_cost_basis_or_location` tests it; the
`public_catalog` view states the same rule (`docs/database-design.md` §10),
and `test_public_catalog_never_exposes_private_columns` tests the view.

## The page

`Receiving.jsx` is one search form (`receiving/ItemFinder.jsx`) and a receipt
dialog. There is no separate order picker: part of an order number does what
picking an order did, and the same form finds an item in hand whose order is
not known.

```
Receiving
  ( ) Any  ( ) Coins  ( ) Currency
  Order number [27-12    ]  Status [Not yet arrived v]  Denomination [Any v]
  (Coins: Year, Mint | Currency: Serial number, Series year)
  [Find]
  CC-000412 1881-S Morgan $1 · 27-1234 · eBay
  CC-000414 $2 1976 FRN · 27-1234 · eBay
```

- **Search for:** -- Any, Coins or Currency. Focus starts on Any when the
  page opens, so the help band explains the choice at once. Any searches both views, since one
  parcel can hold coins and notes. The kind-specific fields appear only once
  that kind is chosen, because the other view would refuse them as unknown
  filters.
- **Status** -- *Not yet arrived* (the default: `ordered` and `missing`),
  *Any status* (no filter: a whole order, received lines included), or any
  single status. The endpoint filters one view and one status per request,
  so the page issues one request per view per status and merges the rows.
- Enter in the order number field searches; so does **Find**.
- The form sits in a `HelpScope`: focusing a field shows what it means in the
  console's help band at the bottom of the window (`management/fieldHelp.js`).
- Each request asks for 200 rows, the endpoint's maximum. Anything beyond
  that is counted and shown ("N more match than are shown"), never dropped
  silently.

**`?order=<id>`** -- the link from the inventory screens' Order column and
from Purchases' *Receive these*. The page loads that purchase, shows its
header (purchase `#id`, order number, vendor, order date, a link to the
vendor's page, and the seller, linked when their page is known) and runs the
search at once **by the purchase's id** (the `purchase_order_id`
filter) with the default *Not yet arrived* status. Not by its number: an
order number is unique only per vendor and is sometimes not recorded, so a
number match would show other orders' items or none. The field shows the
order's number, and the id stays in force while it does; typing another
number searches by number. The finder is keyed on the order id, so
following a link to another order starts a fresh search. A failed link's
error belongs to that order and clears when the address changes.

**Receiving one item.** Choosing a row opens a dialog for that item:

- First, **Identify** (`receiving/IdentifySection.jsx`): the facts that
  identify the piece, filled from the item and saved with **Receive** only,
  as a `PATCH` sent just before the receipt (`identify-first-entry-design.md`).
  Missing, Returned and Cancelled leave the item as it was.
- *Arrived* (defaults to the operator's local today), *Storage location*
  (`LocationSelect`, which can add a location inline),
  *Note*, *Photo*, and the four buttons **Receive**, **Missing**,
  **Returned**, **Cancelled**, each sending its own code in one request.
- The location and date the previous receipt used seed the next dialog, so a
  parcel of twenty into one location is not twenty identical picks. The note
  is not carried over: it describes one object.
- **Confirm or correct fields** opens `ReviewPane`, the inventory
  page's own review component; editing writes `PATCH`. It is disabled while Identify has unsaved changes; Identify
  is hidden while it is open and reads the item again when it closes. While it is closed, `ErrorsPanel` records mint or printing
  errors; only one of the two is mounted at a time, because each replaces the
  item's whole error set (the editor's on its Save, this one on every change).
- For a banknote, **Look up Friedberg number** opens a catalog-number lookup
  (`FriedbergLookup`). A match found in the owner's catalog has one **Use** button that
  puts it on the note at once: as confirmed when the catalog row was
  confirmed before (it is verified), as proposed when it never was. Only a
  number not in the catalog -- typed, or pasted from the web search that a
  miss opens -- is saved with **Save as proposed** or **Save as confirmed**,
  and only once it is in a Friedberg number's form (`list-maintenance-design.md`).
  A number whose type is already recorded under another number is refused
  with that row named, and **Correct 3007- to 3007-L** renames the recorded
  row and uses it. In the item editor the choice is held until the editor's
  Save.
- *Photo* takes files from the file box, dropped onto it, or pasted into it
  with Ctrl+V; anything that is not an image is refused with its name.
  Photographs upload after the receipt, the first as primary, carrying the
  receipt's for-sale acknowledgement. **A failed upload never rolls back the
  receipt**: the arrival is the fact, the photograph evidence added to it.
  The dialog stays open with an error naming each failed file; the chosen
  files are cleared, since resending would resend the receipt, and a
  photograph is added later from the item's own photographs.
- A refused receipt (409, 422, network) keeps every field as typed.
- A for-sale refusal opens `ForSaleConfirm`, which says the listing will be
  ended; confirming resubmits with `acknowledge_for_sale`.

After each receipt the dialog closes and **the search repeats**, so the item
just received leaves the *Not yet arrived* list. Closing the dialog any other
way repeats it too: a receipt whose photograph failed to upload is recorded
all the same, and must not stay listed as outstanding.

## Tests

Backend (`tests/test_receiving.py`, `tests/test_lifecycle_writes.py`,
`tests/test_for_sale_guards.py`, `tests/test_offer_races.py`):

- `set_status` writes one history row per change with the previous status.
  A history mechanism nothing forces quietly stops working, so removing the
  history write must fail a test.
- A receipt of several items is atomic: one bad id writes nothing.
- Receiving an already-received item is refused and leaves its original
  history untouched; a `missing` item can later be received, and both
  history rows survive.
- `missing` records history exactly as `received` does; location and
  `location_history` are written together, and only for `received`.
- The future-date bound (today and one day ahead of UTC accepted); unknown
  outcome; the reply names the outcome; a shopper cannot receive.
- For-sale items: `missing` is refused until acknowledged, then ends the
  listing (a lot's listing included) and releases its claim; a receipt
  racing a checkout of the coin's lot has one clean winner.

Frontend (`Receiving.test.jsx`, `receiving/ItemFinder.test.jsx`,
`receiving/ReceiptPanel.test.jsx`): the default search asks for `ordered` and
`missing` in both views, part of an order number finds a parcel, *Any
status* drops the filter, kind-specific fields are sent only for their kind,
a `?order=` link shows the header and searches by the order's id (an
unnumbered order included), the search repeats whenever the dialog closes,
more than a page of matches is reported, a failed link's error clears on the
next link, a late response for an abandoned search is dropped, the help area
explains the focused field, each outcome sends its own code, a dropped or
pasted image is added like a chosen one, and a failed upload keeps the
receipt and the dialog open.

## Limits

- **One item per receipt, or the whole remainder.** A line opens a dialog
  for that one item, seeded from the last. On a linked order with two or
  more lines still `ordered`, **Receive all N still ordered** opens the same
  dialog over all of them: one date, one location, one all-or-nothing
  request, with no photograph or Friedberg lookup, which belong to one
  object. A split shipment is still received line by line.
- **Each search request returns one page of 200 rows** per view and status.
  More than that is reported with its count; a larger order is narrowed with
  the other fields.

## Not built

- **Partial receipt of a lot** (twenty ordered, fifteen arrived). A
  short-shipped lot is received and then split.
- **Barcode or scanner input.**
- **Reconciling cost against an invoice.**
