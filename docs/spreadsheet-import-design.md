# Spreadsheet import design

The design of the importer in `backend/app/importers/`, as built. It loads a
collector's spreadsheet into the schema described in
[database-design.md](database-design.md), with a review report for everything
its rules cannot decide.

**It is used for a new collection only.** The existing collection was loaded
from `wnm3_coins.xlsx`; since then the `ccwebdb` database is the record, and
the workbook is never imported into it again -- a re-import would discard
every correction made since. Data in `ccwebdb` is improved by the passes and
the console ([system-administration.md](system-administration.md)).
`scripts\ccweb_rebuild.cmd` runs the importer into a separate database,
`ccwebdb_rebuild`. [data-import-plan.md](data-import-plan.md) records what the
workbook contained and how the import went.

Nothing here should influence the schema. If a rule seems to want a column,
re-read the schema rather than adding one.

---

## 1. Architecture: one seam

All knowledge of one source's layout lives in one module, reached through one
interface.

```
importers/
  engine.py      DURABLE     runs a source through a profile: staging, report, commit
  profile.py     DURABLE     the contract a profile satisfies (RawRow, Issue, RowResult)
  sources.py     DURABLE     reads .xlsx rows as verbatim text; sha256 of the file
  loader.py      DURABLE     normalised fields -> schema rows (SchemaLoader)
  models.py      DURABLE     import_batch, import_row, import_issue
  rating.py      DURABLE     decomposes a condition string; shared with app.rating_pass
  reporting.py   DURABLE     the review files
  profiling.py   DIAGNOSTIC  column profiling and variant detection
  cli.py                     python -m app.importers.cli
  profiles/
    collection_v1.py  DISPOSABLE  every rule specific to wnm3_coins.xlsx
```

The engine and loader know nothing about coins, denominations or one
workbook's conventions. A profile implements one method:

```python
class ImportProfile(Protocol):
    name: str
    def inspect(self, row: RawRow) -> RowResult: ...
```

`RowResult` carries a `Classification` (kind, subtype, and the rule that
decided it), a list of `Issue`s (rule, severity `info`/`warning`/`error`,
column, raw and proposed value, note) and a loose `fields` dict. Mapping
`fields` onto real columns is the loader's job, not the profile's. A row
needs review when its kind is `unknown` or any issue is an `error`.

The rules that matter -- classification order, `Grading#` routing, the
`Received` column, identifier damage, rating decomposition -- are tested in
`tests/test_importer.py` and `tests/test_rating_rules.py`. A collection in a
different layout gets a new profile; the engine and loader do not change. A generic import facility -- upload, map columns, rules as
editable data -- would replace profiles, not the engine, and is not built.

---

## 2. Two-stage load

```
source file --> import_row (verbatim)  --> inventory_item and its detail rows
                      |
                      +--> import_issue
```

Nothing is coerced on the way in. `sources.XlsxSource` renders every cell as
text: dates as ISO, whole-number floats without a spurious `.0`.

```
import_batch   id, source_path, source_kind, sha256, profile_name, mode,
               row_count, started_at, finished_at, notes
import_row     id, batch_id, row_number, raw (JSONB, the whole row),
               status (classified | needs_review | imported),
               item_kind, subtype, classified_by_rule, inventory_item_id
import_issue   id, import_row_id, rule, severity, column_name, raw_value,
               proposed, note, resolved_by, resolved_at
```

`raw` is JSONB so a change in the source's shape needs no migration.
`import_row.inventory_item_id` links each staged row to the item it produced;
that join, not arithmetic on item codes, is the way back to a source row.

### Running it

```cmd
python -m app.importers.cli --file <path.xlsx>              dry run, no database
python -m app.importers.cli --file <path.xlsx> --commit     stage and load
```

Other options: `--sheet`, `--profile` (only `collection_v1`), `--limit N`,
`--top N` (entries per report section), `--out-dir`, `--no-files`, `--quiet`.

**The dry run is the default and touches no database at all**, so a
profile's rules can be iterated on with nothing running. It prints the report
and writes the review files to `logs\import\` (under `CCWEB_LOG_DIR` when
set): `report.txt`, `summary.json`, `issues.csv` (each issue with its full
source row), `corrections.csv` (a known typo and the rows to fix it in),
`unclassified.csv` (distinct unclassified values with their rows),
`columns.csv` and `variants.csv` (column profiling). Reviewing distinct values
rather than rows is what makes the queue tractable. The exit code is 1 when
any issue is an `error`.

`--commit` opens a batch, stages every row, and loads each through one
`SchemaLoader`, whose reference lookups are cached for the run. **It is not
idempotent**: the file's sha256 is recorded but not checked, so committing the
same file twice loads it twice. Import into an empty database, as the rebuild
script does.

There is no console screen over `import_issue`; the review files are the
review surface, and the console's named diagnostics (`app/issues.py`) are the
ongoing one.

---

## 3. Classification

`collection_v1` classifies a row by its `Denom` cell. First match wins, and
the order matters more than the rules:

1. **known values** -- an explicit map of exact cells (`mint proof`,
   `silver mint`, ...) to a kind and subtype
2. **bullion** keywords -- Silver/Gold Eagle, rounds, bars, Maple, Libertad,
   Krugerrand, Britannia, Philharmonic, Buffalo, Panda, `oz`, gram or grain
   weights
3. **set** keywords -- Proof Set, Mint Set, Prestige, Coin Set
4. **medal**, **token**, and named other objects (meteorite)
5. **currency** -- `Bill`, `Note`, `Fractional` (and two recorded misspellings)
6. **currency** -- a leading `$`
7. **coin** -- a bare number
8. **coin** -- a number in a unit this collection holds only as coins (pence,
   yen, francs, pesos, ...)
9. otherwise **unknown**, into review -- including a number followed by a word
   that is none of the above

Bullion and sets come before the currency rules or `1oz Copper Round` would be
read as a banknote. A number followed by an unrecognised word is left
`unknown` rather than guessed as currency.

A **correction map** fixes known misspellings (`$20 blll`, `silvereagle`).
Every correction is logged as an `info` issue, so the map stays visible. It is
not a fuzzy matcher.

---

## 4. Field-level rules

**Columns that mean different things by row.** `Grading#` is a banknote's
printed serial (`currency_detail.serial_number`) and anything else's
certificate number (`item_certification`, one row per number, since some
cells list several). The classified kind decides. An arrival marker typed
there (`x`, `canceled`) is warned and not stored; a word saying the identifier
is absent (`missing`, `none`) is warned and not stored.

**Arrival comes from `Received` only.** `x` is received; a blank is `ordered`,
since the absence of the mark is what the column says; `canceled`,
`returned` and `missing` set that status; `counterfeit` sets authenticity and
leaves status alone; anything else is `ordered` with a
`received-not-understood` warning. An unreadable cell reads as *not* arrived
on purpose: an item wrongly left `ordered` is received in the console in one
click, while one wrongly marked `received` drops silently out of everything
that asks what is outstanding.

**`Value` is an appraisal.** A number becomes `numismatic_value`. A status
marker found there is reported (`status-marker-in-value-column`), not obeyed:
two columns both setting status is how they come to disagree.

**Compound values are decomposed, never stored whole.** `rating.py` splits
`PR69DCAM PCGS` into strike type, grade, designation and grader, and reads
attributes (CAC, First Strike, No Motto) and authenticity. The original stays
in `grade_raw`; unrecognised text goes to `attributes.rating_unparsed`. A bare
number (`69 PCGS`) is graded only once something settles the strike.

**Ranges and multi-value cells.** Year ranges become `year_start`/`year_end`.
A coin's `1921-P` or `2019-P/D/S` is a year and mint marks; a note's `2017-A`
is a series year and series letter, stored separately. A multiplier prefix
(`20x ...`) becomes `piece_count`. Weights in grams, kilos or pounds convert
to troy ounces, with `weight_raw` kept.

**Identifiers are text, always.** Order numbers, serials and certificate
numbers are never re-typed. A value the spreadsheet already coerced to a
number -- leading zeros lost, or scientific notation -- is flagged
(`identifier-lost-to-scientific-notation`). **That damage is detectable, not
repairable**; recovery is from the item or a vendor document.

**Purchase orders are grouped by a real identifier or not at all.** A row
with an order number joins that vendor's order. Without one, the vendor's
transaction id in the item URL identifies the purchase (a HiBid, Proxibid or
LiveAuctioneers lot, an Etsy receipt). An eBay item number names a listing, so
it groups rows but is never written into `order_number`. A row with neither
gets no purchase order: keying on `order_number or ""` once collapsed every
numberless row from a vendor into one fabricated order.

**Composition is filled from the facts** (`composition.json`) where the row
states no weight or metal, and recorded as a derived default so
`app.classifier_defaults` may refresh it. A stated value always wins.

### Domain facts that look like errors

The column profiler proposes corrections by collapsing case, spacing and
punctuation. It is a **proposal engine, not an authority**; the profile's
`KNOWN_GOOD` lists values confirmed correct as written:

| Value | Looks like | Means |
|---|---|---|
| `1980's`, `1970's` | a typo for mint mark `S` | a **decade**, e.g. a roll spanning the 1980s |
| `1989-P&D`, `1988-P/D` | inconsistent separators | a **mint set holding both mints** |
| `2024?` | a stray character | the year is **uncertain** |
| `1953` vs `1953-` | a missing suffix | a year and an **open range** are different claims |
| `UNC+`, `MS64+`, `BU++` | inconsistent grades | **real grading distinctions** |
| `25`, `Silver Round .5 oz` | `2.5`, `5oz` | a $25 gold eagle; a half ounce |

Normalising is safe only for case, stray whitespace and the separator between
year and mint mark. Anything that adds, removes or reinterprets a meaningful
character is left for a person.

### Authority

**`Description` has no authority over status, errors or anything else.** It
is seller copy. Scanning it for cancellation words matches auction
boilerplate (`NO CANCELLATIONS`), product names (`Lost Coins`) and set
contents (`MISSING 1991`). It feeds full-text search only.

---

## 5. Reference resolution and provenance

The loader resolves every classifier to a reference row by code, then by
label or alias (`app.aliases`), before doing anything else. What it cannot
resolve:

- **Most vocabularies** get a new row marked `source = derived`, counted in
  the report (`derived  : grade 2, mint 1`). A large count means the seeded
  vocabulary is missing something real. `SchemaLoader(create_missing=False)`
  raises instead, for a strict re-run.
- **Grades** are stricter. A number grade the seed lacks (`61+`) is added as
  derived, since it is a real point on the scale, but text that is not a
  condition (`5-Coin Mint Set`) is declined and kept in `grade_raw`. A
  banknote's grade is found on the note scale or left for a person.
- **An alias shared by two values** resolves to neither.
- A value read through an alias is reported
  (`aliased  : note_type: Legal Tender -> us_note (3 rows)`).

Every item gets its opening status row through
`lifecycle_writes.record_initial_status`; attributes read from the rating are
linked as derived with `derived_by = import`. `derived` rows stay
distinguishable from curated ones, and `app.seeding export` leaves them out
unless asked.

---

## 6. What the importer does not do

- **Series.** The rebuild runs `app.series_match`, `app.classifier_defaults`,
  `app.series_classify` and `app.serial_patterns` after the import; skipped,
  their work is simply absent.
- **Photographs.** Linked afterwards by `app.photo_import`, by the
  `<item_code>_<nn>` filename convention, or by hand on `/owner/photos`
  ([data-import-plan.md](data-import-plan.md) §9).
- **Vendor document cross-check.** Not built: no tables exist for it. If
  built, saved order pages would be parsed from word coordinates rather than
  text (their text layer is lossy), with a coverage figure per document,
  matched by order number, then date and total, then description, and
  findings reported, never applied -- an unmatched record means "not
  confirmed", not "wrong".
- **Demo data.** `python -m app.seed` runs last in the rebuild; run earlier,
  its demo items take the first item codes and offset every real one.

---

## 7. Risks

**Damage already in the source cannot be undone.** Identifiers coerced to
numbers have lost information. The importer detects and flags; recovery is
manual.

**Rules drifting into the schema.** Guarded by the engine/profile seam and by
`source` on every reference row, so a value a rule invented never passes for
a curated one.

**Over-investment in the profile.** Its rules describe one file. Polishing
them or generalising the profile is the wrong work; a second layout gets its
own profile.
