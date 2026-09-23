# Editing in Excel: export a search, import the corrections

**Status: not built.** This is a design proposal. No export or import endpoint
exists, and nothing below describes current behaviour. Bulk correction today is
the console's bulk edit (`attribution-design.md`).

## Why

Excel is a better bulk editor than any screen this project will build in
reasonable time: fill-down, sort, find-and-replace, a formula to derive one
column from another, and years of the owner's muscle memory. The round trip is
a second surface over the same data for the job Excel is better at.

**The database is the system of record.** A workbook is a working copy, and an
import is a set of proposed changes reconciled against the database. The
direction never reverses.

## Shape

**Two workbooks, coins and currency**, because their columns differ (a coin has
a mint mark and variety; a note a series letter, seal and serial), as their
browse screens do.

**A workbook is a search result, not the collection.** Export "the trinary
notes" or "everything with no grade", fix that, import it back. The job is
bounded, so a mistake is bounded; a stale export cannot revert work it never
contained; the file is small enough to scan.

```
GET  /api/inventory/{view}/export?<the search filters>   -> .xlsx
POST /api/inventory/{view}/import   (multipart)          -> a report (dry run)
POST /api/inventory/{view}/import?commit=true            -> applied
```

The export takes **exactly the browse screen's search parameters**, so "export
what I am looking at" needs no second query language. Every column is
exported, and every stored field is editable; the safeguards below, not a
shorter column list, are what make that tolerable.

## Safeguards

- **Identity and concurrency.** Every row carries `item_code` and `version`,
  both locked. A row whose stored version has moved on is a **conflict**,
  reported with both values and never applied -- the optimistic concurrency
  the web forms already use. Without it, an export edited offline for a week
  silently reverts everything done in between.
- **Import is a diff.** Only cells that differ from the exported value are
  applied; an untouched row produces no update.
- **A missing row never means delete.** Filtering a sheet and saving is normal
  Excel behaviour. Deletion is explicit (a `delete` column) and goes through
  the same guarded soft delete as the console.
- **Every import is a dry run first**: rows updated, fields changed, conflicts,
  rejects, with the values. `?commit=true` applies it.
- **Import never creates items.** Creating from a spreadsheet would
  reintroduce two sources of truth.

## What Excel does to data

A General-format cell destroys long numbers: `5.0157E+14` where a fifteen-digit
certificate number used to be. The export writes **every identifier column as
text-formatted at the cell level** (`number_format = "@"` in openpyxl):

| Column | Without text format |
|---|---|
| `serial_number` | `00003333` becomes `3333`, and the low-serial and double-quad designations vanish with the zeros |
| `cert_number` | 15 digits become `5.0157E+14`, unrecoverably |
| `order_number` | `05-13811-93563` may be read as a date or formula |
| plate numbers and plate position | a digit-only plate number is as vulnerable as any identifier |

The export re-reads what it wrote as a self-check rather than trusting the
library.

## Classifiers travel as codes, with dropdowns

A classifier column holds its `code`, and Excel data validation offers the
vocabulary from a hidden lookup sheet. Codes are the stable contract the API
uses (labels can be renamed), and validation refuses a typo at entry, where
the person can still see what they meant. An unknown code on import is a
**reject naming the row and column**, never a silent null. A new value is
added deliberately through the console's pickers, never by typing it into a
cell.

## Derived columns are shown, not accepted

`total_cost`, `taxes`, melt value, `series_designation` and the serial-derived
attributes are computed. They are exported as context, visually distinct and
locked, and **an edit to one is reported rather than applied** -- silence would
look like acceptance. `tax_rate` and `tax_includes_shipping` are stored inputs,
so they are editable, and the derived columns recompute on import.

## Multi-valued fields: one comma-separated column

A note's attributes, and an item's errors, are one cell each:

    attributes    star, radar, fancy_serial, high_serial

A boolean column per value would add dozens of mostly empty columns and a new
one every time the vocabulary grows. Rules: codes, not labels; order carries no
meaning; whitespace around a comma is ignored; an empty cell means none; an
unknown code rejects the row. No code contains a comma. Serial-derived
attributes can only be changed by correcting the serial, except `consecutive`,
which describes a run and cannot be derived from one serial.

## Sheets

| Sheet | Contents |
|---|---|
| `Items` | one row per item |
| `Lookups` | hidden; the vocabularies behind the dropdowns |
| `About` | the filters, timestamp, row count, and the rule that a missing row is not a deletion |

`About` makes a workbook found in six months explain itself.

## An unfiltered export is an archive, not a backup

With no filters the export is a complete, human-readable record of the item
catalogue, openable without PostgreSQL or this application. It cannot carry
what is one-to-many or not item-shaped -- status history, import provenance,
listings, sales, customers, shipments, images, valuation and location
histories -- so restoring from it would silently drop them. `About` says so.
The database's own backup is `python -m app.backup`
(`docs/system-administration.md`).

## Out of scope

Photographs: a workbook cannot carry them.

## Tests it would need

- A round trip with no edits changes nothing.
- A leading-zero serial survives; removing the text format must fail the test.
- A stale version conflicts and is not applied.
- A missing row deletes nothing.
- An unknown code is rejected, naming row and column.
- An edited derived column is reported, not applied.
- A dry run writes nothing, asserted against row counts and a checksum.
