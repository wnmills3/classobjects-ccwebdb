# Series classification by denomination and year

Every item should carry its design series -- Morgan Dollar, Winged Liberty
Head Dime, Funnyback -- because that is how collectors and buyers look for
things. A nickname then finds what it names: `mercury` finds Winged Liberty
Head dimes and `funnyback` the right $1 notes in the management console's
inventory search (`/management/inventory/coins` and `/currency`), whether or
not any listing used the word. That works by **classifying, then searching**: items are assigned a
design series (`inventory_item.series_id`) from their facts, and search
matches a term against each design's label and nicknames (`app.aliases`,
`item-attributes-design.md`). Search also reads the title, description and
rating text, which finds what classification cannot, such as a note left for
review.

Series are assigned on every save and by two batch passes that staff run and
review; Receiving's Identify section also shows, as the facts are typed, the
design they decide:

- `app.series_match` reads what a description says ("1883-O AU/UNC MORGAN
  SILVER DOLLAR"). It matches **coins only, against coin designs**; ambiguous
  names (Barber, Seated Liberty, Indian Head, Liberty Head, and
  "Presidential", which is the Presidential Dollar only on a dollar) need the
  denomination to decide, and an item whose denomination is unknown is left
  alone. **A name is not believed against the item's own year or
  denomination**: for an item of a single year, a design not struck that year
  (by its year ranges, else its span) is dropped from what the text names,
  and so is a design struck in other denominations than the item's ("$5
  Commemorative" on a half eagle is not a commemorative half dollar; a design
  with no denomination recorded fits any). "National Parks Quarter" on
  a piece dated 2005 is not an America the Beautiful quarter; a 1946 piece
  called "Commemorative" can only be the half dollar. An item left with no
  name is counted *contradicted* and stays unclassified, for
  `series_classify` to list as a conflict. A range of years rules nothing
  out. "Franklin" followed by "Mint" names the private mint, not the half
  dollar. A bar, round, medal or token (kinds `bullion`, `medal`, `token`)
  with no denomination recorded is not assigned a design that has one: a
  "Buffalo" round is not an Indian Head nickel, while a Silver Eagle, whose
  design records no denomination, is still matched. It reads every title and description as the item's own: the
  lot-text rule below is `series_classify`'s alone, so a design a lot's
  shared listing names, and the piece's year and denomination allow, is
  assigned here.
- `app.series_classify` assigns from the facts -- denomination, year and, for
  a note, series letter -- and is the only pass that classifies notes.

Both report by default and write only with `--commit`. Neither touches an item
that already has a series, so a hand correction always stands, nor one whose
series a person emptied (`held`). Each records what it wrote in
`item_field_source` (`series_match`, `series_classify`), per
`classifier-defaults-design.md`.

**On save.** Creating or editing an item runs `series_classify` for that item
alone (`refresh_series`, from `classifier_defaults.refresh_items`), in the
same transaction:

1. A series recorded as `series_classify` or `suggestion` (a value a create
   named in its `suggested` list) that the facts now rule out, or that has
   no facts left under it, is cleared with its record -- the machine takes
   back its own guess.
2. An item with no series and no `held` record for it is decided as the batch
   decides it, its own text included; a design found is written and recorded
   as `series_classify`.

A series a person chose, or one `series_match` read from the text, is never
cleared on save; one the facts contradict appears in the batch report's
*disagrees* list. Cases a save cannot decide are left unassigned without a
message; the batch report lists them.

**In Receiving.** The Identify section (`receiving/IdentifySection.jsx`) is
told the design the facts alone decide (`suggest_series`, the `series` field
of `GET /api/defaults/note` and `/coin`) and shows it beside them ("From
these facts: ..."), with no text read: a title is the seller's words. Nothing
is written until the save, which decides the series as above. For a note, a
seal or class the person chose, or else the one the facts gave, is evidence
-- a brown seal makes a $5 1934A a Hawaii note.

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
| Seal color | `series.seal_color_id` | Evidence for a needs-evidence note design. |
| Note class | `series.note_type_id` | A design belonging to one note class. |

## The pass: `python -m app.series_classify`

Run from `backend`; `--commit` writes. It considers items with no series, not
`held`, and with a known denomination and a single year (for a note, its
series year; a coin whose years are a range spans designs and is skipped).
**Candidates** are the designs for the item's inventory whose ranges cover its
denomination, year and letter. **Evidence** is text -- title,
description and rating, read with `series_match`'s vocabulary -- or, for a
note, a seal color matching the design's, or a recorded note class matching
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

- **Lot text is not evidence about a piece.** A lot's pieces carry the
  lot's listing, so for a piece whose title and description are
  shared with another item of the same order, only its rating counts as
  text. The listing is as much the lot's once `app.seller_titles` has
  moved it into the title and written each piece its own description: a
  title longer than a face value, shared within the order, is lot text
  whatever the descriptions say. Lot text can still send a piece to review -- "Lot of 3
  Washington/Carver Commemorative Half Dollars" sends a 1952 half to review
  rather than to Franklin -- but never assigns it in this pass. `series_match`
  has no such rule, and run first it assigns that half the commemorative
  design from the same words.
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

The report prints counts (assigned, boundary, conflict, ordinary, no
candidate, no facts), what would be assigned per design, the boundary and
conflict cases grouped by the designs in play with sample item codes, and a
read-only **disagrees** list: items whose series is already set but which the
facts (or the recorded note class) rule out. The pass changes none of those.

Run `series_match`, then `classifier_defaults` (which decides a note's class,
evidence for the Series 1929 designs), then `series_classify`.

**Boundary years** make most of the review list: 1856-57 cents, 1865-73
three-cent pieces (silver and nickel), 1866-73 five-cent pieces (Seated
Liberty half dime and Shield nickel), 1795 dollars, 1883 and 1913 and 1938 nickels, 1909 cents, 1837 dimes and
half dimes, 1838 quarters, 1807 and 1839 halves, 1916 dimes and
quarters, 1921 dollars, 2016 dimes, quarters and halves (the gold
centennials), $1 of 2007-2016 and 2020 (Presidential and Sacagawea), 2021
dollars and quarters.

**What the facts cannot catch:** an item recorded with the wrong country or
face value is classified by what was recorded; unworded modern commemoratives
(an Apollo 11 half) are taken for Kennedys; an American Women quarter whose text does not name the
program is filed as a Washington quarter, whose obverse it carries.

## The designs

Seeded from `backend/data/reference/series.json`: facts only -- designs, year
spans, denominations, nicknames -- per the reference-data rule. No catalog
numbering.

### Coins

Every circulating coin design has its denomination, except those that span
several faces or would swamp the pass: **Capped Bust Gold**, **Classic Head
Gold**, **Liberty Head Gold** and **Indian Head Gold** span several
denominations, and the **bullion and program designs** (Silver, Gold,
Platinum and Palladium Eagle, Gold Buffalo, First Spouse Gold) stay matched
by name -- a Silver Eagle's $1 face would make every dollar since 1986 a
boundary case.

**One face value is one denomination, and the design tells its coins apart.**
The half dimes (Flowing Hair, Draped Bust, Capped Bust, Seated Liberty) are
five-cent designs beside the nickels; Three Cent Silver and Three Cent Nickel
share three cents; the Gold Dollar shares the dollar.

The designs before the Seated Liberty coinage, by denomination:

| Denomination | Designs: years |
|---|---|
| Half cent | Liberty Cap 1793-1797, Draped Bust 1800-1808, Classic Head 1809-1836, Braided Hair 1840-1857 |
| Two cents | Two Cent Piece 1864-1873 |
| Three cents | Three Cent Silver 1851-1873, Three Cent Nickel 1865-1889 |
| Five cents | Flowing Hair Half Dime 1794-1795, Draped Bust 1796-1805, Capped Bust 1829-1837, Seated Liberty 1837-1873 |
| Dime | Draped Bust 1796-1807, Capped Bust 1809-1837 |
| Twenty cents | Twenty Cent Piece 1875-1878 |
| Quarter | Draped Bust 1796-1807, Capped Bust 1815-1838 |
| Half dollar | Flowing Hair 1794-1795, Draped Bust 1796-1807, Capped Bust 1807-1839 |
| Dollar | Flowing Hair 1794-1795, Draped Bust 1795-1804, Gobrecht 1836-1839 |
| Three dollars | Three Dollar Gold Piece 1854-1889 |
| Gold, several faces | Capped Bust Gold 1795-1834, Classic Head Gold 1834-1839 |

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
| First Spouse Gold | 2007-2016, 2020 (no denomination: matched by name, and the years checked against it) |
| Washington Quarter | 1932-1998, 2021- (State and ATB quarters are their own designs) |

**Evidence-only coin designs** share denomination and years with a far
commoner design, so they are assigned only when the piece's text names them,
and an item that says nothing is taken for the common design:

| Design | Denomination: years | Nicknames |
|---|---|---|
| Commemorative Half Dollar | 50c: 1892-1954, 1982- | Commemorative(s), Commem, Comem |
| Commemorative Dollar | $1: 1900-1922, 1983- | the same |
| Gold Dollar | $1: 1849-1889 | (its label) |
| Trade Dollar | $1: 1873-1885 | (its label) |
| American Innovation Dollar | $1: 2018-2032 | American Innovation, Innovation Dollar |
| American Women Quarters | 25c: 2022-2025 | American Women, American Women Quarter |
| Flowing Hair Cent | 1c: 1793 | Chain Cent, Wreath Cent |
| Liberty Cap, Draped Bust, Classic Head, Coronet Head and Braided Hair Cent | 1c: 1793-1796, 1796-1807, 1808-1814, 1816-1839, 1839-1857 | Matron Head (Coronet Head) |

**A dollar of 1878-1904 that says nothing is a Morgan.** The Trade Dollar
shares 1878-1885 with it (and 1873 with the Seated Liberty dollar) but was
struck for commerce abroad, and from 1879 only as proofs; it is assigned
when the piece's text says "Trade Dollar". A dollar of 1874-1877 that does
not say so is left unassigned.

The six early cent designs are the Large Cent's own types. A cent of those
years that names none of them is a Large Cent; one whose text says "Draped
Bust Cent" is that design. Their labels leave out "Large" so that "Large
Cent" in a description names the Large Cent alone.

### Notes

| Design | Denomination: series | Needs evidence | Nicknames |
|---|---|---|---|
| Funnyback | $1: 1928, 1934 (any letter, any seal) | no | Funny Back |
| Barr Note | $1: 1963B | no | Barr |
| North Africa | $1: 1935A; $5: 1934A; $10: 1934, 1934A | yes -- yellow seal | Yellow Seal |
| Hawaii | $1: 1935A; $5, $10, $20: 1934, 1934A | yes -- brown seal | Hawaii Overprint |
| Series 1929 National Bank Note | $5-$100: 1929, no letter | yes -- note class National Bank Note | Brown Seal, 1929 National, Small Size National |

The large-size designs are the collector names in general use, each for one
denomination and series of one note class. None needs evidence: a note of
that denomination and series is assigned the design unless it is recorded as
another class, which rules the design out.

| Design | Denomination: series | Note class | Nicknames |
|---|---|---|---|
| Educational Series | $1, $2, $5: 1896 | Silver Certificate | Educational |
| Black Eagle | $1: 1899 | Silver Certificate | |
| Indian Chief Note | $5: 1899 | Silver Certificate | Chief Note, Running Antelope |
| Bison Note | $10: 1901 | United States Note | Bison |
| Technicolor | $20: 1905 | Gold Certificate | Technicolor Note |
| Woodchopper | $5: 1907 | United States Note | |
| Battleship | $2: 1918 | Federal Reserve Bank Note | Battleship Note |
| Porthole | $5: 1923 | Silver Certificate | Lincoln Porthole |

A note's class, denomination and series year are fields of their own, so a
note needs no design to be found by them; the designs are the names
collectors search by.

- **Every $1 Series 1928 note is a Funnyback**, whatever its seal: the 1928
  red-seal United States Note shares the back, and the owner counts it.
- **Hawaii $10 includes plain Series 1934.** Sources disagree; since Hawaii
  needs evidence, a wider range cannot assign it wrongly.
- **A seal's name is found by search, and is not evidence of a design.**
  "Brown Seal" and "Yellow Seal" are nicknames, so a search for either
  finds the designs; but a seal color is a field, and a description
  written from the record states it. Read as evidence, "Brown Seal" would
  make every brown-seal Federal Reserve Bank Note name the National's
  design and be listed as a conflict. A nickname that is a seal color's
  label is therefore left out of the matcher's rules (`build_rules`); the
  seal the note records is the evidence, where a design names one.
- **"National" alone is not a name for the Series 1929 design.** A
  National bank's name carries the word on notes of every series -- a
  Series 1902 $20 of the National City Bank of New York -- so as a nickname
  it made each of them a conflict. The word still decides a 1929 note's
  class, below, and the class is the design's evidence.
- **The Series 1929 National's evidence is its class, not its seal.** The
  Series 1929 Federal Reserve Bank Notes carry the same brown seal. The class
  comes from `classifier_defaults`, which reads the rating ("Fed Res",
  "Federal Reserve Bank" name a Federal Reserve Bank Note; "National", "Natl"
  a National Bank Note). A 1929 note whose rating names neither ("Chase NYC
  2370") stays for a person.

Not designs, so not here: *star note* (a serial feature), *blue / red seal*
(a field), *confederate*, *obsolete*, *fractional* (issuers and eras).

Seeding follows the file (`app.seeding`). A design named in the file gets
exactly the file's ranges -- added, updated, and removed when no longer
listed -- unless someone edited the design by hand (`source` manual). Aliases
are added and never removed, and one retired in the console stays retired.
The reference export carries neither ranges nor aliases; the seed file is
their source.

## Out of scope

- The review list is read, not worked, in the console: the *Series to
  review* report (`dq_series_review`, `reporting-design.md`) lists the
  pass's conflict, boundary and disagrees cases and opens each item; there
  is no page that settles a case in place.
- Friedberg numbering, which would decide note designs exactly, is a
  publisher's arrangement and is not seeded.

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
- Early designs, by denomination:
  https://en.wikipedia.org/wiki/Half_cent_(United_States_coin),
  https://en.wikipedia.org/wiki/Large_cent,
  https://en.wikipedia.org/wiki/Half_dime,
  https://en.wikipedia.org/wiki/Dime_(United_States_coin),
  https://en.wikipedia.org/wiki/Quarter_(United_States_coin),
  https://en.wikipedia.org/wiki/Half_dollar_(United_States_coin),
  https://en.wikipedia.org/wiki/Dollar_coin_(United_States)
- Two, three and twenty cents, three dollars:
  https://en.wikipedia.org/wiki/Obsolete_denominations_of_United_States_currency,
  https://en.wikipedia.org/wiki/Three-dollar_piece
- Early gold: https://en.wikipedia.org/wiki/Quarter_eagle,
  https://en.wikipedia.org/wiki/Half_eagle
- First Spouse and Palladium Eagle:
  https://en.wikipedia.org/wiki/First_Spouse_Program,
  https://en.wikipedia.org/wiki/American_Palladium_Eagle
- Large-size note designs:
  https://en.wikipedia.org/wiki/Silver_certificate_(United_States),
  https://www.pmgnotes.com/news/article/6483/Iconic-and-Artistic-Large-Size-US-Banknotes/,
  https://coinweek.com/a-closer-look-at-the-series-of-1918-2-battleship-note,
  https://www.greysheet.com/glossary-of-numismatic-terms/T
