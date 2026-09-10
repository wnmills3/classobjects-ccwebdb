# Receiving purchases: logging what actually arrived

Design. Status: draft for review (2026-09-09).

The second of the two documents that began as one request. The first,
`owner-console-separation-design.md`, is implemented and merged; this page is
the first written *into* that structure rather than moved into it.

| | Depends on |
|---|---|
| 1. Owner console separation | -- |
| **2. Receiving purchases** (this document) | 1 |

## What receiving is here

A package arrives. Somebody opens it, checks what is inside against what was
ordered, puts the objects somewhere, and records that this happened.

The important word is *record*. Receiving does not create inventory: the
spreadsheet import already did that, at the moment of purchase. An item
awaiting arrival exists in the database with `status = ordered`
(`importers/loader.py` maps a profile's `status_marker` to that code;
`DEFAULT_STATUS` is `received`, so only things explicitly marked as ordered
are outstanding). Receiving is a **state transition on a row that already
exists**, which is why the whole design problem is *finding the right row*
rather than typing anything in.

It is also the first moment a person holds the object. `ItemFieldReview`
exists for precisely that -- its docstring says "one field of one item,
confirmed by a person looking at the object", and notes that absent means
unconfirmed, "the correct default for every one of the 7,591 imported items".
Until now that mechanism had no natural trigger. Receiving is it.

## What already exists

Nearly all of it. `docs/database-design.md` and `models/lifecycle.py` designed
for this before there was a page.

| Need | Already there |
|---|---|
| The "came in" axis | `item_status`: `ordered`, `received`, `canceled`, `returned`, `missing`, `unknown` |
| When it actually arrived, surviving a later correction | `item_status_history` (`from_status_id` nullable, `changed_at`, `changed_by_id`, `note`) |
| Where it physically went | `storage_location` + `location_history` (`moved_at`, `moved_by_id`, `note`) |
| Confirming the seller's claims | `item_field_review`, and `POST /api/inventory/{id}/reviewed` |
| Correcting them | `PATCH /api/inventory/{id}` |
| Photographs | `POST /api/images` (multipart, optional `inventory_item_id`, `image_role`, `is_primary`) |
| Finding an item by attributes | `GET /api/inventory/{view}/search` |

That last row deserves emphasis, because it removes what looked like the
largest piece of work. Every attribute in the original request is **already a
supported filter**:

- coins: `denomination`, `mint` (matches the mint mark), `year_min` / `year_max`
- currency: `denomination`, `serial_number` (an `ilike`, so a partial serial
  works), `series_year`, `series_letter`, `series_designation`
- both: `status`, so `status=ordered` narrows any of the above to things not
  yet arrived

The attribute-lookup half of this feature is a UI over an endpoint that
already does the job.

## The gap this must close

`item_status_history` is written in exactly two places: `importers/loader.py`
when an item is created (`note="set at import"`), and `splitting.py` when a lot
is broken up. **Nothing writes history when an item's status later changes.**

`status` is an ordinary editable classifier on `PATCH /api/inventory/{id}`, so
today you can already set an item to `received` -- and nothing records when,
or by whom, and a later correction silently erases the arrival date. The table
was created so that "when did this actually arrive" survives a correction to
the status, and that promise is currently unkept.

The fix is not local to receiving. **Status changes get one door.** A single
helper becomes the only way `status_id` is assigned:

```python
def set_status(session, item, to_status_id, *, user_id, note=None) -> None:
    """Change an item's status and record that it changed."""
```

`PATCH`, the receive endpoint and `splitting.py` all go through it. A helper
that some callers bypass is a convention; one that is the only way to reach
the column is a property of the code. `set_location` is its counterpart for
`storage_location_id` / `location_history`.

Both flush the session when `item.id` is still `None` before writing their
history row -- an unpersisted item has no id yet, and a NULL
`inventory_item_id` would either violate the history table's NOT NULL
constraint or attach the row to nothing. Flushing assigns the id from the
database's sequence without committing the transaction, which is what makes
both helpers usable at creation time too: a caller can build an item and set
its status or location in the same breath, before anything else has flushed,
and still get a correctly-linked history row.

Writing history for status changes that happen through `PATCH` is a
**behaviour change to an existing endpoint**, made deliberately: an untracked
status change is the bug, not the baseline.

## The page

Order-first, because a package arrives from one vendor and contains one
order's items. Attribute search is the fallback for when you have an object in
hand and do not know which order it came from.

```
+- Receive ----------------------------------------+
| Order [eBay 27-1234  v]   or  [search by item v] |
+--------------------------------------------------+
  Order 27-1234 (3 of 5 not yet arrived) [select all]
  [ ] CC-000412  1881-S Morgan $1        $84.00  ordered
  [ ] CC-000414  $2 1976 FRN  L1234...   $12.00  ordered
  [ ] CC-000415  1899-O Morgan $1        $75.00  missing
  --------------------------------------------------------
  [x] CC-000413  1923 Peace $1           $91.00  received   (dimmed, not tickable)
  --------------------------------------------------------
  Selected: CC-000412
    Arrived [2026-09-09]   Into [Safe deposit box v]
    Note    [edge knock not in the listing photos  ]
    Photos  [+ add]
    Confirm or correct fields                     v
    [ Receive ]  [ Missing ]  [ Returned ]  [ Cancelled ]
```

`OrderLines` (named `OutstandingList` in an earlier draft, before it showed
every line) renders every line on the order, not just the ones still coming:
otherwise an order whose sole receivable line is `missing` had no route back
in at all, and there was no way to see what an order had contained once
everything on it arrived. Not-yet-arrived lines (`ordered`, `missing`) sort
first, each line's status is shown so the two groups read apart, and only a
not-yet-arrived line's checkbox is enabled -- a `received`/`canceled`/
`returned` line is shown for context and cannot be ticked, individually or
through "select all". The backend would refuse re-receiving one with a 409
anyway, but the page should not invite the click.

Ticking several lines and pressing **Receive** applies the same arrival date,
location and note to all of them -- the common case for a box of twenty coins.
Per-item notes and photographs are added in the panel for that item.

**A photograph attaches only to a single-item receipt.** With several items
selected, the photo control is disabled outright rather than left to guess
which of them the picture is of: a photograph is evidence of one physical
object, and attaching it to every selected item -- or to "the first" of them
-- would put a wrong provenance record on all but one, which is worse than no
photograph at all. Receive that item on its own to attach one to it.

The four outcome buttons are the four codes. A page that can only record
success cannot close out an order line that never showed up, and "paid for,
not cancelled, never arrived" is a distinct state the vocabulary already
names (`missing`).

**Field confirmation is collapsed by default.** Expanding it shows the
reviewable fields for the item with their current values, each with a
*confirm* control and an editable input. Confirming writes
`POST /api/inventory/{id}/reviewed`; editing writes `PATCH`. This is the
existing `ReviewPane` behaviour, reused rather than reimplemented -- the panel
is composed from the same components the inventory page uses.

## What is new on the backend

Four endpoints. Everything else is reused.

**`POST /api/inventory/receive`** -- the transition. Takes one or many items,
because the UI's checkbox list maps to it directly and a box of twenty coins
should be one transaction, not twenty:

```
{ "item_ids": [412, 413],
  "outcome": "received" | "missing" | "returned" | "canceled",
  "arrived_on": "2026-09-09",          // optional; NULL in history if omitted
  "storage_location_id": 3,            // optional; only meaningful when received
  "note": "edge knock not in the listing photos" }   // optional
```

`arrived_on` does **not** default to today. Omitted, it is written to
`item_status_history.arrived_on` as NULL -- the fact "we don't know when this
arrived" is a real and different answer from "it arrived today," and the
endpoint has no business guessing between them. The page always sends one:
`ReceiptPanel` defaults its date field to the operator's local today (see the
UI section below), so in practice a receipt made through this page always
carries a date. A caller that omits it deliberately gets NULL, not today.

A supplied `arrived_on` more than one day ahead of UTC's own today is a 422:
"the thing has not physically turned up yet" is what a future date would
mean. The bound is `utc_today + 1 day`, not `utc_today` itself, because the
endpoint only has UTC to compare against while "today" is inherently local. A
caller's local calendar date can lead UTC's by up to a day -- anywhere east
of UTC, for as long as UTC has not yet turned over -- so a bound of exactly
`utc_today` would refuse a genuine same-day receipt for a large share of the
world for several hours every evening. Widening the bound by a day accepts
every timezone's honest "today" while still refusing what an actual
fat-fingered date looks like: two or more days out. The tradeoff is that a
typo exactly one day ahead of the true date is no longer caught here, because
it is indistinguishable from a legitimate ahead-of-UTC today.

All or nothing, in one transaction, exactly as `POST /api/inventory/bulk`
already is and for the same reason: a partial receipt across twenty coins
leaves a state nobody can describe. Every id is resolved and every code checked
before anything is written. Per item it calls `set_status`, and when an outcome
of `received` carries a location, `set_location`.

`arrived_on` is a **date**, not a timestamp, and is distinct from
`changed_at`. When the box sat unopened over a weekend, the date it arrived and
the moment somebody recorded it are different facts, and the second is not a
useful substitute for the first. `changed_at` records the keystroke;
`arrived_on` records the event.

**This is the one schema change in the document:** a nullable
`arrived_on DATE` column on `item_status_history`. The alternative considered
was writing the date into the free-text `note` in some parseable form, which
was rejected: "what arrived last week" is a question worth being able to ask
in SQL, and a date inside prose is not a date. Nullable because most status
changes are not arrivals -- a cancellation has no arrival date, and neither
does the opening `set at import` row.

**`GET /api/purchase-orders`** -- every purchase order, unfiltered and
unpaginated, ordered by id descending. Each row carries the vendor, the order
number, `ordered_on`, and counts of outstanding versus total lines, computed
in one grouped SQL query rather than by loading every order's items.
`outstanding` counts a line that is `ordered` **or** `missing` -- a line
written off as missing is paid for, not cancelled, and sometimes turns up
later, so it is exactly as outstanding as one still `ordered`; counting only
`ordered` would make an order whose sole receivable line is `missing` report
`outstanding=0` and vanish from `OrderPicker` with no route back to it.
There is no `order_number` or vendor filter on the endpoint itself, and
`OrderPicker` no longer filters by `outstanding` either -- it shows every
order the endpoint returns, a fully-received one included, since that is the
only way to look back at what an order contained once everything on it had
arrived. There is no purchase-order router today; acquisition has been
import-only.

**`GET /api/purchase-orders/{id}`** -- one order with its line items: item
code, a short description, cost, current status. This is the list the page
works down.

**`GET /api/storage-locations`** -- for the *Into* picker. `storage_location`
is not a `ReferenceMixin` table, so the reference router does not serve it.

All four sit behind `AdminUser`, like every other endpoint the console uses.

**Storage locations are never customer-visible.** `models/lifecycle.py` states
this as an authorisation boundary enforced by the `public_catalog` view and by
tests, not a convention -- "a public listing that leaked the safe-deposit box
holding the item would be a security failure, not a cosmetic one." The new
endpoint is admin-only and the page lives in the console, which the previous
document put behind its own bundle; neither is an excuse to relax the view.

## Error handling

- **An unknown item id:** 404 naming the id. `POST /receive` takes no order
  id -- an item is resolved on its own id, not scoped to a particular order --
  so there is no "id not on this order" case to refuse separately. Every id
  is resolved before anything is written.
- **An item that is already `received`:** refused with 409 naming its current
  status and the date it arrived. Receiving something twice is more likely a
  double-submitted form or the wrong row than an intention, and silently
  re-receiving overwrites a true arrival date with today's. Correcting a
  genuine mistake is `PATCH`, which now records the correction too.

  `ordered` and `missing` are both receivable -- `OrderLines` and the
  attribute search both let either status be selected, never `received`,
  `canceled` or `returned`. A parcel written off as missing and then turning
  up months later is exactly the case the `missing` code was added for --
  refusing it would leave the only route to the truth a manual status edit,
  which is the untracked path this document exists to close.
- **A `storage_location_id` that does not exist:** 422 listing valid ids.
- **An outcome other than the four codes:** 422 naming them, matching how the
  routers already refuse an unknown filter or an unknown review field.
- **A photograph upload that fails:** does not roll back the receipt. The
  arrival is the fact; the photograph is evidence added to it, and losing a
  recorded arrival because an upload failed would be the worse trade. The page
  reports the failed upload against the item and lets it be retried.

## Testing

Backend:

- `set_status` writes one history row per change, with `from_status_id` set to
  the previous value -- and a **mutation test**: remove the history write and
  confirm a test fails. A history mechanism nothing forces is the thing that
  quietly stops working.
- Receiving many items is atomic: one bad id in a list of five writes nothing.
- Receiving an already-received item is refused, and its original
  `changed_at` and arrival note are unchanged afterwards.
- A `missing` outcome records history exactly as `received` does.
- Location and `location_history` are written together, and only for
  `received`.
- A late arrival works: an item marked `missing` can be received, and both
  history rows survive.
- `alembic check` is clean after the one migration this design needs
  (`item_status_history.arrived_on`). It adds **no tables** and no other
  column; a migration touching anything else means something has been
  misunderstood.
- The `public_catalog` view still exposes no storage location -- the existing
  test for this must continue to pass, unmodified.

Frontend, in `src/owner/`:

- `OrderLines` shows every line on the order, not-yet-arrived (`ordered`,
  `missing`) ones sorted first, with each line's status shown.
- A `received`/`canceled`/`returned` line appears but cannot be ticked, and
  "select all" skips it.
- A `missing` line is selectable and sorts above a `received` one.
- `OrderPicker` still offers an order whose only receivable line is
  `missing` (backend: the `outstanding` count includes it; frontend: the
  picker no longer filters on `outstanding` at all).
- Selecting several and receiving sends one request, not several.
- Each of the four outcomes sends its own code.
- The shop bundle does not grow: `check-bundle-isolation.mjs` and the ESLint
  boundary rules cover this automatically, which is what the first document
  bought.

## Not in this document

- **Partial receipt of a lot** -- twenty ordered, fifteen arrived. The schema
  can express it only by splitting the lot, `splitting.py` already does that,
  and wiring a split into the receiving flow is a second interaction with its
  own questions. Until then, a short-shipped lot is received and then split.
- **Barcode or scanner input.** The lookup is designed so a scanned item code
  would drop into the same search box, but nothing here assumes a scanner.
- **Vendor management.** Vendors arrive through import; this page reads them.
- **Reconciling cost against an invoice.** Money is a separate concern from
  arrival.
