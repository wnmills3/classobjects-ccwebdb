# Classifier defaults from known facts

Most classifiers are not independent observations; they follow from a few
facts recorded about the object:

- A $1 note of Series 1957 **is** a Silver Certificate -- the Treasury issued
  no other $1 note of that series -- with a blue seal and the signatures of
  Priest and Anderson.
- A Federal Reserve Note with serial `B12345678A` was issued by the Federal
  Reserve Bank of New York.
- A dime struck in 1963 is 90% silver because the law said so.

So staff enter the facts and the software fills in the rest from published
facts, which saves typing and removes a class of entry error. It works in
three places: live in Receiving's Identify section
(`identify-first-entry-design.md`), on every save of an item -- which is how
an item entered on a purchase gets them --
and as a batch pass over the whole collection. A person can always override.
Design-series assignment (`series-classification-design.md`) works the same
way and records its results in the same table. The rules live in
`app.classifier_defaults`.

## Principles

1. **Facts, not guesses.** A default is written only when the facts allow
   exactly one value. Two possible values is a case for a person, reported.
2. **A person always wins.** A default never replaces a value a person set.
   A person's value still narrows the facts: a note recorded with a red seal
   is not the blue-seal issue of its series.
3. **Visible provenance.** A default is recorded as derived, per field, so it
   can be told apart from what someone typed.
4. **Report before writing.** The batch pass prints what it would do;
   `--commit` writes.
5. **Official names.** Vocabulary follows the issuing agency first (BEP for
   notes, US Mint for coins), then the grading services, then collector usage
   independent sources agree on. Dealer lists are evidence, not authority.

## Per-field provenance: `item_field_source`

One row per (item, field) holding a derived default, with `derived_by` naming
the rule (`app/field_sources.py`). Field names are column names
(`note_type_id`, `fineness`), as in `item_field_review`, whether the column is
on the item or its currency detail.

| `derived_by` | Written by |
|---|---|
| `note_issue` | class, seal and signatures from the note's issue |
| `serial_district` | Reserve Bank from the serial |
| `composition` | a coin's composition, metal, fineness and weights |
| `series_match` | design series read from the description |
| `series_classify` | design series decided from the facts |
| `suggestion` | a value an entry form suggested and the person left alone |
| `weight` | fine weight worked out as gross weight times fineness |
| `weight_text` | a weight or fineness `app.bullion_weights` read from the item's own title, description or weight as written |
| `weight_peers` | a weight or fineness `app.bullion_weights` took from what most items of the same bullion form and metal hold |
| `held` | not a rule: a person emptied the field |

`series_backfill` and `rating` also appear on existing rows and are read like
any other rule; nothing in the code writes them.

- A pass may refresh a field recorded here and never touches one that is not.
- Saving a field by hand removes its row: from then on it is the person's.
- **Emptying is a choice.** A person emptying a field a pass fills records it
  as `held`; it stays empty through refreshes and batch runs until someone
  sets it again. Emptying a field no pass fills records nothing.
- **The machine takes back its own guesses.** A derived value the facts no
  longer support -- a series corrected to a large-size year, a class that is
  no longer a Federal Reserve Note (so has no Bank), a coin whose year range
  now spans two compositions -- is cleared with its record and counted as
  *retracted*. A person's value is never cleared; one the facts contradict is
  reported instead.

`GET /api/inventory/{id}` returns the item's `derived` map (field to rule,
`held` excluded), which the editor marks.

## Currency vocabulary

`note_type`, aligned to the classes BEP names on its history page and FAQ:

| Code | Label | Nicknames (`reference_alias`) |
|---|---|---|
| `frn` | Federal Reserve Note | FRN |
| `frbn` | Federal Reserve Bank Note | FRBN, Federal Reserve Bank, Fed Res, National Currency |
| `silver_certificate` | Silver Certificate | Silver Cert |
| `gold_certificate` | Gold Certificate | Gold Cert |
| `us_note` | United States Note | Legal Tender Note, Legal Tender |
| `national_bank_note` | National Bank Note | National Currency, National Banknote, National, Natl |
| `fractional` | Fractional Currency | |
| `demand_note` | Demand Note | |
| `treasury_note` | Treasury Note | Coin Note, Treasury Coin Note |

"Legal Tender Note" is a nickname, not a class: BEP uses *legal tender* in its
statutory sense (31 USC 5103), which covers every class. "National Currency"
is the wording printed on National Bank Notes (and on Series 1929 Federal
Reserve Bank Notes).

Nicknames live in the general `reference_alias` table, so they are recognized
in search and in the pickers (`item-attributes-design.md`). Seal colors (blue,
red, brown, green, gold, yellow) and the twelve Federal Reserve districts
(A Boston through L San Francisco) match BEP's serial-number page.

## The facts

`backend/data/reference/note_issue.json` seeds `note_issue`: one row per
small-size issue (Series 1928-2021, $1 through $1000; 332 rows) with
denomination, series year and letter, class, seal, signature combination,
`serial_prefix`, an optional `variant` (Hawaii, North Africa, experimental and
similar), and the sources it rests on. Every row names at least two
independent sources: Wikipedia's per-denomination series tables,
USPaperMoney.info's chronology, BEP's serial-number table for Series 1996 on,
and the SPMC wiki for the Series 1929 National Bank Notes. The six rows
sourced `web` (the $500 and $1000 Federal Reserve Notes) rest on
search-result summaries and are the weakest.

`signatures.json` holds the signer pairs. BEP writes pairs
Secretary/Treasurer; our codes are `treasurer_secretary`.

More than one seal per class occurs only in the WWII emergency issues -- brown
(Hawaii) and yellow (North Africa) beside the ordinary seal, Series
1934-1935A -- so a note from those series needs its seal to decide it.

These are historical facts (who signed, which class, which seal) and are safe
to seed. No Friedberg number is involved or implied.

Coin compositions come from `composition.json` (`app.composition`): the
legislated metal, fineness and weights per denomination, country and span of
years.

### Serial numbers (BEP)

- Through Series 1995, and for $1 and $2 notes still, a serial is one letter,
  eight digits, one letter. **The first letter is the issuing Federal Reserve
  Bank** (A-L).
- From Series 1996, $5 and higher carry two leading letters: **the series**
  (A = 1996, B = 1999, ...) then **the Bank**. `note_issue.serial_prefix`
  records the series letter.
- A star in place of a prefix letter hides the Bank.

The Bank letter means something only on a **Federal Reserve Note**; a Silver
Certificate's prefix letter says nothing about a district.

### Plate numbers and printing location

`app.plates` holds the rules. A **face plate** is a check letter and digits
(`E82`), or digits alone on older notes (`153`); a Fort Worth note has `FW`
before it (`FW E82`), a Washington note nothing. A **back plate** is digits.
The **printing location** (`dc` or `fw`) is read from the face plate when one
is given, so the two cannot disagree, and set by hand only where no face
plate is recorded. The location tells two Friedberg numbers apart (a 2017-A
$1 from each facility), and a face and back from different eras is how a
mule is found.

## What the rules decide

| Default | From | Only when |
|---|---|---|
| note type | denomination, series year and letter, seal | one class remains |
| seal color | the issue | the issue has one seal |
| signature combination | the issue | one pair signed it |
| Federal Reserve district | serial number | the class is Federal Reserve Note and the serial is well formed |
| composition, metal, fineness, gross and fine weight | denomination, country, year | one composition covers every year of the item's range |
| fine weight | gross weight times fineness | the item is not a note, has both, and no composition decides its fine weight |

Note type is decided first, because seal, signatures and Bank depend on it.

**Weight.** A coin's weight is its composition's. A bar, a round or a medal
has no denomination, so its gross weight and fineness are entered by hand --
per piece, in troy ounces or grams, kept in troy ounces -- and the fine weight
is worked out from them: filled when empty, kept in step with the two while
it is the rule's, cleared when either is gone, and left alone once a person
types it (a one-ounce gold coin holds 1 troy ounce and weighs more). A weight
`app.bullion_weights` guessed is derived like any other default, but it has no
composition to lose: a composition's facts replace it, and their absence does
not take it back.

**Text as evidence.** Where a series was issued in several classes, a note's
**rating** (the grade text in the owner's words) that names exactly one of
them, by label or nickname, decides it -- "Legal Tender" picks United States
Note. Titles and descriptions are not read: on notes they are too often a
lot's listing.

**Attributes** that follow from a note's facts -- No Motto on a $1 Silver
Certificate of Series 1928-1935F (`app.attribute_rules`,
`item-attributes-design.md`) -- are added the same way: only where the note
has no link for that attribute at all, a removed one included, and taken back
only where the rule added it.

Reported and never written:

| Case | Finds |
|---|---|
| unknown issue | a small-size series and denomination with no issue in the facts |
| ambiguous | several classes remain and nothing decides between them |
| disagrees | a recorded class, seal, signatures, Bank or composition the facts rule out; a note carrying an attribute its series never has |
| needs evidence | a note whose series may or may not carry a rule's attribute (a 1935G without No Motto) |
| serial prefix | a $5-or-higher Federal Reserve Note of Series 1996 on whose first serial letter is not its series' letter, or whose Bank letter is not a Bank |
| retracted | a derived value cleared because the facts no longer support it |

## When defaults are applied

- **On every write.** Creating an item, a single edit and a bulk edit each call
  `refresh_items` in the same transaction, so a corrected series year corrects
  the class derived from it. It refreshes the design series too
  (`series_classify.refresh_series`).
- **At entry.** Receiving's Identify section asks
  `GET /api/defaults/note` (denomination, series year and letter, serial,
  rating, plus any class, seal, signatures or Bank the person chose) or
  `GET /api/defaults/coin` (denomination, country, year; answers the metal).
  Both also answer the design series the facts decide
  (`series_classify.suggest_series`), and the note lookup returns a `warning`
  when no issue of that denomination has that series, naming the ones on
  record. The lookup runs after a 250 ms pause. A form sends only
  the person's own picks -- a value sent narrows the answer and is never
  suggested back -- and marks what it filled as *suggested*. Picking a value,
  even the
  suggested one, makes it the person's; clearing the denomination withdraws
  the suggestions. Suggestions the person kept are sent as `suggested` and
  recorded as `suggestion`.
- **In batch**, run from `backend`:

  ```
  python -m app.classifier_defaults            report, touching nothing
  python -m app.classifier_defaults --commit   write the defaults
  ```

  Run it after `app.series_match` and before `app.series_classify`, which
  reads a note's class as evidence.

## The editor

For a banknote the item editor shows the note's own fields right after its
kind (`NoteFields.jsx`): first what identifies it -- series year and letter,
serial, face and back plate, where it was printed -- then Note class, Seal,
Signatures and Reserve Bank. A derived value carries a *suggested* mark whose
tooltip names the rule. Only "Note class" (Alt+A) and "Reserve Bank" (Alt+B)
have accelerators: no other free letter is in their labels.

## Out of scope

- **Large-size notes** (before Series 1928). Their classes overlap far more
  (Series 1875 and 1882 served several classes at once) and need text
  evidence.
- **Friedberg numbers**: never seeded.
- **Mint** cannot be derived -- it is what is struck on the coin -- though a
  mint that did not strike a denomination in a year could be flagged.
- **Key dates**: not built.

## Sources

- BEP, public domain: https://www.bep.gov/currency,
  https://www.bep.gov/currency/serial-numbers/,
  https://www.bep.gov/currency/history, https://www.bep.gov/currency/faqs
- Wikipedia, "United States one-dollar bill" and the $2 through $100 articles,
  section "Series dates / Small size" (evidence, not authority)
- USPaperMoney.info; SPMC wiki (Series 1929 National Bank Notes)
- US Mint glossary: the preferred source for coin terms
