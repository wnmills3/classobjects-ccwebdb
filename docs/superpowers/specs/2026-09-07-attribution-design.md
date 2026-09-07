# Attribution: finding what is wrong and fixing it

Design, 2026-09-07. Status: awaiting review.

The first of three pieces of work. Attribution comes first because nothing
downstream is possible without it: you cannot list a coin whose year you do not
know, and you cannot design a customer-facing catalogue against zero listings.

| | Depends on |
|---|---|
| **A. Attribution** (this document) | -- |
| B. Listing | A |
| C. Public catalogue | B, for data to design against |

Photograph linking is deliberately excluded. Matching the ~673 photographs
already on disk to items is a bulk-matching problem, which is a different job
from grading a coin in hand, and it gets its own document.

## The problem

Building an inventory is slow and error-prone. The need is to **search for
anomalies and fix them**, both in the imported data and in everything acquired
from now on.

The concrete case: a lot of fifty Morgan dollars is bought with no detail known
beyond "supposed to be BU". After splitting, each of the fifty needs its own
year, mint mark, grade and variety. None of that was knowable at purchase.

### What is actually wrong, measured

Over 7,598 items, excluding split parents:

| Anomaly | Count |
|---|---|
| No grade (coins and currency only) | 2,970 |
| No country | 2,395 |
| No year | 1,230 |
| Bullion with no weight | 712 |
| No denomination (coins and currency) | 263 |
| `Mixed` marker in grade or description | 186 rows, 43 groups |
| Zero or missing price | 51 |
| "LOT OF *n*" where *n* does not match the row count | 44 |
| Kind still `unknown` | 33 |
| Repeated currency serial numbers | 18 notes, 9 groups |
| Repeated certification numbers | 80 rows, 40 numbers |
| Reversed or implausible years | 0 |

### How the collection is grouped

| Population | Count | State | Needs |
|---|---|---|---|
| Flattened purchase lots | 770 lots / **3,780 items** | one row per coin already, no parent | reconstruct the parent, then attribute each row |
| Unsplit conglomerates | 12 items / **240 pieces** | still one row holding many coins | run the existing split, then attribute |
| Bought individually | 3,813 items | fine | nothing |

**These populations overlap.** Seven of the twelve conglomerates are *also*
inside a flattened group -- rows like `20x 1oz Copper Round Mixed`, repeated
nine times, each row itself holding twenty rounds. Those are lots of lots, and
they need reconstructing *and* splitting. Any code that treats the three
populations as disjoint will mishandle them.

The 770 groups are rows sharing a purchase order and an identical description
-- the spreadsheet's only way to say "twenty of these". The literal example:
`(LOT OF 29) MORGAN SILVER DOLLARS "BETTER YEARS" 1878 - 1898`, 23 rows.

`Mixed` is a marker the owner used for lot attributes where the individuals
vary. It is not a grade and the import correctly declined to make it one; it
survives in `grade_raw` unresolved. It means something different from *missing*
-- not "unknown" but "known to vary", which is a positive statement that the
row stands for several different coins and the purchase lot needs decomposing.

## Three gaps in the current code

1. **An item that is not for sale cannot be edited at all.** The only editing
   path is `PATCH /api/catalog/{listing_id}`, which requires a listing. Every
   freshly split piece has none.
2. **`split_item` creates no detail row.** Every one of the 7,598 items has
   exactly one -- `currency_detail` for currency, `coin_detail` for everything
   else, 100% coverage -- but split children get neither. Mint mark, variety
   and serial number have nowhere to be written. This is a bug, not a design
   question.
3. **Split children inherit the lot's guess with nothing marking it as one.**
   `INHERITED` copies `year_start`, `grade_id`, `metal_id` and twenty other
   columns. All fifty Morgans say `grade = BU`, which is the seller's claim
   about the lot, not a verified grade for coin 37.

## Design

### A purchase lot is an item that was split

No new table and no new column. `parent_item_id` already means exactly this,
and reusing it inherits behaviour that a parallel `lot` table would have had to
reimplement:

- `WHERE i.split_at IS NULL` already appears in **all four views**, so a lot is
  excluded from inventory and valuation automatically
- `parent_item_id` is already indexed, so "every item in this lot" is one
  lookup
- the `pieces` / `parent` relationship already exists
- cost-basis lineage semantics are already documented on the column

A separate table would have needed its own exclusion rule in four views, which
is the kind of thing that gets missed in one of them.

**The backfill reconstructs the parent the spreadsheet flattened away.** For
each of the 770 groups: create one `inventory_item` holding the purchase order,
the shared description, `price` and `shipping` summed from its rows, and
`split_at` set; then point the members at it. This is not inventing a fiction.
A roll of 20 Morgans on one order *was* one purchase; the spreadsheet could not
express it.

Grouping by `(purchase_order_id, description)` will be wrong somewhere in 770
groups -- two genuinely separate purchases of the same thing on one order would
merge -- so the backfill needs a review pass and the manual repair tools below.

### Do not group by a column being edited

Membership must be stored, not derived from description equality. The moment
the first Morgan's description is edited to `1881-S Morgan`, it would drop out
of a derived group, the group would shrink, and any position in a review queue
would shift. A key that is actively being edited cannot be the key iterated by.

### Lot operations

**The backfill is the manual tool run 770 times.** One operation -- group these
items under a new parent -- exposed as an endpoint and used by both the cleanup
panel and the backfill script. A migration with its own copy of the logic is a
migration whose behaviour drifts from the UI's.

| Operation | Effect | Guard |
|---|---|---|
| **Group** | create a parent from selected items, sum `price` and `shipping`, set `split_at`, point members at it | items unsold and sharing a purchase order |
| **Detach** | remove one child from its parent; it becomes standalone | none |
| **Split** | existing endpoint: one item holding many becomes many | already built |
| **Delete** | soft delete so the item leaves search | refuse if it has pieces or appears in an order |

Detach-then-delete is the repair path for a wrong grouping: pull the
misassigned children off, then delete the childless parent.

Normal processing is lot-first -- receive a purchase lot as one item, then
decompose it. Grouping is **cleanup tooling for imported data**, not a routine
workflow, and should not accumulate conveniences for a path that will not
normally be taken.

### Soft delete

`inventory_item.deleted_at timestamptz null`, not a `disposition` value.
`disposition` records what happened to a coin -- held, listed, sold, shipped --
and "this row was created by mistake" is not something that happened to a coin.
Putting it there would corrupt every disposition report with rows that were
never real.

A separate column also lets `WHERE deleted_at IS NULL` sit beside the
`split_at IS NULL` already present in all four views: same shape of rule, same
place.

Search excludes deleted by default and accepts `?deleted=no|only|any`. An
unrecognised value is a 422, consistent with the existing rule that an
unrecognised filter is never silently ignored.

**Known hazard.** After detaching its last child, a parent still has `split_at`
set, so it is invisible in every view but not deleted -- a row that exists and
cannot be found. Delete must therefore be reachable from the lot panel, not
only from search, because search is exactly where it is not.

### Search as a diagnostic

**Anomalies are named, kind-aware checks, not generic field filters.**
`?issue=no_grade` means *a coin or banknote with no grade*, because bullion has
no grade by nature and 712 rounds have no weight either. A generic `grade=null`
would bury the 2,970 real cases under rounds that will never have one. The
domain knowledge belongs in the check, defined once, rather than in the head of
whoever types the filter.

Each check appears three ways from one definition: a filter (`?issue=no_year`),
a facet (`no_year 1,230`, so the size is visible before committing), and a
badge on the row. Adding a check later means adding one predicate.

Initial checks: `no_year`, `no_grade`, `no_country`, `no_denomination`,
`no_weight_bullion`, `mixed_marker`, `lot_count_mismatch`, `zero_price`,
`kind_unknown`, `repeated_identity`, `star_mismatch`, `interior_asterisk`,
`inherited_from_lot`.

### Duplicate detection needs a natural key

`repeated_identity` covers the two fields that identify a *physical object*
rather than describe it: `currency_detail.serial_number` and
`item_certification.cert_number`. A PCGS or NGC number names exactly one slab
and a serial names exactly one note, so a repeat is real evidence.

**It must not be generalised to items without such a key.** 731 groups of 3,543
items share a purchase order, a price and a description -- and those are the
flattened purchase lots, not duplicates. Twenty Morgans bought together at $19
each look identical by every available column and are twenty different coins.
Treating that as a duplicate signal would condemn $91,752 of real inventory.

Measured, the check finds three different problems that look alike and must not
be auto-resolved together:

| Shape | Example | Almost certainly |
|---|---|---|
| Same identifier, same order, same price | 5 currency pairs | the same item entered twice |
| Same identifier, *different* orders | 32 cert numbers | one purchase recorded twice, or a mistranscription |
| Identifier that is not an identifier | `1973` on 5 rows | a year parsed into the cert field |

Only the shape of the value distinguishes them, so the check surfaces
candidates and a person decides. It never merges rows.

### The auction lot id is the only reliable duplicate key

Resolved 2026-09-07 after three wrong answers, each wrong the same way.

`import_row.raw->>'Link'` carries the venue's own lot id, e.g.
`.../lot/226778844/1886-morgan-silver-dollar-ngc-ms66`. It is issued by the
auction house and means exactly one thing: **one lot, won once.**

63 lot ids appear on more than one row. Almost all are innocent -- one lot
containing twenty coins becomes twenty rows, which is the flattened purchase
lot again. What separates the duplicates is a signal with no innocent reading:

| | Lot ids | Rows |
|---|---|---|
| All rows share one order date -- a genuine multi-item lot | 58 | 397 |
| **Rows carry different order dates** -- the same lot recorded twice | **5** | **14** |

A lot of twenty coins arrives on one date. The same lot appearing on 9 January
*and* 12 January cannot be two purchases. Four of the five are one HiBid batch
entered three days apart; the fifth is a three-note set entered twice.

**Three earlier attempts failed, and the pattern is worth keeping.** Grouping by
`(order, description)` returned 731 groups and $91,752 -- those were the Morgan
rolls. Grouping by repeated lot id returned 63 groups and $26,353 -- those were
multi-coin lots. Parsing counts out of the description (`[3]`, `x20`, `(2) x`)
returned 38 suspects and $20,170 -- but `$10 Morgan Silver Dollar Sealed roll`
means ten dollars of face value, `Silver Eagle Roll` means twenty coins, and
`Red Seal $1, $2 & $5 Set` means three notes. Free text written by an auction
house to sell something will not classify.

**Prefer a field with a structural guarantee over free text.** The lot id is
issued by the venue and means one thing. The description means whatever sold
the lot.

**Exact matching is not enough.** Three further currency duplicates hide behind
single-character errors -- `O` for `U`, a dropped digit, `6` for `3` -- and
were invisible to equality. The check should compare normalised identifiers
(case folded, non-alphanumerics stripped) and flag near-matches within one
purchase order as candidates, since that is where they cluster.

### Cross-field checks find what no single field shows

Some anomalies are only visible as a *disagreement between two fields*, and
they are the most valuable kind because neither field looks wrong alone.

**Star notes are the worked example.** A star (replacement) note carries an
asterisk at the start or end of its serial -- never in the middle -- and there
is a `star` entry in `note_attribute` to record it. Among notes with a serial:

| Asterisk in serial | `star` attribute | Count |
|---|---|---|
| yes | yes | 161 |
| yes | **no** | **28** |
| **no** | yes | **5** |
| no | no | 821 |

33 disagreements, none of which any single-field check would surface. The 28
include `B08084501*` through `B08084510*`, ten consecutive star notes whose
attribute was never set. This is not cosmetic: star notes carry a premium, so
those 28 would currently be listed as ordinary notes and sold too cheaply.

Two related rules fall out:

- **An interior asterisk is invalid.** The character is positional -- start or
  end only. Currently 0 rows violate this, and the check exists to keep it that
  way.
- **Position is recorded inconsistently**: 179 trailing, 10 leading. Both mean
  the same thing, so a search keyed on a trailing `*` silently misses ten
  notes. The attribute, not the punctuation, should be what searches use --
  which is exactly why the 33 disagreements need resolving first.

The general shape is worth reusing: wherever a fact is recorded in two places
-- a marker inside a free-text field and a structured attribute beside it --
the check is that they agree, and the fix is to make the structured one
authoritative.

**And a repeat is sometimes correct.** Four of the nine currency groups are
genuinely different notes -- different series or series letter -- that share a
serial because collecting matched serial numbers across issues is a deliberate
pursuit. `E00003333B` appears on a 2021 and a 2017-A $1, and the serial itself
is a repeating-digit note bought on purpose. A check that assumed repeats were
errors would fight the collection's actual theme.

`mixed_marker` is separate from `no_grade` because it means "known to vary"
rather than "unknown", and the remedy is different: decompose the purchase lot
rather than fill the field.

Two new structural filters: `lot=CC-004120` (everything with that parent) and
`deleted=no|only|any`.

### Review and edit

**The queue is the search result, frozen at entry.** Search, get 23 Morgans,
enter review, walk them one at a time with previous/next and a progress count.

Freezing is required for the same reason membership is stored: if the queue
re-ran the search at each step, fixing item 3's missing year would remove it
from `?issue=no_year`, the set would shrink to 22, and every position after it
would shift -- silently skipping an item. Entering review captures the id list
once; navigation walks that list; fixed items stay visible, marked done.

Two renderings of one result set:

| | Table view | Review view |
|---|---|---|
| Shows | many rows, few columns | one item, every field |
| For | finding work, bulk-setting what is common | exceptions, one at a time |
| Edits | select rows, set shared fields, apply | full form, save and next |

They share the search, the filters, the URL and the edit API. Switching is a
toggle that preserves the query, so "the 23 Morgans" is one set viewed two
ways rather than two screens to navigate between.

**The edit form shows the parent's value beside each field** --
`Grade [AU58] · lot says BU` -- so it is always visible what is being
overridden and what is still only the seller's claim.

**This replaces a stored `examined` flag.** An earlier draft proposed
`examined_at`, a column someone maintains. It was dropped: the same question is
answered by comparing the item to its parent, which cannot drift out of sync,
needs no backfill decision for the 7,598 existing items, and finds problems
nobody thought to flag.

## API

```
PATCH  /api/inventory/{id}           edit an item; optimistic via version, 409 on conflict
POST   /api/inventory/bulk           set fields across selected ids
POST   /api/inventory/group          create a parent from selected ids
DELETE /api/inventory/{id}/parent    detach a child from its parent
DELETE /api/inventory/{id}           soft delete, guarded
GET    /api/inventory/{view}/search  extended: issue, lot, deleted filters
```

`PATCH` unblocks everything else.

Existing conventions carry over unchanged: classifiers cross the API as codes
rather than ids, an unknown code is a 422 naming the field, and edits are
optimistic against `version` with a 409 and the current state on conflict.

`POST /bulk` is all-or-nothing in one transaction. A partial bulk edit across
50 coins leaves a state nobody can describe, and "which of the 50 applied?" is
not a question the UI should ever have to answer.

## Frontend

`Inventory.jsx` is 284 lines and already carries search, filters, facets,
sorting and paging. Selection, bulk edit, review mode and an edit form would
take it past 800. It gets split as part of this work, not after, since every
one of those modules is touched anyway:

```
inventory/
  useInventorySearch.js   URL state, fetch, cancellation, facets
  InventoryTable.jsx      rows, sorting, selection
  FilterPanel.jsx         text, facet selects, issue checks
  BulkEditBar.jsx         appears with a selection
  ReviewPane.jsx          one item at a time, previous/next
  ItemEditForm.jsx        fields, parent value hints, save
  specs.js               the coin and currency column/filter specifications
```

## Testing

Follows the existing practice of proving a guarantee by removing it.

- **Group/detach/delete round trip.** Group five items, detach one, delete the
  parent once childless; the four remaining are standalone and cost basis is
  unchanged.
- **Delete guards.** Deleting a parent with pieces fails; deleting an item in
  an order fails. Both must fail *for the stated reason*, not incidentally.
- **Soft delete leaves search.** A deleted item is absent by default, present
  with `deleted=only`, and absent from all four views.
- **Queue stability.** Enter review on `issue=no_year`, fix the first item,
  confirm the queue still has the same members in the same order. Remove the
  freezing and this test must fail.
- **Bulk atomicity.** A bulk edit where one id is invalid changes nothing.
- **Optimistic conflict.** Two edits from one loaded state: the second gets a
  409. Remove the version check and this must fail.
- **Split creates detail rows.** Every child of a split has exactly one detail
  row, of the kind its `item_kind` implies.
- **Backfill is the endpoint.** The migration calls the same code path as
  `POST /api/inventory/group`; a test asserts the 770 reconstructed parents
  have `price` equal to the sum of their children and that total cost basis
  across the collection is unchanged to the penny.

## Migration

1. Add `deleted_at`; add `AND i.deleted_at IS NULL` to all four views.
2. Fix `split_item` to create the detail row.
3. Backfill the 770 parents through the group operation, in a transaction, with
   a report written to `logs/` naming every group created and its members.
4. Verify cost basis is unchanged: `sum(total_cost)` over non-split,
   non-deleted items must still be $535,436.59.

Step 4 is the one that matters. The backfill creates 770 rows holding money;
if any of them is counted alongside its children the collection's value
silently doubles in places.

## Open questions

- **Grouping accuracy.** `(purchase_order_id, description)` is a heuristic.
  The 44 rows where "LOT OF *n*" disagrees with the row count are evidence it
  will not always be right. The backfill report exists to be read, not filed.
- **Item 0 of the 12 conglomerates.** The unsplit items hold 240 pieces between
  them, but the piece count comes from `storage_quantity`, which was itself
  parsed from the spreadsheet. Those counts want checking against the
  descriptions before anyone splits on them.
- **Duplicate rows: 5 lots, 7 surplus rows, $1,183.55.** Resolved 2026-09-07,
  see the auction-lot-id section above. Needs confirming against the physical
  collection -- one 1886 Morgan MS66 or two -- before anything is deleted.
- **Currency duplicates.** Of the 9 repeated serials, 4 are legitimate
  matched-serial notes and 5 are the same note entered twice, with 3 further
  duplicates hidden behind typos.
- **Certification numbers are not reliable and should not be trusted as an
  identifier.** 40 numbers repeat across 80 rows. The 29 largest cases sit
  across two eBay orders six months apart with different totals -- two genuine
  purchases whose seller reused one listing template across identical PR69DCAM
  sets. The certificates were copied from listing text, so they identify the
  *listing*, not the slab. They want either re-reading from the physical slabs
  or clearing.
