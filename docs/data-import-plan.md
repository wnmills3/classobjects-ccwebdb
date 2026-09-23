# Data import and the collection record: current state

Current state as of 2026-09-23. The history is in git
(`git log -p docs/data-import-plan.md`).

This document says where the collection's data came from, how it got into the
database, and how the collection record is structured and kept correct now.
It is edited in place when something changes. Where it and the code disagree,
the code is right and this document is the bug.

Related documents: [database-design.md](database-design.md) (the schema in
full), [spreadsheet-import-design.md](spreadsheet-import-design.md) (the
importer's own design), [system-administration.md](system-administration.md)
(operating procedures), and the specs under `docs/specs/`.

---

## 1. The database is the record

Since 2026-09-16 the `ccwebdb` database is the system of record for the
collection. The workbook `wnm3_coins.xlsx` is a historic reference and is not
imported again. In the owner's words: "at this point we can think of our
database as the ground truth and continue to improve the data there. the
spreadsheet can be a historic reference document." They repeated it on
2026-09-22: new data and updates go into the database, and the workbook is not
needed again.

What follows from that:

- **New acquisitions are entered in the owner console** (New purchase, then
  items on it), not typed into the workbook.
- **Corrections are made in the console, or by a pass over stored items**
  (§7.9). A pass fills what is empty, never overwrites what a person set, and
  records what it filled as derived.
- **A rebuild from the workbook is never used to fix live data.** It would
  discard everything corrected or entered since: series assignments, derived
  attributes, receipts, the zero-tax Whatnot orders, photographs, sales
  history, user accounts.
- **Recovery from a data problem is a restore from a verified backup** (§11),
  not a re-import.

`scripts\ccweb_rebuild.cmd` still exists, for a **new** collection only. It
builds a separate database, `ccwebdb_rebuild`, alongside the live one and never
touches `ccwebdb`: migrate, load reference data, import the workbook, run
`series_match`, `classifier_defaults`, `series_classify` and `serial_patterns`,
then the demo seed last. The importer is kept correct for that use: the rating
rules it applies (`app/importers/rating.py`) are the same ones the rating pass
applies to stored items.

---

## 2. Where the data came from

None of these files is in the repository.

| Source | Location | Used for | State |
|---|---|---|---|
| Collection workbook | `C:\Users\wnmil\OneDrive\wnm3_coins.xlsx` | every item, purchase and vendor in the collection | imported; now historic |
| Vendor PDFs | `C:\Users\wnmil\OneDrive\Documents\coins\` (about 1,100: `ebay_*`, `hibid_*`, `liveauction_*`, `probid_*`, `aabid(s)_*`, plus `whatnot_*.csv`) | cross-checking purchases | **not parsed**; see §12 |
| Whatnot order report | `C:\Users\wnmil\OneDrive\Documents\whatnot-order-report.xlsx` (72 orders; its sheet is misnamed `ebay-order-report`) | which Whatnot orders were charged no sales tax | applied by hand in the database |
| Safe-deposit photographs | `C:\Users\wnmil\OneDrive\Documents\coins\Classified\SafetyDeposit804` and `...\SafetyDeposit809` (673 JPEGs, about 2.0 GB) | item photographs | **not imported**; see §9.5 |

**The canonical workbook is that exact path.** Seven files on the machine are
named `wnm3_coins.xlsx`; the others are stale snapshots, some only days older,
and `OneDrive\` also holds timestamped `wnm3_coins.backup-*.xlsx` copies. Match
the exact name, never a prefix. `system-administration.md` (*The canonical
spreadsheet*) has the detail.

**The Whatnot report's `processed date` is the settlement date, not the
purchase date.** Do not "correct" purchase dates from it.

---

## 3. What the workbook contained

One sheet, about 7,600 data rows (the count moved with every edit), purchases
from February 2024 onward across more than ten vendors. No formulas. Its final
layout, after the owner restructured it on 2026-09-15:

| Column | Became |
|---|---|
| `Ordered` | `purchase_order.ordered_on` |
| `Order Number` | `purchase_order.order_number` (text) |
| `Denom` | item kind, denomination, bullion or set form, storage form, piece count; kept verbatim in `denom_raw` and `source_title` |
| `Year` | `year_start`/`year_end`, mint marks (coins) or series year and letter (notes); kept in `year_raw` |
| `Rating` | grade, strike type, designation, grading service, attributes, authenticity; kept in `grade_raw` |
| `Price` | `item_cost` |
| `Shipping` | `shipping_cost` |
| `Link` | `listing_url`, and the vendor's transaction id where the URL carries one |
| `Description` | `description` (full-text indexed; no other authority) |
| `Vendor` | `vendor` |
| `Grading#` | a note's serial (currency) or a certificate number (everything else) |
| `Value` | `numismatic_value` — an appraisal, and only that |
| `My Rating` | `notes_raw` — the owner's own grade in their shorthand |
| `Received` | `item_status` (§4.3) |
| `Taxes`, `Total Cost`, `Profit`, `Profit %` | ignored — headers over nearly empty cells |

`My Rating` was called `Comment` before 2026-09-15. The importer reads the new
name; reading the old one would have dropped about 2,800 of the owner's own
assessments without a word, because a missing column reads as an empty cell.

---

## 4. How the workbook was imported

### 4.1 One seam: a durable engine, a disposable profile

```
backend/app/importers/
  engine.py, loader.py, sources.py, models.py, profile.py, rating.py   durable
  profiles/collection_v1.py                                            disposable
```

Everything specific to this one workbook — its column names, the `Denom`
typo map, the `Received` markers, `Grading#` routing — lives in
`profiles/collection_v1.py`. The engine and loader know nothing about coins;
they stage rows, hand each to the profile, and write what comes back into the
schema. Replacing the profile does not touch them. That is deliberate: this
workbook's conventions do not generalise, and a later generic import facility
(upload, map columns, rules as data) will replace the profile, not the engine.
A new collection in a different layout therefore needs its own profile, or
that facility.

### 4.2 Two stages, dry run by default

```
workbook ──► import_row (verbatim, JSONB) ──► inventory_item and friends
                   │
                   └──► import_issue  (and issues.csv / corrections.csv /
                                       unclassified.csv under logs\import\)
```

- `import_batch` records the file's sha256, row count and mode.
- `import_row.raw` holds the whole source row as `JSONB`, so a change in the
  sheet's shape needs no migration. `import_row.inventory_item_id` links each
  staged row to the item it produced; that join, not arithmetic on item codes,
  is the authoritative way back to a workbook row.
- `python -m app.importers.cli --file <path>` is a dry run that touches no
  database and writes the review CSVs. `--commit` writes.
- There is no console screen over `import_issue`; the CSVs were the review
  surface, and the console's named diagnostics (§7.9) are the ongoing one.

### 4.3 Rules that matter

**Classification order.** Bullion keywords, then set keywords, then medal and
token, then currency (`Bill`/`Note`/`Fractional`, or a leading `$`), then a
bare number or a number in a coin-only unit is a coin, else `unknown` for
review -- including a number followed by a word none of those rules know. The
full list is in [spreadsheet-import-design.md](spreadsheet-import-design.md).
Bullion and sets come first so `1oz Copper Round` or a Panda's "10 yuan" is
never read as currency.

**Arrival comes from `Received` only.**

| `Received` cell | Status |
|---|---|
| `x` | `received` |
| blank | `ordered` — the absence of the mark is what the column says |
| `canceled` / `returned` / `missing` | that status |
| `counterfeit` | authenticity `counterfeit`; status left as received |
| anything else | `ordered`, warned `received-not-understood` |

An unreadable cell reads as *not* arrived on purpose: an item wrongly left
`ordered` is received in the console in one click, while one wrongly marked
`received` drops silently out of everything that asks what is outstanding.
A status marker left in `Value` is reported (`status-marker-in-value-column`),
not obeyed — two columns both setting status is how they come to disagree.
`Counterfeit` in `Value` still sets authenticity, with a warning.

**`Description` has no authority over status, errors or anything else.** It is
seller copy. Scanning it for `cancel|return|missing|lost` matched hundreds of
rows, nearly all false: auction boilerplate ("NO CANCELLATIONS"), product names
("Lost Coins"), set contents ("MISSING 1991"). It feeds full-text search only.

**`Grading#` is routed by kind.** On a banknote it is the note's printed serial
(`currency_detail.serial_number`); on anything else a certificate number
(`item_certification`, one row per number, since some cells list several). A
bare eight-digit value is ambiguous on its own; the kind decides. An arrival
marker typed there (`x`, `canceled`) is warned and not stored; a word saying
the identifier is absent (`missing`, `none`) is warned and not stored, because
a note with no printed serial is an error on the note, not a serial.

**Identifiers are text, always.** Order numbers, serials and certificate
numbers are never re-typed as numbers. Damage the workbook already did cannot
be undone: about 1,000 `Order Number` cells had been stored as Excel integers,
losing leading zeros, and six `Grading#` values survive only as scientific
notation (`5.0157E+14` and the like). The importer flags these
(`identifier-lost-to-scientific-notation`) rather than storing a rounded lie;
recovery is from the slab or a vendor document.

**`Year` means two different things.** On a coin, `1921-P` or `2019-P/D/S` is
a year and one or more mint marks. On a note, `2017-A` is a series year and a
series letter, which is not a mint mark and is stored separately. Decades
(`1980's`), uncertain years (`2024?`), open ranges (`1953-`) and multi-mint
sets (`1989-P&D`) are real distinctions, not typos, and are not normalised
away.

**Compound ratings are decomposed** (`app/importers/rating.py`): `PR69DCAM
PCGS` is a strike type, a grade, a designation and a grader. Unrecognised text
stays in `grade_raw` and `attributes.rating_unparsed`. `Mixed` means "known to
vary", not "unknown", and is never turned into a grade.

**A value outside the vocabulary is declined, not invented.** An early version
created a new reference row for every unmatched string and produced 185 junk
grades. Now an unmatched value stays in its `*_raw` column and is reported.

**Purchase orders are grouped by a real identifier or not at all.** A row with
an order number joins that vendor's order. Without one, the vendor's own
transaction id in the item URL identifies the purchase (a HiBid, Proxibid or
LiveAuctioneers lot, an Etsy receipt). An eBay item number names a *listing*,
not a purchase, so it groups rows but is never written into `order_number`. A
row with neither gets no purchase order. An earlier version keyed on
`order_number or ""`, which collapsed every numberless row from a vendor into
one fabricated order — 1,922 eBay purchases over two years into a single
order; that is why "unknown" is never treated as a value.

**Composition is filled from the facts at import** (§6.2), and recorded as a
derived default so a later pass may refresh it.

### 4.4 Item codes

Every item gets a permanent code, `CC-000001` onward, from a database
sequence: assigned once, never reused, never changed, surviving sale, return
and relisting. Codes were issued in workbook row order. The workbook was
re-sorted before the 2026-09-15 import, so **1,121 codes then named a
different purchase than before**; any `CC-` number quoted outside the database
from before 2026-09-15 may not mean what it did. That cost is not paid again,
because the workbook is no longer imported into live.

`python -m app.seed` creates demo items. Run before an import it takes
`CC-000002` onward and offsets every real code, so the rebuild runs it last.

### 4.5 The last import into live

The 2026-09-16 rebuild is the last time the workbook reached the live
database: 7,660 items, $536,118.82 cost basis, 2,366.253 ozt fine metal. Five
of those were demo items, deleted on 2026-09-17. Figures have moved since
through passes and entry; query, don't quote.

---

## 5. The inventory model

### 5.1 One table for every kind of item

Coins, banknotes, bullion, sets, medals and tokens all live in
`inventory_item`, with 1:1 `coin_detail` and `currency_detail` tables for what
differs. Separate tables were considered and rejected: the split is not two-way
(bullion and sets would be homeless), the shared fields dominate (purchase,
cost, grade, certification, status, location, images), most purchase events
mix kinds, a sale must reference any item through one foreign key, and an item
whose kind is not yet known still needs a row. The `coin_inventory` and
`currency_inventory` views present two inventories, though as built nothing
reads the views: search queries the base tables for speed
(`app/inventory_search.py` explains why).

PostgreSQL is the store, with `JSONB` for genuinely variable data
(`import_row.raw`, `inventory_item.attributes`) and bytes in object storage.
A document store was considered and rejected: foreign keys, exact decimal
money, generated columns, constraints and transactions are all relied on.
**Promotion rule:** anything filtered, sorted, joined or aggregated on earns a
real column or reference table; `attributes` is for the long tail.

### 5.2 What an item row holds

| Group | Columns |
|---|---|
| identity | `item_code`, `version` (optimistic concurrency) |
| acquisition | `purchase_order_id` (nullable) |
| classification | `item_kind_id`, `denomination_id`, `country_id`, `series_id`, `bullion_form_id`, `set_form_id`, `storage_form_id`, `piece_count`, `year_start`, `year_end` |
| condition | `strike_type_id`, `grade_id`, `grade_designation_id`, `grading_service_id`, `authenticity_id` |
| lifecycle | `status_id`, `disposition_id`, `storage_location_id` |
| lineage | `parent_item_id`, `split_at`, `deleted_at` |
| description | `source_title`, `description`, `listing_url`, `local_catalog_number` |
| verbatim source | `denom_raw`, `year_raw`, `grade_raw`, `notes_raw`, `weight_raw` |
| cost basis | `item_cost`, `shipping_cost`, `tax_rate`, `tax_includes_shipping`, `sales_tax` (generated), `total_cost` (generated) |
| value and metal | `numismatic_value`, `valuation_basis_id`, `composition_id`, `metal_id`, `fineness`, `gross_weight_ozt`, `fine_weight_ozt` |
| long tail | `attributes` (JSONB), `source` (seeded / derived / manual) |

`coin_detail` holds the mint, variety and PCGS type. `currency_detail` holds
note type, series year and letter (with a generated `series_designation` such
as `1935A`), seal colour, signatures, Federal Reserve district, serial number,
Friedberg number and status, and face and back plate numbers and plate
position (text, because plate designations carry letters). Every detail column
is nullable.

**`piece_count`, not a storage quantity.** A roll or tube bought as one thing
stays one row with a piece count, and every weight and valuation multiplies by
it.

**Lots and pieces.** `POST /api/inventory/{id}/split` breaks a lot into one
child per piece, dividing cost `equal`ly (identical pieces) or `relative` to a
value supplied per piece (a mint set's cent and half dollar). The parent is
kept — it holds the purchase order, the price actually paid and the code a
receipt names — and marked `split_at`; everything that counts inventory or
money excludes it, or the collection would appear to cost twice what it did.
The console has no Split panel yet (§12).

**Soft delete.** `deleted_at` means "this row should never have existed" (a
typo, a duplicate). It is not how an item leaves the collection. A lot with
pieces, and any item that has ever been listed, cannot be deleted.

**Purchase lots were not reconstructed.** Grouping rows that share a
description into parent lots was planned and dropped: shared descriptions come
from copy-and-paste in the workbook and are being replaced, so they are not a
signal. The repaired purchase orders (§4.3) are what "bought together" means.

---

## 6. Money

### 6.1 Cost basis is stored, and sales tax is stamped per row

```
sales_tax  = round((item_cost + CASE WHEN tax_includes_shipping
                                     THEN shipping_cost ELSE 0 END) * tax_rate, 2)
total_cost = item_cost + shipping_cost + sales_tax
```

Both are `GENERATED ALWAYS AS ... STORED`: they cannot drift from their inputs
or be written by mistake. Money is `NUMERIC(12,2)` and `Decimal` throughout; no
floats anywhere.

`tax_rate` and `tax_includes_shipping` are **columns on each item, copied from
the settings `SALES_TAX_RATE` (default 0.0635) and
`SALES_TAX_INCLUDES_SHIPPING` (default true) when the item is created, and
never read from the settings again.** Tax paid is a historical fact, so a
change to a setting governs later purchases and rewrites none before it.
`tax_rate` has no server default on purpose: a copy of the rate in the schema
would be a second source of truth. A purchase charged no tax has `tax_rate` 0
(the **No sales tax charged** box in the item editor, or bulk edit).

The workbook carried no usable tax data, so imported items took the default
rate. The Whatnot orders that were charged no tax were set to 0 in the
database afterwards, from the Whatnot report; they exist nowhere else, which
is one of the things a rebuild would destroy. A rate cannot reproduce a
marketplace's own rounding (Whatnot charged $0.35 where 6.35% of $5.40 is
$0.34); those pennies stay as computed.

**Profit is not a column.** It depends on current value, which moves.

### 6.2 Weight, composition and fine metal

Weight is stored in troy ounces as `NUMERIC(12,6)`, never a float, because it
multiplies into money. `gross_weight_ozt` is the whole piece;
`fine_weight_ozt` is the precious-metal content **per piece**, the melt input.
`weight_raw` keeps what was written (grams, kilos, pounds convert on the way
in). Copper rounds are often sold by the avoirdupois ounce, 9.7% lighter than
troy; record the unit the source meant rather than assuming.

`composition` holds US coinage composition by denomination, country and year
range, seeded from published mint specifications (`composition.json`, with
the published ASW figures). It resolves from what the item already is, so
nobody types that a 1963 dime is 90% silver. The importer fills metal,
fineness and weights from it; `app.classifier_defaults` refreshes them when an
item's facts change; a person's value always stands. Bullion forms carry a
typical metal, fineness and fine weight of their own.

**Fine metal for the collection is `sum(fine_weight_ozt * piece_count)`**
over live rows (`split_at IS NULL AND deleted_at IS NULL`). The unweighted sum
reads about 10% low.

### 6.3 Value is computed: melt versus numismatic

A common-date silver coin is worth its metal, which moves daily; a key date in
high grade carries a premium unrelated to spot. One stored number cannot be
both, and a stored melt figure is stale the next morning.

- `numismatic_value` is the owner's appraisal (the workbook's `Value` column,
  and edits since).
- `valuation_basis` is `melt`, `numismatic` or `manual`. At import it was set
  to `melt` where a fine weight resolved and no appraisal was given, otherwise
  `numismatic`.
- The `item_valuation` view computes
  `melt_value = fine_weight_ozt * latest spot * piece_count`, then
  `reported_value` by basis, `profit` and `profit_pct`, from the latest
  `metal_price` row per metal.
- `valuation_snapshot` records a point-in-time value with the spot price used,
  so a past figure stays reproducible.

**As built, melt value is always empty**: nothing writes `metal_price` (no
spot-price feed has been chosen), nothing writes `valuation_snapshot`, and no
endpoint reads `item_valuation`. The model is in place; its inputs are not.

---

## 7. Classification and reference data

### 7.1 Principles

- **Classifiers are foreign keys to reference tables, never free text.**
  Search and filtering are standardised because of it.
- **Raw text is kept beside every parsed value** (`*_raw`), so a parser's
  mistake is always recoverable.
- **Every reference row says where it came from:** `seeded` (shipped fact or
  vocabulary), `derived` (learned by a rule), or `manual` (one owner's
  decision). Machine guesses never pass for curated facts.
- **Reference data is shipped as versioned JSON** under
  `backend/data/reference/` and loaded with `python -m app.seeding load`
  (idempotent). Foreign keys travel as codes, not ids. `export --source ...`
  writes it back; only `seeded` is exported by default, so one collection's
  guesses do not leak into another installation's vocabulary. A hand-edited
  row is never overwritten by a load.
- **Only facts are seeded, never a publisher's arrangement.** Office holders
  and their terms, design series and their years, mint specifications and
  legislated compositions, and common nicknames are safe. Friedberg numbering,
  Pick numbering, price-guide values and any catalogue's mapping of attributes
  to its own numbers are not. Seed files carry a `_comment` naming their
  sources.
- **Standard terminology first** — the US Mint glossary, then the grading
  services' published terms, then usage agreed by independent sources.

### 7.2 Vocabularies as built

| File | Tables |
|---|---|
| `classification.json` | `item_kind` (coin, currency, bullion, set, medal, token, other, unknown), `bullion_form`, `set_form`, `storage_form` |
| `issuer.json` | `currency`, `country`, `denomination` (with `kind`: coin or note — the same face value exists as both), `mint` |
| `condition.json` | `grade_scale`, `strike_type`, `grade`, `grade_designation`, `grading_service`, `authenticity`, aliases |
| `banknote.json` | `note_type` (BEP's class names), `seal_color`, `fed_district`, aliases |
| `signatures.json` | `signature_combination` (Treasurer and Secretary, with terms) |
| `note_issue.json` | `note_issue` (small-size notes, Series 1928–2021) |
| `series.json` | `series`, `series_alias`, `series_year_range` |
| `composition.json` | `composition` |
| `attribute.json` | `item_attribute`, aliases |
| `error_type.json` | `error_type` |
| `operations.json` | `metal`, `valuation_basis`, `item_status`, `disposition`, `storage_location_kind`, `image_role`, `vendor_kind`, `sales_venue_kind`, `carrier`, `sales_order_status`, `shipment_status` |

In the console, pickers offer only values that fit the item's kind (a metal is
never offered for a banknote; coin and note denominations are kept apart, and
the API refuses a mismatch). Pickers are alphabetical except the scales and
lifecycles, which keep their natural order. A missing attribute or error type
is added by typing its name. The **Vocabularies** page renames, retires and
merges values, and moves a value within a sequenced vocabulary; it does not
create values -- a new one is added from a field's picker.

**Retire or merge, never delete.** Retiring stops a value being offered.
Merging (`app.reference_merge`) moves every item that holds a value onto the
one kept, turns the old label, code and aliases into aliases of the survivor,
and records the merge so a later seed load does not bring it back. A value
used by another vocabulary or a facts table refuses to merge. Values the
application looks up by code (such as the image roles) cannot be retired or
merged.

**Aliases.** The label is the standard term; what people write — UCAM, Legal
Tender, Mercury, Godless — is an alias, in `series_alias` for series and
`reference_alias` for every other vocabulary. Search, the importer and the
rating pass all read them. A removed seeded alias is retired rather than
deleted, so a load does not restore it.

### 7.3 Grades are a number and a strike type

`PR69+` is strike type `proof` with grade `69+`; `MS65` is `business` with
`65`. A strike type is a way of making the coin, not a rank, so it is its own
column. `grade.grade_rank` (generated) places `64+` between 64 and 65. Note
grades are their own scale. Adjectival grades are folded into numbers at the
bottom of their range, with the owner's ladder for BU and UNC: plain 60, one
plus 63, two pluses 65. `app/grades.py` splits and displays compound grades,
and the database mirrors the display in `grade_display()`.

### 7.4 Series

A design series (Morgan Dollar, Winged Liberty Head Dime, Funnyback) is a
foreign key on the item itself, so faceting stays fast and notes can carry one
too. `series_year_range` holds each run of years at a denomination (the Morgan
is 1878–1904, 1921 and 2021 on) and, for notes, the letters allowed. Designs
that share a face value and years with a commoner one (Hawaii and North Africa
notes, commemoratives) are marked `needs_evidence` and assigned only when the
text or the seal colour says so; the Series 1929 National Bank Notes are told
from the Federal Reserve Bank Notes by note class.

Two passes assign series, both report-only unless given `--commit`, and
neither touches an item that already has one:

- `app.series_match` — coins, by the design their text names;
- `app.series_classify` — what the text left, and all notes, from
  denomination and year (and series letter), with text and seal as evidence.
  Boundary years and conflicts go to a review list.

`docs/specs/series-classification-design.md` has the facts and sources.

### 7.5 Note facts and classifier defaults

`note_issue` records, once, what each small-size issue was: denomination,
series year and letter, class, seal colour, signatures, variant (Hawaii,
North Africa) and the serial's series-letter prefix where there is one. Every
row is backed by at least two independent public sources, named in the file.
It carries no catalogue numbers and no values.

`app.classifier_defaults` (and the console, as an item is created or saved)
fills what follows from facts a person entered:

| Filled | From |
|---|---|
| note class, seal, signatures | denomination, series year and letter (`note_issue`) |
| Federal Reserve district | a Federal Reserve Note's serial |
| composition, metal, fineness, weights | a coin's denomination, country and year |
| No Motto | a $1 Silver Certificate of Series 1928–1935F (1935G needs evidence) |

A field is written only when the facts allow exactly one value. A value a
person or the workbook recorded is never replaced, though it narrows the
facts (a red-seal $1 Series 1928 is a United States Note). Ambiguous,
disagreeing and unknown-issue cases are reported by item code. Filled values
show a *suggested* mark in the item editor.

**A series year is the design year, not the signing year.** Lettered series
carry later signers, so the signatures a note can carry come from
`note_issue`, never from which Treasurer and Secretary held office in the
series year.

### 7.6 Item attributes

What an item *is* beyond its grade is an `item_attribute`, linked many-to-many
through `item_attribute_link`, for coins and notes alike. Each attribute has a
group — `serial` (Star Note, Radar, Fancy Serial), `variety` (No Motto, Mule),
`release` (First Strike, Early Releases, First Day of Issue), `verification`
(CAC), `qualifier` (Details, Genuine, NET) — and says which kind of item it
fits. Each link says whether a rule or a person made it (`derived` with
`derived_by`, or `manual`).

**A removed attribute stays removed.** A person removing one sets
`removed_at`; the row stays so that no rule adds it back.

Sources of derived links: the importer (from the rating), `app.rating_pass`,
`app.classifier_defaults` (No Motto), and `app.serial_patterns`, which reads
star, radar, repeater, binary, solid, ladder and low-serial designations from a
note's serial. Patterns need a full eight-digit serial; a short one is
reported as incomplete, not read. `consecutive` is never derived, since no
single serial can show a run.

### 7.7 Errors

Mint and printing errors are rows in `item_error` (error type plus free-form
details, many per item — a miscut note can also have an offset), with
`error_type.applies_to` keeping struck and printed errors apart. Errors are
set by a person, in the item editor, New item or Receiving; they are never
inferred from description text.

### 7.8 Identification

Four things that are routinely conflated are kept apart:

| Concept | Scope | Where |
|---|---|---|
| grading service | who certified it | `inventory_item.grading_service_id` |
| certificate number | one holder | `item_certification.cert_number` (many per item) |
| note serial | printed on the note | `currency_detail.serial_number` |
| type number | every item of a type | `friedberg_number`, `pcgs_type` |

**Certificate numbers are unreliable as identifiers** in this collection: some
repeat because a seller reused a listing template.

**Friedberg numbers are the owner's own, never a shipped table.** The
Friedberg numbering is a copyrighted arrangement, so `friedberg_number` holds
only numbers the owner has read off their own notes and slabs (source
`manual`). The owner records one from the item editor or Receiving; a lookup
searches that private catalogue by what is visible on the note (denomination,
series, class, district, signatures, web press) and proposes candidates;
attaching one sets `friedberg_status` to proposed or confirmed. The identifying
tuple -- denomination, series year and letter, note type, district, web press,
signatures and seal -- is unique among rows whose denomination, year and note
type are known, `NULLS NOT DISTINCT` (without that, a series with no letter
could be recorded twice under two numbers). Signatures and seal belong to it
because many series differ by nothing else. `fr_number` is unique outright so
a licensed dataset could be merged later.
A match is shown as its number with a Copy button into the field. When the
catalogue has no match, Look up opens a Google AI Mode search for that note in
a pop-up window; the owner reads the answer and types or pastes the number,
saving it as proposed until checked. **The software never fetches, parses or
stores search results** (owner's ruling, 2026-09-23), since that would
harvest the catalogue's arrangement into a product that is sold.

`pcgs_type` exists with the same shape for coins and is unused: nothing
captures a PCGS number yet (§12).

### 7.9 Who set a field: provenance per field

| Table | Meaning |
|---|---|
| `item_field_source` | this field holds a default a pass derived (`derived_by`: composition, rating, a classifier rule); a pass may refresh it. Saving the field by hand deletes the row. A row marked `held` means a person emptied the field on purpose and no pass may fill it |
| `item_field_review` | a person confirmed this field by looking at the object |

No `item_field_source` row means the value is a person's or came with the
data, and no pass touches it. The two tables answer different questions, and
a derived value can also be confirmed.

**The passes over stored items**, each report-only by default and writing only
with `--commit`, each respecting the rules above:

| Pass | Does |
|---|---|
| `app.classifier_defaults` | note class, seal, signatures, district, composition, No Motto (§7.5) |
| `app.series_match` | series from text (coins) |
| `app.series_classify` | series from facts |
| `app.serial_patterns` | serial designations |
| `app.rating_pass` | reads stored ratings again with the current rules; fills empty grade, strike, designation, grader and attributes; corrects only two machine misreadings (a strike the rating names outright, FS on anything but a Jefferson nickel) |

Run `classifier_defaults` before `series_classify`: note class is evidence for
series. Live writes need the owner's go-ahead, a verified backup, and the
dry-run counts shown first.

**Named diagnostics** (`app/issues.py`) are the ongoing review queue in the
console: no year, no country, no grade (coins and notes only — bullion has
none by nature), no denomination, zero cost, `Mixed` marker, unreviewed,
unknown kind, bullion with no weight, repeated identifiers, star attribute
without an asterisk, malformed serial, near-duplicate serial. Each is a
filter, a count and a row badge.

---

## 8. Lifecycle and receipt

### 8.1 Two axes, one writer each

How an item came in (`status`) and how it goes out (`disposition`) are
separate columns; one column would make "received and sold" unrepresentable.

| Axis | Values |
|---|---|
| `item_status` | ordered, received, canceled, returned, missing, unknown |
| `disposition` | held, listed, sold, shipped, delivered, returned_by_buyer |

`missing` means paid for, not cancelled, never arrived; a missing parcel can
still be received later. `unknown` exists in the vocabulary; neither the
importer nor receiving sets it.

Status is **per item**, not per order: one order can hold dozens of items and
split shipments are normal. Every change is a row in `item_status_history`
(from, to, who, when, note, and `arrived_on` — a calendar date, not a
timestamp). **Status and location have exactly three writers**, in
`app/lifecycle_writes.py`: `record_initial_status` (the opening row, with no
"from"), `set_status` and `set_location`. Assign `status_id` or
`storage_location_id` anywhere else and the history silently stops being true.
Every creation path — import, entry, split, demo seed — records an opening
row.

Disposition is driven by selling (listings, sales lots, auctions, recorded
sales), described in `docs/specs/selling-design.md`.

### 8.2 How items are received

- **Imported items** took their status from the workbook's `Received` column
  (§4.3).
- **Entered items** are created on a purchase as `ordered`, or `received` for
  something already in hand. No item is entered outside a purchase.
- **Receiving** (`POST /api/inventory/receive`; console `/owner/receiving`)
  records one of four outcomes — received, missing, returned, canceled — with
  an optional arrival date and, for received, a storage location. The console
  page is one search form: part of an order number (matched anywhere in it,
  any case), and/or what the item is; by default it finds only what has not
  arrived (`ordered` or `missing`), and "Any status" shows a whole order. Each
  item is received in a dialog that also takes a note, photographs, field
  reviews and, for a banknote, the Friedberg lookup. Date and location carry
  to the next item; the note does not, because it describes one object.
  Whenever the dialog closes the search repeats, so what arrived drops off
  the list. Results come 200 at a time per kind and status, and anything
  beyond that is counted on screen. Receiving something already received is
  refused with 409. A link naming one order (`?order=<id>`, from the
  inventory screens or New purchase) opens with that order's header and its
  items already found -- searched by the order's id, since a number is not
  unique across vendors and is sometimes not recorded.
- **Corrections** go through the item editor's status field, which writes a
  history row like any other transition. Receiving only moves forward.

Receiving is the moment the object is in hand, so it is where attributes the
workbook never had (seal, signatures, district, plate numbers, errors,
Friedberg number) are best recorded. Nothing there is required; receipt is
never blocked by a field nobody can fill.

---

## 9. Photographs

### 9.1 The file and its use are separate

`image` is the file, stored once and identified by the sha256 of its
**cleansed** bytes. Link tables record use: `item_image` (inventory
photographs, never public), `listing_image` (what a buyer sees),
`shipment_image` (dispute evidence). Real foreign keys rather than a
polymorphic subject. `item_image.inventory_item_id` is nullable: a photograph
can be stored and browsed before anyone decides what it shows.

Bytes live under `MEDIA_ROOT` (default `<repo>\media`) behind a
`StorageBackend` interface, never in the database, so a database backup does
not include them (§11).

### 9.2 Metadata is stripped at ingest

The safe-deposit photographs carry the GPS coordinates of the bank (40 of 40
sampled in 804, 36 of 40 in 809) and camera identifiers; nearly all have
Orientation 6. Stripping at publish time would leave the coordinates sitting
in storage, so `app/imaging.py` does it on the way in:

```
1. read    capture DateTimeOriginal, dimensions, orientation into columns
2. rotate  apply the orientation to the pixels
3. strip   drop every metadata segment
4. verify  reopen and assert none remains; refuse the file if any does
5. hash    sha256 of the cleansed file -- its identity
6. store   object storage; the database records metadata only
```

Rotation must precede stripping or every phone photograph displays sideways.
Step 4 re-reads the written bytes rather than trusting the library that wrote
them. The originals stay where they were; ingest copies. Stripping discards
the original metadata permanently, which was accepted: the facts worth keeping
are columns.

### 9.3 Serving

Originals are never served. Requests are answered from `image_derivative`
renditions (`thumb`, 320 px, and `web`, 1600 px, by default), addressed by
content hash rather than sequential id so the collection cannot be walked.

### 9.4 Roles and the primary photograph

`image_role`: obverse, reverse, edge, detail, slab, certificate, group,
packaging, unassigned. At most one photograph per item is primary (a partial
unique index), and the shop shows only the primary, with no fallback.
`app/image_links.py` is the only writer of `item_image`: it demotes the
incumbent before promoting another in one transaction, promotes on attach when
an item has no primary, and fills the vacancy when a primary is detached or
deleted. Detaching keeps the photograph; deleting it destroys it and its
bytes.

### 9.5 Photographs are attached after receipt

Receipts are logged quickly and photographs taken at leisure, so a photograph
can be added to an item at any time:

- **PhotosPanel** in the item editor: upload, set role, make primary, remove.
- **`/owner/photos`**: photographs nobody has filed yet, filed by hand.
- **`python -m app.photo_import`** walks `PHOTO_LIBRARY_ROOT` (default
  `<repo>\photos`, git-ignored) or `--root`. Dry run by default, and genuinely
  side-effect free; `--commit` writes. The filename convention is
  `<item_code>_<nn>.<ext>`, e.g. `CC-000412_01.jpg`: `_01` is obverse and
  primary, `_02` reverse, `_03` on unassigned. Nothing is repaired (a
  lowercase `cc-` is a miss). Every file is stored; only the link is withheld,
  for an unparseable name, an unknown code, a deleted or split item, two files
  claiming one slot, or an occupied slot. An existing primary is kept.

**The 673 safety-deposit photographs are still unimported.** Nothing links
their filenames to items: 804's are camera-default timestamps, 809's mostly a
hand-numbered `N001`–`N324` sequence, which is a local catalogue number, not a
reference to a workbook row. They need renaming to the `CC-` convention
first; whatever does not match lands unattached in `/owner/photos`. The first
real run is a dry run the owner watches.

**Photographs and storage location never reach a customer.** That is an
authorisation boundary: it is enforced by `routers/catalog.py` building each
public response field by field, and tested — not by the `public_catalog`
view, which forbids the columns but which no endpoint reads.

---

## 10. Physical location

`storage_location` (kind, institution, identifier, notes) says where an item
is; `location_history` records every move, written only by
`lifecycle_writes.set_location`. Kinds: safe_deposit_box, safe, home,
in_transit, consigned, sold, unknown. A location is recorded on receipt, and
consigning items to an auction house moves them to a consigned location
created on first use.

The application lists storage locations for the receiving picker but has no
way to create an ordinary one; consignment is the only code that constructs a
row. The workbook had no location column, so imported items carry none unless
set since.

`local_catalog_number` exists for an owner's own numbering scheme (such as the
`N###` sequence in the 809 photographs); the importer does not fill it.

---

## 11. Backups and schema releases

- **Before a migration: `pg_dump`, verified by restoring it** into a scratch
  database and comparing every table's row count; then rehearse the migration
  on that restore and compare item count and cost basis before and after; only
  then migrate live, with the servers stopped, then `app.seeding load`, then
  restart (uvicorn runs without `--reload`). The full procedure is in
  `system-administration.md`, *Applying a schema release*. Dumps are kept
  outside the repository in `C:\Users\wnmil\dev\ccwebdb-backups\`.
- **`app.backup` is not a pre-migration backup.** It copies the database into
  another database with the schema built from the *current models* and no
  `alembic_version`, so once new code is checked out its copy already has the
  new tables and cannot be migrated. It is portable (another engine is a URL)
  and useful for a working copy beside live.
- **`app.backup --list` is not evidence.** A copy that aborted partway lists at
  a plausible size: on 2026-09-20 the newest copy held no inventory items and
  no purchase orders. Always `--verify <name>`, which compares row counts per
  table and reports `OLDER SCHEMA` for a copy that predates a migration. A
  restore is a copy in the other direction, into a fresh database.
- **Photograph bytes are not in either kind of backup.** Back up `MEDIA_ROOT`
  separately.
- **The workbook is not a backup.** It has not described the collection since
  the import.

---

## 12. Not built, or still open

Only what the code and the dated notes support.

- **The safe-deposit photographs** (§9.5): renaming, then a watched dry run.
- **Vendor PDF cross-check.** No `source_document` or `validation_finding`
  tables exist. The saved eBay pages are purchase-history pages, about 25
  orders each, with a lossy text layer (order numbers missing on some pages
  even after whitespace normalisation). When built: layout-aware parsing from
  word coordinates, a coverage figure per document, matching by order number
  then date and total then description, and findings reported, never applied.
  The same "join, report, don't overwrite" rule applies to marketplace order
  reports (the Whatnot report, an eBay export).
- **Spot prices and valuation history.** No feed chosen; `metal_price` and
  `valuation_snapshot` are empty, so melt value and profit are blank (§6.3).
- **PCGS type numbers.** `pcgs_type` exists; nothing captures one. Whether to
  record them is the owner's catalogue-numbering call.
- **Split panel.** The split API works; the console has no screen for it.
  When last measured (2026-09-08), twelve imported items held more than one
  piece between them and awaited splitting; query for `piece_count > 1`, and
  check each against its description before splitting.
- **Creating storage locations** in the console (§10).
- **Excel round trip** (export a search, re-import corrections):
  `docs/specs/excel-roundtrip-design.md` is a design awaiting review, not
  code.
- **Generic import facility** for another collector's file: not started; the
  profile seam (§4.1) is what it will replace.
- **Vocabularies page**: no creating a value there -- a new value is added from
  a field's picker ("Add a new value").
- **Review lists printed by the passes** are the owner's to work: bare-number
  ratings with nothing to settle the strike, Series 1935G notes needing No
  Motto evidence, ambiguous note classes, series missing from the note facts,
  Peace dollars rated "No Motto", notes rated FDOI. The passes' reports list
  them by item code; rerun a report rather than trusting an old list.
- **`derived` reference rows** must be reviewed before anyone runs
  `app.seeding export --source derived`.
- **Signers against denomination** as well as series, only from a source the
  owner trusts.
- **Physical checks** (last confirmed 2026-09-10; re-check before acting):
  possible duplicate rows across a few lots (`logs/duplicate-rows-review.csv`),
  and star attributes on notes whose serials show no asterisk (the
  `star_mismatch` diagnostic finds them).

---

## 13. Standing facts easy to get wrong

- **Never re-import the workbook into live, and never propose
  `ccweb_rebuild.cmd` to fix live data.** Passes, the console, or a verified
  restore.
- **Pass order:** `classifier_defaults` before `series_classify`.
- **Fine metal is `sum(fine_weight_ozt * piece_count)`**, over rows with
  `split_at IS NULL AND deleted_at IS NULL`. Cost basis is `sum(total_cost)`
  over the same rows. Query, don't quote: figures move with every pass and
  entry.
- **`sales_tax`, not `taxes`; `item_cost` and `shipping_cost`, not `price` and
  `shipping`; `piece_count`, not `storage_quantity`.** `listing.price` is the
  asking price, a different thing.
- **Tax rate is per row**, stamped at creation. Changing the setting changes
  nothing already recorded.
- **Status and location have three writers** (`lifecycle_writes.py`).
- **`item_image` has one writer** (`image_links.py`), and the shop shows only
  a primary photograph.
- **A series letter is not a mint mark**, and a note serial is not a
  certificate number.
- **A series year is the design year, not the signing year.**
- **Certificate numbers repeat** in this data; do not key on them.
- **Workbook dates:** the Whatnot report's `processed date` is settlement.
- **`CC-` codes before 2026-09-15** may name a different purchase now.
- **`python -m app.seed` makes demo items**; never run it on live, and in a
  rebuild run it last.
- **Nothing reads the four views**; search uses base tables, and the public
  boundary is enforced in `routers/catalog.py`.
- **No Friedberg or Pick mapping, catalogue numbering or price-guide value is
  ever seeded or fetched.** The owner types numbers for their own notes.
- **The test suite uses `ccwebdb_test`**, never `ccwebdb`.
