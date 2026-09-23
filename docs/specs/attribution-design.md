# Attribution: finding what is wrong and fixing it

Attribution is the work of establishing what each item actually is -- year,
mint mark, grade, variety, serial -- and of finding and repairing the rows where
that is missing or contradictory. Nothing downstream works without it: a coin
whose year is unknown cannot be listed or valued.

The worked case: a lot of fifty Morgan dollars bought as "supposed to be BU".
After splitting, each of the fifty needs its own year, mint mark, grade and
variety, and each is a separate act of examination. That sets three
requirements the design meets:

1. Every item is editable, whether or not it is on offer.
2. A piece created by a split has a detail row to hold a mint mark or serial.
3. It is visible which values are the seller's claim about the lot and which a
   person has confirmed.

## Editing any item

`PATCH /api/inventory/{id}` edits an item regardless of its sale state.
Classifiers cross the API as codes; an unknown code is a 422 naming the field.
The edit is optimistic: with `base` (each changed field's value where the edit
began) it is merged field by field and refused only where someone else changed
one of those fields since -- a 409 whose `conflicts` name each field with
`was`, `theirs` and `yours`; without `base`, a stale `version` is a 409. The
editor sends `base`, checks for changes made elsewhere while it is open, and
shows conflicts for a choice (`owner/pages/inventory/fieldMerge.js`). The
inventory API speaks the item's own column names (`source_title`,
`item_cost`). A new status or disposition on an offered item, acknowledged,
ends its offers (see `for-sale-guards-design.md`).

`POST /api/inventory/bulk` sets the same fields across selected ids in one
transaction, all or nothing: every id is resolved and every code checked before
anything is written, because a partial bulk edit leaves a state nobody can
describe. The attribute set is refused in bulk (it is per item, and one set
across many items would wipe what each carried). Neither edit path filters out
soft-deleted ids; correcting a row that should never have existed is still a
correction.

## Splitting creates detail rows

Every item has exactly one detail row: `coin_detail` or `currency_detail`
according to its `item_kind`. `POST /api/inventory/{id}/split` creates an empty
detail row for each piece rather than copying the lot's, because the lot's
detail row describes the lot, not any one piece. Cost is divided so the pieces
sum exactly to the lot.

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
  reviewed_by        -> users
  unique (inventory_item_id, field_name)
```

One row per field a person has confirmed by examining the object; absent means
unconfirmed, the correct default for every imported item. Per field, not per
item, because attributing fifty coins means confirming grade on all fifty, then
year on all fifty, and a half-done coin is the normal state. It is a table,
not a key in the `attributes` JSONB, because that column holds the long tail
awaiting promotion to real columns.

- `GET /api/inventory/{id}/reviewed` lists the confirmed fields.
- `POST /api/inventory/{id}/reviewed` records fields (idempotent; `replace`
  sets the whole set). Only the fields in `REVIEWABLE_FIELDS` are accepted;
  anything else is a 422 listing the allowed names.

The lot's claim is the complementary half. `GET /api/inventory/{id}` returns,
for each field in `LOT_CLAIM_FIELDS`, what the parent holds, and the edit form
shows it beside the field (`Grade [AU58] · lot says BU`). The claim is derived
from the parent every time, so it cannot go stale; the review record answers
"has anyone checked?". Neither derives the other.

## Repair operations

| Operation | Endpoint | Effect | Guard |
|---|---|---|---|
| Split | `POST /api/inventory/{id}/split` | one item becomes many; the parent keeps `split_at` and leaves every view | -- |
| Detach | `DELETE /api/inventory/{id}/parent` | `parent_item_id` becomes null; idempotent | none |
| Delete | `DELETE /api/inventory/{id}` | soft delete; idempotent | see below |

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

A parent whose last child has been detached still has `split_at` set, so it is
invisible in every view. Delete is therefore reachable from the lot panel, not
only from search.

## Search as a diagnostic

Anomalies are **named, kind-aware checks** defined in `app/issues.py`, not
generic field filters. `?issue=no_grade` means *a coin or banknote with no
grade*: bullion has no grade by nature, and a generic `grade=null` would bury
the real cases under rounds that will never have one.

Each check appears three ways from one definition: a filter (`?issue=...`), a
count returned with the page so the size of a job is visible first, and a badge
on the row. Its `description` is shown in the filter panel; the code is the
stable contract used in bookmarked URLs. An unknown issue is a 422 listing the
available ones for that view.

| Check | Views | Finds |
|---|---|---|
| `no_year` | both | no year |
| `no_country` | both | no country |
| `no_grade` | both | a coin or banknote with no grade |
| `no_denomination` | both | a coin or banknote with no denomination |
| `zero_cost` | both | cost missing or zero |
| `mixed_marker` | both | `mixed` in the grade text or description |
| `unreviewed` | both | no field confirmed by anyone |
| `kind_unknown` | coin | the import could not classify it |
| `no_weight_bullion` | coin | bullion with no fine weight |
| `repeated_identity` | coin | a certification number on more than one row |
| `repeated_identity` | currency | a serial on more than one note |
| `star_mismatch` | currency | the serial's asterisk and the `star` attribute disagree |
| `malformed_serial` | currency | an uppercase letter between two digits |
| `near_duplicate_serial` | currency | a serial one edit from another in the same order, and of different length |

`kind_unknown` is coin-only because the coin view is the only one an `unknown`
item can appear in. `mixed_marker` is separate from `no_grade` because it means
"known to vary": the row stands for several different coins, and the remedy is
to split the lot, not fill the field.

Two structural filters join them: `lot=<item code>` (everything split from that
item) and `deleted=no|only|any`, default `no`. An unrecognised `deleted` value
is a 422; a filter is never silently ignored.

### Identity checks surface candidates, never merge

`repeated_identity` covers only fields that identify a physical object: a
serial names one note, a certification number one slab. It must not be
generalised to items without such a key -- twenty Morgans bought together are
identical in every column and are twenty coins. Repeats come in shapes that
look alike (the same item entered twice, one purchase recorded twice, a year
parsed into the cert field), and some are correct: collecting matched serials
across issues is a deliberate pursuit. So a person decides.

`near_duplicate_serial` compares normalised serials (case folded,
non-alphanumerics stripped) within one purchase order and flags a pair at edit
distance 1 **whose lengths differ** -- a dropped or duplicated character. A
same-length substitution is indistinguishable from the next note in a
consecutive run, which the collection holds deliberately, so it is not
flagged. Widening the check to edit distance alone floods it with those runs.

**Nothing is built on a shared description.** Many rows share identical
descriptions through copy-and-paste, and that text is being replaced. Auction
listing text written to sell something does not classify: counts parsed from
it break on "$10 Morgan roll" (face value) and "Red Seal $1, $2 & $5 Set". Where
a structural identifier exists -- a venue's lot id, a serial, a cert number --
use it instead.

### Cross-field checks

Some anomalies are visible only as two fields disagreeing, and neither looks
wrong alone. A star note carries an asterisk at the start or end of its serial
and a `star` attribute; `star_mismatch` compares them, only for notes with a
recorded serial (a missing serial is not a disagreement). Star notes carry a
premium, so a wrong attribute misprices the note. The general rule: where a
fact is recorded both as a marker in free text and as a structured attribute,
check they agree and make the structured one authoritative.

`app.serial_patterns.check` reports a malformed serial as a warning and never
refuses one: the collection is more varied than any rule written in advance.

## Review and edit in the console

`frontend/src/owner/pages/inventory/`:

```
useInventorySearch.js   URL state, fetch, cancellation, counts
InventoryTable.jsx      rows, sorting, selection
FilterPanel.jsx         text, facet selects, issue checks
BulkEditBar.jsx         appears with a selection
ReviewPane.jsx          one item at a time, previous/next
ItemEditForm.jsx        fields, lot claims, review marks
specs.js                coin and currency column and filter specifications
```

**The review queue is the search result, frozen at entry.** `ReviewPane`
captures the id list once. If it re-ran the search at each step, fixing item
3's missing year would drop it from `?issue=no_year`, shift every later
position and silently skip an item. Fixed items stay in the queue.

The table is for finding work, review for exceptions one at a time, and bulk
edit for setting one value across a selection. All three stand on the same
search and the same edit API.

## Friedberg numbers

The owner records Friedberg numbers read from their own notes and slabs; none
are seeded, fetched or hardcoded, because the catalogue's numbering is a
publisher's arrangement (see `CLAUDE.md`, Reference data). `app/routers/friedberg.py`
serves the owner's private catalogue: `GET /api/friedberg` searches it by what
is visible on a note, `POST /api/friedberg` records a number, and
`POST|DELETE /api/inventory/{id}/friedberg` attaches or clears one on an item.
`GET /api/friedberg/signatures` returns the signature choices, drawn from
`note_issue` facts.

## Valuation inputs (not built)

Valuation is not built. These constraints bind it when it is.

**Cost-plus.** The asking price derives from what was paid and a markup the
owner chooses, informed by a wholesale reference. The derived number is the
owner's to publish; the reference behind it may not be.

**Metal spot prices** are bare published facts. Melt value comes from
`metal_price`, a time series, so a past valuation stays reproducible. Bullion
needs no price guide.

**A licensed price service (CDN Public API v2)**, if subscribed, is a service
called rather than a table copied. Its published terms shape the design:

| Data | Licence |
|---|---|
| Greysheet wholesale values | back-end only; never on a public page |
| CPG retail values | may be shown publicly, labelled as CDN's |
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
