# From spreadsheet to a catalogue you can sell from

The one-off job of getting an existing collection out of a spreadsheet and into
a state where items can be listed. Distinct from
`docs/workflow-new-collection.md`, which covers everything acquired afterwards.

`docs/spreadsheet-import-design.md` describes the import machinery and is
deliberately disposable -- it serves one spreadsheet. This document describes
the **workflow around it**, most of which is not disposable, because the review
and repair tools outlive the import that motivated them.

## Where things stand

The import has run. The collection is loaded.

**Rebuilt 2026-09-07** after seven duplicate rows were removed from the
spreadsheet: four Morgan dollars from one HiBid batch entered on both 9 and 12
January 2025, and a three-note Red Seal set entered on 20 and 23 December. The
rebuild was verified against the prior database restored from a dump -- cost
basis fell by exactly $1,258.70 and fine metal by exactly 3.093760 ozt, which
is four Morgan dollars to six decimal places.

| | |
|---|---|
| Items | 7,591 |
| Cost basis | $534,177.89 |
| Fine metal | 2,358.05 troy oz |
| Classification | 99.6% (the 33 unknowns are blanks and `????`) |
| Every staging row linked to its item | yes |

What the import could do, it did. What remains is the part no importer could
have done, because the information was never in the spreadsheet.

## The four stages

```
1. extract     spreadsheet -> staging          done
2. normalise   staging -> inventory + refs     done
3. reconstruct rebuild the purchase lots       next
4. attribute   describe the individual coins   the long tail
```

### 1 and 2: what the importer already handled

**Categorical references were resolved by code, not by guessing.** Grades,
denominations, countries, metals and mints were matched against the seeded
vocabularies. Where a value did not match, it was **not** invented as a new
vocabulary entry -- an early version did that and produced 185 junk "grades"
including `ACADIANP`. Condition strings are decomposed instead: grade,
designation, grading service and catalogue number come out as separate fields,
and note attributes and seal colours route to their own tables rather than
being stuffed into the grade column.

Unresolvable values survive verbatim in `grade_raw` and
`attributes.rating_unparsed` rather than being dropped. `Mixed` is the
important case -- see below.

**Every run is reversible and reviewable.** A dry run touches no database and
writes review files to `logs/import/`. The database can be rebuilt from scratch
at any time: drop it, `alembic upgrade head`, `python -m app.seeding load`, then
the importer with `--commit`.

### 3: reconstruct the purchase lots

This is the next piece of work and the attribution design specifies it.

The spreadsheet had no way to say "twenty of these", so a roll of 20 Morgan
dollars became 20 rows with an identical description and order number. Half the
collection is like this:

| Population | Count |
|---|---|
| Flattened purchase lots | 769 groups / **3,777 items** |
| Unsplit conglomerates (`storage_quantity > 1`) | 12 items / **240 pieces** |
| Bought individually | 3,809 items |

The first two overlap: **7 of the 12 conglomerates are also inside a flattened
group** -- rows like `20x 1oz Copper Round Mixed` repeated nine times, each row
holding twenty rounds. Lots of lots. They need reconstructing *and* splitting,
and any tool that assumes the populations are disjoint will mishandle them.

Reconstruction creates the parent the spreadsheet flattened away: one
`inventory_item` per group holding the shared order and description, with
`price` and `shipping` summed and `split_at` set, and the members pointed at it
by `parent_item_id`.

**The grouping rule is a heuristic and will be wrong somewhere.** Two genuinely
separate purchases of the same item on one order merge into one lot. The 44
groups where a description says "LOT OF *n*" but *n* does not match the row
count are direct evidence. So:

- the backfill writes a report naming every group it created and its members,
  and that report exists to be read
- the repair tools -- detach a child, delete a childless parent, group items
  manually -- ship *with* the backfill, not after it

Running a backfill you cannot undo, over 769 guesses, is how a catalogue
becomes untrustworthy in one transaction.

**Verification is the step that matters.** After reconstruction, cost basis
across non-split, non-deleted items must still be exactly $534,177.89. The
backfill creates 769 rows that hold money; if any is counted alongside its
children, the collection's value silently inflates.

### 4: attribute the individual coins

The long tail, and the only stage with no shortcut. Each of the 3,777 coins in
a reconstructed lot carries its parent's description and nothing of its own.

Measured, over 7,591 items excluding split parents:

| Anomaly | Count |
|---|---|
| No grade (coins and currency only) | 2,965 |
| No country | 2,394 |
| No year | 1,230 |
| Bullion with no weight | 712 |
| No denomination (coins and currency) | 262 |
| `Mixed` marker in grade or description | 186 rows, 43 groups |
| Zero or missing price | 51 |
| "LOT OF *n*" disagreeing with the row count | 44 |
| Kind still `unknown` | 33 |
| Repeated currency serial numbers | 18 notes, 9 groups |
| Repeated certification numbers | 108 rows, 48 numbers |
| Star notes not marked as star notes | 28 |

**`Mixed` deserves separate treatment.** It was used for lot attributes where
the individuals vary, and it means something different from a blank: not
"unknown" but "known to vary". It is a positive statement that the row stands
for several different coins, so the remedy is to decompose the purchase lot,
not to fill in a field. The import correctly refused to turn it into a grade.

**Not every anomaly is an error.** 712 bullion rounds have no weight and 713
have no grade, because a generic silver round has neither. This is why the
anomaly checks are kind-aware: `no_grade` means *a coin or banknote with no
grade*, so the 2,965 real cases are not buried under rounds that will never
have one.

## Finding the spreadsheet row behind an item

For the imported collection only:

```
spreadsheet row = item code number + 1
```

`CC-000001` is row 2; `CC-007591` is row 7592. The
offset is the header row and it is exactly 1 for every one of the 7,591 items
-- verified, not sampled. It holds because `item_code`, `inventory_item.id` and
`import_row.id` were all assigned in the same pass.

This makes cleanup much faster, since an anomaly found in the database can be
checked against the original cell. But it is a **historical accident, not a
guarantee**:

- It applies only to `CC-000001` through `CC-007591`. Anything created
  afterwards -- including the 769 reconstructed purchase-lot parents --
  corresponds to no spreadsheet row.
- Nothing maintains it. Insert or delete a spreadsheet row and it is gone.

The authoritative link is `import_row.inventory_item_id`, which survives all of
that. Use the arithmetic to find a row by eye; use the join in code.

After the 2026-09-07 rebuild there are **no gaps**: `CC-000001` through
`CC-007591`, with the sequence at 7,591.

Gaps can still appear later and never mean a missing item, because a code is
issued once and never reused. They are not caused by the test suite, though --
`conftest.py` creates a **separate `ccwebdb_test` database**, drops it
afterwards, and never touches the real one or its sequence. The gaps in the
previous database came from the five `app.seed` demo items and from local
experiments.

## Excel destroys long identifiers, silently

Six `Grading#` values are gone and cannot be recovered from any file:

```
5.0157E+14   1.92405E+15   2.00872E+14   5.01791E+14   5.02087E+14 (x2)
```

The cell contains that as **literal text** -- six significant digits where
fifteen used to be. The TSV export carries the same, so the damage predates it.
They need re-reading from the slabs or the vendor's order confirmation, and the
importer flags them as errors rather than storing a rounded lie, which is why a
run containing them exits non-zero.

Two separate mechanisms cause this:

- **Excel holds 15 significant digits.** `1.92405E+15` was a 16-digit number,
  so it lost precision *even stored as a number*. The tail is zeroed silently.
- **General format displays long numbers as scientific notation**, and that
  display becomes the stored value once the sheet round-trips through CSV or is
  pasted as values.

**Format identifier columns as Text before entering anything** -- `Grading#`,
`Order Number`, serial numbers. A certificate number is an identifier, not a
quantity; nothing sensible ever adds two of them. For a value already in a
General cell, prefix it with an apostrophe (`'501570000000000`), which forces
text and is not itself stored.

Currently no value is at risk: the longest identifiers are 11 digits and
nothing of 12 or more is stored as a number. The exposure is prospective.

## Working through it

The tools are search and edit, not a wizard.

```
search for an anomaly     ?issue=no_year          1,230 items
narrow to one purchase    &lot=CC-004120          23 Morgans
bulk-set what they share  year 1921, mint P       23 updated
review the exceptions     one at a time           2 differ
```

Bulk-set first, then walk the exceptions. A run of similar coins is mostly
alike with a few standouts, and setting the common values in one action leaves
only the interesting ones to handle individually.

The queue freezes when you enter it. Fixing an item's missing year would
otherwise remove it from `?issue=no_year` mid-walk, shrinking the set and
shifting every position after it -- silently skipping a coin.

## Order of operations, and why

1. **Reconstruct lots before attributing.** Attributing 3,777 loose rows first
   means doing it again once they are grouped, because bulk-set only helps when
   the system knows which coins belong together.
2. **Split the 12 conglomerates before attributing them.** They hold 240 coins
   between them and cannot be described individually until they are individual
   rows. Check `storage_quantity` against the descriptions first -- those
   counts were themselves parsed from the spreadsheet.
3. **Attribute before listing.** An item still carrying its parent's guessed
   grade is not ready to be described to a buyer.
4. **List before designing the storefront.** The public catalogue currently has
   zero rows, and a card layout cannot be judged against no data.

## What is deliberately left alone

- **2,046 declined ratings** kept verbatim in `grade_raw`. Common ones are
  `Silver`, `Funny Back`, and bare numbers like `65 PCGS` where the grade prefix
  is implied but not written. These need a decision per pattern, not a rule.
- **The 33 `derived` classifiers** must be reviewed before anyone runs
  `python -m app.seeding export --source derived`, or one installation's
  guesses become another's seeded vocabulary.
- **~673 photographs** in `OneDrive/Documents/coins/Classified/SafetyDeposit804`
  and `809` are unlinked. Bulk-matching them to items is its own job.
- **`fed_district_letter` is empty for every note**, because the spreadsheet
  never carried it. It gets filled during attribution, from the notes
  themselves.
