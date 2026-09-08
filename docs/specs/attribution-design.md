# Attribution: finding what is wrong and fixing it

Design. Status: awaiting review.

The first of three pieces of work. Attribution comes first because nothing
downstream is possible without it: you cannot list a coin whose year you do not
know, and you cannot design a customer-facing catalogue against zero listings.

| | Depends on |
|---|---|
| **A. Attribution** (this document) | -- |
| B. Listing and valuation | A |
| C. Public catalogue and auction | B |

Photograph linking is excluded. Matching the ~673 photographs already on disk
to items is a bulk-matching problem, which is a different job from grading a
coin in hand, and it gets its own document.

## What attribution means here

Building an inventory is slow and error-prone. The need is to **search for
anomalies and fix them**, in the imported data and in everything acquired from
now on.

The worked case, in the owner's words: a lot of fifty Morgan dollars is bought
with no detail known beyond "supposed to be BU". After splitting, each of the
fifty needs its own year, mint mark, grade and variety. None of that was
knowable at purchase, and each of the fifty is a separate act of examination.

That case sets three requirements that run through everything below:

1. An item that is not for sale must be editable.
2. A piece created by a split must have somewhere to put a mint mark.
3. It must be visible which values are the seller's claim about the lot and
   which a person has actually confirmed.

## The collection as it stands

7,591 live items, $534,177.89 of cost basis, every one of them with exactly one
detail row -- `currency_detail` for the 1,114 banknotes, `coin_detail` for the
rest.

| Anomaly | Count |
|---|---|
| No grade (coins and banknotes only) | 2,965 |
| No country | 2,394 |
| No year | 1,230 |
| Bullion with no weight | 712 |
| `Mixed` marker in grade or description | 175 |
| No denomination (coins and banknotes only) | 262 |
| Kind still `unknown` | 33 |
| Zero or missing `item_cost` | 51 |
| Repeated banknote serials | 18 notes, 9 groups |
| Repeated certification numbers | 108 rows, 48 numbers |
| Star attribute with no asterisk in the serial | 5 |

Nothing here is a defect of the import. These are the things that were not
knowable, or not recorded, when a row was typed.

**No item has a parent, and none has a listing.** There are 0 split parents and
0 listings. Every figure above describes items bought individually or already
broken out by hand, and the split machinery is currently exercised only by
tests. Twelve items still hold more than one piece -- 240 pieces between them
-- and are the first real work for it.

### What is already built and must not be re-derived

| | State |
|---|---|
| `purchase_order` | 3,879 real orders over 7,497 items; largest holds 85 |
| `series` / `series_alias` | 38 series, 25 aliases; 3,273 items classified |
| `currency_detail.series_designation` | generated, 1,035 notes carry it |
| `note_attribute` | 891 links; star, radar, binary, trinary, repeater, ladder, low and high serial, double quad |
| `app.serial_patterns` | derives the designations from the serial and reports malformed ones |
| `app.order_repair` | recovers a vendor's order number from the listing URL |
| `app.backup` | portable full-database copy; the database is the system of record |
| Excel round trip | designed in `excel-roundtrip-design.md`; the bulk-edit surface |

Two of these change what this document has to specify. The order repair means
`purchase_order_id` is now trustworthy, so "what was bought together" needs no
reconstruction. The serial-pattern work means the star and fancy-serial
attributes are derived rather than typed, so those checks are about
*disagreement*, not absence.

### Shared descriptions are not a signal

809 groups of rows share an identical description, covering 3,983 items. This
looks like structure and is not: it is copy-and-paste from replicating a row,
and the owner's cleanup will replace those descriptions with real ones.

**Nothing may be built on a shared description.** Not grouping, not duplicate
detection, not lot reconstruction. A signal that is about to be deliberately
destroyed is not a signal. This matters because free text is the tempting
option every time -- see the duplicate section below, where it was tried three
times and was wrong three times.

## The three gaps

These are the whole of the implementable core. None is built today.

### 1. An item that is not for sale cannot be edited

The only editing path is `PATCH /api/catalog/{listing_id}`, which needs a
listing. There are no listings, so **there is currently no way to edit any of
the 7,591 items through the API**, and no way to edit a freshly split piece
ever, because a piece is held rather than listed.

`PATCH /api/inventory/{id}` unblocks everything else in this document.

It follows the conventions already in place: classifiers cross the API as
codes, an unknown code is a 422 naming the field, and the edit is optimistic
against `version` with a 409 carrying the current state on conflict.

The catalogue's own vocabulary stays as it is. A listing has a `title` and a
`price` -- what the shop calls the item and what it is offered for -- while the
item has `source_title` and `item_cost`. `ITEM_SCALARS` in the catalogue router
translates between them, and the inventory API speaks the item's names
throughout, because that is the surface the Excel round trip also uses.

### 2. `split_item` creates no detail row

Every existing item has exactly one detail row. Split children get neither, so
mint mark, variety, series letter and serial number have nowhere to be written
-- which is precisely the data the worked case exists to capture.

This is a bug, not a design question. The fix creates a `coin_detail` or
`currency_detail` row per child according to the child's `item_kind`, and
copies from the parent's detail row only what is a fact about every piece
(nothing, for a mixed lot) rather than everything the parent happened to hold.

### 3. Nothing marks a value as the lot's claim

`INHERITED` copies `year_start`, `grade_id`, `metal_id` and twenty other
columns onto every child. All fifty Morgans say `grade = BU`, which is the
seller's claim about the lot rather than a verified grade for coin 37.

The row-level `source = derived` records that the *row* was produced by a
split. It says nothing about which of its fields anyone has since looked at,
and it cannot: it is one value for the whole row.

## Provenance is per field

**Decided with the owner: a small `item_field_review` table.**

```
item_field_review
  inventory_item_id  -> inventory_item, cascade
  field_name         text          the column confirmed, e.g. 'grade_id'
  reviewed_at        timestamptz
  reviewed_by        -> users
  unique (inventory_item_id, field_name)
```

One row per field a person has confirmed by looking at the object. Absent means
unconfirmed, which is the correct default for all 7,591 imported items and
needs no backfill.

Three reasons for a table rather than a flag or a JSON key:

- **It is new information, not derivable.** "A person examined this coin's
  grade" cannot be computed from any other column, which is what distinguishes
  it from an `examined_at` flag that would merely restate what comparing a
  child to its parent already shows.
- **The unit of work is the field, not the item.** Attributing fifty Morgans
  means confirming grade on all fifty, then year on all fifty, in whatever
  order the light and the loupe allow. An item-level flag cannot express a
  half-done coin, and a half-done coin is the normal state.
- **`attributes` is the wrong home.** That JSONB column is documented as the
  long tail of profile output awaiting promotion to a real column. A
  first-class concept starting there would contradict the promotion rule.

This is complementary to, not a replacement for, comparing a child to its
parent. The comparison answers "what does the lot claim?" and is shown beside
each field in the edit form -- `Grade [AU58] · lot says BU`. The review record
answers "has anyone checked?". Both are needed, and neither derives the other.

The owner chose per-field over a single `examined_at` per item. The collection
will take months to work through, and "which fields have I already done on this
coin" is exactly the question that stops work being repeated.

## Repair operations

| Operation | Effect | Guard |
|---|---|---|
| **Split** | one item holding many becomes many; the parent is kept, marked `split_at`, excluded from every view | built |
| **Detach** | a child's `parent_item_id` becomes null and it stands alone | none needed |
| **Delete** | soft delete; the item leaves search | refuse if it has pieces or appears in an order |

**Most items have no parent and never will.** 7,591 of 7,591 have none today,
and lot purchases are the interesting case rather than the majority one.
Nothing in search, the views or valuation may assume a parent exists. Detach
sets `parent_item_id` back to null rather than moving the child anywhere,
because an item with no parent is complete, not orphaned.

**Splitting is the routine path for lots bought from now on**: receive the lot
as one item, decompose it, attribute the pieces. Detach and delete exist for
when a split was wrong.

### Soft delete

`inventory_item.deleted_at timestamptz null`, not a `disposition` value.
`disposition` records what happened to a coin -- held, listed, sold, shipped --
and "this row was created by mistake" is not something that happened to a coin.
Putting it there would corrupt every disposition report with rows that were
never real.

A separate column also lets `WHERE deleted_at IS NULL` sit beside the
`split_at IS NULL` already in all four views: same shape of rule, same place.

Search excludes deleted by default and accepts `?deleted=no|only|any`. An
unrecognised value is a 422, consistent with the existing rule that a filter is
never silently ignored.

**Known hazard.** After detaching its last child, a parent still has `split_at`
set, so it is invisible in every view and not deleted -- a row that exists and
cannot be found. Delete must therefore be reachable from the lot panel, not
only from search, because search is exactly where it is not.

## Search as a diagnostic

**Anomalies are named, kind-aware checks, not generic field filters.**
`?issue=no_grade` means *a coin or banknote with no grade*, because bullion has
no grade by nature and 712 rounds have no weight either. A generic `grade=null`
would bury the 2,965 real cases under rounds that will never have one. The
domain knowledge belongs in the check, defined once, rather than in the head of
whoever types the filter.

Each check appears three ways from one definition: a filter (`?issue=no_year`),
a facet (`no_year 1,230`, so the size is visible before committing), and a
badge on the row. Adding a check later means adding one predicate.

Initial checks: `no_year`, `no_grade`, `no_country`, `no_denomination`,
`no_weight_bullion`, `mixed_marker`, `zero_cost`, `kind_unknown`,
`repeated_identity`, `star_mismatch`, `malformed_serial`, `unreviewed`.

Two structural filters join them: `lot=CC-004120` (everything with that parent)
and `deleted=no|only|any`.

`mixed_marker` is separate from `no_grade` because it means "known to vary"
rather than "unknown". `Mixed` is the owner's marker for a lot attribute where
the individuals differ; the import correctly declined to make it a grade and
left it in `grade_raw`. The remedy is to decompose the lot, not to fill the
field.

## Identity and duplicates

`repeated_identity` covers the two fields that identify a *physical object*
rather than describe it: `currency_detail.serial_number` and
`item_certification.cert_number`. A PCGS or NGC number names one slab and a
serial names one note, so a repeat is real evidence.

**It must not be generalised to items without such a key.** Twenty Morgans
bought together at $19 each are identical in every available column and are
twenty different coins.

Measured, the check finds three problems that look alike and must not be
auto-resolved together:

| Shape | Example | Almost certainly |
|---|---|---|
| Same identifier, same order, same cost | 5 currency pairs | the same item entered twice |
| Same identifier, *different* orders | 32 cert numbers | one purchase recorded twice, or a mistranscription |
| Identifier that is not an identifier | `1973` on 5 rows | a year parsed into the cert field |

Only the shape of the value distinguishes them, so the check surfaces
candidates and a person decides. It never merges rows.

**A repeat is sometimes correct.** Four of the nine repeated-serial groups are
different notes -- different series or series letter -- sharing a serial
because collecting matched serials across issues is a deliberate pursuit.
`E00003333B` appears on a 2021 and a 2017-A $1, and the serial is itself a
repeating-digit note bought on purpose. A check that assumed repeats were
errors would fight the collection's own theme.

### Prefer a field with a structural guarantee over free text

`import_row.raw->>'Link'` carries the venue's own lot id, e.g.
`.../lot/226778844/1886-morgan-silver-dollar-ngc-ms66`. It is issued by the
auction house and means exactly one thing: **one lot, won once.** 63 lot ids
appear on more than one row; what separates the duplicates from the innocent
multi-item lots is a signal with no second reading:

| | Lot ids | Rows |
|---|---|---|
| All rows share one order date -- a genuine multi-item lot | 58 | 397 |
| **Rows carry different order dates** -- one lot recorded twice | **5** | **14** |

A lot of twenty coins arrives on one date. The same lot appearing on 9 January
*and* 12 January cannot be two purchases. Four of the five are one HiBid batch
entered three days apart; the fifth is a three-note set entered twice. Seven
surplus rows, $1,183.55.

Three earlier approaches each failed the same way, and the pattern is the point
rather than the arithmetic: grouping by `(order, description)` condemned the
Morgan rolls; grouping by repeated lot id condemned genuine multi-coin lots;
parsing counts out of the description broke on `$10 Morgan Silver Dollar Sealed
roll` (ten dollars of face value), `Silver Eagle Roll` (twenty coins) and `Red
Seal $1, $2 & $5 Set` (three notes). **Text written by an auction house to sell
something will not classify.** The lot id means one thing; the description
means whatever sold the lot.

**Exact matching is not enough.** Three further currency duplicates hide behind
single-character errors -- `O` for `U`, a dropped digit, `6` for `3`. The check
compares normalised identifiers (case folded, non-alphanumerics stripped) and
flags near-matches *within one purchase order*, which is where they cluster.

## Cross-field checks

Some anomalies are visible only as a disagreement between two fields, and they
are the most valuable kind because neither field looks wrong alone.

**Star notes are the worked example.** A star (replacement) note carries an
asterisk at the start or end of its serial -- never in the middle -- and there
is a `star` entry in `note_attribute`. Among the 1,015 notes with a serial:

| Asterisk in serial | `star` attribute | Count |
|---|---|---|
| yes | yes | 189 |
| no | **yes** | **5** |
| no | no | 821 |

The serial-pattern work closed the larger direction: there is no longer a note
whose serial carries an asterisk without the attribute. The five remaining
disagreements are one consecutive run, `R05945301A` through `R05945305A`.
Those serials are well-formed *non-star* serials -- letter, eight digits,
letter -- so the attribute is the likely error, but five notes in hand settle
it and no code should. This is not cosmetic: star notes carry a premium, so a
wrong attribute either overprices a note or sells a star note as an ordinary
one.

The general shape is worth reusing. Wherever a fact is recorded in two places
-- a marker inside free text and a structured attribute beside it -- the check
is that they agree, and the fix is to make the structured one authoritative.
Position is the reason: 179 asterisks are trailing and 10 leading, both meaning
the same thing, so a search keyed on punctuation silently misses ten notes. The
attribute is what searches use.

`app.serial_patterns.check` reports a malformed serial as a warning and never
refuses one. Three of the first four serials it flagged were valid notes the
rule had not anticipated, which is the argument for warnings: the collection is
more varied than any rule written in advance.

## Review and edit

**The queue is the search result, frozen at entry.** Search, get 23 Morgans,
enter review, walk them one at a time with previous/next and a progress count.

Freezing is required. If the queue re-ran the search at each step, fixing item
3's missing year would remove it from `?issue=no_year`, the set would shrink to
22, and every position after it would shift -- silently skipping an item.
Entering review captures the id list once; navigation walks that list; fixed
items stay visible, marked done.

Three renderings of one result set:

| | Table | Review | Workbook |
|---|---|---|---|
| Shows | many rows, few columns | one item, every field | many rows, every editable column |
| For | finding work | exceptions, one at a time | bulk correction, fill-down, find-and-replace |
| Edits | inline on the selected rows | full form, save and next | offline in Excel, imported as a diff |

The workbook is the third surface and it is deliberate: Excel is a better bulk
editor than any screen this project will build in reasonable time, and the
owner has years of muscle memory in it. `excel-roundtrip-design.md` specifies
it -- export a search, edit, import as a set of proposed changes reconciled
against `item_code` and `version`, dry run by default. Multi-valued columns
travel tilde-separated. That document owns the round trip; this one only
requires that the same search, the same filters and the same edit API stand
behind all three surfaces.

**The edit form shows the parent's value beside each field**, so it is always
visible what is being overridden and what is still only the seller's claim.
Confirming a field writes its `item_field_review` row.

## API

```
PATCH  /api/inventory/{id}           edit an item; optimistic via version, 409 on conflict
POST   /api/inventory/bulk           set fields across selected ids
POST   /api/inventory/{id}/reviewed  record which fields a person has confirmed
DELETE /api/inventory/{id}/parent    detach a child; parent_item_id becomes null
DELETE /api/inventory/{id}           soft delete, guarded
GET    /api/inventory/{view}/search  extended: issue, lot, deleted filters
```

`POST /bulk` is all-or-nothing in one transaction. A partial bulk edit across
50 coins leaves a state nobody can describe, and "which of the 50 applied?" is
not a question the UI should ever have to answer.

## Frontend

`Inventory.jsx` already carries search, filters, facets, sorting and paging.
Selection, bulk edit, review mode and an edit form would take it past 800
lines, so it gets split as part of this work rather than after, since every one
of those modules is touched anyway:

```
inventory/
  useInventorySearch.js   URL state, fetch, cancellation, facets
  InventoryTable.jsx      rows, sorting, selection
  FilterPanel.jsx         text, facet selects, issue checks
  BulkEditBar.jsx         appears with a selection
  ReviewPane.jsx          one item at a time, previous/next
  ItemEditForm.jsx        fields, parent value hints, review marks
  specs.js                the coin and currency column and filter specifications
```

## Where valuation gets its numbers

Attribution exists to make valuation possible, so the sources it will draw on
belong here even though the pricing work is document B.

**The approach is cost-plus.** The asking price is derived from what was paid
and a markup the owner chooses, informed by a wholesale reference. The derived
number is the owner's own and is his to publish; the reference behind it may
not be. That distinction decides the design.

### The standing rule

> Ensure it is okay to use any data we retrieve -- avoid copyrighted or
> proprietary data.

This applies to everything the system ingests, and it has already shaped the
schema twice. A **fact** may be seeded: which offices two people held, what a
series is called, what collectors nickname it. A **publisher's arrangement**
may not: Friedberg's numbering, Pick's numbering, the contents of a price
guide. The test is whether the thing was discovered or authored.

### Friedberg numbers stay supported, not populated

`friedberg_number` models the identifier fully -- `fr_number`, `base_number`,
`note_type_id`, `series_year`, `series_letter`, `seal_color_id`,
`signature_combination_id`, `size_class` -- and stays empty, for two reasons
pointing the same way. Exactly **3** of 7,591 descriptions cite an Fr. number,
so there is nothing to extract; and the numbering comes from Friedberg's
*Paper Money of the United States*, so shipping a seeded mapping would be
republishing a copyrighted work. The field is filled during attribution, from
the owner's own copy or from a slab label, exactly as a certificate number is.

The same reasoning stops a currency *series* vocabulary being invented. Note
classes are already in `note_type`, the specific issue is the Friedberg number,
and "Funnyback" names a design while "Horse Blanket" names a size era --
`size_class` already separates those axes. A made-up list would mix them and be
unusable against any published guide.

### The CDN Public API v2

CDN (Greysheet) publishes a REST API over its catalogue and price guides. It is
the right shape for this project for a reason worth stating plainly: **it turns
the catalogue from a table we would have to copy into a service we call.** The
Friedberg problem above is a copying problem, and a licensed lookup does not
have it.

Access requires a paid dealer subscription -- Coin Dealer Digital or Coin
Dealer for basic access, Dealer+ or Pro for advanced -- and API calls are
billed by usage separately from the subscription. Coverage includes US paper
money, which is the half of the collection with the weakest catalogue support
today.

The licence is workable for cost-plus, and its terms map onto the design
directly. The material clauses, as published:

| Data | What the licence permits |
|---|---|
| **Greysheet / Bluesheet / Greensheet wholesale values** | back-end internal systems only; **not permitted on any public website** without CDN's prior written permission |
| **CPG retail values** | may appear on a front-facing website or collection-management tool, provided values are labelled as sourced from CDN |
| **GSID numbers** | usable in back-end and front-end systems; when displayed publicly, `GSID` must be shown with a hyperlink to the matching record |
| **Caching** | internal caching up to 24 hours, and not past the refresh time or end of business day, 3PM Pacific; data must not be stored with the intent of avoiding future calls |
| **Redistribution** | no reproduction, distribution or publication without express prior written consent; no sale, lease, sublicence or transfer of API access |

Five consequences, none of them onerous:

1. **Wholesale values are an input, never a display.** They feed the markup
   calculation server-side. The number the store shows is the owner's own
   asking price. This is exactly what cost-plus wants, so the strictest clause
   costs nothing.
2. **The store may show CPG retail beside the asking price, labelled as CDN's.**
   That is a genuinely useful thing to show a customer, and it is licensed.
3. **There is no `greysheet_price` table.** The caching clause forbids the
   obvious design -- a nightly refresh into a reference table -- because that
   is storage to avoid future calls. Prices live in an expiring cache with a
   TTL no longer than 24 hours and no later than the day's refresh, and the
   cache is not part of the backup.
4. **A GSID may be stored on an item.** It is an identifier, like a
   certificate number, and storing it enables a call rather than avoiding one,
   which is the use the licence describes. Displayed publicly it carries the
   label and the link.
5. **`valuation_snapshot` needs care.** It exists to make a past valuation
   reproducible by recording its inputs, and a CDN value recorded there
   outlives 24 hours by design. The conservative reading is to snapshot the
   inputs the project owns -- cost basis, metal spot price, the markup applied
   -- plus the *derived* value and a note of which CDN basis and timestamp were
   used, without copying the CDN figure itself. If retaining the figure is
   wanted for audit, that is a written-permission question to put to CDN
   rather than a judgment call to make here.

**Before any of this is built, the current Terms of Use and License Agreement
must be read in full against the subscription actually purchased.** The summary
above is from the published terms and is enough to design against; it is not
enough to rely on, and the terms may change.

Sources: [CDN Public API V2 Usage
Guide](https://www.greysheet.com/cms/1049/cdn-public-api-v2-usage-guide) ·
[API Terms of Use and License
Agreement](https://www.greysheet.com/cms/1053/api-terms-of-use-and-license-agreement)
· [CDN Public API Pricing](https://www.greysheet.com/publications/api-pricing)

### Metal spot prices are separate and simpler

Melt value is already computed from `metal_price`, a time series rather than a
current-value column, so a past valuation stays reproducible. Spot prices are
widely published as bare facts and carry none of the above constraints. Bullion
-- 712 items with no weight recorded, and every round and bar besides -- is
valued this way and needs no price guide at all.

## Testing

Follows the existing practice of proving a guarantee by removing it and
confirming the test fails.

- **Split creates detail rows.** Every child has exactly one detail row, of the
  kind its `item_kind` implies. Remove the creation and this must fail.
- **Splitting conserves money.** The pieces' `item_cost` and `shipping_cost`
  sum exactly to the parent's, and total cost basis across the collection is
  unchanged to the penny. `total_cost` may differ by a cent or two because
  `sales_tax` is generated per row; the difference is reported, not absorbed.
- **An unlisted item is editable.** `PATCH /api/inventory/{id}` succeeds on an
  item with no listing -- which is every item today.
- **Optimistic conflict.** Two edits from one loaded state: the second gets a
  409. Remove the version check and this must fail.
- **Queue stability.** Enter review on `issue=no_year`, fix the first item,
  confirm the queue still has the same members in the same order. Remove the
  freezing and this must fail.
- **Bulk atomicity.** A bulk edit where one id is invalid changes nothing.
- **Soft delete leaves search.** A deleted item is absent by default, present
  with `deleted=only`, and absent from all four views.
- **Delete guards.** Deleting a parent with pieces fails; deleting an item in
  an order fails. Both must fail *for the stated reason*, not incidentally.
- **Detach round trip.** Split an item, detach one child, delete the parent
  once childless; the remaining children stand alone and cost basis is
  unchanged.
- **A review mark survives an edit** and is scoped to one field: confirming
  `grade_id` does not mark `year_start`.

## Migration

1. Add `item_field_review`.
2. Add `inventory_item.deleted_at`; add `AND i.deleted_at IS NULL` to all four
   views.
3. Fix `split_item` to create the detail row.
4. Verify cost basis is unchanged: `sum(total_cost)` over live items must still
   be $534,177.89.

Nothing in this migration creates a row holding money, and nothing recomputes
one.

## Open questions

- **The twelve conglomerates' piece counts.** They hold 240 pieces between
  them, but the count came from `piece_count`, parsed from the spreadsheet.
  Worth checking against the descriptions before splitting on them.
- **Seven surplus rows, $1,183.55**, across five auction lots. Identified;
  needs confirming against the physical collection -- one 1886 Morgan MS66 or
  two -- before anything is deleted.
- **Five star attributes with no asterisk**, one consecutive run. Needs the
  notes in hand.
- **Certification numbers are not reliable as identifiers.** 48 numbers repeat
  across 108 rows. The largest cases sit across two eBay orders six months
  apart with different totals -- two genuine purchases whose seller reused one
  listing template across identical PR69DCAM sets. The numbers were copied from
  listing text, so they identify the *listing*, not the slab. They want
  re-reading from the physical slabs, or clearing.
- **eBay order numbers from the PDFs.** 3,053 eBay orders are recorded, but the
  earliest rows predate the owner recording order numbers at all.
  `OneDrive/Documents/coins` holds 1,020 `ebay_*.pdf` purchase pages and the
  numbers extract cleanly -- a 25-file sample yielded 545 distinct ones, median
  25 per file. The work is the matching, not the extraction: a purchases page
  lists order number, title, price and date together and each must be paired to
  the right row. That wants the discipline the series matcher uses -- apply the
  confident matches, report the ambiguous, never resolve a tie by taking the
  first. Its own piece of work.
- **Whether the CDN subscription is worth buying yet.** It is a recurring cost
  plus per-call billing, and it is only useful once items are attributed well
  enough to look up. That argues for finishing attribution first and treating
  the API as document B's opening move.
