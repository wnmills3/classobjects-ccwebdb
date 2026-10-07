# Attribution: finding what is wrong and fixing it

Attribution establishes what each item actually is -- year, mint mark, grade,
variety, serial -- and finds and repairs the rows where that is missing or
contradictory. Nothing downstream works without it: a coin whose year is
unknown cannot be listed, described or valued.

It is staff work in the management console's inventory pages
(`/management/inventory/coins` and `/management/inventory/currency`): search
for a named problem, walk the results one at a time or fix a selection in
bulk, and record which values a person has confirmed by examining the object.

The worked case: a lot of fifty Morgan dollars bought as "supposed to be BU".
After splitting, each of the fifty needs its own year, mint mark, grade and
variety, and each is a separate act of examination. That sets three
requirements:

1. Every item is editable, whether or not it is on offer.
2. A piece created by a split has a detail row to hold a mint mark or serial.
3. It is visible which values are the seller's claim about the lot and which a
   person has confirmed.

## Editing any item

`PATCH /api/inventory/{id}` edits an item regardless of its sale state.
Classifiers cross the API as codes; an unknown code is a 422 naming the field.
The inventory API speaks the item's own column names (`source_title`,
`item_cost`).

The edit is optimistic. With `base` (each changed field's value where the edit
began) it is merged field by field and refused only where someone else changed
one of those fields since: a 409 whose `conflicts` name each field with `was`,
`theirs` and `yours`. Without `base`, a stale `version` is a 409. The editor
sends `base`, checks for changes made elsewhere while it is open, and shows
conflicts for a choice (`management/pages/inventory/fieldMerge.js`,
`ConflictList.jsx`). A new status or disposition on an offered item,
acknowledged, ends its offers (`for-sale-guards-design.md`).

`POST /api/inventory/bulk` sets the same fields across selected ids in one
transaction, all or nothing: every id is resolved and every code checked
before anything is written, because a partial bulk edit leaves a state nobody
can describe. Attributes are refused in bulk (422): they are a per-item set,
and one set across many items would wipe what each carried.

Neither edit path filters out soft-deleted ids: correcting a row that should
never have existed is still a correction.

## Splitting creates detail rows

Every item has exactly one detail row, `coin_detail` or `currency_detail`
according to its `item_kind`; a change of kind swaps it
(`app.item_kinds.match_detail_to_kind`). `POST /api/inventory/{id}/split`
(`app.splitting`, the console's `SplitDialog`) creates an empty detail row for
each piece rather than copying the lot's, because the lot's detail row
describes the lot, not any one piece. Cost is divided equally or in
proportion to a value given per piece, so the pieces sum exactly to the lot.
The parent keeps `split_at` and leaves every view.

## Provenance is per field

A piece inherits its lot's values -- all fifty Morgans say BU -- and that is
the seller's claim, not a verified grade for coin 37. The row-level
`source = derived` says only that the row was produced by a split.

Confirmation is recorded in `item_field_review`:

```
item_field_review
  inventory_item_id  -> inventory_item, cascade
  field_name         the column confirmed, e.g. 'grade_id'
  reviewed_at        timestamptz
  reviewed_by_id     -> users
  unique (inventory_item_id, field_name)
```

One row per field a person has confirmed by examining the object; absent
means unconfirmed, the correct default for an item nobody has examined. Per
field, not per item, because attributing fifty coins means confirming grade
on all fifty, then year on all fifty, and a half-done coin is the normal
state. It is a table, not a key in the `attributes` JSONB, because that column
holds the long tail awaiting promotion to real columns.

- `GET /api/inventory/{id}/reviewed` lists the confirmed fields.
- `POST /api/inventory/{id}/reviewed` records fields (idempotent; `replace`
  sets the whole set). Only the columns in `REVIEWABLE_FIELDS`
  (`routers/inventory.py`) are accepted; anything else is a 422 listing the
  allowed names.

The lot's claim is the complementary half. `GET /api/inventory/{id}` returns,
beside the item, `lot_claims` -- for each field in `LOT_CLAIM_FIELDS`, what the
parent holds, omitted where the parent says nothing -- and `reviewed`,
`derived` (fields a pass filled, `classifier-defaults-design.md`) and
`last_changes`. The edit form shows the claim beside the field
(`Grade [AU58] · lot says BU`). The claim is derived from the parent every
time, so it cannot go stale; the review record answers "has anyone checked?".
Neither derives the other.

## Repair operations

| Operation | Endpoint | Effect | Guard |
|---|---|---|---|
| Split | `POST /api/inventory/{id}/split` | one item becomes many; the parent keeps `split_at` and leaves every view | -- |
| Detach | `DELETE /api/inventory/{id}/parent` | `parent_item_id` becomes null; idempotent | none |
| Delete | `DELETE /api/inventory/{id}` | soft delete; idempotent | see below |

Split is in the console. Detach and Delete are API operations with no console
control of their own; the console calls Delete only to remove an item made
for entry on a purchase whose editor was closed without a save
(`pages/entry/AddItem.jsx`).

Most items have no parent and never will. Nothing in search, the views or
valuation assumes one exists; an item with no parent is complete, not
orphaned.

### Soft delete

`inventory_item.deleted_at`, not a `disposition` value: `disposition` records
what happened to a coin, and "this row was created by mistake" is not
something that happened to a coin. Every view filters `deleted_at IS NULL`
beside `split_at IS NULL`.

Delete refuses with a 409, checked in this order:

1. **The item has pieces split from it.** Detach them first.
2. **The item has ever been offered** (`sale_state.ever_offered`), alone or
   inside a sales lot. Permanent: the offer is part of the sales history, and
   no listing row is ever removed.
3. **The item is in an assembling sales lot.** Take it out of the lot first.

The order matters: a member of an *offered* lot matches both 2 and 3, and
must get the permanent message rather than a remedy (`remove_member`) that
refuses every lot state but `assembling`.

A parent whose last child has been detached still has `split_at` set, so it
is invisible in every view; it is reached by its id.

## Search as a diagnostic

`GET /api/inventory/{view}/search` (`view` is `coins` or `currency`) finds the
work. Anomalies are **named, kind-aware checks** defined in `app/issues.py`,
not generic field filters. `?issue=no_grade` means *a coin or banknote with no
grade*: bullion has no grade by nature, and a generic `grade=null` would bury
the real cases under rounds that will never have one.

Each check appears three ways from one definition: a filter (`?issue=...`), a
count returned with the page so the size of a job is visible first, and a
badge on the row. Its `description` is shown in the filter panel; the code is
the stable contract used in bookmarked URLs. An unknown issue is a 422 listing
the available ones for that view.

| Check | Views | Finds |
|---|---|---|
| `no_year` | both | coin view: no year; currency view: no series year |
| `no_country` | both | no country |
| `no_grade` | both | a coin or banknote with no grade |
| `no_denomination` | both | a coin or banknote with no denomination |
| `zero_cost` | both | cost missing or zero |
| `mixed_marker` | both | `mixed` in the rating or description |
| `unreviewed` | both | no field confirmed by anyone |
| `kind_unknown` | coin | its kind is not recorded (`unknown`) |
| `no_weight_bullion` | coin | bullion with no fine weight |
| `year_outside_series` | coin | dated outside its design series' years |
| `repeated_identity` | coin | a certification number on more than one row |
| `repeated_identity` | currency | a serial on more than one note |
| `star_mismatch` | currency | the serial's asterisk and the `star` attribute disagree |
| `malformed_serial` | currency | an uppercase letter between two digits |
| `near_duplicate_serial` | currency | a serial one edit from another in the same order, and of different length |

`kind_unknown` is coin-only because the coin view is the only one an `unknown`
item can appear in. `mixed_marker` is separate from `no_grade` because it
means "known to vary": the row stands for several different coins, and the
remedy is to split the lot, not fill the field.

Three structural filters join them:

- `missing=<field>` -- the field is empty, only for the kinds it applies to
  (`inventory_search.MISSING_FIELDS`: year, denomination, grade, country,
  series, metal, photo, storage location, listing link, seller's item id).
  The data-quality reports drill into the inventory through it.
- `lot=<item code>` -- everything split from that item; an unknown code is a
  422.
- `deleted=no|only|any`, default `no`; an unrecognised value is a 422.

A filter is never silently ignored.

### Identity checks surface candidates, never merge

`repeated_identity` covers only fields that identify a physical object: a
serial names one note, a certification number one slab. It must not be
generalised to items without such a key -- twenty Morgans bought together are
identical in every column and are twenty coins. Repeats come in shapes that
look alike (the same item entered twice, one purchase recorded twice, a year
parsed into the cert field), and some are correct: collecting matched serials
across issues is a deliberate pursuit. So a person decides.

`near_duplicate_serial` compares normalized serials (case folded,
non-alphanumerics stripped) within one purchase order and flags a pair at edit
distance 1 **whose lengths differ** -- a dropped or duplicated character. A
same-length substitution is indistinguishable from the next note in a
consecutive run, which the collection holds deliberately, so it is not
flagged. Widening the check to edit distance alone floods it with those runs.

**Nothing is built on a shared description.** Many rows share identical
descriptions through copy-and-paste. Auction listing text written to sell
something does not classify: counts parsed from it break on "$10 Morgan roll"
(face value) and "Red Seal $1, $2 & $5 Set". Where a structural identifier
exists -- a venue's lot id, a serial, a cert number -- use it instead.

### Cross-field checks

Some anomalies are visible only as two fields disagreeing, and neither looks
wrong alone. A star note carries an asterisk at the start or end of its serial
and a `star` attribute; `star_mismatch` compares them, only for notes with a
recorded serial (a missing serial is not a disagreement). Star notes carry a
premium, so a wrong attribute misprices the note. The general rule: where a
fact is recorded both as a marker in free text and as a structured attribute,
check they agree and make the structured one authoritative.

A malformed serial is flagged, never refused on save: the collection is more
varied than any rule written in advance.

## Review and edit in the console

`frontend/src/management/pages/inventory/`:

```
useInventorySearch.js   URL state, fetch, cancellation, counts
InventoryTable.jsx      rows, sorting, selection
FilterPanel.jsx         text, facet selects, issue checks, a missing= chip
missingFields.js        the missing= fields as a person reads them
specs.js                coin and currency column and filter specifications
BulkEditBar.jsx         appears with a selection
ReviewPane.jsx          one item at a time, previous/next
ItemEditDialog.jsx      the editor, opened over the results
ItemEditForm.jsx        fields, lot claims, review marks, suggested marks
kindChange.js           what a change of kind empties
NoteFields.jsx          a note's own classifiers and printing facts
AttributesField.jsx     the item's attributes, with where each was read
ErrorsPanel.jsx         mint and printing errors
FriedbergPanel.jsx      the note's Friedberg number
PhotosPanel.jsx         the item's photographs
SplitDialog.jsx         splitting a lot
OffersPanel.jsx         the item's offers
OfferDialog.jsx         offering items, or a lot, for sale
useLinkedItem.js        the item an address or a report row names by its code
SaleHistory.jsx         every sale of the item, each as it was sold
HistoryPanel.jsx        field changes, status and location moves
fieldMerge.js           field-by-field merge against changes made elsewhere
ConflictList.jsx        fields changed elsewhere meanwhile: keep mine or theirs
```

**The review queue is the search result, frozen at entry.** `ReviewPane`
captures the id list once. If it re-ran the search at each step, fixing item
3's missing year would drop it from `?issue=no_year`, shift every later
position and silently skip an item. Fixed items stay in the queue, marked
done.

The table is for finding work, review for exceptions one at a time, and bulk
edit for setting one value across a selection. All three stand on the same
search and the same edit API.

## Friedberg numbers

The owner records Friedberg numbers read from their own notes and slabs; none
are seeded, fetched or hardcoded, because the catalog's numbering is a
publisher's arrangement (`docs/reference-data.md`). `app/routers/friedberg.py`
serves the owner's private catalog:

| Endpoint | Does |
|---|---|
| `GET /api/friedberg` | searches the catalog by what is visible on a note |
| `POST /api/friedberg` | records a number |
| `GET /api/friedberg/signatures` | the signature pairs a note of the given series can carry, from `note_issue` facts |
| `GET /api/friedberg/catalog` | every row with how many notes hold it (the Lists page) |
| `PATCH` / `DELETE /api/friedberg/{id}` | corrects a row, or deletes one no note holds |
| `POST` / `DELETE /api/inventory/{id}/friedberg` | attaches a number to a note, or clears it |

A search row matches each supplied filter when it equals it or has no value
recorded, and must equal at least one of them, so a half-known type is still
found but a row that knows nothing matches nothing. Printing location is one
of the filters: a 2017-A $1 is a different number from Washington and from
Fort Worth. Results are in the order the rows were recorded.

The lookup (`receiving/FriedbergLookup.jsx`) is offered in Receiving, where a
chosen number is attached at once, and in the item editor, where it is held
until the editor's Save.

When the catalog has no match, **Search the web** opens a question for
Google's AI Mode built from the form (`webSearchText`): the series,
denomination, note class, district, signatures, web press, printing location
and plates, asking for the mule suffix when plates are given. The owner reads
the answer and types the number in. Nothing is fetched or stored from the
search: a machine collecting Friedberg numbers is the harvesting the
reference-data rule forbids.

## Valuation (not built): the rules that bind it

The `metal_price` and `valuation_snapshot` tables exist
(`docs/database-design.md`). A spot price is recorded by hand on the
console's **Spot prices** page (`docs/system-administration.md`), and the
`cb_metal` report and that page compute melt value from the latest
`metal_price`. Nothing records a valuation snapshot, and the only asking
price derived is the offer dialog's fill to a margin over cost
(`docs/specs/selling-design.md`). These constraints bind valuation as the
rest is built.

**Cost-plus.** The asking price derives from what was paid and a markup the
owner chooses, informed by a wholesale reference. The derived number is the
owner's to publish; the reference behind it may not be.

**Metal spot prices** are bare published facts. Melt value comes from
`metal_price`, a time series, so a past valuation stays reproducible. Bullion
needs no price guide.

**A licensed price service (CDN Public API v2)**, if subscribed, is a service
called rather than a table copied. Its published terms shape the design:

| Data | License |
|---|---|
| Greysheet wholesale values | back-end only; never on a public page |
| CPG retail values | may be shown publicly, labeled as CDN's |
| GSID numbers | may be stored and shown; publicly with the `GSID` label and a link |
| Caching | at most 24 hours and not past the daily refresh; no storing to avoid calls |
| Redistribution | none without written consent |

So: wholesale values feed the markup server-side and are never displayed;
there is no stored price table, only an expiring cache kept out of backups; a
GSID may be stored on an item like a certificate number; and
`valuation_snapshot` records the owner's own inputs, the derived value, and
which CDN basis and timestamp were used, but not the CDN figure itself. Read
the current terms in full against the subscription actually bought before
building any of it.
