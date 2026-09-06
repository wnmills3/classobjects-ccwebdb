# Spreadsheet import design

> **This describes temporary code.** It exists to load an existing collection —
> currently maintained in a spreadsheet, with supporting vendor documents and
> photographs — into the schema described in
> [database-design.md](database-design.md). Once the database and admin UI are
> the working system of record, this code is **deleted**, not maintained.
>
> Nothing here should influence the schema. If a rule in this document seems to
> want a column, that is a signal to re-read the schema, not to add one.

---

## 1. Scope and lifetime

**In scope:** a one-time (repeatable while iterating) load of an owner's
existing records into the database, with a review queue for everything the
rules cannot decide, and best-effort cross-checking against saved vendor
documents.

**Out of scope:** anything reusable. No configurable mappings, no rule editor,
no second data source. Generalising before a second source exists would be
building for a hypothetical.

**Exit criteria — when this code is deleted:**

1. All rows are loaded and the review queue is empty or accepted.
2. The admin UI can create, edit and receive items directly.
3. The owner has stopped updating the spreadsheet.

At that point a **generic import facility** — upload, inspect, map columns to
fields, rules as editable data, dry-run with diff — becomes a real feature,
designed without reference to any one file's quirks. This document is not its
precursor; it is its predecessor.

---

## 2. Architecture: one seam

All source-specific knowledge lives in exactly one place, reached through one
interface.

```
importers/
  engine.py            DURABLE   batching, staging, normalisation, issue queue,
                                 provenance, dry-run, reporting
  profile.py           DURABLE   the Protocol a profile must satisfy
  profiles/
    collection_v1.py   DISPOSABLE  every rule in this document
```

The engine knows nothing about coins, denominations or receipt conventions. It
knows how to read a tabular source into staging, hand each row to a profile, and
record what came back.

A profile supplies:

```python
class ImportProfile(Protocol):
    def columns(self) -> Mapping[str, str]:            ...  # source header -> field
    def classify(self, row: RawRow) -> Classification: ...  # kind + subtype
    def normalise(self, row: RawRow) -> Normalised:    ...  # typed values
    def issues(self, row: RawRow) -> list[Issue]:      ...  # anything ambiguous
```

**Effort follows the seam.** The engine is tested thoroughly. The profile is
tested at the level of *"the whole source loads and the kind counts reconcile"*,
not unit tests per correction rule — those rules are being deleted.

---

## 3. Two-stage load

```
source file ──► import_row (verbatim)  ──► normalised tables
                      │
                      └──► import_issue (review queue)
```

Nothing is coerced on the way in. Every value is preserved exactly as read, and
every normalised record keeps `import_row_id` so any field traces back to its
origin.

```
import_batch
  id, source_path, sha256, source_kind, row_count
  started_at, finished_at, mode (dry_run | commit), notes

import_row
  id, batch_id, row_number
  raw jsonb                    -- the entire source row, verbatim
  status                       -- pending | imported | needs_review | rejected
  inventory_item_id null       -- set once normalised

import_issue
  id, import_row_id, column_name, rule, severity
  raw_value, proposed_value, resolved_by, resolved_at, note
```

`raw` is `JSONB` rather than fixed columns: a source whose shape changes needs no
migration, and the engine stays source-agnostic.

**Dry-run is the default.** A run reports what it *would* create, with counts by
kind and a full issue list, and writes nothing until asked to commit. Re-running
is idempotent — `import_batch.sha256` plus `row_number` identifies work already
done.

---

## 4. Classification

Ordered rules, first match wins. Order matters more than the rules themselves.

1. **bullion** keywords — Eagle, Round, Bar, oz, Libertad, Maple, Krugerrand
2. **set** keywords — Mint Set, Proof Set, Prestige, Coin Set
3. **medal**, **token**, other named object types
4. **currency** — contains Bill or Note
5. **currency** — leading currency symbol
6. **currency** — a number followed by a word or letter (`10c`, `5 Rupees`)
7. **coin** — purely numeric
8. otherwise **unknown**, and into the review queue

Rules 1–3 must precede 6, or `1oz Copper Round` matches "number followed by a
word" and becomes currency. This ordering is the single most important thing in
the profile.

A **correction map** handles known misspellings and spacing variants. Every
correction is logged as an `import_issue` with severity `info`, so the map stays
visible rather than becoming invisible magic. It is not a general-purpose fuzzy
matcher.

Expected outcome: the large majority classified automatically, with the
remainder concentrated in a small number of distinct values — reviewing distinct
values rather than rows is what makes the queue tractable.

---

## 5. Field-level rules

These exist because a hand-maintained spreadsheet accumulates conventions that a
schema does not have.

**Columns that mean different things depending on the row.** A single column may
hold a grading certificate serial for one kind of item and the item's own printed
serial for another. Routing is by classified kind; a value that is ambiguous in
isolation is decided by what the row turned out to be.

**Columns doing double duty.** A value column may carry either an appraisal or a
status marker. Numeric values become the estimate; known markers map to status;
anything else goes to review rather than being guessed at.

**Conventions that started partway through.** Where a marker only appears after
some date, that date is established from the data and used to interpret absence:
before it, absence means "the convention did not exist"; after it, absence is
meaningful. Rows on the wrong side of that boundary go to review.

**Compound values are decomposed, never stored whole.** A condition string may
carry a grade, a designation, a grading service and — for notes — attributes that
are not grades. Each is extracted into its own field, the original is retained,
and anything unrecognised is flagged.

**Ranges and multi-value cells.** Year ranges become `year_start`/`year_end`.
Comma-separated certificate lists become multiple `item_certification` rows.
Multiplier prefixes (`20x …`) become `storage_quantity`.

**Identifiers are text, always.** Order numbers, certificate serials and note
serials are read as strings and never re-typed. Values that a spreadsheet has
already coerced to numbers — losing leading zeros or, worse, becoming scientific
notation — are flagged for manual recovery. **That damage is not repairable by
the importer**, only detectable.

### Domain facts that look like errors

A variant detector proposes corrections by collapsing case, spacing and
punctuation, then pointing rare spellings at dominant ones. It is a **proposal
engine, not an authority** — several of its highest-confidence suggestions would
destroy real information. Confirmed with the collection owner:

| Value | Looks like | Actually means |
|---|---|---|
| `1980's`, `1970s` | a typo for mint mark `S` | a **decade** — e.g. a roll of pennies spanning the 1980s |
| `1989-P&D`, `1988-P/D` | inconsistent separators | a **mint set containing both mints**, Philadelphia and Denver, not broken out as separate items |
| `2024?` | a stray character | the year is **uncertain** |
| `1953` vs `1953-` | a missing suffix | a bare year and an **open range** are different claims |
| `-2024` | a transposition | may denote a **range** |
| `UNC+`, `MS64+`, `BU++` | inconsistent grades | **real grading distinctions**, never normalised away |

A year may be a single year, a range, a decade, or uncertain. **Normalising year
formatting is safe only for case, stray whitespace and the separator between
year and mint mark.** Anything that adds, removes or reinterprets a character
carrying meaning is left for a human.

`P&D` to `P/D` was accepted as a separator normalisation because it preserves
the multi-mint meaning; the parser reads both marks out of either form.

### Authority rules

**Status is read only from fields the owner controls deliberately.** Free-text
description fields are seller-supplied marketing copy and carry no authority over
status. A naive search for cancellation words across a whole row matches
boilerplate (`NO CANCELLATIONS`), product names (`Lost Coins`) and set contents
(`MISSING 1991`) — and would cancel a large number of items the owner actually
holds. Description contributes to full-text search and nothing else.

---

## 6. Vendor document cross-check

Saved order pages and auction invoices are used to **corroborate** the loaded
records. They are never authoritative and never block the import.

```
source_document   id, sha256 unique, storage_key, vendor_id, doc_kind
                  captured_on, page_count, parse_status, extraction_coverage
document_order    id, source_document_id, order_number, order_date,
                  order_total, seller, page_number
document_item     id, document_order_id, title, price
validation_finding
                  id, inventory_item_id null, purchase_order_id null,
                  document_order_id null, field, sheet_value, document_value,
                  severity   -- match | variance | mismatch | unmatched
```

**Parsing is layout-aware, not regex-over-text.** Saved marketplace pages
interleave navigation furniture with content, and their text layer can be lossy —
some records simply do not survive text extraction. Extraction works from word
coordinates, filtering by position, and records
`extraction_coverage` per document so under-reporting is visible rather than
silent.

**Matching order:** order number → (date + total) → fuzzy description.

**Findings are reported, never applied.** An unmatched record means *"not
confirmed"*, never *"wrong"*. Auto-correcting records from a lossy source would
be worse than not checking at all.

---

## 7. Photograph import

Directories of photographs are ingested through the same pipeline the
application uses, described in [database-design.md](database-design.md) §8: read
metadata into columns, apply orientation, strip all metadata, verify by
re-reading, hash the cleansed file, store.

Two import-specific points:

**There is generally no reliable link from filename to item.** Camera-default
names carry only a timestamp. Owner-assigned catalogue numbers, where present,
are recorded in `local_catalog_number` but do not resolve to rows on their own.
`item_image.inventory_item_id` stays null and **linking is a manual, UI-assisted
task**, not an import step. Any design that assumes filenames resolve to records
is wrong.

**Assists worth offering, to be validated before relied on:** present unlinked
images in capture order beside candidate items, since both photo sessions and
hand-numbering tend to be chronological; offer images taken seconds apart as
obverse/reverse pairs; narrow subsequent candidates once neighbours are linked.

The import **copies**; it does not move. Original files stay where they are.

---

## 8. Delivery order

| Step | Deliverable | Done when |
|---|---|---|
| 1 | Reference tables and seed data | seeded counts match the catalogue |
| 2 | Core schema, generated columns | arithmetic tests pass on cost basis |
| 3 | Engine: batch, staging, dry-run | source loads verbatim, sha256 recorded |
| 4 | Profile: classify and normalise | kind counts reconcile to the row total |
| 5 | Review UI for the issue queue | queue can be worked to empty |
| 6 | Document parsing and validation | coverage reported per document |
| 7 | Photograph ingest | metadata verified absent post-ingest |

Steps 1–5 stand alone: the collection is in the database and queryable before any
document or photograph work begins.

---

## 9. Risks

**Damage already present in the source cannot be undone.** Identifiers coerced to
numbers have lost information at rest. The importer detects and flags; recovery
is manual, from the physical item or a vendor document.

**Lossy document text.** Mitigated by coordinate-based extraction and coverage
reporting, not eliminated. Validation is advisory.

**Rules drifting into the schema.** Guarded by the engine/profile seam and by
`source` on every reference row, so a value invented by a rule is never
indistinguishable from a curated one.

**Over-investment.** The most likely failure is treating this code as permanent —
polishing correction maps, generalising the profile, writing unit tests for rules
that will be deleted. The measure of success is how quickly it becomes
unnecessary.
