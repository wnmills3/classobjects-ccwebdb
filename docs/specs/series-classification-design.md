# Series classification by denomination and year

Design. Status: **approved by the owner 2026-09-16**, with the decisions
recorded below; facts verified against the sources at the end.

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
| notes with a seal colour recorded | a minority (most $1 Series 1928 and 1934 have none) |
| items rated "funnyback" | 48, the word only in `grade_raw` |

## Decisions

| Question | Decision |
|---|---|
| Find by guessing at search time, or by classifying? | **Classify, then search.** A reviewable pass assigns a series to unclassified items from the design's facts. Search stays exact (`series_id`), and the Series column and dropdown show the result. |
| Coverage of the first version | **The 38 existing coin designs, plus a starter set of note designs** found in this collection. |
| Is the 1928 red-seal $1 United States Note a funnyback? | **Yes.** Every $1 Series 1928 note is one, whatever its seal; no evidence needed. |
| Do the 2016 gold centennials count as their designs? | **Yes.** 2016 joins the Mercury dime, Standing Liberty quarter and Walking Liberty half -- which makes 2016 a boundary year for all three. |
| The starter note set | **Approved as proposed.** |

## What a design is

A design (`series`) already has a label, nicknames (`series_alias`), a year span
and `applies_to` (`coin` / `currency`). It gains what classification needs:

| Fact | Where | Notes |
|---|---|---|
| Denomination | `series.denomination_id` (exists, empty) | Null for designs that span several faces, which stay matched by name only. |
| One or more year ranges | new `series_year_range` | One span cannot say "Morgan: 1878-1904, 1921, 2021 on". Without the gaps, "a dollar in the Morgan span" is every dollar coin since 1878 -- Peace, Ike, Anthony, Sacagawea. `series.year_start` / `year_end` stay as the overall span for display. |
| Denomination per range | `series_year_range.denomination_id` | Hawaii and North Africa pair **different denominations with different series** -- $1 with 1935A, $5 with 1934A -- so the denomination belongs on the range. Null falls back to the design's own. |
| Series letters (notes) | `series_year_range.letters` | A note's year is `currency_detail.series_year`, its letter `series_letter`. Null means any letter. Otherwise the allowed letters, with `*` for none: `*A` is plain 1934 and 1934A. The Barr note is 1963, letters `B`. |
| Needs evidence | `series.needs_evidence` | The design shares its denomination and series with ordinary notes, so the facts alone do not decide: a Hawaii note is a 1934 or 1935A note like any other, apart from its seal and overprint. |
| Seal colour | `series.seal_color_id`, optional | Evidence that decides a needs-evidence design: brown for Hawaii, yellow for North Africa. |

A design with no range rows is matched on its own span and denomination, so
the coin designs whose span is already right need no rows.

## A fix this depends on

`app.series_match` matches every design's names against every item's text,
whatever `applies_to` says. Adding a "Hawaii" nickname would classify a
"2008 Hawaii State Quarter" as a Hawaii overprint note. It must consider only
designs for the item's own inventory: currency for notes, coin otherwise.

## The classification pass: `python -m app.series_classify`

Reports by default and writes only with `--commit`, like `app.series_match`.
Items that already have a series are never touched, so a hand correction
always outranks it.

It considers only items with a known denomination and a single year -- for a
note, its series year. For each, the **candidates** are the designs whose
`applies_to` matches its inventory and one of whose ranges covers its
denomination and year (and letter, for a note). **Evidence** is the text --
title, description and rating, read with `series_match`'s own vocabulary --
or, for a note, a seal colour matching the design's. Then:

| Candidates | Evidence | Result |
|---|---|---|
| exactly one, not needs-evidence | -- | **assign** |
| exactly one, needs-evidence | seal colour or text names it | **assign** |
| exactly one, needs-evidence | none | **leave**: an ordinary note of that series |
| several (a boundary year) | names exactly one of them | **assign** that one |
| several | none, or names more than one | **review list** |
| any | text names only designs the item cannot be | **review list: conflict**, nothing assigned |

A conflict is judged only against designs whose ranges are known, so text
naming a bullion design (which has no denomination) is never a conflict. Nor
is text that names a candidate as well: "Abraham Lincoln Presidential Dollar"
names the Lincoln cent too, and the facts settle which it is.

`series_match` no longer classifies notes at all. It believes text alone, so
it would file the Series 1923 note rated "funnyback" as a Funnyback; this pass
checks the note's series first.

**Boundary years** make most of the review list: 1856-57 cents (Large and
Flying Eagle), 1873 dollars (Seated and Trade), 1878-85 dollars (Trade and
Morgan), 1883 nickels (Shield and Liberty Head), 1909 cents (Indian Head and
Lincoln), 1913 nickels (Liberty Head and Buffalo), 1916 dimes and quarters
(Barber and the new designs), 1921 dollars (Morgan and Peace), 1938 nickels
(Buffalo and Jefferson), 2016 dimes, quarters and halves (the gold
centennials), 2021 dollars and quarters.

**Conflicts are findings, not errors to suppress.** Measured now: three $1
notes rated "funnyback" are recorded as Series 1923 (large-size notes, never
funnybacks) and one as Series 1935 (standard back). Either the rating or the
series year is wrong, and only the note in hand can say which. The pass names
them and assigns nothing.

The report prints, per design, how many would be assigned, the boundary cases
and each conflict with its item code. **The owner sees the counts before
anything is written** to the live database.

## Refinements from the first measured run (2026-09-16)

The pass was run against a copy of the live database before anything was
written, and the audit changed three rules. Each is covered by a test.

**Lot text is not evidence about a piece.** 3,777 items share their
description word for word with another item of the same order: a lot's pieces
are imported carrying the lot's listing. On the first run 30 of 38 conflicts
were one such listing ("Coin Collection ... Large Cents ... Morgan ...") on
pieces whose own year and denomination were plain -- a 1943 cent is a Lincoln
cent. For a piece whose title and description are shared, only its rating
counts as text. Lot text can still send a piece to review (below), never
assign it.

**Evidence-only coin designs.** The audit found commemoratives filed as
circulating designs -- 1990s commemorative halves as Kennedy halves,
commemorative dollars as Sacagawea dollars -- and an 1852 gold dollar as a
Seated Liberty dollar. A design that shares its denomination and years with a
far commoner one is now `needs_evidence`, exactly like the Hawaii note: it is
assigned only when the piece's text names it, and an item that says nothing is
taken for the common design. Added, facts only:

| Design | Denomination: years | Nicknames |
|---|---|---|
| Commemorative Half Dollar | 50c: 1892-1954, 1982- | Commemorative(s), Commem, Comem |
| Commemorative Dollar | $1: 1900-1922, 1983- | the same |
| Gold Dollar | $1: 1849-1889 | (its label) |
| American Innovation Dollar | $1: 2018-2032 | American Innovation, Innovation Dollar |

When lot text names an evidence-only candidate -- "Lot of 3 Washington/Carver
Commemorative Half Dollars" -- the piece goes to review rather than to the
common design (a 1952 half there is not safely a Franklin).

Sources: https://en.wikipedia.org/wiki/Gold_dollar,
https://en.wikipedia.org/wiki/Early_United_States_commemorative_coins,
https://en.wikipedia.org/wiki/Modern_United_States_commemorative_coins,
https://en.wikipedia.org/wiki/American_Innovation_dollars. The first modern
commemorative dollar is the 1983 Los Angeles Olympiad dollar.

**A conflict needs the text to name only impossible designs** (above), and the
report adds a third, read-only section: **disagrees**, items whose series is
already set but which the facts rule out. The first run found 61, all real: a
Franklin Pierce dollar matched as a Franklin half by `series_match`, a Silver
Buffalo commemorative matched as a Buffalo nickel, an 1878-S Morgan recorded
as 1978, Walking Liberty and Kennedy halves recorded as $1. The pass changes
none of them.

What the facts cannot catch: an item recorded with the wrong country or face
value (a UK penny recorded as a US cent, a silver medal recorded as a dime) is
classified by what was recorded. Unworded modern commemoratives (an Apollo 11
half) are still taken for Kennedys. American Women quarters (2022-2025) are
filed as Washington quarters, whose obverse they carry.

Boundary cases the list above did not name: $1 of 2007-2016 and 2020
(Presidential and Sacagawea).

## Search

Unchanged in mechanism: a term matching a design's label or nickname already
returns items with that `series_id`. Once classified, `mercury` finds every
Winged Liberty Head dime and `funnyback` every funnyback, and the **Series**
dropdown on the currency screen -- disabled today, "None recorded" -- starts
offering them. Searching the rating text, added 2026-09-16, stays: it finds
what classification cannot, such as a note the pass left for review.

## Facts to seed

Facts only -- designs, year spans, denominations, nicknames -- per the
project's reference-data rule. No catalogue numbering.

### Coins

Every coin design gets its denomination, except the ones that span several
faces or would swamp the pass:

- **Liberty Head Gold** and **Indian Head Gold** span several denominations.
- **The four bullion designs** (Silver, Gold and Platinum Eagle, Gold Buffalo):
  a Silver Eagle's $1 face would make every dollar coin since 1986 a boundary
  case, and the gold ones span several faces. They stay matched by name, which
  is how they are sold.

Saint-Gaudens is $20 only and gets it. Ranges that differ from today's span:

| Design | Today | Ranges |
|---|---|---|
| Morgan Dollar | 1878 - open | 1878-1904, 1921, 2021- |
| Peace Dollar | 1921 - open | 1921-1928, 1934-1935, 2021- (the 1964 strikes were melted) |
| Winged Liberty Head Dime | 1916-1945 | 1916-1945, 2016 |
| Standing Liberty Quarter | 1916-1930 | 1916-1930, 2016 |
| Walking Liberty Half Dollar | 1916-1947 | 1916-1947, 2016 |
| Susan B. Anthony Dollar | 1979-1999 | 1979-1981, 1999 |
| Presidential Dollar | 2007-2020 | 2007-2016, 2020 |
| Washington Quarter | 1932 - open | 1932-1998, 2021- (the State and ATB programmes are their own designs, as PCGS files them) |

### Notes: the starter set

Chosen from what this collection actually contains:

| Design | Denomination: series | Needs evidence | In the collection |
|---|---|---|---|
| **Funnyback** | $1: 1928 (any letter, any seal), 1934 | no | 48 rated; 50 by definition |
| **Barr Note** | $1: 1963B | no | 10 mention "Barr" |
| **North Africa** | $1: 1935A; $5: 1934A; $10: 1934, 1934A | yes -- yellow seal | 4 |
| **Hawaii** | $1: 1935A; $5, $10, $20: 1934, 1934A | yes -- brown seal | 0 (an earlier count of 2 was coins: the Hawaii state quarter) |

Nicknames: Funnyback -> "Funny Back"; Barr Note -> "Barr"; Hawaii ->
"Hawaii Overprint"; North Africa -> "Yellow Seal".

Sources disagree on whether the Hawaii $10 includes plain Series 1934. It is
included: Hawaii needs evidence, so a wider range cannot assign it wrongly.
`yellow` is added to the seal colours, which lacked it.

**Funnyback, decided.** Wikipedia defines it as the $1 Silver Certificate of
Series 1928 and 1934; dealers also sell the 1928 red-seal United States Note
(Fr. 1500, the only small-size $1 red seal) as one, since it shares the back.
The owner counts it, so every $1 Series 1928 note is a funnyback.

Not designs, so not here: *star note* (a serial feature, already derived),
*blue / red seal* (already a dropdown), *confederate*, *obsolete*,
*fractional* (issuers and eras, not one denomination and series).

## Testing

- The pass, on fixture items: single candidate assigned; needs-evidence left
  alone without seal or text, assigned with either; boundary year sent to
  review, and resolved by text; conflict reported and not assigned; an item
  with a series never touched; a re-run changes nothing.
- The Morgan gap: a 1910 dollar is not Morgan; a 1921 dollar is a boundary case.
- Letters: $1 1963B is a Barr Note, $1 1963A is not.
- `series_match`: a coin whose text says "Hawaii" is not a Hawaii note.
- Seeding: ranges load, re-load unchanged, and follow the file when it changes.
  Export (`app.seeding export`) carries neither ranges nor aliases, as it
  carried no aliases before; the seed file is their source.
- Search: a classified funnyback with no nickname in its text is found by
  `funnyback`.

## Out of scope

- A console page for the review list. The report is the review for now; the
  issue chips are the natural home for a later "series conflict" check.
- Friedberg numbering, which would decide note designs exactly -- it is not
  free to seed.
- Note type: recorded on no note, so no design here depends on it.

## Sources

- Funnyback: https://en.wikipedia.org/wiki/Funnyback
- $1 1928 red seal, only Series 1928, Fr. 1500:
  https://oldcurrencyvalues.com/1928_one_dollar_red_seal/
- $1 Silver Certificates 1928-1928E:
  https://oldcurrencyvalues.com/1928_blue_seal_one_dollars/
- Morgan and Peace years: https://en.wikipedia.org/wiki/Morgan_dollar,
  https://en.wikipedia.org/wiki/Peace_dollar
- Barr Note, $1 1963B only: https://oldcurrencyvalues.com/joseph_barr_signature/
- Hawaii: https://coinweek.com/hawaii-overprint-currency-note/
- North Africa: https://oldcurrencyvalues.com/north_africa_currency/
- 2016 centennials:
  https://www.usmint.gov/learn/coins-and-medals/collectible-coins/centennial-gold-coins
