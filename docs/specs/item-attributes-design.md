# Item attributes and classifier aliases

Design. Status: **decisions made by the owner 2026-09-16** (at the end);
building.

## The requests

The owner, 2026-09-16:

- *"It would be nice to be able to handle aliases for the main
  classification, and both coins and currency may have multiple attributes as
  classifiers."*
- *"An example is Godless 1935 $1 Bills (I'd used No God as a label in the
  spreadsheet)."*
- Earlier the same day, on grades: use standard terminology, with the grading
  services' own terms (researched below).

Three things follow:

1. **Aliases for every classifier**, not only design series: "Legal Tender" is
   a United States Note, "UCAM" is DCAM, "No God" is No Motto, "EF40" is XF40.
2. **Many attributes per item, coins as well as notes.** A coin can be DCAM
   *and* First Strike *and* CAC-approved; a note can be a star note *and* a
   fancy serial *and* No Motto. Today only notes have a many-to-many link
   (`note_attribute`), and it holds serial features only.
3. **The grade vocabulary aligned with PCGS, NGC, PMG and CAC**, and the
   importer taught to read what the owner's ratings actually say.

## What the data says (live, 2026-09-16)

| | Items | Today |
|---|---|---|
| ratings (`grade_raw`) | 5,970 | 3,417 carry a grade |
| ungraded coins whose rating is a bare Sheldon number ("69 PCGS", "70DCAM PCGS", "P70DCAM") | 306 | 277 decidable as MS or proof from the rating or description (60 by a cameo designation, 12 by a P prefix, 192 by the description); 29 left for a person |
| "UCAM" / "Ultra Cameo" | 72 | 71 with no designation recorded |
| "CAM/DCAM", "CAM" alone, ungraded | 22 | CAM recorded |
| First Strike / Early Releases / First Releases / First Day of Issue | 26 / 22 / 16 / 12 | nothing recorded |
| plus grades ("65+") | 43 | as ad-hoc grade rows `MS64+`, `MS65+`, `MS66+`, `MS67+`, `PR69+`, `AU+`, `UNC+`, `BU++` with no scale and no number |
| CAC | 24 | nothing recorded |
| "Genuine" (PCGS no-grade holder) | 21 | ungraded, authenticity not recorded |
| SP (specimen) grades | 14 | ungraded |
| Reverse Proof | 12 | nothing recorded |
| "No God" / "No Motto" ($1 Series 1935D-G) | 6 / 11 | text only |
| note serial features (star, fancy, radar...) | 904 links | `item_note_attribute`, 12 of 17 codes in use |

## 1. Aliases for every classifier

`reference_alias` (table, row, alias) exists since the classifier-defaults
work and already holds the note-class nicknames. It is used by the defaults
pass and by search for note classes only. This extends its use to every
vocabulary:

| Where | Today | Proposed |
|---|---|---|
| **Import** | a value is looked up by code, then by label; a miss becomes a new `derived` row or a warning | then by alias, before either: "UCAM" resolves to DCAM, "EF" to XF, "No God" to No Motto. A value found only by alias is logged as such |
| **Search** | series names and nicknames; note classes | the name or nickname of any classifier the view filters on: grade designations, attributes, note classes, series, mints |
| **Pickers** (`ReferenceSelect`) | codes and labels | typing a nickname finds the entry ("Walker" offers Walking Liberty Half Dollar); the alias shows as a hint |
| **The console** | aliases are seeded only | a reference page lists each vocabulary's nicknames and lets an administrator add or remove them (the owner console is a superset of what the data files can say) |

`series_alias` stays as it is. It predates the general table, and folding it
in is a separate clean-up.

**Standard terms stay the labels; the owner's words become aliases.** DCAM,
not UCAM; No Motto, not No God; United States Note, not Legal Tender.

## 2. Item attributes, for coins and notes

`note_attribute` becomes **`item_attribute`**: same shape (code, label,
sort order), plus:

- `applies_to` -- coin, currency or any;
- `attribute_group` -- what kind of fact it is, so the editor can group them
  and the importer knows which may appear together:

| Group | Examples | For |
|---|---|---|
| `serial` | Star Note, Fancy Serial, Radar, Ladder, Binary | notes (the existing 17) |
| `variety` | No Motto (*Godless*, *No God*), Motto, Wide / Narrow (1935D), Type 1 / Type 2 (1929 Nationals), Mule, R / S experimental (1935A) | coins and notes |
| `release` | First Strike (PCGS), Early Releases / First Releases (NGC), First Day of Issue, First Delivery / First Day of Delivery (CACG), First Print (PCGS Banknote) | coins and notes |
| `verification` | CAC (green sticker), CAC Gold | coins |
| `qualifier` | Details, Genuine (PCGS no-grade holder), Star (NGC/PMG eye appeal -- not a star note), NET (PMG) | coins and notes |

The link table becomes `item_attribute_link` (item, attribute, `source`).
Rows move over unchanged; `note_attribute`'s codes keep their meaning.

Each attribute keeps the service's own term. The three release pedigrees are
kept apart rather than merged into one "early release", because each service
defines its own window.

**Genuine** is also an authenticity: a PCGS Genuine holder records
`authenticity = genuine` as well as the qualifier.

### Attributes from facts: the Godless $1

"No Motto" follows from the facts for most notes: "In God We Trust" first
appeared on paper money on some Series 1935G $1 Silver Certificates (BEP FAQ).
So:

| $1 Silver Certificate | No Motto |
|---|---|
| Series 1928 through 1935F | always -- derived, no evidence needed |
| Series 1935G | printed both ways: needs evidence (text, or the person) |
| Series 1935H, 1957 on | never (they carry the motto) |

The same rule shape as the classifier defaults: an attribute rule (attribute,
class, denomination, series range, needs evidence), applied by
`app.classifier_defaults` and recorded as derived. A person can remove a
derived attribute, and it stays removed.

"Godless" is kept to that $1 run (decision 5), which is where collectors use
the word.

## 3. The grade vocabulary

Researched from the services' own pages on 2026-09-16 (PCGS's standards PDF,
glossary and articles; NGC's grading scale, designations and "Learn Grading"
articles; PMG's grading scale; CAC's standards). Findings, and the proposal:

**Equivalences become aliases, not rows.**

| Standard row | Aliases | Why |
|---|---|---|
| DCAM (Deep Cameo) | Ultra Cameo, UC, UCAM | NGC: Ultra Cameo "is generally synonymous with Deep Cameo"; UCAM is informal |
| DMPL (Deep Mirror Prooflike) | DPL | NGC: its DPL "is equivalent to the DMPL designation" |
| PR grades | PF | NGC writes PF, PCGS PR |
| XF grades | EF | no service writes EF |
| Poor 1 | PO | every service writes PO; the vocabulary's `P1` does not match |

**Designations to add** (strike and surface, still one per item):

- `FT` Full Torch (NGC, Roosevelt dimes -- PCGS calls the same thing FB);
- `5FS` and `6FS` (NGC's Jefferson nickel steps; PCGS's FS means five or
  more, CACG's FS means six);
- `BM` / `BMCA` Branch Mint proof (PCGS), `SF` Satin Finish (PCGS) -- lower
  priority.

`FS` is ambiguous on PCGS labels (Full Steps, a Fivaz-Stanton variety, or
First Strike): the importer reads "FS" as Full Steps only after a Jefferson
nickel, and never shortens First Strike to FS.

**Strike type becomes its own field.** MS, PR, SP (specimen), Reverse Proof,
Enhanced Reverse Proof and SMS are a way of striking, with a Sheldon number
beside it -- `SP68`, `REVERSE PF 70`. `grade.is_proof` cannot say this. A
`strike_type` vocabulary replaces it, and grades become (strike type,
number) pairs: MS65 is MS + 65, SP68 is SP + 68 (decision 1).

**Plus is a qualifier on a numbered grade.** PCGS grades plus from XF45 to 68,
never 60 or 61; NGC from 45 to 68, not on Details, bullion or modern
commemoratives. Proposed: `MS64+` etc. become proper grade rows on the
Sheldon scale with `numeric_value` 64 and a `plus` flag, so "MS64 and better"
finds them. Found in the data and outside every service's range: `PR69+`
(plus stops at 68), and `AU+`, `UNC+`, `BU++` (no service puts a plus on an
adjectival grade). Those are the owner's own shorthand, mapped by decision 3.

**Paper money.** The note labels match PMG exactly. PCGS Banknote also grades
3 (About Good), 2 (Fair) and 1 (Poor), which the vocabulary lacks: add N3, N2,
N1. EPQ (PMG) and PPQ (PCGS) stay designations; PMG's Star and NET become
qualifier attributes.

**Adjectival grades stay unnumbered.** The services disagree on their ranges
(Choice is 63-64 at PCGS and the note services, 63 and up at NGC; Gem 65-66
versus 65 and up), which is why the vocabulary already keeps BU, Gem and
Choice without a number.

**CAC is not a grading service.** A CAC sticker verifies a grade another
service gave; CACG, which grades, is already a service. The 24 "CAC" ratings
don't say green or gold: they are the green sticker unless CAC graded the
coin (decision 4).

## 4. The importer

Measured gains:

| Change | Items |
|---|---|
| a bare Sheldon number beside a grading service or designation, with the strike type from a P/SP prefix, a cameo designation (proof), or "proof" / "uncirculated" in the description | 277 graded |
| UCAM, Ultra Cameo, DPL through aliases | 72+ designations |
| release pedigrees, CAC, Genuine, No Motto, Reverse Proof as attributes | ~150 items |
| SP and PO grades | 14+ |

Each new rule follows the existing parser's discipline: a match only inside
the service's published range (a grade of 73 is prose, not a grade), a
missing prefix never guessed when the description does not settle it, and
every miss visible rather than defaulted.

## 5. Order of work

1. Aliases: import, search, pickers, console page. (Built; see *As built:
   aliases* below.)
2. `item_attribute` replacing `note_attribute`, with groups, `applies_to` and
   link provenance; the editor lists an item's attributes. (Built; see *As
   built: item attributes* below.)
3. Grade vocabulary: equivalences as aliases, the new designations, strike
   type, plus grades, N1-N3. (Built. Strike type, plus grades and N1-N3:
   see *As built: strike type and number grades*. Ultra Cameo, UC and UCAM
   are aliases of DCAM and DPL of DMPL; FT, 5FS and 6FS are designations.
   PF, EF and PO need no rows: grades are numbers, and `app.grades` reads
   those prefixes. BM, BMCA and SF, the lower-priority ones, are not added
   yet. The importer does not read the new words until step 4.)
4. Importer rules and a re-derivation over the live data (report first).
   (Built; see *As built: the rating rules* below.)
5. Attribute rules in the defaults pass (No Motto). (Built; see *As built:
   attribute rules* below.)

## Decisions (owner, 2026-09-16)

1. **Strike type is its own field**, and compound grades are split: `PR69+`
   is strike type PR with grade 69+; `MS65` is MS with 65; `SP68` is SP with
   68. A grade is a Sheldon number, optionally with a plus.
2. **Plus grades are allowed** as part of the grade number (`64+`), wherever
   they occur, including where the services would not give one (`69+`).
   **A plus ranks above its number** (owner): 64+ sorts and filters between
   64 and 65, and AU+ (55+) above AU (55).
3. **Adjectival grades take the bottom of their standard range** (the
   owner's answers, 2026-09-16, taken together):

   | Adjectival | Becomes | Items |
   |---|---|---|
   | UNC, BU | MS 60 (Uncirculated 60-62) | 568, 233 |
   | UNC+, BU+, CHOICE BU, CHOICE UNC | MS 63 (Choice 63-64) | 2, 0, 41, 2 |
   | BU++, GEM BU, GEM UNC | MS 65 (Gem 65-66) | 2, 291, 15 |
   | GEM BU++ | MS 65+ | 1 |
   | PROOF, CHOICE PROOF | PR 63 | 615, 2 |
   | GEM PROOF | PR 65 | 55 |
   | AU | 55 (the owner's explicit choice) | 115 |
   | AU+, AU++ | 55+ | 5, 1 |
   | XF, VF, VG (and VF+, VG+) | 40, 20, 8 (20+, 8+) | 29, 16, 4 (1, 1) |
   | note UNC, AU, XF, VF | 60, 50, 40, 20 | 112, 10, 8, 6 |

   The owner first gave UNC 63 and BU 65, then placed BU with UNC (60-62),
   BU+ with Choice (63-64) and BU++ with Gem (65-66); the later answer
   stands. The rating as written stays in `grade_raw`.
4. **CAC is the green sticker** unless the item was graded by CAC: a rating
   saying CACG, or carrying a CACG-only label (First Delivery, First Day of
   Delivery), records grading service CACG instead. Measured: 1 says CACG, 7
   say "CAC First Delivery", 16 are stickers.
5. **"Godless" (No Motto) is the $1 Series 1928 through 1935G only** --
   Silver Certificates, as "In God We Trust" first appeared on some Series
   1935G $1 notes. Series 1935G was printed both ways, so a 1935G note needs
   evidence; 1928-1935F are always No Motto.
6. **The console page for aliases ships in the first version** (not answered
   explicitly; the owner console is to be a superset of what the data files
   can say).
7. **Searching a grade** (owner): `55` finds only 55, and `55%` finds 55
   and 55+. The same goes for the ladder: `BU`, `BU+`, `BU++` and `BU%`.
   As built, `BU` is 60-62, `BU+` 63-64, `BU++` 65-66 and `BU%` all three
   (60-66+); `55+` is exactly 55+. A prefix names the strike too: `PR65`
   is a proof, `MS65` anything shown as MS (business, SMS, or no strike
   recorded). Any other word is its whole range -- `AU` is 50 to 58+ --
   and a plus after it narrows to the plus grades. `grade_min` takes the
   bottom of its term and `grade_max` the top, so `grade_max=64` stops
   below 64+ and `64%` includes it. A term that is not a grade (`MS55`,
   `BU+++`) is refused, not matched against nothing.

## As built: strike type and number grades

Migration `e4b8c1d27f63` adds `strike_type` (business, proof, specimen,
reverse proof, enhanced reverse proof, SMS, each with the prefix and
suffix it is shown with), `grade.is_plus`, the generated `grade.grade_rank`
(the number, plus a half for a plus) and `inventory_item.strike_type_id`.
It moves every item from its old grade to the number and strike type in
decision 3, and removes the old rows nothing references. Downgrade
recomposes MS/PR codes but cannot restore an adjectival grade.

`app.grades` holds the rules: `split` takes a grade apart (the importer,
and any API client sending `MS65`), `display` composes it, mirrored by
the database's `grade_display()`, which the inventory views and the
search use, and `search_term` reads a grade filter. The API returns
`grade` (the code, `65`), `strike_type` and `grade_display` (`MS65`); a
strike type the client names wins over the one a compound grade implies.
The item editor, New item form and shop console offer a strike type for
anything but a note, and the inventory views type the grade filter
rather than pick it.

## As built: aliases

Migration `f3c5d8a91b20` gives `series_alias` and `reference_alias` an
`is_active` flag and a `source`. Removing a shipped alias sets `is_active`
false, and the seed loaders count a retired row as present, so a removal
survives the next load; an alias added in the console is `manual` and is
deleted. `app.aliases` hides the two tables from its callers:

- `resolve` names the row a word means: an exact code, then a code or label
  ignoring case, then an active alias. An alias two rows share resolves to
  neither. **Sharing is allowed**: the live data already has five shared
  series aliases (Cartwheel, Commemorative and its spellings) and "National
  Currency" on two note classes.
- `add_alias` refuses an alias that is any value's label or code, since
  `resolve` would never reach it.
- `ids_named` is the search's lookup. Text of three letters or more matches
  part of a label, code or alias; shorter text must match one whole, so `s`
  does not name the strike aliased `MS`.

**Search** (`inventory_search`): each view lists the vocabularies it
searches by name (`Named`): series, strike type and grade designation for
both; mint for coins; note class and note attribute for currency. A value
on a detail or link table is matched with `i.id IN (subquery)`, which
PostgreSQL hashes once; a correlated EXISTS took twice as long on the live
data. Measured on a copy of live (2026-09-16), before and after:

| Search | Coins | Currency |
|---|---|---|
| `denver` | 66 -> 535 | |
| `san francisco` | 11 -> 1,348 | |
| `business` | 1 -> 2,098 | |
| `PF` | 306 -> 1,245 | |
| `deep cameo` | 69 -> 386 | |
| `star note` | | 130 -> 224 |
| `fancy` | | 129 -> 287 |
| `BU` | 1,390 -> 1,381 | |

`BU` fell because a short term no longer matches part of a series name
(Buffalo). Nickname searches such as `mercury`, `walker` and `cartwheel`
are unchanged. A page with facets took 94 ms for `denver`, against 81 ms
before, while finding eight times as many items.

**Import**: `SchemaLoader.code_id` tries `resolve` before inventing a
`derived` row, and counts each row read through an alias; the report
prints them as `aliased` lines, and `summary.json` carries them with the
derived counts.

**API**: every reference value carries `aliases`; with `include_inactive`,
also `retired_aliases` and its own `is_active`. `POST
/api/reference/{table}/{code}/aliases` adds one, `DELETE ...?alias=`
removes one; both are staff only and return the value.

**Console**: `ReferenceSelect` shows a Find box on a vocabulary of more
than ten values, matching label, code or alias and showing the alias that
matched. The Vocabularies page lists, adds, removes and restores aliases,
and marks a shared one.

## As built: item attributes

Migration `a7d4e2c9b813` renames `note_attribute` to `item_attribute`, adds
`applies_to` and `attribute_group`, and renames `item_note_attribute` to
`item_attribute_link` with `source`, `derived_by`, `noted_by_id`,
`noted_at` and `removed_at`. The 17 note attributes keep their codes: 13
are `serial`, and error, web press, specimen and proof notes `variety`. The
904 existing links are `derived` -- the importer and the serial check made
all of them -- with no `derived_by`, which the column did not exist to say.

The vocabulary moved to `data/reference/attribute.json` and gained the
section 2 rows: No Motto, Motto, Wide, Narrow, Type 1, Type 2, Mule, R and
S Experimental; First Strike, Early Releases, First Releases, First Day of
Issue, First Delivery, First Print; CAC and CAC Gold; Details, Genuine,
Star (eye appeal) and NET. Aliases: Godless and No God for No Motto, Early
Release, First Release, FDI, First Day of Delivery, CAC Green, Gold CAC.

**Removal is recorded for every link**, not only derived ones
(`app.item_attributes`). Deleting a link a person added would let the
serial check add it back the moment it was taken away. Setting a removed
attribute again clears the mark and keeps the link's source. Every reader
-- the detail, search, the `attribute=` filter and the star check -- skips a
removed link; the serial check skips any link at all.

**API**: the item detail lists `attributes` (code, label, group, source,
derived_by). `PATCH /api/inventory/{id}` takes `attributes`, the whole set,
checked against the item's kind after any kind change in the same request;
a change moves the item's version, so a stale form is a 409. Bulk edit
refuses `attributes`. Search matches attribute names and aliases on both
screens and filters on `attribute=<code>`.

**Console**: the item editor's Attributes row shows chips (marked *read*
when a rule made them) and a picker of the attributes that fit the item.

Measured on a copy of live, 2026-09-16: the load created 21 attributes and
8 aliases, a full seed load then changed nothing else, the serial check
found nothing to add, and the search totals of the alias release were
unchanged.

## As built: the rating rules

The rating parser moved to `app/importers/rating.py`, which the importer
and `app.rating_pass` share. Since 2026-09-16 the database is the record
(data-import-plan Amendment K), so the pass over stored items is what
changes live data; the importer rules matter for new rows.

What the survey of live ratings (2026-09-16) changed from the plan:

- **Run-together ratings** -- `PR70DCAMPCGS`, `SP68PCGS`, `SP69ICG` -- had
  no designation or grader; a designation may now run into a grader, and a
  grader follow a number or designation.
- **P70** (12 items, `P70DCAM PCGS`) is PCGS's proof shorthand; P stays Poor
  at 1.
- **Grades from a number**: 305 items, not 306. The strike is settled by a
  prefix (26: SP 14, P70 12), a cameo designation (62), the same grade in the
  description (197; most descriptions write it, "PCGS MS63"), or the
  description's words (12) -- 297 graded. A grade written in the
  description with another number still counts as a word, so a lot
  described "PF 68 ... Ms65" settles nothing. Eight are left for a person,
  among them CC-006140, a "coin" rated "67 EPQ Radar" that is surely a note.
- **CAC**: the 7 "MS70 CAC First Delivery" become grader CACG with First
  Delivery (decision 4); 17 are the green sticker; FDOI is First Day of
  Issue.
- **FS** on two Silver Eagles (`MS70 FS NGC`) was recorded as Full Steps and
  is cleared; FS is read only on a Jefferson nickel, and 5FS/6FS always.
- **Reverse proof**: 26 items stored as a plain proof (and two as business,
  "MS69 NGC Rev Proof") are corrected; a named strike beats a prefix.
- A colour designation after a number below 60 is an ordinal ("3RD").
- **Not found in the live ratings**: PO, Details, NET, First Print,
  Enhanced Reverse Proof (the one "Enhanced" is "Enhanced Mint").
- **Suspect data for the owner**: nine Peace dollars rated "No Motto" (every
  Peace dollar carries the motto), and two notes rated FDOI, an attribute
  for coins.

The pass fills only empty fields, skips anything held or confirmed,
records what it fills as derived by `rating`, and adds attribute links only
where none exists, removed ones included. Its dry run on live proposed: 297
grades and strikes, 85 designations (82 DCAM), 25 graders, 21 Genuine, 141
attributes, 26 strike corrections, 2 FS clears.

## As built: attribute rules

`app/attribute_rules.py` holds the rule as data (attribute, class, face
value, the series that always have it, the series that need evidence;
every other series of that class never does), and
`app.classifier_defaults` applies it alongside the note's class, so an edit
that changes a note's series or class brings its No Motto up to date at
once. The class the rule reads is the one the pass has just decided: a
class taken back takes the rule's link with it.

The rule adds a derived link (`derived_by` `attribute_rule`) only where the
note has no link for the attribute at all -- a removed one, or one the
importer, the rating pass or a person made, is left as it is -- and takes
back only its own. A 1935G without the attribute is reported as *needs
evidence*; a later note with it as *disagrees*.

Dry run on live, 2026-09-16: 108 notes get No Motto; 15 Series 1935G notes
need evidence; no later note carries it. The nine Peace dollars rated "No
Motto" are coins and outside the rule.

## Sources

PCGS: https://www.pcgs.com/resources/pdf/PCGSGradingStandards.pdf,
https://www.pcgs.com/lingo/ALL, https://www.pcgs.com/firststrike,
https://www.pcgs.com/banknote/grades, and PCGS news articles on designations,
Plus, specimens and Details (pcgs.com/grades refuses automated reading).
NGC: https://www.ngccoin.com/coin-grading/grading-scale/,
https://www.ngccoin.com/coin-grading/designations/, and its "Learn Grading"
articles (Plus and Star, Deep Prooflike, Cameo, proof types, SP/PL prefixes,
Details). PMG: https://www.pmgnotes.com/paper-money-grading/grading-scale/.
CAC: https://www.cacgrading.com/stickering,
https://www.cacgrading.com/grading-standards. BEP FAQ on the motto:
https://www.bep.gov/currency/faqs. Research notes, row by row, were kept with
the session's working files.
