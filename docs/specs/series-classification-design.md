# Series classification by denomination and year

Design. Status: **draft for the owner's review** (2026-09-16). Nothing here is
built or seeded yet.

## The problem

The owner wants a nickname to find what it names: `mercury` should find dimes
of the right years, `funnyback` the right $1 notes -- whether or not any
listing ever used the word.

Today a nickname finds only items **already classified** into a design series
(`inventory_item.series_id`), plus text that happens to contain the word.
Classification comes from `app.series_match`, which reads description text
alone. Measured on 2026-09-16:

| | |
|---|---|
| coins with a year and no series | **1,326** (455 dollars, 254 halves, 209 cents, 157 quarters, 74 dimes, 73 nickels, 104 gold) |
| currency with a series | **0** of 1,114 -- no currency design series exists |
| series with a denomination recorded | **0** of 38, though the column exists |
| notes with a note type recorded | **0** of 1,114 |
| notes with a seal colour recorded | a minority (most `$1` Series 1928/1934 have none) |
| items rated "funnyback" | 48, all in `grade_raw` only |

## Decisions already made

| Question | Decision |
|---|---|
| Find by guessing at search time, or by classifying? | **Classify, then search.** A reviewable pass assigns a series to unclassified items from the design's facts. Search stays exact (`series_id`), and the Series column and dropdown show the result. |
| Coverage of the first version | **The 38 existing coin designs, plus a starter set of note designs** found in this collection. |

## What a design is

A design (`series`) already has a label, nicknames (`series_alias`), a year span
and `applies_to` (`coin` / `currency`). It gains what classification needs:

| Fact | Where | Notes |
|---|---|---|
| Denomination | `series.denomination_id` (exists, empty) | Nullable: the gold series span several denominations and stay text-matched only. |
| **One or more year ranges** | **new `series_year_range`** (series, year_start, year_end nullable) | One span cannot say "Morgan: 1878-1904, 1921, 2021 on". Without the gaps, "a dollar in the Morgan span" is every dollar coin since 1878 -- Peace, Ike, Anthony, Sacagawea. `series.year_start/end` stay as the overall span for display. |
| Series letters (notes) | on the range: optional allowed letters | A note's year is `currency_detail.series_year`; its letter is `series_letter`. The Barr note is $1 **1963B only**. |
| **Needs evidence** | `series.needs_evidence` boolean | The design shares its denomination and series with ordinary notes, so the facts alone do not decide. Hawaii and North Africa notes are 1934/1935A notes like any other except for the seal and overprint. |
| Seal colour (notes) | `series.seal_color_id`, optional | Evidence that decides a needs-evidence design: brown for Hawaii, yellow for North Africa. |

## The classification pass: `python -m app.series_classify`

Reports by default and writes only with `--commit`, like `app.series_match`.
Items already classified are never touched, and anything written is
`derived`, so a hand correction always outranks it.

For each unclassified item, the **candidates** are the designs whose
`applies_to` matches its kind, whose denomination is its denomination, and one
of whose ranges contains its year (and letter, for notes). Then:

| Candidates | Evidence | Result |
|---|---|---|
| exactly one, not needs-evidence | -- | **assign** |
| exactly one, needs-evidence | seal colour or text names it | **assign** |
| exactly one, needs-evidence | none | **leave**; the item is an ordinary note of that series |
| several (a boundary year) | text or seal names exactly one | **assign** that one |
| several | none, or it names more than one | **review list** |
| none, but the text names a design | -- | **review list: conflict** |

"Text names it" reuses `series_match`'s own vocabulary -- labels, aliases and
its extra patterns -- over **title, description and rating**, since the rating
is where 48 of 48 funnybacks say so.

**Boundary years** produce most of the review list: 1856-57 cents (Large and
Flying Eagle), 1883 nickels (Shield and Liberty Head), 1909 cents (Indian Head
and Lincoln), 1916 dimes and quarters (Barber and the new designs), 1921
dollars (Morgan and Peace), 1938 nickels (Buffalo and Jefferson).

**Conflicts are findings, not errors to suppress.** Measured now: three `$1`
notes rated "funnyback" are recorded as **Series 1923** (large-size notes,
never funnybacks) and one as **Series 1935** (standard back). Either the
rating or the series year is wrong, and only the note in hand can say which.
The pass names them and assigns nothing.

The report prints, per design: how many would be assigned, how many are
boundary cases, and each conflict with its item code. **The owner sees the
counts before anything is written** -- this is the live database.

## Search

Unchanged in mechanism: a term matching a design's label or nickname already
returns items with that `series_id`. Once classified, `mercury` finds every
Winged Liberty Head dime and `funnyback` every funnyback, and the **Series**
dropdown on the currency screen -- disabled today, "None recorded" -- starts
offering them. (Searching the rating text, added 2026-09-16, stays: it finds
what classification cannot, such as a note the pass left for review.)

## Facts to seed

Facts only -- designs, year spans, denominations, nicknames -- per the
project's reference-data rule. No catalogue numbering.

### Coins: fill in denominations, and add the year gaps

All 38 designs get their denomination except the three gold series that span
several (Liberty Head Gold, Indian Head Gold stay text-only; Saint-Gaudens is
$20 only and gets it). Ranges that differ from today's single span:

| Design | Today | Proposed ranges |
|---|---|---|
| Morgan Dollar | 1878 - open | 1878-1904, 1921, 2021- |
| Peace Dollar | 1921 - open | 1921-1928, 1934-1935, 2021- |

To verify before seeding: any other design with a modern commemorative
re-strike on its original denomination (the 2016 gold centennial Mercury dime,
Standing Liberty quarter and Walking Liberty half are gold, on their original
face values). **Owner's call:** should those count as the design?

### Notes: a starter set

Chosen from what this collection actually contains:

| Design | Denomination | Series | Needs evidence | In the collection |
|---|---|---|---|---|
| **Funnyback** | $1 | 1928, 1928A-E, 1934 | no (see below) | 48 rated; up to 50 by definition |
| **Barr note** | $1 | 1963B | no | 10 mention "Barr" |
| **North Africa** | $1, $5, $10 | 1935A; 1934A; 1934, 1934A | yes -- yellow seal | 4 |
| **Hawaii** | $1, $5, $10, $20 | 1935A; 1934, 1934A; 1934A; 1934, 1934A | yes -- brown seal | 2 |

Nicknames: Funnyback -> "Funny Back"; Barr note -> "Barr"; Hawaii ->
"Hawaii overprint"; North Africa -> "yellow seal".

Not designs, so not here: *star note* (a serial feature, already derived),
*blue / red seal* (already a dropdown), *confederate*, *obsolete*,
*fractional* (issuers and eras, not one denomination and series).

**Owner's call on Funnyback.** Wikipedia defines it as the $1 **Silver
Certificate** of Series 1928 and 1934. Dealers also sell the 1928 **red-seal
United States Note** (Fr. 1500, the only small-size $1 red seal, and only
Series 1928) as a funnyback, since it shares the back design. If that counts,
every $1 Series 1928 note is a funnyback and no evidence is needed. If it does
not, plain Series 1928 needs a blue seal or a "funnyback" rating -- which in
this collection all ten have.

## Testing

- The pass, on fixture items: single candidate assigned; needs-evidence left
  alone without seal or text, assigned with either; boundary year sent to
  review, and resolved by text; conflict reported, not assigned; a manual
  series never overwritten; a re-run changes nothing.
- The Morgan gap: a 1910 dollar is not Morgan, a 1921 dollar is a boundary case.
- Seeding round-trips through `app.seeding export`.
- Search: a classified funnyback with no nickname in its text is found by
  `funnyback`.

## Out of scope

- A console page for the review list. The report is the review for now; the
  existing issue chips (`repeated_identity`, `star_mismatch` ...) are the
  natural home for a later "series conflict" check.
- Friedberg numbering, which would decide note designs exactly -- it is not
  free to seed.
- Note type: recorded on no note, so no design here depends on it.

## Sources

- Funnyback: https://en.wikipedia.org/wiki/Funnyback
- $1 1928 red seal only Series 1928, Fr. 1500:
  https://oldcurrencyvalues.com/1928_one_dollar_red_seal/
- $1 Silver Certificates 1928-1928E:
  https://oldcurrencyvalues.com/1928_blue_seal_one_dollars/
- To be cited when verified: Morgan and Peace mintage years; Barr, Hawaii and
  North Africa series and denominations.
