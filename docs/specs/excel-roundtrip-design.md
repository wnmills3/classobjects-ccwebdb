# Editing in Excel: export a search, import the corrections

Design, 2026-09-08. Status: awaiting review.

## Why

Excel is a better bulk editor than any web screen this project will build in
reasonable time. Fill-down, sort, find-and-replace, a formula to derive one
column from another, and the muscle memory of someone who kept a 7,591-row
collection in it for years. The attribution work needs exactly those.

This is not a retreat from the web UI. It is a second surface over the same
data, for the job Excel is genuinely better at.

## What changed to make this safe

**The database is the system of record** (2026-09-08). Before that, a
spreadsheet round trip would have had two candidate truths and no way to
settle a disagreement. Now the direction is unambiguous: the database is
authoritative, a workbook is a working copy, and an import is a set of
proposed changes to be reconciled against it.

## Shape

**Two workbooks, not one.** Coins and currency have different columns -- a coin
has a mint mark and a variety, a note has a series letter, a seal colour and a
serial. One sheet would leave most columns blank most of the time, which is the
same argument that gave them separate browse screens.

**A workbook is a search result, not the collection.** You export "the 124
trinary notes" or "everything with no grade", fix that, and import it back.
Three things follow:

- The job is bounded, so a mistake is bounded with it.
- A stale export cannot revert work it never contained.
- The file is small enough to open, sort and scan.

Exporting all 7,591 is possible -- it is just a search with no filters -- but
it is not the intended use and the documentation should not lead with it.

## The round trip

```
GET /api/inventory/{view}/export?<the same filters as search>   -> .xlsx
POST /api/inventory/{view}/import   (multipart)                 -> a report
POST /api/inventory/{view}/import?commit=true                   -> applied
```

The export takes **exactly the search parameters the browse screen already
uses**, so "export what I am looking at" needs no second query language.

### Identity and concurrency

Every row carries `item_code` and the item's `version`, both locked.

`item_code` says which row this is. `version` says which *state* of it was
exported. On import, a row whose stored version has moved on is a **conflict**:
reported with both values, never silently overwritten. That is the optimistic
concurrency already built for the web forms, reused rather than reinvented.

Losing this is how a spreadsheet round trip destroys work: someone exports on
Monday, edits offline, imports on Friday, and silently reverts everything done
in between.

### Import is a diff

Only cells that differ from the exported value are applied. An untouched row
produces no update at all.

This matters more than it sounds. Applying every row would mean re-writing
7,591 rows to change one, turning every import into a collection-wide
rewrite and every stale cell into a regression.

### A missing row never means delete

Filtering a sheet and saving is normal Excel behaviour, and it must not delete
the filtered-out rows. Deletion is explicit -- a `delete` column, or a separate
action -- and goes through the same guarded soft delete as the web UI.

### Every import is a dry run first

The default reports and changes nothing: rows updated, fields changed, conflicts,
rejects, and the specific values. `?commit=true` applies it. This is the pattern
the one-off `wnm3_coins.xlsx` importer already uses and it has caught real
problems.

## What Excel does to data, and how the export prevents it

This project has already lost data to Excel. Six `Grading#` values are gone --
`5.0157E+14` where fifteen digits used to be -- and the TSV export carries the
same corruption, so it predates any recovery. That is not carelessness; it is
what a General-format cell does to a long number.

The export therefore writes **every identifier column as text-formatted**, at
the cell level, not merely as a string value:

| Column | Without text format |
|---|---|
| `serial_number` | `00003333` becomes `3333`; the low-serial and double-quad designations vanish with the leading zeros |
| `cert_number` | 15 digits become `5.0157E+14`, unrecoverably |
| `order_number` | `05-13811-93563` may be read as a date or a formula |
| `friedberg_number` | `Fr. 1601` is safe; a bare `1601` is not |
| `item_code` | safe only because `CC-` forces text |
| `face_plate_number`, `back_plate_number` | `E82` is safe today, but an older, digit-only plate number is exactly as vulnerable as any other identifier here |
| `plate_position` | a check letter plus a quadrant number, e.g. `A4` -- same risk as the plate numbers above |

`openpyxl` sets `number_format = "@"` per cell. The import also **re-reads what
it wrote** on export in a self-check, the same way image ingest verifies the
EXIF strip rather than trusting the library.

## Classifiers travel as codes, with dropdowns

A classifier column holds its `code` -- `MS64`, `morgan_dollar`, `silver` --
and Excel data validation offers the vocabulary from a hidden lookup sheet.

Two reasons. Codes are the stable contract the API already uses, and labels are
ambiguous once anyone renames one. And validation refuses a typo *at entry*,
in Excel, where the person can still see what they meant -- rather than at
import, in a report, an hour later.

An unknown code is a **reject with the row and column named**, never a silently
null column. The importer learned this the hard way: an early version invented
vocabulary entries from unmatched text and produced 185 junk grades including
`ACADIANP`.

Adding a genuinely new value stays a deliberate act through
`POST /api/reference/{table}`, not a side effect of typing it into a cell.

## Derived columns are shown, not accepted

Series, the fancy-serial designations, `total_cost`, `taxes`, melt value: these
are computed from other fields. They belong in the export because they are the
context that makes a row make sense, and they must not be writable, because a
hand-edited `total_cost` would be silently recomputed away.

They are visually distinct, locked, and **an edit to one is reported rather
than applied** -- silence would look like acceptance.

`tax_rate` and `tax_includes_shipping` are not derived: they are the stored,
per-row *inputs* to `taxes` and `total_cost`, and so are ordinary editable
columns. Setting `tax_rate` to 0 is how a batch of purchases that were charged
no tax gets corrected, and the derived columns recompute on import. Both are
stamped from settings when an item is created; see
`docs/system-administration.md`, *Sales tax on acquisitions*.

## Multi-valued fields are one comma-separated column

Decided with the owner, 2026-09-08. A note carrying several designations gets
one cell:

    note_attributes    star, radar, fancy_serial, high_serial

The alternative -- a boolean column per value -- would put **seventeen**
columns on the currency sheet for `note_attribute` alone, most of them empty
on most rows, and would need a new column every time the vocabulary grows.
The vocabulary is meant to grow.

Rules that keep it unambiguous:

- **Codes, not labels**, as everywhere else at this boundary.
- **Order carries no meaning.** `star, radar` and `radar, star` are the same
  set, and reordering a cell is not an edit.
- **Whitespace around a comma is ignored**, because Excel and people both add
  it.
- **An empty cell means no values**, and is distinct from an untouched row --
  the diff already knows which cells changed.
- **An unknown code rejects the row**, naming the cell, exactly as a
  single-valued classifier does. No code in any vocabulary contains a comma,
  so the separator is safe.

The derived designations are the interesting case. `star`, `radar` and the
rest are computed from the serial by `app.serial_patterns`, so the column is
**shown but not accepted**: editing it is reported rather than applied, and
the way to change it is to correct the serial. `consecutive` is the exception
-- it describes a run of notes and cannot be derived from one serial, so it is
editable.

## Sheets in the workbook

| Sheet | Contents |
|---|---|
| `Items` | one row per item, the editable and derived columns |
| `Lookups` | hidden; the vocabularies backing the dropdowns |
| `About` | the export's filters, timestamp, row count, and the rule that a missing row is not a deletion |

`About` exists so a workbook found in six months explains itself.

## The full export is an archive, not a backup

Exporting with no filters produces a complete record of the **item catalogue**,
and that is worth having as a first-class feature. It is human-readable,
portable, and openable in twenty years without PostgreSQL or this application
-- none of which is true of a `pg_dump`.

It is not a database backup, and the workbook never becomes the system of
record. The direction stays one-way.

**What the item catalogue is**, and a workbook can carry, one row per item:

| | Rows today |
|---|---|
| `inventory_item` | 7,591 |
| `coin_detail` / `currency_detail` (plate numbers included) | 6,477 / 1,114 |
| `item_certification` | 1,456 |
| `item_note_attribute` | 891 |
| `item_error` | 0 |
| `purchase_order`, `vendor` | 3,879 / 21 |

**What it cannot carry**, because it is one-to-many or not item-shaped:

| | Rows today | Why |
|---|---|---|
| `item_status_history` | **7,591** | an append-only event log per item |
| `import_row` | **7,591** | the one-off `wnm3_coins.xlsx` row that seeded this item, as JSON -- the provenance chain |
| `listing`, `sales_order`, `customer`, `address`, `shipment` | 0 | **the whole business goes here** |
| `image`, `item_image` | 0 | binary |
| `valuation_snapshot`, `location_history` | 0 | histories by design |

The second table is why the direction cannot reverse. Restoring from a
workbook today would silently drop 7,591 status rows and every item's link
back to the `wnm3_coins.xlsx` row it started from. After the first sale it
would drop the sale.

A workbook is also lossy in a way that gets worse rather than better: the
zero-row tables are the ones the business is about to start filling.

**So:** rebuilding the catalogue from a full export is a supported
disaster-recovery path, and the `About` sheet states plainly what a restore
would not bring back. Anyone restoring should know what they are missing
before they start, not afterwards.

## Backing up the database itself

The archive above covers items. The database needs its own backup, and it has
one: `python -m app.backup` copies every table -- the histories, the sales
side, the reference data -- into a timestamped database beside the live one.

**It is built on SQLAlchemy rather than `pg_dump`, deliberately.** The schema
comes from the models and the destination is a URL, so pointing it at another
engine is a change of URL rather than of code:

    python -m app.backup                     a local copy, ccwebdb_bak_<stamp>
    python -m app.backup --to <url>          anywhere SQLAlchemy reaches
    python -m app.backup --list              what exists
    python -m app.backup --verify <name>     row counts, table by table

Three details make it faithful rather than merely complete. Tables copy in
`Base.metadata.sorted_tables` order, so a row never arrives before the row it
references. Generated columns are skipped and recomputed by the destination,
because writing them would let the stored value disagree with the expression
defining it. And each serial sequence is advanced past the copied ids, without
which a restored backup works until the first insert collides.

`--verify` compares row counts on every table; any mismatch is a failed
backup. First run: 29,439 rows, matching the source on every table, with the
generated columns recomputed to the same figures.

`pg_dump` remains the right tool for taking a file off the machine. This is
for a copy you can query.

## Testing

- **Round trip with no edits changes nothing.** Export, import, and assert zero
  updates. This is the test that catches a formatting or parsing asymmetry.
- **A leading-zero serial survives.** Export `00003333`, read the file back,
  assert the cell still holds eight characters. Mutation: remove the text
  formatting and this must fail.
- **A stale version conflicts.** Export, change the item by another route,
  import: reported, not applied.
- **A missing row deletes nothing.**
- **An unknown classifier code is rejected**, naming row and column.
- **An edited derived column is reported**, not applied.
- **A dry run writes nothing**, asserted against the row count and a checksum.

## Open questions

- ~~Which columns are editable~~ **Decided 2026-09-10: every field, for both
  views.** The coin browse shows 47 columns, and the owner's own words: "the
  spreadsheet we export and import should have all fields to allow for
  external editing (albeit a dangerous task)." Nothing is withheld to make the
  workbook safer -- the safety already in this spec (dry run first, a stale
  version conflicts rather than silently overwriting, a derived column is
  reported rather than applied, the database staying the system of record) is
  what makes wide-open editing tolerable, not a shorter column list. This
  includes the three currency plate-number columns and the multi-valued
  errors column below; see their own sections for how each is represented.
- ~~Multi-valued fields.~~ **Decided 2026-09-08: one comma-separated column.**
  See below.
- **Photographs** are out of scope. A workbook cannot carry them and should
  not pretend to.
- **Whether import should ever create items.** Editing existing rows is the
  need. Creating from a spreadsheet is how the one-off `wnm3_coins.xlsx`
  import worked and would reintroduce two sources of truth, so the default
  is no.
