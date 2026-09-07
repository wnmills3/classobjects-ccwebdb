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

| | |
|---|---|
| Items | 7,598 |
| Cost basis | $535,436.59 |
| Fine metal | 2,361.14 troy oz |
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
| Flattened purchase lots | 770 groups / **3,780 items** |
| Unsplit conglomerates (`storage_quantity > 1`) | 12 items / **240 pieces** |
| Bought individually | 3,813 items |

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

Running a backfill you cannot undo, over 770 guesses, is how a catalogue
becomes untrustworthy in one transaction.

**Verification is the step that matters.** After reconstruction, cost basis
across non-split, non-deleted items must still be exactly $535,436.59. The
backfill creates 770 rows that hold money; if any is counted alongside its
children, the collection's value silently inflates.

### 4: attribute the individual coins

The long tail, and the only stage with no shortcut. Each of the 3,780 coins in
a reconstructed lot carries its parent's description and nothing of its own.

Measured, over 7,598 items excluding split parents:

| Anomaly | Count |
|---|---|
| No grade (coins and currency only) | 2,970 |
| No country | 2,395 |
| No year | 1,230 |
| Bullion with no weight | 712 |
| No denomination (coins and currency) | 263 |
| `Mixed` marker in grade or description | 186 rows, 43 groups |
| Zero or missing price | 51 |
| "LOT OF *n*" disagreeing with the row count | 44 |
| Kind still `unknown` | 33 |
| Duplicate currency serial numbers | 18 notes, 9 groups |

**`Mixed` deserves separate treatment.** It was used for lot attributes where
the individuals vary, and it means something different from a blank: not
"unknown" but "known to vary". It is a positive statement that the row stands
for several different coins, so the remedy is to decompose the purchase lot,
not to fill in a field. The import correctly refused to turn it into a grade.

**Not every anomaly is an error.** 712 bullion rounds have no weight and 713
have no grade, because a generic silver round has neither. This is why the
anomaly checks are kind-aware: `no_grade` means *a coin or banknote with no
grade*, so the 2,970 real cases are not buried under rounds that will never
have one.

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

1. **Reconstruct lots before attributing.** Attributing 3,780 loose rows first
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
