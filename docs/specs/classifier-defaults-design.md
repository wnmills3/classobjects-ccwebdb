# Classifier defaults from known facts -- currency first

Design. Status: **approved by the owner 2026-09-16**; the decisions are
gathered at the end.

## The idea

The owner, 2026-09-16: *"consider the basic input data and some automated
suggestions for classifiers based on denomination, series, mint, etc. based on
known facts about coins and currency. These could be overridden, but the
default values would be applied to help with classification."*

Most classifiers are not independent observations. They follow from a few
facts recorded about the object:

- A $1 note of Series 1957 **is** a Silver Certificate. That is not a guess;
  the Treasury issued no other $1 note of that series.
- A Silver Certificate of Series 1957 carries a **blue** seal and the
  signatures of **Priest and Anderson**.
- A Federal Reserve Note with serial `B12345678A` was issued by the Federal
  Reserve Bank of **New York**.

The project already works this way in two places, which set the pattern:

- **Composition** resolves from denomination, country and year
  (`composition.json`: "a dime struck in 1963 is 90% silver because the law
  said so, not because anyone typed it in").
- **Series** is assigned from denomination and year by `app.series_classify`
  (docs/specs/series-classification-design.md). It reports before it writes,
  never touches a value a person set, and records what it wrote as `derived`.

This spec generalises that into **classifier defaults**: the basic facts are
entered, the rest is filled in from published facts, and a person can always
override. Currency comes first, because it has the most to gain -- see below.
It also brings the currency **vocabulary** in line with official terminology,
since the defaults can only be as good as the names they fill in.

## What the notes record today

Measured on the live database, 2026-09-16 (1,114 notes):

| Field | Recorded on | Derivable from |
|---|---|---|
| denomination | nearly all | -- (input) |
| series year (and letter) | **1,039** | -- (input) |
| serial number | **1,053** | -- (input) |
| seal colour | 219 | note type and series |
| note type | **0** | denomination, series, seal |
| signature combination | **0** | note type, denomination, series with letter |
| Federal Reserve district | **0** | serial number, for Federal Reserve Notes |

The last four rows follow from the three inputs above them, and three of the
four are empty today. That is the size of the win.

**Estimated coverage of note type**, from the published series tables (below)
against the live notes:

| | Notes |
|---|---|
| decided by denomination + series alone | **907** |
| decided once the recorded seal is used | 19 |
| still ambiguous (seal missing where it would decide) | 16 -- e.g. $1 Series 1928: United States Note or Silver Certificate |
| large-size notes (before Series 1928), not covered by this version | 32 |
| series not in the facts table (a parse gap to close, or a data error) | 49 |
| no denomination or series year | 91 |

About **83%** of notes get a note type with no one typing it.

## Principles

1. **Facts, not guesses.** A default is written only when the facts allow
   exactly one value. Two possible values is a case for a person, reported
   like `series_classify`'s boundary list.
2. **A person always wins.** A default never replaces a value a person set.
3. **Visible provenance.** A default is recorded as derived, so it can be
   told apart from what someone typed -- see the decision below.
4. **Report before writing.** Every pass has a dry run that prints the counts,
   and the owner sees them before the live database is written.
5. **Official names.** Vocabulary follows the issuing agency first (BEP for
   notes, US Mint for coins), then the grading services, then collector usage
   that independent sources agree on. Dealer lists are evidence, not
   authority.

## Currency vocabulary, aligned to BEP

The Bureau of Engraving and Printing names the classes of US paper money on
its History page (bep.gov/currency/history) and FAQ. Compared with
`note_type` today:

| Today (`code` -- label) | BEP's name | Change |
|---|---|---|
| `frn` -- Federal Reserve Note | Federal Reserve Notes | none |
| `frbn` -- Federal Reserve Bank Note | Federal Reserve Bank Notes | none |
| `silver_certificate` -- Silver Certificate | Silver Certificates | none |
| `gold_certificate` -- Gold Certificate | Gold Certificates | none |
| `us_note` -- United States Note | United States Notes ("characterized by a red seal", FAQ) | none |
| `legal_tender` -- Legal Tender Note | -- | **merge into `us_note`**; keep "Legal Tender Note" as a nickname. BEP uses *legal tender* in its statutory sense (31 USC 5103), which covers every class, so as a class name it is misleading |
| `national_currency` -- National Currency | National Bank Notes (National Banknotes) | **relabel "National Bank Note"**; keep "National Currency", the wording printed on the notes, as a nickname. The code becomes `national_bank_note` |
| `fractional` -- Fractional Currency | Fractional Currency | none |
| -- | Demand Notes (1861) | **add** `demand_note` |
| -- | Treasury Notes, also Treasury Coin Notes (1890) | **add** `treasury_note`; nickname "Coin Note" |

**This is the cheapest moment to change codes.** No note, Friedberg row or
saved URL uses a note-type code yet (measured: all eight have zero
references). The frontend reads the list from the API; it names no code.

Note types need **nicknames**, as series do, so text such as "Legal Tender"
or "Coin Note" is recognised on import and in search. They live in a general
`reference_alias` table (decision 2).

Seal colours (blue, red, brown, green, gold, yellow) and the twelve Federal
Reserve districts already match BEP's serial-number page exactly: A Boston
through L San Francisco, numbered 1-12.

## The facts to seed

### Small-size series (Series 1928 on)

For each denomination, series (year and letter): the class, the seal colour,
and the Treasurer and Secretary who signed it. For example:

| $ | Class | Seal | Series |
|---|---|---|---|
| 1 | United States Note | red | 1928 |
| 1 | Silver Certificate | blue | 1928-1928E, 1934, 1935-1935H, 1957-1957B |
| 1 | Silver Certificate (Hawaii) | brown | 1935A |
| 1 | Silver Certificate (North Africa) | yellow | 1935A |
| 1 | Federal Reserve Note | green | 1963 on |
| 2 | United States Note | red | 1928-1963A |
| 2 | Federal Reserve Note | green | 1976 on |
| 5 | United States Note | red | 1928-1963 |
| 5 | Silver Certificate | blue / yellow | 1934-1953C / 1934A |
| 5 | Federal Reserve Note | green / brown (Hawaii) | 1928 on / 1934, 1934A |
| 5-100 | National Bank Note, Federal Reserve Bank Note | brown | 1929 |
| 10-100 | Gold Certificate | gold | 1928 (1928A for some denominations: to verify) |
| 100 | United States Note | red | 1966 (1966A: to verify) |

Sources and their standing:

- **Wikipedia's per-denomination articles** ("United States one-dollar bill"
  and siblings) carry a "Series dates / Small size" table with exactly these
  columns. The section is flagged as possibly original research, so it is
  evidence, not authority.
- **BEP's serial-number page** (bep.gov/currency/serial-numbers) gives
  denomination, Secretary/Treasurer and series for Series 1996-2021.
  **Cross-checked 2026-09-16: all 37 overlapping rows agree** on the signers.
- The parse is not yet complete: the $10 and $100 tables yielded only four
  rows each, and the $2 table mixes in large-size notes. Every row is to be
  checked before it is seeded, and the 49 live notes whose series the table
  lacks are the test of completeness.

These are historical facts -- who signed, which class, which seal -- and are
safe to seed. No Friedberg numbers are involved or implied.

The facts live in a new `note_issue` table (denomination, series year,
series letter, class, seal, signature combination): one row per issue, from
which every note default is looked up (decision 3).

### Signature combinations

`signature_combination` holds 11 pairs, Series 1934-1971, and its seed
comment explains why it stopped: an earlier attempt at a full chronology
produced corrupt rows. The table above supplies every pair from Series 1928
to 2021 in structured form, and BEP confirms 1996 on. Complete the list from
it. BEP writes pairs Secretary/Treasurer; our codes are
`treasurer_secretary`, so each is turned round on entry.

### Serial numbers (BEP)

From bep.gov/currency/serial-numbers:

- Through Series 1995, and for $1 and $2 notes still, a serial is one letter,
  eight digits, one letter. **The first letter is the issuing Federal Reserve
  Bank** (A-L).
- From Series 1996, $5 and higher carry two leading letters: **the first is
  the series** (A = 1996, B = 1999, ... Q = 2021) and **the second is the
  Bank**.
- O is never used (confused with 0), Z is reserved for test printings, and a
  star replaces the last letter on a replacement note.

The Bank letter applies to **Federal Reserve Notes only**; a Silver
Certificate's prefix letter means nothing about a district.

BEP's own table contradicts its footnote in two rows (it gives $1 Series 2021
the prefix Q and $2 Series 2017A the prefix P, while the footnote says $1 and
$2 carry no series letter), and it repeats two rows. The footnote is taken as
the rule; the notes in hand will settle it.

## The passes

One module, like `series_classify`: report by default, `--commit` to write,
fill only what is empty, record what it wrote.

| Default | From | Only when |
|---|---|---|
| note type | denomination, series year and letter, seal | the issues table leaves one class |
| seal colour | note type, denomination, series | the issue has one seal |
| signature combination | note type, denomination, series with letter | one pair signed it |
| Federal Reserve district | serial number | note type is Federal Reserve Note and the serial is well formed |

And one check, reported and never written:

| Check | Finds |
|---|---|
| series prefix | a $5-or-higher Federal Reserve Note of Series 1996 on whose first serial letter is not its series' letter -- a mistyped series or serial |
| recorded vs derived | a recorded seal, note type or signature that the facts rule out, as `series_classify`'s "disagrees" list does |

Order matters: note type first, because seal and signatures depend on it.
`scripts\ccweb_rebuild.cmd` runs the pass after `series_classify`.

**At entry time**, the same lookups fill the New item form as its facts are
typed: choose $1 and Series 1957 and the note type, seal and signatures appear,
marked as suggestions and editable. This ships in the first version.

## How a default is told apart from a person's value

Provenance today is per row (`inventory_item.source`), not per field, and
`item_field_review` records only that a person *confirmed* a field. Two ways:

- **A. Fill blanks only.** No schema change. A pass writes a field only when it
  is empty and never touches it again. Simple, and safe. But once written, a
  default looks like a typed value: the UI cannot mark it as a suggestion, and
  a corrected fact (a row fixed in the issues table) cannot be re-applied.
- **B. Per-field provenance.** A small `item_field_source` table (item, field,
  source), shaped like `item_field_review`. A pass may refresh fields whose
  source is `derived` and never touches `manual` ones. The editor can show
  "suggested" beside a derived value, and saving a field by hand turns it
  `manual`.

**Chosen: B** (decision 4). It is what "these could be overridden, but the default
values would be applied" asks for, and it is the only way the defaults can
improve when the facts do.

## Coins, next

The same mechanism, with the coin facts the project already has or has
researched:

- **Series** -- done (`series_classify`).
- **Composition and metal** -- already resolved from denomination and year;
  under this design they would be recorded as derived defaults rather than
  looked up only at valuation time (decision 6).
- **Key date** -- a flag from series, year and mint, researched on 2026-09-16
  and to be its own spec: about 60 dates two independent sources agree on;
  varieties need text evidence; tier disputes are the owner's call, with US
  Mint mintages as the tie-breaker.
- **Mint** cannot be derived (it is what is struck on the coin), but a mint
  that did not strike that denomination in that year can be flagged.

## Testing

- Each default: written when the facts allow one value; not written when they
  allow two; never written over a value a person set.
- Note type: $1 1957 is a Silver Certificate; $1 1963 a Federal Reserve Note;
  $1 1928 needs the seal (red: United States Note, blue: Silver Certificate).
- Seal and signatures follow the note type; $1 1935B is Julian/Vinson.
- District: `B12345678A` on a Federal Reserve Note is New York; on a Silver
  Certificate, nothing.
- Series prefix: a $20 Series 2004 with serial `CA...` is reported.
- Vocabulary: `legal_tender` merged into `us_note`, "Legal Tender Note" still
  recognised; `national_bank_note` labelled "National Bank Note".
- Provenance (option B): a hand edit turns a field manual; a re-run refreshes
  derived fields only.
- Coverage: every live note's series is present in the issues table, or
  reported.

## Out of scope

- Large-size notes (before Series 1928): 32 notes. Their classes overlap far
  more (Series 1875 and 1882 were used by several classes at once) and need
  text evidence; a later version.
- Friedberg numbers: never seeded.
- Note designs by nickname (Big Head, Educational, Black Eagle...): researched
  on 2026-09-16 and to follow as series additions, now that note type exists as
  evidence.

## Decisions (owner, 2026-09-16: "yes to all, go with option B")

1. **Codes:** `national_currency` is renamed `national_bank_note` (label
   "National Bank Note") and `legal_tender` is merged into `us_note`, both in
   place by migration -- no row references either. The old names become
   nicknames.
2. **Nicknames: one general alias table**, `reference_alias` (table, code,
   alias), for note types and every vocabulary after them. "Yes to all" did
   not choose between the two options, so this was decided in the build:
   the grade vocabulary that follows needs nicknames too (UCAM for DCAM, DPL
   for DMPL), and one table serves both. `series_alias` stays as it is.
3. **`note_issue`** holds the small-size facts: denomination, series year,
   series letter, class, seal, signature combination.
4. **Provenance: option B**, per-field. `item_field_source` records which
   fields of an item hold a derived default. A pass may refresh those and
   never touches any other; saving a field by hand removes its row, which
   makes it the person's. `series_classify` records the series it assigns
   the same way.
5. **Entry-time suggestions ship in the first version**: the New item form
   fills note type, seal and signatures as the facts are chosen, marked as
   suggestions.
6. **Coin composition is a derived default too.** The importer already fills
   composition, metal, fineness and fine weight for 3,188 of 4,387 coins
   (measured 2026-09-16); those fields are recorded as derived, and the pass
   fills the coins that lack them.

## Sources

- BEP, currency hub and subpages, fetched 2026-09-16 (public domain):
  https://www.bep.gov/currency,
  https://www.bep.gov/currency/serial-numbers/,
  https://www.bep.gov/currency/history,
  https://www.bep.gov/currency/faqs,
  https://www.bep.gov/currency/production-figures/annual-production-reports
- Wikipedia, "United States one-dollar bill" and the $2, $5, $10, $20, $50
  and $100 articles, section "Series dates / Small size", fetched 2026-09-16
- US Mint glossary and anatomy-of-a-coin pages
  (https://www.usmint.gov/learn/collecting-basics/glossary,
  .../anatomy-of-a-coin): the preferred source for coin terms, not yet read --
  the site refuses automated fetches.
