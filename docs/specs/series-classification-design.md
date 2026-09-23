# Series classification by denomination and year

A nickname finds what it names: `mercury` finds Winged Liberty Head dimes and
`funnyback` the right $1 notes, whether or not any listing used the word. That
works by **classifying, then searching**: items are assigned a design series
(`inventory_item.series_id`) from their facts, and search matches a term
against each design's label and nicknames exactly. Search also reads the
title, description and rating text, which finds what classification cannot,
such as a note left for review.

Two passes assign series:

- `app.series_match` reads what a description says ("1883-O AU/UNC MORGAN
  SILVER DOLLAR"). It matches **coins only, against coin designs**; ambiguous
  names (Barber, Seated Liberty, Indian Head, Liberty Head) need the
  denomination to decide, and an item whose denomination is unknown is left
  alone.
- `app.series_classify` assigns from the facts -- denomination, year and, for
  a note, series letter -- and is the only pass that classifies notes.

Both report by default and write only with `--commit`. Neither touches an item
that already has a series, so a hand correction always stands. Each records
what it wrote in `item_field_source` (`series_match`, `series_classify`), per
`classifier-defaults-design.md`.

## What a design is

A design (`series`) has a label, nicknames (`series_alias`), an overall year
span for display, and `applies_to` (`coin` / `currency`). For classification:

| Fact | Where | Notes |
|---|---|---|
| Denomination | `series.denomination_id` | Null for designs that span several faces; they are matched by name only. |
| Year ranges | `series_year_range` | One span cannot say "Morgan: 1878-1904, 1921, 2021 on"; without the gaps every dollar since 1878 would be a Morgan candidate. A design with no range rows uses its own span and denomination. |
| Denomination per range | `series_year_range.denomination_id` | Hawaii and North Africa pair different denominations with different series. Null falls back to the design's. |
| Series letters (notes) | `series_year_range.letters` | Null means any letter; otherwise the allowed letters, `*` for none. `*A` is plain 1934 and 1934A. |
| Needs evidence | `series.needs_evidence` | The design shares its denomination and years with a commoner one, so the facts alone do not decide. |
| Seal colour | `series.seal_color_id` | Evidence for a needs-evidence note design. |
| Note class | `series.note_type_id` | A design belonging to one note class. |

## The pass: `python -m app.series_classify`

It considers items with a known denomination and a single year (for a note,
its series year). **Candidates** are the designs for the item's inventory whose
ranges cover its denomination, year and letter. **Evidence** is text -- title,
description and rating, read with `series_match`'s vocabulary -- or, for a
note, a seal colour matching the design's, or a recorded note class matching
the design's.

| Candidates | Evidence | Result |
|---|---|---|
| exactly one, not needs-evidence | -- | **assign** |
| exactly one, needs-evidence | present | **assign** |
| exactly one, needs-evidence | none | **leave** (an ordinary item of that year) |
| several (a boundary year) | names exactly one | **assign** that one |
| several | none, or more than one | **review: boundary** |
| any | text names only designs the item cannot be | **review: conflict**, nothing assigned |

Rules that keep it honest:

- **Lot text is not evidence about a piece.** A lot's pieces are imported
  carrying the lot's listing, so for a piece whose title and description are
  shared with another item of the same order, only its rating counts as
  text. Lot text can still send a piece to review -- "Lot of 3
  Washington/Carver Commemorative Half Dollars" sends a 1952 half to review
  rather than to Franklin -- but never assigns it.
- **A conflict needs the text to name only impossible designs.** It is judged
  only against designs whose ranges are known, so text naming a bullion design
  is never a conflict; nor is text that names a candidate as well ("Abraham
  Lincoln Presidential Dollar" names the Lincoln cent too).
- **A note class excludes.** A design of another class than the note's
  recorded class is not a candidate: a Federal Reserve Bank Note whose text
  says "Brown Seal" is a conflict, not a Series 1929 National.
- **Conflicts are findings, not errors to suppress.** A $1 note rated
  "funnyback" but recorded as Series 1923 has a wrong rating or a wrong year;
  only the note in hand can say which.

The report prints, per design, what would be assigned, the boundary cases, each
conflict with its item code, and a read-only **disagrees** list: items whose
series is already set but which the facts (or the recorded note class) rule
out. The pass changes none of those.

`scripts\ccweb_rebuild.cmd` runs `series_match`, then `classifier_defaults`
(which decides a note's class, evidence for the Series 1929 designs), then
`series_classify`.

**Boundary years** make most of the review list: 1856-57 cents, 1873 and
1878-85 dollars, 1883 and 1913 and 1938 nickels, 1909 cents, 1916 dimes and
quarters, 1921 dollars, 2016 dimes, quarters and halves (the gold
centennials), $1 of 2007-2016 and 2020 (Presidential and Sacagawea), 2021
dollars and quarters.

**What the facts cannot catch:** an item recorded with the wrong country or
face value is classified by what was recorded; unworded modern commemoratives
(an Apollo 11 half) are taken for Kennedys; American Women quarters are filed
as Washington quarters, whose obverse they carry.

## The designs

Seeded from `backend/data/reference/series.json`: facts only -- designs, year
spans, denominations, nicknames -- per the reference-data rule. No catalogue
numbering.

### Coins

Every circulating coin design has its denomination, except those that span
several faces or would swamp the pass: **Liberty Head Gold** and **Indian Head
Gold** span several denominations, and the four **bullion designs** (Silver,
Gold and Platinum Eagle, Gold Buffalo) stay matched by name -- a Silver
Eagle's $1 face would make every dollar since 1986 a boundary case.

Designs with more than one range:

| Design | Ranges |
|---|---|
| Morgan Dollar | 1878-1904, 1921, 2021- |
| Peace Dollar | 1921-1928, 1934-1935, 2021- |
| Winged Liberty Head Dime | 1916-1945, 2016 |
| Standing Liberty Quarter | 1916-1930, 2016 |
| Walking Liberty Half Dollar | 1916-1947, 2016 |
| Susan B. Anthony Dollar | 1979-1981, 1999 |
| Presidential Dollar | 2007-2016, 2020 |
| Washington Quarter | 1932-1998, 2021- (State and ATB quarters are their own designs) |

**Evidence-only coin designs** share denomination and years with a far
commoner design, so they are assigned only when the piece's text names them,
and an item that says nothing is taken for the common design:

| Design | Denomination: years | Nicknames |
|---|---|---|
| Commemorative Half Dollar | 50c: 1892-1954, 1982- | Commemorative(s), Commem, Comem |
| Commemorative Dollar | $1: 1900-1922, 1983- | the same |
| Gold Dollar | $1: 1849-1889 | (its label) |
| American Innovation Dollar | $1: 2018-2032 | American Innovation, Innovation Dollar |

### Notes

| Design | Denomination: series | Needs evidence | Nicknames |
|---|---|---|---|
| Funnyback | $1: 1928, 1934 (any letter, any seal) | no | Funny Back |
| Barr Note | $1: 1963B | no | Barr |
| North Africa | $1: 1935A; $5: 1934A; $10: 1934, 1934A | yes -- yellow seal | Yellow Seal |
| Hawaii | $1: 1935A; $5, $10, $20: 1934, 1934A | yes -- brown seal | Hawaii Overprint |
| Series 1929 National Bank Note | $5-$100: 1929, no letter | yes -- note class National Bank Note | Brown Seal, 1929 National, Small Size National, National, Natl |

- **Every $1 Series 1928 note is a Funnyback**, whatever its seal: the 1928
  red-seal United States Note shares the back, and the owner counts it.
- **Hawaii $10 includes plain Series 1934.** Sources disagree; since Hawaii
  needs evidence, a wider range cannot assign it wrongly.
- **The Series 1929 National's evidence is its class, not its seal.** The
  Series 1929 Federal Reserve Bank Notes carry the same brown seal. The class
  comes from `classifier_defaults`, which reads the rating ("Fed Res",
  "Federal Reserve Bank" name a Federal Reserve Bank Note; "National", "Natl"
  a National Bank Note). A 1929 note whose rating names neither ("Chase NYC
  2370") stays for a person.

Not designs, so not here: *star note* (a serial feature), *blue / red seal*
(a field), *confederate*, *obsolete*, *fractional* (issuers and eras).

Seeding follows the file: ranges and aliases load, re-load unchanged, and
change when the file does. `app.seeding export` carries neither ranges nor
aliases; the seed file is their source.

## Not built

- A console page for the review list: the pass's report is the review.
- Friedberg numbering, which would decide note designs exactly, is not free to
  seed.

## Sources

- Funnyback: https://en.wikipedia.org/wiki/Funnyback
- $1 Series 1928 notes: https://oldcurrencyvalues.com/1928_one_dollar_red_seal/,
  https://oldcurrencyvalues.com/1928_blue_seal_one_dollars/
- Morgan and Peace years: https://en.wikipedia.org/wiki/Morgan_dollar,
  https://en.wikipedia.org/wiki/Peace_dollar
- Barr Note: https://oldcurrencyvalues.com/joseph_barr_signature/
- Hawaii: https://coinweek.com/hawaii-overprint-currency-note/
- North Africa: https://oldcurrencyvalues.com/north_africa_currency/
- 2016 centennials:
  https://www.usmint.gov/learn/coins-and-medals/collectible-coins/centennial-gold-coins
- Commemoratives and gold dollars: https://en.wikipedia.org/wiki/Gold_dollar,
  https://en.wikipedia.org/wiki/Early_United_States_commemorative_coins,
  https://en.wikipedia.org/wiki/Modern_United_States_commemorative_coins,
  https://en.wikipedia.org/wiki/American_Innovation_dollars
