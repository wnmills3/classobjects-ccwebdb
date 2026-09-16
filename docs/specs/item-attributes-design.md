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

**Decision:** whether "Godless" should apply beyond the $1 -- strictly, every
note before its denomination gained the motto lacks it, but collectors use
the word for the $1 Series 1935 run.

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
number) pairs: MS65 is MS + 65, SP68 is SP + 68. **Decision:** a new column on
the item, or strike-typed grade rows.

**Plus is a qualifier on a numbered grade.** PCGS grades plus from XF45 to 68,
never 60 or 61; NGC from 45 to 68, not on Details, bullion or modern
commemoratives. Proposed: `MS64+` etc. become proper grade rows on the
Sheldon scale with `numeric_value` 64 and a `plus` flag, so "MS64 and better"
finds them. Found in the data and outside every service's range: `PR69+`
(plus stops at 68), and `AU+`, `UNC+`, `BU++` (no service puts a plus on an
adjectival grade). Those are the owner's own shorthand: kept, as aliases of
AU, UNC and BU with the plus noted, or reported. **Decision.**

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
don't say green or gold: proposed as the green sticker (the common one),
reported for a person to confirm. **Decision.**

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

1. Aliases: import, search, pickers, console page.
2. `item_attribute` replacing `note_attribute`, with groups, `applies_to` and
   link provenance; the editor lists an item's attributes.
3. Grade vocabulary: equivalences as aliases, the new designations, strike
   type, plus grades, N1-N3.
4. Importer rules and a re-derivation over the live data (report first).
5. Attribute rules in the defaults pass (No Motto).

## Decisions (owner, 2026-09-16)

1. **Strike type is its own field**, and compound grades are split: `PR69+`
   is strike type PR with grade 69+; `MS65` is MS with 65; `SP68` is SP with
   68. A grade is a Sheldon number, optionally with a plus.
2. **Plus grades are allowed** as part of the grade number (`64+`), wherever
   they occur, including where the services would not give one (`69+`).
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
