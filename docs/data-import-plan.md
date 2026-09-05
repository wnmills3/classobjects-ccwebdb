# Data import plan: wnm3_coins.xlsx and vendor PDFs

Plan for loading the existing collection spreadsheet into a normalised
database and validating it against the saved vendor order PDFs.

**Status:** proposal. Nothing here is implemented yet.

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
**verbatim as text**, with `status` (`pending | imported | needs_review |
rejected`). Normalised records keep `import_row_id`, so any value traces back
to its original cell. Nothing is silently coerced.

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
