# Data import plan: wnm3_coins.xlsx and vendor PDFs

Plan for loading the existing collection spreadsheet into a normalised
database and validating it against the saved vendor order PDFs.

**Status:** proposal. Nothing here is implemented yet. Amendments are appended
as numbered sections rather than edited in place, so the baseline stays legible;
sections they supersede carry a pointer.

**Guiding principle:** every free-text column in the spreadsheet becomes a
foreign key to a reference table, so searching and filtering are standardised
rather than string matching. The original text is always retained alongside.

---

## 1. What the source data actually contains

Measured from `C:\Users\wnmil\OneDrive\wnm3_coins.xlsx` on 2026-09-05.

- **One sheet** (`wnm3_coins`), **7,581 data rows**, 17 columns.
- **No formulas anywhere.** `Taxes` is filled on only 20 rows (0.3%);
  `Total Cost`, `Profit` and `Profit %` are **completely empty**. All four
  become database-computed fields.
- Purchases run from **Feb 2024** onward across ten or more vendors.

| # | Column | Filled | Becomes |
|---|--------|--------|---------|
| 0 | Ordered | 100% | `purchase_order.ordered_on` |
| 1 | Order Number | 65.1% (1,000 stored as **int**) | `purchase_order.order_number` (text) |
| 2 | Denom | 99.6%, 300 distinct | `item_kind` + `denomination` + `bullion_form` + `set_form` |
| 3 | Year | 95.6%, 834 distinct | `year_start`/`year_end` + `mint` + `series_letter` |
| 4 | Rating | 77.8%, **1,751 distinct** | `grade` + `grade_designation` + `grading_service` + `note_attribute` |
| 5 | Price | 100% | `inventory_item.price` |
| 6 | Link | 99.8% | `inventory_item.listing_url` |
| 7 | Description | 100% | free text (kept verbatim, full-text indexed) |
| 8 | Vendor | 99.9% | `vendor` |
| 9 | Shipping | 98.6% | `inventory_item.shipping` |
| 10 | Grading# | 32.4% | **kind-dependent** — cert serial *or* note serial (see §4) |
| 11 | Value | 86.2%, 490 non-numeric | `estimated_value` + `item_status` (overloaded — see §5) |
| 12 | Comment | 37.9%, 126 distinct | numeric grades + notes |
| 13–16 | Taxes / Total Cost / Profit / Profit % | ~0% | **generated columns** |

---

## 2. Computed fields

```
taxes      = round((price + shipping) * tax_rate, 2)      tax_rate default 0.0635
total_cost = price + shipping + taxes
profit     = estimated_value - total_cost                 (null when no value)
profit_pct = profit / total_cost                          (null when total_cost = 0)
```

Implemented as PostgreSQL **`GENERATED ALWAYS AS ... STORED`** columns, so they
cannot drift from their inputs or be written by mistake. `tax_rate` is a
**column** defaulting to `0.0635`, not a literal in the expression — rates vary
by jurisdiction and change over time, and generated columns cannot reference
one another.

> Recorded observation: on the 20 rows with a hand-entered `Taxes` value, the
> figure matches `0.0635 x price` rather than `0.0635 x (price + shipping)`.
> Built to specification as `(price + shipping)`; those rows will appear as
> variances in the validation report.

---

## 3. Classification: measured results

A prototype rule set was run against all 7,581 rows.

### Kind — 94.3% classified automatically

| Rows | Share | Kind |
|---|---|---|
| 4,242 | 56.0% | coin |
| 1,166 | 15.4% | bullion |
| 1,091 | 14.4% | currency |
| 615 | 8.1% | set |
| 28 | 0.4% | medal |
| 5 | 0.1% | token |
| **434** | **5.7%** | **other / unknown — manual review** |

The 434 needing review are only **96 distinct values**, so review is a short
exercise: `Mint Proof` (65), `Mint Silver` (57), `Mixed` (50),
`Box Pennies` (50), `Ltd Ed. Silver` (13), `Multi` (7), `Premier Silver` (5),
`Reverse Proof` (4), `Pirate Money` (4), `Duit` (4), `????` (3), blank (27).

### Currency detection

Ordered rules, first match wins — bullion and sets are tested **before**
currency, because `1oz Copper Round` would otherwise match "number followed by
a word":

1. bullion keywords → `bullion`
2. set keywords → `set`
3. `medal` / `token` / `meteorite`
4. contains `Bill` / `Note` (incl. typos `Blll`, `B`) → `currency`
5. leading `$` → `currency`
6. **number followed by a word or letter** (`10c`, `5 Rupees`, `20 Pound`) → `currency`
7. purely numeric (`0.25`, `1`, `2.5`) → `coin`
8. otherwise → review queue

### Bullion and set forms (become reference rows)

| Rows | Form | | Rows | Form |
|---|---|---|---|---|
| 505 | Silver Eagle | | 363 | Mint Set |
| 302 | Copper Round | | 202 | Proof Set |
| 223 | Silver Round | | 38 | Coin Set |
| 48 | Generic Round | | 12 | Prestige Set |
| 42 | Silver Bar | | | |
| 23 | Copper Bar | | | |
| 8 | Libertad | | | |
| 7 | Gold Maple | | | |

### Storage form — from `Denom` + `Description`

Per your direction, multi-quantity rows stay as single rows with a storage
quantity rather than being exploded.

| Rows | Storage form |
|---|---|
| 6,306 | single |
| 454 | proof set |
| 370 | roll |
| 221 | mint set |
| 204 | box |
| 12 | tube |
| 9 | bag |
| 5 | album |

`storage_quantity` captures the piece count where stated — e.g.
`20x 1oz Copper` (12 rows) yields `storage_quantity = 20`.

---

## 4. Reference tables

`inventory_item` holds foreign keys, not free text. Every reference table has
`id`, `code`, `label`, `sort_order`, `is_active`; those inferred from data also
carry `source` (`seeded | derived | manual`) so machine guesses stay
distinguishable from curated entries.

| Table | Approx. rows | Seeded from |
|---|---|---|
| `item_kind` | 8 | fixed list |
| `bullion_form` | ~15 | Silver Eagle, Copper Round, Silver Bar, … |
| `set_form` | 4 | Mint Set, Proof Set, Prestige, Coin Set |
| `storage_form` | 8 | single, roll, box, tube, bag, album, mint set, proof set |
| `currency` | ~12 | USD plus Peso, Peseta, Pound, Rupee, Kwacha, Piastre, … |
| `denomination` | ~80 | `(currency_id, face_value, label, kind)` — `$1 Bill`, `0.25`, `10c` |
| `country` | ~20 | parsed from Description / denomination |
| `mint` | 7 | P (2,524), S (1,359), D (554), O (238), W (147), CC (91), C (17) |
| `grade_scale` | 2 | Sheldon numeric (MS/PR 1–70), adjectival (UNC, AU, BU, …) |
| `grade` | ~90 | MS63, MS64, MS70, PR69, AU, UNC, GEM BU, VF-20, F-12, … |
| `grade_designation` | ~10 | DCAM, CAM, RD, RB, BN, FS, FB |
| `grading_service` | 6 | PCGS (585), NGC (575), ICG (64), ANACS (53), PMG (9), SEGS (3) |
| `note_attribute` | ~10 | Star Note (83), Blue Seal (82), Red Seal (50), Consecutive, Fancy Serial |
| `vendor` | ~12 | ebay (5,740), whatnot (931), hibid (369), liveauctioneers (131), … |
| `item_status` | 5 | ordered, received, canceled, returned, unknown |
| `authenticity` | 4 | unverified, genuine, counterfeit, questionable |
| `note_type` | 7 | Federal Reserve Note, Silver Certificate, United States Note, … |
| `seal_color` | 5 | blue, red, brown, green, gold |
| `fed_district` | 12 | A Boston … L San Francisco |
| `signature_combination` | ~60 | Treasurer + Secretary pairs with terms |
| `friedberg_number` | grows | curated; see below — **currency** |
| `pcgs_type` | grows | curated; see below — **coins** |

### Certificates, serials and type numbers are three different things

These are routinely conflated, and the spreadsheet conflates two of them in one
column. They are separated in the schema:

| Concept | Scope | Example | Where it lives |
|---|---|---|---|
| **Grading agency** | who slabbed it | PCGS, NGC, PMG | `grading_service` FK |
| **Cert serial** | unique to one slab | `2179332-006`, `45141114` | `item_certification.cert_number` |
| **Note serial** | printed on the banknote | `L10861665*` | `currency_detail.serial_number` |
| **Type number** | shared by all of that type | Fr. 1935-B, PCGS #7328 | `friedberg_number` / `pcgs_type` |

**`Grading#` holds two different things depending on kind** — measured:

| Content | Currency rows (987) | Coin/other rows (1,467) |
|---|---|---|
| banknote serial | **821** | — |
| cert serial (digits or NGC `nnnnnnn-nnn`) | 1 | **679** |
| junk / unparsed | 165 | 90 |
| Excel scientific notation (**data lost**) | 0 | 6 |

So the importer routes by `item_kind`: currency → `currency_detail.serial_number`,
everything else → `item_certification`. A bare 8-digit value is genuinely
ambiguous on its own; kind is what disambiguates it.

**Star notes come free.** 188 currency serials contain `*` — more than double the
83 found by reading `Rating` — so the serial is the better source for the
`Star Note` attribute.

**The grading agency is not in `Grading#`.** It appears there exactly **once**.
It lives in `Rating` (1,289 rows, e.g. `MS70 NGC`, `PR69DCAM PCGS`) and
`Description` (1,141), so `grading_service_id` is parsed from those — which is
precisely why it needs to be its own field rather than left embedded in a grade
string.

Certification stays **one-to-many** (`item_certification`): two rows carry
five-cert lists such as `3000220317, 3000220268, 3000220113, …`, which a single
column could not hold.

### PCGS type numbers (coins)

The coin counterpart to a Friedberg number. A PCGS # identifies a
date/mintmark/variety type (e.g. #7328), shared by every coin of that type —
unlike a cert serial, which is unique to one slab.

Same structure and the same reality: **only one PCGS type number exists anywhere
in the data** (`PCGS #131`), and PCGS numbers come from PCGS's own proprietary
catalogue, so there is no bulk source to import. `pcgs_type` is therefore
curated the same way `friedberg_number` is:

```
pcgs_type
  pcgs_number   int unique
  description   text                -- "1921 Morgan $1 MS"
  denomination_id, series, variety
  year, mint_id
  source        seeded | derived | manual
```

`coin_detail` mirrors `currency_detail` (1:1 with `inventory_item`), holding
`year`, `mint_id`, `variety`, `pcgs_type_id` and `pcgs_status`. Both detail
tables are optional and every field is nullable.

### Friedberg numbers (US currency)

A Friedberg number identifies a **note type**, keyed by denomination, note type,
series year and letter, signature combination and seal colour — plus a district
suffix for Federal Reserve Notes (`Fr. 2016-B`, where `B` is New York).

**Is there a public source? Effectively no.** The numbering system comes from
*Paper Money of the United States* (Robert Friedberg, 1953; now in its 23rd
edition from the Coin & Currency Institute) — a copyrighted catalogue. There is
no official machine-readable dataset, and scraping the book is not a licit
option. Older editions are scanned on the Internet Archive for
borrowing/reading, which does not make them a data source.

`uspapermoney.info` is an excellent **free** reference for the *inputs* —
series-to-signature chronology, denominations, serial ranges — and I verified
its chronology page directly: it lists Treasurer/Secretary, terms and series
designations (e.g. `Woods | 1/29-2/32 | 28A,28B | …`) but **carries no Friedberg
numbers**. Those facts (who signed what, which series exist) are not
copyrightable and are a legitimate seed for `signature_combination`.

**What the current data supports.** Friedberg numbers are essentially absent
from the spreadsheet: **3 of 1,042** currency rows carry one —
`FR 1705N`, `FR#2025-G`, `F2033` — and the word "Friedberg" appears **zero**
times. The fields needed to *derive* one are also sparse:

| Needed input | Present on currency rows |
|---|---|
| denomination | ~100% |
| series year + letter | ~40% (from `Year`: `2017-A` 183, `1957-B` 25, `1935-E` 16, …) |
| seal colour | 16.3% |
| note type | 4.6% |
| district | 2.4% |
| signature combination | ~0% |

**Therefore:** `friedberg_number` is a curated reference table that we grow, and
`inventory_item.friedberg_id` is **nullable** with a `friedberg_raw` text column
alongside. It is never a required field and never auto-guessed.

```
friedberg_number
  fr_number        text unique     -- "1935-B", "2025-G", "1705"
  base_number      int             -- 1935
  district_letter  char(1) null    -- B  (FRNs only)
  note_type_id     -> note_type
  denomination_id  -> denomination
  series_year      int
  series_letter    char(1) null
  seal_color_id    -> seal_color
  signature_combination_id -> signature_combination
  size_class       large | small | fractional
  source           seeded | derived | manual
```

#### Capture the attributes, then propose the number

The identifying attributes are recorded whether or not a Friedberg number is
ever found. They live in a **`currency_detail`** table (1:1 with
`inventory_item` where `kind = currency`) rather than as six mostly-null columns
on `inventory_item` — 86% of rows are not currency.

```
currency_detail
  inventory_item_id        pk, fk
  note_type_id             null
  denomination_id          null
  series_year              null
  series_letter            null
  seal_color_id            null
  signature_combination_id null
  fed_district_id          null
  serial_number            null   -- also feeds star-note / fancy-serial flags
  friedberg_id             null   -- fk, the resolved answer
  friedberg_raw            null   -- verbatim "FR#2025-G" if the seller stated one
  friedberg_status         unknown | proposed | confirmed | conflicting
```

**Every field is optional.** A note entered with nothing but a denomination is
valid and simply yields a weaker proposal.

#### Proposal, not derivation

Given whatever subset the user supplied, the lookup returns **ranked
candidates** rather than an answer:

```sql
select * from friedberg_number fr
where (:note_type   is null or fr.note_type_id   is null or fr.note_type_id   = :note_type)
  and (:denom       is null or fr.denomination_id is null or fr.denomination_id = :denom)
  and (:series_year is null or fr.series_year    is null or fr.series_year    = :series_year)
  -- ... one clause per key
order by matched_keys desc, specificity desc
```

Both sides tolerate nulls: a supplied field never excludes a catalogue row that
is silent on it, and vice versa. Outcomes:

| Candidates | Behaviour |
|---|---|
| exactly 1 | propose it, `friedberg_status = proposed`, user confirms in one click |
| 2–20 | show them with the *differentiating* columns highlighted, so the user sees which extra field to fill in |
| 0 | offer "record a new type" — the entered attributes pre-fill the new row |

Confirmation sets `friedberg_status = confirmed` and stamps
`verified_by` / `verified_at` on the catalogue row. The table therefore gets
better with use: the first 1957-B $1 Silver Certificate costs a lookup, every
later one resolves instantly.

**Uniqueness correction.** My earlier draft proposed a plain unique index on the
key tuple. That is wrong once partial rows are allowed — it would reject two
different half-known types. Instead: a **partial** unique index that applies
only to fully-specified rows,

```sql
create unique index on friedberg_number
  (note_type_id, denomination_id, series_year, series_letter,
   seal_color_id, signature_combination_id, district_letter)
  where note_type_id is not null and denomination_id is not null
    and series_year is not null;
```

plus `unique (fr_number)` unconditionally, so a future bulk import merges on the
catalogue number rather than duplicating.

#### If an importable source appears later

`fr_number` is the natural key and `source` / `catalog_edition` are already
columns, so a licensed dataset can be merged in without touching
`inventory_item`: rows we authored keep `source = manual` and win on conflict
unless explicitly overwritten.

#### Assignment happens at receipt

Type numbers are **not** assigned during import. They are prompted when the item
is received, which is the moment the physical note or coin (and its slab label)
is actually in hand — the only reliable source for seal colour, signature
combination and district.

The receiving panel therefore does double duty: flip `ordered → received`, and
while the item is in front of you, capture the identifying attributes and accept
or decline the proposed Friedberg number (currency) or PCGS type (coins). Every
field stays optional, so receiving is never blocked by a number you cannot
determine.

Population in order of yield:

1. **Parse** `Fr.` / `FR#` / `F-` tokens from descriptions and auction PDFs —
   only 3 rows today, but PMG/PCGS holder labels routinely carry them, so the
   auction PDFs you plan to add are the richest future source.
2. **Prompt at receipt** and confirm, which also fills seal, type and signature
   for the note itself.
3. **Propose automatically** from the accumulated table.

### Why `Rating` cannot be one lookup table

Its 1,751 distinct values are compound. Decomposition is the core normalisation
work:

| Raw value | grade | designation | service | note attribute | storage |
|---|---|---|---|---|---|
| `PR69DCAM PCGS` | PR69 | DCAM | PCGS | — | — |
| `MS70 NGC` | MS70 | — | NGC | — | — |
| `GEM BU` | GEM BU | — | — | — | — |
| `Star Note` | — | — | — | Star Note | — |
| `UNC Roll` | UNC | — | — | — | roll |
| `Morgan Silver Dollar Gem BU` | GEM BU | — | — | — | — |

The last row is description text that leaked into the grade column; the parser
extracts what it recognises and flags the remainder for review.

Note also that **`Comment` carries bare numeric grades** — `63` (391), `53`
(235), `58` (143), `64` (134), `45` (116) — alongside notes such as `ebay`
(610). These resolve into `grade` where a Sheldon number is unambiguous.

### `Year` is two different things

- **Coins:** `1921-P`, `2019-P/D/S` → year plus one or more **mint marks**.
- **Banknotes:** `2017-A` → year plus a **series letter**, which is *not* a
  mint mark and must not be stored as one.
- 723 rows are `????` and 98 are `Mixed`; both map to null years with the raw
  value retained.

---

## 5. Item lifecycle: ordered → received

`Value` in the spreadsheet is overloaded: it holds an appraisal *and* doubles as
a receipt marker. That becomes a first-class status.

### What the data says

| `Value` | Rows | Ordered date range |
|---|---|---|
| numeric | 6,046 | 2024-02-29 → 2026-07-09 |
| blank | 1,045 | 916 of them 2026-05 onward |
| `x` | 474 | **2026-06-06 → 2026-08-31 only** |
| `?` | 7 | |
| `Canceled` | 4 | |
| `Returned` | 3 | |
| `Counterfeit` | 1 | |
| `XF-40` | 1 | a grade in the wrong column |

The `x` convention began **June 2026** and appears nowhere before it. Prior to
that, `Value` was purely an appraisal. This dates the convention and lets the
migration be reasoned about rather than guessed.

### Migration mapping

| Source | `item_status` | Other effect |
|---|---|---|
| `x` | `received` | — |
| numeric | `received` | `estimated_value` set |
| `Canceled` | `canceled` | — |
| `Returned` | `returned` | — |
| `Counterfeit` | `received` | `authenticity = counterfeit` |
| `?` / `XF-40` | `unknown` | flagged to review queue |
| blank, ordered **2026-05 or later** (916) | `ordered` | awaiting receipt |
| blank, ordered **before 2026-05** (129) | `unknown` | flagged — predates the `x` convention |

Only **129 rows** need a human decision. Everything else is determined.

> Assumption worth confirming: a numeric appraisal implies the item is in hand.
> If you sometimes value things before they arrive, those 6,046 rows should be
> `unknown` instead — a one-line change in the migration.

### Status model

`item_status` is per **line**, not per order. One order number covers up to 85
rows here, and split shipments are normal, so receiving must work line by line
with an order-level bulk action over the top.

```
inventory_item
  status_id      -> item_status     default 'ordered'
  authenticity_id-> authenticity    default 'unverified'
  ordered_on     date               -- from purchase_order
  received_on    date null
  closed_on      date null          -- canceled / returned

item_status_history
  inventory_item_id, from_status_id, to_status_id,
  changed_at, changed_by, note
```

History is a separate table rather than overwritten columns, so "when did this
actually arrive" survives later corrections.

### UI

**Two ordered-inventory panels, split by kind** — Coins and Currency are browsed
and filtered differently (mint mark and grade vs series, seal and Friedberg), so
they get separate panels rather than one grid with a filter.

**A receiving panel** listing outstanding `ordered` items grouped by purchase
order and vendor, with:

- per-line **Receive** (defaulting `received_on` to today), **Cancel**, **Return**
- an order-level **Receive all** for the common case
- partial receipt leaves the remaining lines `ordered`
- the 129 undetermined rows surfaced as their own review bucket

---

## 6. Core tables

**`import_batch` / `import_row`** — two-stage import. Every row is first stored
**verbatim**, with `status` (`pending | imported | needs_review | rejected`).
Normalised records keep `import_row_id`, so any value traces back to its
original cell. Nothing is silently coerced.

> Amended by **§11 Amendment A**: `import_row` stores the raw row as a single
> `JSONB` column rather than 17 fixed text columns, so a change to the
> spreadsheet's shape does not require a migration.

**`import_issue`** — one row per ambiguity: which row, which column, which rule
failed, what was guessed. This is the manual-review queue.

**`purchase_order`** — `order_number` (**text**, nullable), `vendor_id`,
`ordered_on`, `source_url`. 624 order numbers repeat across rows (one appears
**85 times**), so this is a genuine one-to-many.

**`inventory_item`** — one per spreadsheet row:

- FKs: `purchase_order_id`, `item_kind_id`, `denomination_id`, `bullion_form_id`,
  `set_form_id`, `storage_form_id`, `mint_id`, `grade_id`,
  `grade_designation_id`, `grading_service_id`, `country_id`, `value_status_id`
- raw text kept: `denom_raw`, `year_raw`, `rating_raw`, `comment_raw`, `description`
- parsed: `year_start`, `year_end`, `series_letter`, `storage_quantity`
- money: `price`, `shipping`, `tax_rate`, `estimated_value`
- generated: `taxes`, `total_cost`, `profit`, `profit_pct`

**`item_certification`** — one-to-many. `Grading#` holds comma-separated lists
(`3000220317, 3000220268, …`), so certificates cannot be a single column.

**`item_note_attribute`** — many-to-many join for banknote attributes.

**`source_document` / `document_order` / `document_item`** — parsed PDF content,
keyed by sha256 so re-parsing is idempotent.

**`validation_finding`** — `field`, `sheet_value`, `document_value`, `severity`
(`match | variance | mismatch | unmatched`). **Reported, never auto-applied.**

---

## 7. The PDFs

**1,096 PDFs** in `C:\Users\wnmil\OneDrive\Documents\coins`, of which **120**
match `ebay_260831_*`. Other prefixes to support later: `hibid_*`,
`liveauction_*`, `probid_*`, `aabid(s)_*`, plus `whatnot_*.csv`.

These are saved **eBay purchase-history pages**, not per-order invoices: 9–10
pages each, ~25 orders per file, with the navigation sidebar interleaved.

```
Order date:Aug 31, 2026 - Order total:US $151.50(Auto-
paid) - Order number:03-15118-54690
ERROR $1 BILL 1988 A SERIES MISCUT DOLLAR BILL "MINT CONDITION"
US $151.50
Sold by:alwoodsworld
```

**Measured extraction reliability (8 files):** `Order date` was found 25 times
per file consistently, but `Order number` only 24, 19, 19, 18, 22, 24, 22 and
**5** times. Normalising whitespace did **not** recover the missing ones, so
this is not line wrapping — the text layer itself is lossy on some pages (note
the doubled glyphs, e.g. `AAllll PPuurrcchhaasseess`). Only 153 distinct order
numbers were recovered from 8 files against ~200 expected.

**Consequence:** parsing must be layout-aware (word coordinates, filtering the
sidebar by x-position), and validation is **best-effort with a coverage
metric** — never a gate that blocks import. An unmatched row means *"not
confirmed"*, never *"wrong"*.

Matching order: order number → (date + total) → fuzzy description.

---

## 8. Delivery phases

| Phase | Deliverable | Verifiable by |
|---|---|---|
| 1 | Reference tables + seed data; Alembic migration | drift test; seeded row counts |
| 2 | Schema for `import_*`, `purchase_order`, `inventory_item`; generated columns | arithmetic tests on taxes/total/profit |
| 3 | `import-xlsx` CLI → verbatim staging | 7,581 rows loaded; sha256 recorded |
| 4 | Classifier + normaliser → typed tables + `import_issue` | kind counts reconcile to 7,581; ≤500 review items |
| 5 | `parse-pdfs` CLI → document tables | coverage % reported per file |
| 6 | Validator → `validation_finding` + report | findings reconcile; no auto-writes |
| 7 | Admin UI: review queue, findings, inventory browse | — |

Phases 1–4 stand alone: the full inventory is in the database and queryable
before any PDF work begins.

---

## 9. Risks

**R1 — Leading zeros already lost.** 1,000 `Order Number` cells are stored as
Excel integers. eBay numbers begin `03-`, `08-`; once Excel typed the cell as a
number the leading zero is gone *in the file* and cannot be recovered from it.
The importer preserves what is there and flags non-conforming values — the PDFs
are the recovery path.

**R2 — Lossy PDF text.** Measured above. Mitigated by layout-aware parsing and
coverage reporting, not eliminated.

**R3 — Reference tables drifting into free text.** Guarded by `source`
(`seeded | derived | manual`) on every reference row, so machine guesses never
masquerade as curated values.

**R4 — Serial numbers destroyed by Excel (already lost).** Six `Grading#` values
are stored as scientific notation — `5.0157E+14`, `1.92405E+15`,
`2.00872E+14` — because Excel typed long serials as numbers. The original digits
are **not recoverable from the file**; only the leading 5–6 significant figures
survive. A further four (`762070990.05864`) were mangled into decimals. These 10
rows are flagged for re-entry from the slab or the auction PDF. Storing serials
as `text` in the database prevents any recurrence.

---

## 10. Open questions

1. **`Value = "x"` on 474 rows** — the single most common value in that column.
   Does it mean "not yet appraised", "sold", or something else? It determines
   whether those rows get `value_status = unknown` or their own status.
2. **Historise `estimated_value`?** Market values move. A `value_history` table
   is cheap now and painful to retrofit. Recommendation: include it.
3. **Does `inventory_item` supersede the existing `coins` table?** The current
   `coins` model is a *sales listing*; the spreadsheet is *what you own and
   paid*. Recommendation: `inventory_item` becomes the core record, with a thin
   `listing` table marking what is for sale at what price. Best done now, before
   there is real sales data to migrate.

---

## 11. Amendment A — storage architecture

**Added 2026-09-05, after the baseline commit.** Question raised: should this use
an object store rather than a SQL database, for more flexibility?

**Decision: keep PostgreSQL as the system of record, add `JSONB` where the shape
genuinely varies, and use an object store for files.** A hybrid, with each part
doing what it is good at.

### Why not a document store for the records

The flexibility wanted here is real, but it is *attribute* variability — coins
have mint marks, notes have seals and districts, bullion has weight and purity —
not schema chaos. PostgreSQL answers that two ways already in this plan: the
`coin_detail` / `currency_detail` split, and `JSONB` columns with GIN indexes
for attributes that do not warrant their own table. That is schemaless storage
*inside* a relational database, still queryable and indexable.

What a document store would cost, specifically:

| Capability | Why it matters here |
|---|---|
| Referential integrity | The stated goal is standardised searching via reference tables — that *is* foreign keys. Without them, integrity becomes application code, which is how the sheet acquired `$20 Blll`, `$2Bill` and `$20 B` as three distinct things. |
| Exact decimal money | `NUMERIC(12,2)` guarantees `2 x 189.00 = 378.00`. Profit across 7,581 rows is the wrong place to accept float rounding. |
| Generated columns | `taxes`, `total_cost`, `profit` are computed *by the database* and cannot drift from their inputs or be written by mistake. |
| Constraints | `unique (fr_number)`, the partial unique index on Friedberg keys, `check` constraints on quantities. |
| Aggregation | "Profit by vendor by year by kind" is one SQL statement. |
| Transactions | Already relied on for the `SELECT ... FOR UPDATE` oversell guarantee. |

**Scale is not an argument either way.** 7,581 rows is trivial; PostgreSQL would
not notice a hundred times that.

### Where `JSONB` is the right answer

1. **`import_row.raw`** — the whole spreadsheet row as one `JSONB` document.
   Genuinely schema-free ingestion: if the sheet gains or reorders a column, the
   loader does not change and no migration is needed. This supersedes the
   17-text-column design in §6.
2. **`inventory_item.attributes`** — kind-specific extras that do not justify a
   column or a reference table (weight, purity, diameter, error type, packaging
   notes).

Rule for promotion: **anything filtered, sorted, joined or aggregated on gets a
real column or a reference table.** `JSONB` is for the long tail, not a way to
avoid deciding. A field that gets a saved search built on it has earned a column.

### Where an object store genuinely wins

Files — and there are already **1,096 PDFs**, with coin and note images to come.
These do **not** belong in the database.

```
source_document
  id, sha256 (unique), storage_key, media_type, byte_size,
  vendor_id, doc_kind, captured_on, page_count, parse_status

item_image
  inventory_item_id, storage_key, kind (obverse|reverse|slab|detail),
  sha256, sort_order
```

- **Content-addressed by sha256**, so re-saving the same eBay export is a no-op
  and re-parsing is idempotent.
- Accessed through a small `StorageBackend` interface with two implementations:
  local filesystem now, S3-compatible later. Callers never learn which.
- The database holds *metadata and parsed content*; the blob store holds bytes.

### What would change this decision

Revisit if any of these become true:

- Users need arbitrary per-item user-defined fields at scale (and even then,
  `JSONB` likely covers it).
- The record count grows by several orders of magnitude *and* access becomes
  key-value rather than analytical.
- The domain model churns so fast that migrations dominate effort — mitigated
  today by Alembic plus the drift test.

None hold now, and none look likely for a personal-to-small-business inventory.

### Consequences for the phased delivery

- Phase 2 gains `import_row.raw JSONB` instead of 17 text columns.
- Phase 5 gains the `StorageBackend` interface; PDFs move under content-addressed
  keys rather than being read from their OneDrive paths in place, so parsing
  stops depending on that directory's layout.
- No change to phases 1, 3, 4 or 6.

---

## 12. Amendment B — one inventory table, not two

**Added 2026-09-05.** Question raised: should coins and currency be separate
inventories, given how differently they are described?

**Decision: one `inventory_item` table**, with `coin_detail` / `currency_detail`
side tables for the divergent fields and database **views** presenting them as
separate inventories. Considered and rejected: physically separate tables.

### Evidence

Purchases mix kinds far more than a first look suggested.

An initial measurement grouped by order number and found only 9 orders
containing both a coin and a note. **That measurement was wrong** — it silently
excluded the 2,648 rows with no order number, and auction vendors are exactly
the ones that lack one:

| Vendor | Rows | With an order number |
|---|---|---|
| liveauctioneers.com | 131 | **0%** |
| proxibid.com | 59 | **0%** |
| hibid.co / www.hibid.com | 97 | **0%** |
| goldstandardauctions.hibid.com | 108 | 0.9% |
| hibid.com | 369 | 24.1% |
| www.ebay.com | 5,740 | 66.5% |
| www.whatnot.com | 931 | 100% |

Re-measured by vendor + order date (an auction-invoice proxy), over 503
multi-line purchase events:

- **56.7%** contain more than one kind
- **68.6% of all rows** sit inside a mixed-kind purchase event
- coin + currency specifically: 6.2% of events, 8.3% of auction events

### Why not two tables

1. **The split is not two-way.** coin 56% · bullion 15.4% · currency 14.4% ·
   set 8.1% · medal/token 0.4% · unclassified 5.7%. Two inventories leave ~30%
   homeless — 1,166 bullion rows, 615 sets, 28 medals. A Silver Eagle is legally
   a coin and practically bullion. The honest version is six tables.
2. **The shared surface dominates.** ~27 fields are common (purchase order,
   vendor, price, shipping, tax_rate, the three generated money columns, status,
   received_on, authenticity, grade, grading service, certification, estimated
   value, storage form and quantity, import provenance, images) against ~5
   coin-specific and ~9 currency-specific. Splitting duplicates the generated
   arithmetic, the status lifecycle and its history, the receiving workflow and
   the validation findings — two copies to keep in lockstep.
3. **Mixed purchases break the receiving panel.** It groups by order and
   supports partial receipt; with 68.6% of rows in mixed events it would have to
   union two tables and coordinate partial receipt across both.
4. **Sales tracking would need a polymorphic foreign key** — two nullable
   columns plus a check constraint, or a discriminator. One FK to one table is
   materially cleaner, and sales are the next planned area.
5. **Unclassified rows would have no home.** 434 rows cannot pick a table at
   insert time, which is precisely when a split design forces the choice.

### What delivers the separation instead

```
inventory_item            -- the shared 27 fields, one row per acquisition
  coin_detail             -- 1:1, coin-only fields, pcgs_type_id
  currency_detail         -- 1:1, note-only fields, friedberg_id, serial_number

create view coin_inventory     as select ... where item_kind in ('coin','bullion','set')
create view currency_inventory as select ... where item_kind = 'currency'
```

The views are what the API and UI consume, so both read as independent
inventories: separate panels, separate columns, separate type catalogues
(`pcgs_type` vs `friedberg_number`), separate receiving flows. Nothing shared is
written twice.

**Field-name consistency comes free.** Because the common fields live in one
table, `price`, `shipping`, `total_cost` and `status` cannot drift apart between
the two inventories — with separate tables that consistency would depend on
discipline.

### Consequences

- No change to any phase. §6 already describes this shape; the views are a small
  addition to Phase 2.
- Cross-cutting reporting (total cost basis, profit by vendor or month, PDF
  order reconciliation) stays a single query, which matters because an eBay or
  auction document lists every kind together regardless of how we store them.

### What would change this decision

If the two inventories diverge until they share little beyond price and date —
different lifecycles, different money handling, different sales mechanics — the
shared table stops earning its keep. Nothing in the current data points that way.
If physical separation is ever wanted without giving up the single logical table,
PostgreSQL LIST partitioning on `item_kind` provides it; at 7,581 rows that would
be ceremony without benefit.

---

## 13. Amendment C — receipt status resolved

**Added 2026-09-05.** Supersedes the migration mapping in §5.

**Rule given:** for non-currency, a row existing means the item was received,
unless the row carries a form of cancellation.

That resolves nearly all of the ambiguity, because the `x` convention turns out
to be **a currency practice**: 431 of the 474 `x` marks are on currency rows.

### Measured split

| `Value` | Non-currency (6,539) | Currency (1,042) |
|---|---|---|
| numeric | **5,971** (91.3%) | 75 (7.2%) |
| blank | 523 (8.0%) | **522** (50.1%) |
| `x` | 43 (0.7%) | **431** (41.4%) |
| `Canceled` | 0 | 4 |
| `Returned` | 0 | 3 |
| `?` | 0 | 7 |
| `Counterfeit` / `XF-40` | 1 / 1 | 0 |

Currency blanks are recent — 2026-05 (102), 2026-06 (327), 2026-07 (47),
2026-08 (40), 2026-09 (3) — with only **3** older (2026-01). They read as
awaiting receipt, not as missing data.

### Final mapping

**Non-currency — all `received`** unless a cancellation signal is present.
Numeric values additionally set `estimated_value`; `Counterfeit` also sets
`authenticity = counterfeit`; the single `XF-40` is a grade in the wrong column
and goes to review with status `received`.

**Currency:**

| `Value` | Rows | Status |
|---|---|---|
| `x` | 431 | `received` |
| numeric | 75 | `received` + `estimated_value` |
| blank, ordered 2026-05 or later | 519 | `ordered` — awaiting receipt |
| blank, ordered before 2026-05 | **3** | `unknown` — review |
| `Canceled` | 4 | `canceled` |
| `Returned` | 3 | `returned` |
| `?` | **7** | `unknown` — review |

**Rows needing a human decision: 11** (7 `?`, 3 old currency blanks, 1 `XF-40`),
down from 129 under the previous date-based rule.

### New status: `missing`

Eight rows carry a `Comment` of `Missing`, two with the note's serial —
`Missing E84256368C`, `Missing I87847860B`. That is neither cancelled nor
returned: it was paid for and never arrived. `item_status` therefore becomes
**ordered, received, canceled, returned, missing, unknown**.

### Guardrail: never scan `Description` for cancellation

A regex for `cancel|return|refund|missing|lost` across the row matches **408**
rows, but **389 of them come from `Description` and are all false positives**:

- `ITEM SEEN ON SCREEN ASK QUESTIONS NO CANCELLATION` — auction boilerplate
- `2010 Lost Coins Never Released In Circulation` — a product name
- `COLLECTION of 10 US Mint Uncirculated Sets (MISSING 1991, …)` — set contents

Applying it naively would cancel 389 received items. **Cancellation and missing
status are read only from `Value` and `Comment`**, never from `Description`,
which is marketing copy written by sellers.

---

## 14. Amendment D — this plan is deliberately bespoke

**Added 2026-09-05.** Recorded intent, not a change of design.

Everything in §1–§13 is fitted to one spreadsheet belonging to one collector.
The `x`-means-received convention, the `$20 Blll` typo map, the 2026-05 cutoff,
the overloaded `Value` and `Grading#` columns — none of that generalises, and
none of it should. A later, generic import facility will let any user map their
own file to the schema, at which point much of this becomes deprecated.

**This is accepted, not regretted.** Getting real data in and queryable now is
worth more than a configurable engine built against a hypothetical second user.
What matters is knowing *which* code is disposable, so effort is not spent
hardening what will be deleted.

### Durable vs disposable

| Durable — outlives the bespoke import | Disposable — dies with this spreadsheet |
|---|---|
| The domain model: `inventory_item`, `coin_detail`, `currency_detail`, reference tables | The 17-column mapping of `wnm3_coins.xlsx` |
| `friedberg_number`, `pcgs_type` and their propose-and-confirm flow | The `Denom` rule set and its typo corrections |
| `purchase_order`, `vendor`, certifications, `item_status` lifecycle | `x` = received; the `Value` overload; the 2026-05 cutoff |
| Generated money columns and the tax model | `Grading#` routing by kind |
| The two-stage pattern: raw staging → normalise → review queue | The specific `Rating` decomposition patterns |
| `import_batch` / `import_row` with `JSONB` raw | The eBay purchase-history page parser |
| `import_issue` and the review UI | The `Description`-is-not-authoritative guardrail |
| `StorageBackend`, `source_document`, content addressing | |
| `validation_finding` and the reconciliation model | |
| The receiving workflow and its UX | |

The durable column is most of the value. The disposable column is mostly
*rules*, and rules are cheap to rewrite once the tables they populate are right.

### The seam that makes replacement cheap

All spreadsheet-specific logic goes in **one place** and is reached through one
interface, rather than being spread across the loader:

```
importers/
  engine.py          # durable: batch, staging, normalise, issues, provenance
  profile.py         # durable: the Protocol a profile must satisfy
  profiles/
    wnm3_coins.py    # DISPOSABLE: every rule in the right-hand column above
```

A profile declares column mappings, classification rules, value dictionaries and
status derivation. The engine knows nothing about coins, `Denom` or `x`. Phase 3
and 4 build both, but only the engine gets treated as long-lived code.

**Effort allocation follows from this.** Test the engine thoroughly; test the
profile at the level of "the 7,581 rows land with these counts" rather than
unit-testing each typo correction. Do not generalise a profile rule before a
second profile exists to justify it.

### What the generic facility looks like later

A separate architecture document, written without reference to this
spreadsheet's anomalies. Sketch only:

- **Upload → inspect → map.** User uploads a file; the system infers columns and
  types; the user maps each to a target field in a UI, saving the result as a
  reusable named mapping.
- **Rules as data, not code.** Classification patterns, value dictionaries and
  typo maps become editable tables, versioned per user, replacing
  `profiles/wnm3_coins.py` entirely.
- **Derived-field expressions** defined by the user (a safe expression language,
  not arbitrary code) so things like "status comes from column K" are configured.
- **Pluggable document parsers** registered per vendor, replacing the hardcoded
  eBay parser.
- **Dry run and diff** before committing an import, with the same
  `import_issue` review queue.

At that point §1–§13 are superseded for import purposes; the schema, the
reference tables and the workflows described here remain.

### Consequence now

Only one: the phase 3 and 4 code is organised around the engine/profile seam
from the start. That costs nothing today and turns the eventual migration into
deleting a directory rather than untangling a codebase.

---

## 15. Amendment E — valuation: melt vs numismatic

**Added 2026-09-05.** Amends §2 (computed fields) and closes the open question in
§10 about historising `estimated_value`.

A common-date, low-grade 90% silver coin is worth its metal, and that number
moves daily with spot. A key date in MS64 is worth a collector premium that has
nothing to do with spot. One stored `estimated_value` column cannot represent
both, and storing a melt figure guarantees it is stale by tomorrow.

### What the data supports

| Signal | Rows |
|---|---|
| mentions silver | 2,895 |
| mentions gold | 392 |
| mentions copper | 370 |
| mentions platinum | 5 |
| states a fineness (90%, .999, .9999, 40%) | ~540 |
| states a weight | 753 |

Fineness is rarely written down — but it rarely needs to be. US coinage
composition is **public fact keyed by denomination and year**, so it is
derivable for **1,920 rows** without being stated:

| Rows | Rule |
|---|---|
| 1,045 | dollar, ≤1964 → 90% Ag |
| 434 | half, ≤1964 → 90% Ag |
| 251 | dime, ≤1964 → 90% Ag |
| 170 | quarter, ≤1964 → 90% Ag |
| 20 | half, 1965–70 → 40% Ag |

Unlike Friedberg numbers, this is not a proprietary catalogue — it is
legislation and mint specification, and can be seeded outright.

### Schema

```
metal              silver | gold | copper | platinum | palladium

composition        -- public-fact lookup, seeded
  denomination_id, country_id, year_from, year_to
  metal_id, fineness            -- 0.900, 0.999, 0.400, 0.350
  fine_weight_ozt               -- actual metal weight, e.g. dime 0.07234
  source: seeded | manual

inventory_item
  composition_id   null   -- resolved from denomination + year, overridable
  metal_id         null   -- for bullion, set directly
  fineness         null
  gross_weight_ozt null
  fine_weight_ozt  null   -- computed or stated; the melt input
  numismatic_value null   -- manual estimate; the collector premium
  valuation_basis         -- melt | numismatic | manual

metal_price        -- time series, fetched
  metal_id, quoted_at, price_per_ozt, source
```

`fine_weight_ozt` figures are seeded from published mint specifications and
should be verified against a reference at seed time rather than trusted from
memory.

### Melt value is reported, not stored

```
melt_value = fine_weight_ozt * (latest spot for that metal) * storage_quantity
```

Computed in a view against the most recent `metal_price` row. Never a column —
a stored melt figure is wrong the moment spot moves.

`reported_value` then follows `valuation_basis`:

- **melt** — common-date, low grade: the metal is the value
- **numismatic** — `numismatic_value`, entered manually, for anything with a premium
- **manual** — an explicit override

Default is `melt` where a composition resolves and no `numismatic_value` exists,
otherwise `numismatic`.

### Consequence: profit stops being a generated column

**This amends §2.** `profit` and `profit_pct` were specified as
`GENERATED ALWAYS AS ... STORED`. That is no longer sound: one of their inputs —
spot price — changes daily, and a stored column cannot track it.

| Field | Before | After |
|---|---|---|
| `taxes` | generated column | **unchanged** — inputs are static |
| `total_cost` | generated column | **unchanged** |
| `profit` | generated column | **moves to the reporting view** |
| `profit_pct` | generated column | **moves to the reporting view** |

Cost basis is fixed at purchase and stays generated. Only the value side is
time-varying, so only the value side moves.

### `valuation_snapshot` — closing the §10 question

Yes, historise it, and this is the shape:

```
valuation_snapshot
  inventory_item_id, captured_at
  basis, spot_price_used, fine_weight_ozt
  melt_value, numismatic_value, reported_value
```

Recording the spot price *used* makes each snapshot reproducible, which a bare
value column never would be. Written on a schedule and on demand, giving a real
portfolio history rather than a single mutable number.

### Open

A spot-price feed is an external dependency not yet chosen. Until one is wired
in, `metal_price` can be populated by hand and melt values simply carry the date
of the last quote — the model does not change, only its freshness.

---

## 16. Amendment F — images and physical location

**Added 2026-09-05.** Extends the object-store decision in §11 with measured
facts, and adds a concept the plan was missing entirely: where an item
physically is.

### What is there

`Documents\coins\Classified\` holds two directories of photographs:

| Directory | Files | Size | Median image | Captured |
|---|---|---|---|---|
| `SafetyDeposit804` | 302 jpg | 536 MB | 1.7 MB | 2025-12-31, 2026-01-02 |
| `SafetyDeposit809` | 371 jpg | 1.5 GB | 4.5 MB | 2025-12-24 to 2025-12-26 |

**673 images, ~2.0 GB**, and this is one snapshot of two boxes. Any argument for
holding images in the database ends here.

Two naming conventions:

- `20251231_104754.jpg` — camera default. All 302 in 804, and 51 in 809.
- `N001-20251224-125550.jpg` — **320 files in 809**, a hand-assigned sequence
  running `N001`–`N324` with gaps (`N012` is absent).

So **809 has been catalogued and 804 has not**. The `N###` sequence is a local
catalogue number, not a grading or vendor reference.

### There is no automatic link to the spreadsheet

Checked directly: `N###` tokens appear in `Description` **7 times** — incidental
matches, not a mapping — safety-deposit references appear twice, and there is no
location column anywhere in the 17.

**Attaching images to inventory items is therefore a manual task**, assisted by
the UI, not an import step. Any plan that assumes the filenames resolve to rows
is wrong.

Assists worth building, to be validated before relied on:

- Present unlinked images in capture order beside candidate rows; both the photo
  session and the `N###` sequence are chronological.
- Images seconds apart are plausibly obverse/reverse of one item, so offer them
  as a pair.
- Once an item is linked, its neighbours narrow the search for the next.

### Schema

```
storage_location
  kind        safe_deposit_box | safe | home | in_transit | sold | unknown
  institution, identifier          -- e.g. "804", "809"
  notes

inventory_item
  storage_location_id  null
  local_catalog_number null          -- the N### where one exists

location_history
  inventory_item_id, storage_location_id, moved_at, moved_by, note

item_image
  inventory_item_id null             -- NULL until linked
  storage_key, sha256 unique, byte_size, media_type
  captured_at                        -- from filename, verified against EXIF
  source_directory                   -- provenance: which import it came from
  kind    obverse | reverse | slab | detail | group | unassigned
  sort_order
```

`item_image.inventory_item_id` being nullable is the important part: **353
images have no catalogue number at all** and must be storable, browsable and
searchable before anyone decides what they depict. `sha256` is unique so
re-importing a directory — likely, given OneDrive — is a no-op.

`location_history` exists because items move between boxes, and "where was this
in March" is a question worth being able to answer.

### Privacy

These are photographs of valuables in identified bank boxes, under a directory
named `Classified`. Two consequences:

- **Storage location and images must never reach the customer-facing catalogue.**
  The public views expose the item, never `storage_location`,
  `local_catalog_number` or `location_history`.
- Image URLs must not be guessable or publicly served; the object store is
  private and access is mediated by the application.

This is a hard boundary, not a preference, and belongs in the authorisation
tests when the sales side is built.

### Consequence

Phase 5 gains an image import: walk a directory, hash, store, record
`captured_at` and `source_directory`, leave `inventory_item_id` null. Phase 7
gains the linking UI. Neither blocks phases 1–4.
