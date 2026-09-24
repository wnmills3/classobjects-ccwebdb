# Item attributes, classifier aliases and the grade vocabulary

Three related pieces:

1. **Aliases for every classifier.** "Legal Tender" is a United States Note,
   "Ultra Cameo" is UCAM, "No God" is No Motto, "Walker" is a Walking Liberty Half.
2. **Many attributes per item, coins as well as notes.** A coin can be DCAM
   *and* First Strike *and* CAC-approved; a note a star note *and* a fancy
   serial *and* No Motto.
3. **A grade vocabulary aligned with PCGS, NGC, PMG and CAC.**

**Standard terms are the labels; the owner's words are aliases.** UCAM, not
Ultra Cameo; No Motto, not No God; United States Note, not Legal Tender.

## 1. Aliases

Two tables hold them: `series_alias` for design series (it predates the
general table and keeps its own shape) and `reference_alias` (table, row,
alias) for every other vocabulary. Both carry `is_active` and a `source`.
`app/aliases.py` hides the split from its callers:

- **`resolve`** names the row a word means: an exact code, then a code or
  label ignoring case and extra spaces, then an active alias. An alias shared
  by two rows resolves to neither -- a guess between them would be silent.
- **Sharing is allowed.** "Cartwheel" is any large silver dollar; "National
  Currency" is on two note classes. Search finds both; `resolve`, which
  cannot choose, names neither.
- **`add_alias`** refuses empty or over-long text, a name that is already any
  value's label or code (`resolve` would never reach it), and an alias the row
  already has. A retired alias added again is brought back.
- **`remove_alias`** deletes an alias an administrator added (`manual`) and
  retires a shipped one (`is_active` false). Seed loads count a retired row as
  present, so a removal survives the next load.
- **`ids_named`** is the search lookup. Text of three letters or more matches
  part of a label, code or alias; shorter text must match one whole, so `s`
  does not name the strike aliased `MS`.

Where aliases are read:

| Where | Behaviour |
|---|---|
| **Search** | Each inventory view names the vocabularies it searches by name (`Named` in `inventory_search`): series, strike type, grade designation and attribute in both; mint for coins; note class for currency. A value on a detail or link table is matched with `i.id IN (subquery)`, which PostgreSQL hashes once. |
| **Pickers** | `ReferenceSelect` shows a Find box on a vocabulary of more than ten values (`FIND_FROM`), matching label, code or alias and showing the alias that matched; Enter picks the first. |
| **Console** | The Vocabularies page lists each value's aliases, adds and removes them, shows a retired shipped alias struck through with a Restore, and marks a shared one. |

API: every reference value carries `aliases`; with `include_inactive`, also
`retired_aliases` and its own `is_active`. `POST
/api/reference/{table}/{code}/aliases` adds one and `DELETE
/api/reference/{table}/{code}/aliases?alias=` removes one; both are staff only
and return the value.

## 2. Item attributes

`item_attribute` (code, label, sort order, `applies_to` coin / currency / any,
`attribute_group`) and the link table `item_attribute_link` (item, attribute,
`source`, `derived_by`, `noted_by_id`, `noted_at`, `removed_at`). The
vocabulary is seeded from `backend/data/reference/attribute.json`:

| Group | Attributes | For |
|---|---|---|
| `serial` | Star, Fancy Serial, Consecutive, Low / High Serial, Solid, Radar, Repeater, Binary, Trinary, Birthday, Ladder, Double Quad | notes |
| `variety` | Error, Web Press, Specimen, Proof; No Motto, Motto, Mule (any); Wide / Narrow, Type 1 / Type 2, R / S Experimental | mostly notes |
| `release` | First Strike (PCGS), Early Releases / First Releases (NGC), First Day of Issue, First Delivery (CACG) | coins |
| `release` | First Print (PCGS Banknote) | notes |
| `verification` | CAC (green sticker), CAC Gold | coins |
| `qualifier` | Details, Genuine, Star (NGC/PMG eye appeal -- not a star note) | any |
| `qualifier` | NET (PMG) | notes |

Aliases: Godless and No God (No Motto), Early Release, First Release, FDI,
First Day of Delivery, CAC Green, Gold CAC.

Each attribute keeps the service's own term. The release pedigrees are kept
apart rather than merged into one "early release", because each service
defines its own window.

**Removal is recorded for every link** (`app.item_attributes`), not only
derived ones: deleting a link a person added would let the serial check add it
straight back. Setting a removed attribute again clears the mark and keeps the
link's source. Every reader -- the item detail, search, the `attribute=`
filter, the star check -- skips a removed link, and every pass that adds links
skips an item holding *any* link for that attribute, removed ones included.

API: the item detail lists `attributes` (code, label, group, source,
derived_by). `PATCH /api/inventory/{id}` takes `attributes` as the whole set,
checked against the item's kind after any kind change in the same request; a
change moves the item's version, so a stale form is a 409. Bulk edit refuses
`attributes`. Search matches attribute names and aliases and filters on
`attribute=<code>`.

Console: the item editor's Attributes row shows chips (marked *read* when a
rule made them) and a picker of the attributes that fit the item's kind.

### Attributes from facts: No Motto

"In God We Trust" first appeared on paper money on some Series 1935G $1
Silver Certificates (BEP FAQ). So for a $1 Silver Certificate:

| Series | No Motto |
|---|---|
| 1928 through 1935F | always -- derived, no evidence needed |
| 1935G | printed both ways: needs evidence (text, or a person) |
| 1935H, 1957 on | never |

`app/attribute_rules.py` holds the rule as data (attribute, class, face value,
the series that always have it, the series that need evidence; every other
series of that class never does). `app.classifier_defaults` applies it
alongside the note's class, using the class it has just decided, so an edit
that changes a note's series or class updates No Motto at once, and a class
taken back takes the rule's link with it. The rule adds a derived link
(`derived_by` `attribute_rule`) only where the note has no link for the
attribute at all, and takes back only its own. A 1935G without the attribute
is reported as *needs evidence*; a later note with it as *disagrees*.
"Godless" is used only for this $1 run, which is where collectors use the word.

## 3. The grade vocabulary

**A grade is a strike type plus a Sheldon number, optionally with a plus.**
MS65 is business strike + 65; SP68 is specimen + 68; PR69+ is proof + 69+.

- **`strike_type`**: Business Strike (aliases MS, Mint State, Business), Proof
  (PR, PF), Specimen (SP), Reverse Proof, Enhanced Reverse Proof, Special Mint
  Set -- each with the prefix and suffix it is displayed with.
- **`grade`** rows are numbers; `grade.is_plus` marks a plus, and the
  generated `grade.grade_rank` is the number plus a half for a plus, so **a
  plus ranks above its number**: 64+ sorts and filters between 64 and 65.
  Plus is allowed wherever it occurs, including where no service would give
  one (69+).
- **Adjectival grades take the bottom of their standard range**
  (`grades.ADJECTIVAL`): UNC and BU are MS60, Choice MS63, Gem MS65; PROOF and
  Choice Proof PR63, Gem Proof PR65; AU 55, XF 40, VF 20, F 12, VG 8, G 4.
  UNC and BU climb with pluses (BU+ is 63, BU++ 65); AU+ is 55+. Note
  adjectivals map the same way onto the note scale (Gem Unc N65 ... G N4).
  Only CIRC and UNGRADED stay unnumbered. The rating as written stays in
  `rating`.
- **Paper money** labels match PMG; N1-N3 cover PCGS Banknote's Poor, Fair
  and About Good. EPQ and PPQ are designations.

`app/grades.py` holds the rules: `split` takes a compound grade apart (for
any API client sending `MS65`), `display` composes it, mirrored
by the database's `grade_display()` used by the views and search. The API
returns `grade` (the code, `65`), `strike_type` and `grade_display` (`MS65`); a
strike type the client names wins over one a compound grade implies. The item
editor, New item form and shop console offer a strike type for anything but a
note.

**Equivalences are aliases, not rows**: Ultra Cameo and UC are UCAM; DPL is
DMPL. UCAM and DCAM are separate rows, not aliases of one another: each is
one grading service's designation, and the holder names one or the other.
PF, EF and PO need no rows: grades are numbers and `app.grades` reads those
prefixes.

**Designations** (one per item): DCAM, CAM, RD, RB, BN, FS, 5FS, 6FS, FB, FT,
FBL, FH, PL, DMPL, EPQ, PPQ. FT is NGC's Full Torch (PCGS calls it FB); 5FS and
6FS are NGC's Jefferson nickel steps.

**CAC is not a grading service.** A CAC sticker verifies another service's
grade and is the `cac` attribute; CACG, which grades, is a grading service.
First Delivery (First Day of Delivery) is a CACG-only label; "CAC" alone is
the green sticker.

### Searching a grade

`grades.search_term` reads the grade filter as a range:

- A number is exact: `55` is 55, not 55+; `55%` is both; `55+` is 55+ only.
- A prefix names the strike: `PR65` is a proof, `MS65` anything shown as MS
  (business, SMS, or no strike recorded).
- The ladder words are steps: `BU` is 60-62, `BU+` 63-64, `BU++` 65-66, `BU%`
  all three.
- Any other word is its whole range -- `AU` is 50 to 58+ -- and a plus after
  it narrows to the plus grades.
- `grade_min` takes the bottom of its term and `grade_max` the top, so
  `grade_max=64` stops below 64+ and `64%` includes it.
- A term that is not a grade (`MS55`, `BU+++`) is refused, not matched against
  nothing.

## Sources

PCGS: https://www.pcgs.com/resources/pdf/PCGSGradingStandards.pdf,
https://www.pcgs.com/lingo/ALL, https://www.pcgs.com/firststrike,
https://www.pcgs.com/banknote/grades. NGC:
https://www.ngccoin.com/coin-grading/grading-scale/,
https://www.ngccoin.com/coin-grading/designations/. PMG:
https://www.pmgnotes.com/paper-money-grading/grading-scale/. CAC:
https://www.cacgrading.com/stickering,
https://www.cacgrading.com/grading-standards. BEP FAQ on the motto:
https://www.bep.gov/currency/faqs.
