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
the spreadsheet importer already uses and it has caught real problems.

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

## Sheets in the workbook

| Sheet | Contents |
|---|---|
| `Items` | one row per item, the editable and derived columns |
| `Lookups` | hidden; the vocabularies backing the dropdowns |
| `About` | the export's filters, timestamp, row count, and the rule that a missing row is not a deletion |

`About` exists so a workbook found in six months explains itself.

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

- **Which columns are editable** for each view. The coin browse shows 47 and
  not all of them should be hand-editable; the list wants going through once
  with the owner rather than guessing.
- **Multi-valued fields.** A note can carry several `note_attribute` values.
  One column of comma-separated codes is editable but loose; several boolean
  columns are rigid but unambiguous. Undecided.
- **Photographs** are out of scope. A workbook cannot carry them and should
  not pretend to.
- **Whether import should ever create items.** Editing existing rows is the
  need. Creating from a spreadsheet is how the original import worked and
  would reintroduce two sources of truth, so the default is no.
