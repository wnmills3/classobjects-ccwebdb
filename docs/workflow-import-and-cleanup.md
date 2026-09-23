# From a workbook to a catalogue you can sell from

Two jobs: loading an existing collection from a workbook into a **new**
database, and the clean-up that makes an imported collection trustworthy
enough to list. Everything acquired afterwards follows
[workflow-new-collection.md](workflow-new-collection.md).

[data-import-plan.md](data-import-plan.md) is the account of how this
collection was imported and how its record is structured and kept correct;
[spreadsheet-import-design.md](spreadsheet-import-design.md) describes the
importer itself. This document is the procedure around them.

## For this collection: import is finished

The workbook reached the live database for the last time on 2026-09-16. Since
then `ccwebdb` is the system of record and the workbook is historic. **Never
re-import it into live, and never rebuild to fix live data**: a rebuild
discards everything corrected or entered since -- series assignments, derived
attributes, receipts, tax corrections, photographs, sales, accounts. Fix data
in the console or with a pass over stored items (below); recover from a
problem by restoring a verified backup
([system-administration.md](system-administration.md), *Backing up and
restoring*).

What remains for this collection is clean-up, which is ongoing.

## Loading a workbook into a new database

Only for a collection that is not yet in any database.

### 1. Dry run and review

```cmd
cd backend
uv run python -m app.importers.cli --file <workbook.xlsx>
```

The dry run touches no database and writes review files to `logs\import\`
([runtime-operations.md](runtime-operations.md) lists them). Fix what the
workbook can fix -- typos, a status marker in the wrong column -- and run it
again until the report says what you expect. The importer reads one layout,
through the `collection_v1` profile; a workbook laid out differently needs its
own profile in `backend/app/importers/profiles/`.

Before entering anything more in a workbook, **format identifier columns as
Text** (order numbers, serials, certificate numbers). Excel keeps 15
significant digits and shows long numbers in scientific notation, and once
that display is saved the digits are gone; the importer flags such values
(`identifier-lost-to-scientific-notation`) rather than storing a rounded one.
An apostrophe prefix (`'501570000000000`) forces text in a General cell.

### 2. Build the database

```cmd
scripts\ccweb_rebuild.cmd <workbook.xlsx>
```

It drops and recreates `ccwebdb_rebuild` -- never `ccwebdb` -- then migrates,
loads reference data, imports with `--commit`, runs `series_match`,
`classifier_defaults`, `series_classify` and `serial_patterns`, and runs
`app.seed` **last** to create the administrator. That order is the only safe
one: before the import, `app.seed`'s five demo items would take `CC-000002`
onward and offset every real item code.

### 3. Remove the demo items

`app.seed` also creates five demo items with shop listings. Delete them from
the new database (`set PGDATABASE=ccwebdb_rebuild`, then
`scripts\ccweb_psql.cmd`):

```sql
BEGIN;

-- The five demo items, by the titles app.seed matches on.
CREATE TEMP VIEW demo_items AS
  SELECT id FROM inventory_item WHERE source_title IN (
    '1881-S Morgan Silver Dollar',
    '1916-D Mercury Dime',
    '2021 American Silver Eagle',
    '1957-B $1 Silver Certificate',
    '1964 Kennedy Half Dollar'
  );

-- Check before deleting: five rows, all five demo titles.
SELECT item_code, source_title FROM inventory_item
  WHERE id IN (SELECT id FROM demo_items) ORDER BY item_code;

-- Claims and listings restrict the delete, so they go first; the item's
-- details, history and photographs cascade.
DELETE FROM offer_claim    WHERE inventory_item_id IN (SELECT id FROM demo_items);
DELETE FROM listing        WHERE inventory_item_id IN (SELECT id FROM demo_items);
DELETE FROM inventory_item WHERE id IN (SELECT id FROM demo_items);

-- COMMIT when the counts above were what you expected; ROLLBACK otherwise.
ROLLBACK;
```

The transaction is deliberate: the `SELECT` in the middle is the chance to see
what is about to go.

### 4. Check the totals

Over live rows (`split_at IS NULL AND deleted_at IS NULL`): the item count,
the cost basis `sum(total_cost)`, and fine metal
`sum(fine_weight_ozt * piece_count)`. Compare them with the workbook before
trusting anything downstream. Multiply by `piece_count`: a row holding twenty
coins carries twenty coins' metal, and the unweighted sum reads about 10% low.

### 5. Switch over

Point `DATABASE_URL` in `.env` at the new database and restart the servers
(`scripts\ccweb_shutdown.cmd /keepdb`, then `scripts\ccweb_startup.cmd`). Any
previous database stays untouched until the new one has been checked, and
switching back is one line. From here the database is the record.

## Clean-up: attribution

Establishing what each item actually is. The workbook recorded purchases, not
coins: many rows share a copied description, grades and years are missing,
and a `Mixed` rating means "known to vary", not "unknown". No importer can
supply what was never written down.

### The tools

- **Named diagnostics** in the inventory pages (`app/issues.py`): no year, no
  country, no grade (coins and notes only -- bullion has none by nature), no
  denomination, zero cost, `Mixed` marker, unreviewed, unknown kind, bullion
  with no weight, repeated identifiers, star attribute without an asterisk,
  malformed serial, near-duplicate serial. Each is a filter, a count and a row
  badge. Query the counts; do not quote old ones.
- **Bulk edit** a selection, then **review** the exceptions one at a time.
  The review queue is frozen when you enter it, so fixing an item's missing
  year does not drop it from the set and silently skip the next one.
- **Passes over stored items**, each a dry-run report unless given
  `--commit`. A pass fills only what is empty, never overwrites a value a
  person set, and records what it filled as derived:

  | Pass | Fills |
  |---|---|
  | `app.classifier_defaults` | note class, seal, signatures, district, composition and metal, No Motto |
  | `app.series_match` | a coin's series from the design its text names |
  | `app.series_classify` | series from denomination, year and letter; boundary cases to a review list |
  | `app.serial_patterns` | star, radar, repeater and other serial designations |
  | `app.rating_pass` | grade, strike, designation, grader and attributes from the stored rating |

  Run `classifier_defaults` before `series_classify` (note class is evidence
  for series). A live `--commit` needs the owner's go-ahead, a verified
  backup, and the dry-run counts shown first. The review lists the passes
  print are the owner's to work; rerun the report rather than trusting an old
  list.

### Finding the workbook row behind an item

`import_row.inventory_item_id` links every staged row to the item it produced,
and `import_row.row_number` is the row as the workbook shows it (header is row
1):

```sql
select r.row_number, r.raw
from import_row r join inventory_item i on i.id = r.inventory_item_id
where i.item_code = 'CC-000412';
```

Use the join, not arithmetic on item codes. Items entered since the import
have no workbook row.

### Order of work, and why

1. **Attribute before listing.** An item still carrying a copied description
   and a guessed grade is not ready to be described to a buyer.
2. **Split a multi-piece row before attributing its pieces**, when the pieces
   differ -- they cannot be described individually until they are individual
   rows. Query `piece_count > 1` and check each count against the description
   first; the counts were themselves parsed from the workbook. (The console
   has no Split panel; the API does it.)
3. **Photograph as items are handled.** The shop shows only a primary
   photograph.

## Deliberately left alone

- **Ratings the importer declined** stay verbatim in `grade_raw`
  (`Silver`, `Funny Back`, `65 PCGS` with the prefix implied). They need a
  decision per pattern, not a rule; `app.rating_pass` applies the rules as
  they grow.
- **`derived` reference rows** must be reviewed before anyone runs
  `python -m app.seeding export --source derived`, or one installation's
  guesses become another's vocabulary.
- **Purchase lots were not reconstructed.** Rows sharing a description came
  from copy-and-paste, not a shared purchase, so they are not a grouping
  signal; purchase orders are what "bought together" means.
- **The safe-deposit photographs** are not imported yet: their filenames do
  not name items and must be renamed to the `CC-` convention first
  ([data-import-plan.md](data-import-plan.md) §9.5).
- **Federal Reserve districts** the workbook never carried are filled from a
  note's serial by `classifier_defaults`, or at receipt from the note itself.
