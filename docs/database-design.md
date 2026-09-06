# Database design

The data model for the numismatic and currency inventory and sales platform.

This document describes the **target schema** and the reasoning behind it. It is
deliberately independent of any particular source of data: the collection grows
and changes, and nothing here should need revising when it does. Loading an
existing collection is a separate, temporary concern — see
[spreadsheet-import-design.md](spreadsheet-import-design.md).

Decision history, including alternatives considered and rejected, is in
[data-import-plan.md](data-import-plan.md).

---

## 1. Principles

These constrain everything below.

**Classifiers are foreign keys, never free text.** Anything used to search,
filter, group or report is a reference table with a stable code. Free text is
where meaning goes to hide: `$20 Bill`, `$20 Blll` and `$20 B` are three
different strings and one concept.

> Copying the label onto each record as well -- keeping the key for integrity
> and the string for speed -- was considered and measured. It buys nothing.
> Over 6,370 coins a page plus all facets took 400 ms through the inventory
> views, 29 ms reading the base tables with only the joins each query needs,
> and 23 ms with partial indexes on the facet columns; a fully denormalised
> table measured ~6 ms in the same test. The cost was never the normalisation:
> it was asking a fourteen-way join for one column. Since the speed was
> available without a second copy of every label that can drift from the
> first, there was no reason to accept one.
>
> The two things denormalising was meant to enable are better without it. New
> values are added from the picker that needed them, so the tables grow with
> use. And renaming a value changes it everywhere the moment it commits,
> because there is only one copy -- the migration a denormalised design would
> need to run is a cost that design creates, not a feature it provides.

**Raw text is kept beside every parsed value.** A parser can be wrong or
incomplete. `*_raw` columns preserve exactly what a human or an importer
supplied, so nothing is unrecoverable and re-parsing is always possible.

**Money is `NUMERIC(12,2)` mapped to `Decimal`.** No floats anywhere in the
stack — not in the database, not in the API layer, not in the UI.

**Cost basis is fixed; value is time-varying.** What was paid never changes
after purchase and is stored. What something is worth moves daily and is
computed at read time.

**Machine guesses never masquerade as curated facts.** Every reference row and
every resolved identifier records whether it was seeded, derived by a rule, or
confirmed by a person.

**Storage location and inventory photographs are never customer-visible.** This
is an authorisation boundary enforced by views and tested, not a convention.

---

## 2. Entity overview

```
                    vendor
                      |
               purchase_order ──────────┐
                      |                 |
                inventory_item ─────────┴── item_certification
                 |     |     |                 (0..n)
      coin_detail  currency_detail  item_note_attribute
           |             |
       pcgs_type   friedberg_number

  inventory_item ── storage_location ── location_history
  inventory_item ── item_image ── image ── image_derivative
  inventory_item ── valuation_snapshot
  inventory_item ── listing ── order_item ── order ── customer
                                              |
                                          shipment ── carrier
```

Everything hangs off `inventory_item`. Coins and currency share it rather than
splitting into parallel tables — they share far more than they differ (purchase,
cost, grade, certification, status, location, images), acquisitions routinely
contain both, sales must reference either through a single foreign key, and an
item whose kind is not yet determined still needs somewhere to live. The
divergent attributes live in 1:1 detail tables, and separate **views** present
the two inventories independently to the API and UI.

---

## 3. Core tables

### `inventory_item`

One row per acquired item or lot. The shared spine.

| Column | Type | Notes |
|---|---|---|
| `id` | bigint pk | surrogate key, internal |
| `item_code` | text unique | **permanent identifier for the physical object** |
| `purchase_order_id` | fk null | null when the acquisition is not recorded |
| `item_kind_id` | fk | coin, currency, bullion, set, medal, token, other, unknown |
| `denomination_id` | fk null | face value + currency |
| `bullion_form_id` | fk null | Silver Eagle, Copper Round, Silver Bar, … |
| `set_form_id` | fk null | Mint Set, Proof Set, … |
| `storage_form_id` | fk | single, roll, tube, box, bag, album, mint set, proof set |
| `storage_quantity` | int | pieces in the lot; 1 for a single item |
| `country_id` | fk null | issuing country |
| `year_start`, `year_end` | int null | a range covers multi-year sets |
| `grade_id` | fk null | |
| `grade_designation_id` | fk null | DCAM, CAM, RD, RB, BN, FS, FB |
| `grading_service_id` | fk null | who graded it, distinct from the grade |
| `authenticity_id` | fk | unverified, genuine, counterfeit, questionable |
| `error_type_id` | fk null | mint or printing error — see §4 |
| `error_details` | text null | free-form notes about the error |
| `status_id` | fk | **acquisition** lifecycle — see §7 |
| `disposition_id` | fk | **sales** lifecycle — see §7 |
| `storage_location_id` | fk null | where it physically is |
| `local_catalog_number` | text null | a hand-assigned number, if the owner uses one |
| `title`, `description` | text | free text, full-text indexed |
| `listing_url` | text null | where it was acquired from |
| `notes_raw` | text null | preserved verbatim |
| `denom_raw`, `year_raw`, `grade_raw` | text null | preserved verbatim |
| `price`, `shipping` | numeric(12,2) | cost basis inputs |
| `tax_rate` | numeric(6,4) | default `0.0635`, per row |
| `taxes` | **generated** | `round((price + shipping) * tax_rate, 2)` |
| `total_cost` | **generated** | `price + shipping + taxes` |
| `numismatic_value` | numeric(12,2) null | manual estimate, the collector premium |
| `valuation_basis_id` | fk | melt, numismatic, manual |
| `composition_id` | fk null | resolves metal content — see §6 |
| `metal_id` | fk null | set directly for bullion |
| `fineness` | numeric(6,4) null | 0.9000, 0.9990, 0.4000 |
| `gross_weight_ozt` | numeric(12,6) null | whole-item weight, troy ounces |
| `fine_weight_ozt` | numeric(12,6) null | precious metal content — the melt input |
| `weight_raw` | text null | the weight as originally written, e.g. `1/2 Kilo` |
| `attributes` | jsonb | long-tail attributes; see the promotion rule below |
| `source` | enum | seeded, derived, manual |
| `created_at`, `updated_at` | timestamptz | |

`taxes` and `total_cost` are `GENERATED ALWAYS AS ... STORED`. Their inputs are
fixed at purchase, so they can never drift and cannot be written by mistake.
`tax_rate` is a column rather than a literal in the expression because rates vary
by jurisdiction and change over time, and generated columns cannot reference one
another.

**Profit is not a column.** It depends on current value, which moves — see §6.

**`attributes` promotion rule:** anything filtered, sorted, joined or aggregated
on earns a real column or a reference table. `JSONB` is for the long tail
(diameter, edge type, error description, packaging notes), not a way to avoid
deciding.

### `coin_detail` and `currency_detail`

1:1 with `inventory_item`, present only for the relevant kind. Every column is
nullable; an item identified only by a photograph is still a valid row.

```
coin_detail
  inventory_item_id  pk, fk
  mint_id                     -- P, D, S, O, W, CC, C
  variety                     -- VAM, FS-, doubled die, etc.
  pcgs_type_id                -- see §5
  pcgs_status                 -- unknown | proposed | confirmed | conflicting

currency_detail
  inventory_item_id  pk, fk
  note_type_id                -- Federal Reserve Note, Silver Certificate, …
  series_year, series_letter  -- a series letter is NOT a mint mark
  seal_color_id
  signature_combination_id
  fed_district_id             -- A Boston … L San Francisco
  serial_number               -- text, always; leading zeros and stars matter
  friedberg_id                -- see §5
  friedberg_status            -- unknown | proposed | confirmed | conflicting
```

Two modelling points worth stating explicitly, because conflating them loses
information:

- A **series letter** on a banknote (`1957-B`) and a **mint mark** on a coin
  (`1921-D`) look alike and mean different things. They are separate columns on
  separate tables.
- A banknote's **serial number** is printed on the note; a **certificate
  serial** identifies a grading holder. Both are "a serial", neither is the
  other — see §5.

### `purchase_order` and `vendor`

```
purchase_order
  id, vendor_id, order_number text null, ordered_on date, source_url
  unique (vendor_id, order_number) where order_number is not null

vendor
  id, name, host, url, vendor_kind_id     -- marketplace | auction | mint | dealer
```

`order_number` is **text**, always. Order identifiers from marketplaces and
auction houses contain leading zeros, letters and separators; storing them as a
number destroys them irreversibly. Many acquisition channels issue no order
number at all, hence nullable and a partial unique index.

One order commonly contains many items, so this is a genuine one-to-many rather
than a denormalised convenience.

---

## 4. Reference tables

Every reference table carries `id`, `code`, `label`, `sort_order`, `is_active`,
and `source` (`seeded | derived | manual`). `code` is stable and machine-facing;
`label` is display text and may be edited freely.

| Table | Purpose | Representative values |
|---|---|---|
| `item_kind` | top-level classification | coin, currency, bullion, set, medal, token, other, unknown |
| `bullion_form` | bullion product type | Silver Eagle, Gold Eagle, Silver Round, Copper Round, Silver Bar, Copper Bar, Gold Maple, Libertad, Krugerrand, Britannia, Philharmonic, Buffalo |
| `set_form` | packaged set type | Mint Set, Proof Set, Silver Proof Set, Prestige Set, Coin Set |
| `storage_form` | physical packaging | single, roll, tube, box, bag, album, mint set, proof set |
| `currency` | issuing currency | USD, plus foreign currencies as encountered |
| `denomination` | `(currency_id, face_value, label, kind)` | 0.01, 0.05, 0.10, 0.25, 0.50, 1.00; $1 Bill, $2 Bill, $5 Bill … |
| `country` | issuer | United States, Canada, Mexico, United Kingdom, … |
| `mint` | coin mint mark | P, D, S, O, W, CC, C |
| `grade_scale` | grading system | Sheldon numeric (1–70), adjectival |
| `grade` | condition | MS60–MS70, PR/PF60–70, AU50–58, XF40–45, VF20–35, F12–15, VG8–10, G4–6, plus UNC, BU, GEM BU, Choice BU |
| `grade_designation` | grade suffix | DCAM, CAM, RD, RB, BN, FS, FB |
| `grading_service` | grader | PCGS, NGC, ANACS, ICG, PMG, SEGS |
| `note_attribute` | banknote features (m:n) | Star Note, Blue Seal, Red Seal, Green Seal, Consecutive, Fancy Serial, Error |
| `note_type` | banknote class | Federal Reserve Note, Silver Certificate, United States Note, Gold Certificate, National Currency, Legal Tender |
| `seal_color` | treasury seal | blue, red, brown, green, gold |
| `fed_district` | FRN district | A Boston, B New York, C Philadelphia, D Cleveland, E Richmond, F Atlanta, G Chicago, H St. Louis, I Minneapolis, J Kansas City, K Dallas, L San Francisco |
| `signature_combination` | Treasurer + Secretary, with terms | seeded from public records |
| `metal` | bullion metal | silver, gold, copper, platinum, palladium |
| `item_status` | acquisition lifecycle | ordered, received, canceled, returned, missing, unknown |
| `disposition` | sales lifecycle | held, listed, sold, shipped, delivered, returned_by_buyer |
| `authenticity` | authenticity finding | unverified, genuine, counterfeit, questionable |
| `error_type` | mint / printing error | Doubled Die, Off Center, Clipped Planchet, Struck Through, Repunched Mintmark, Miscut, Misaligned Print, Offset Printing, Gutter Fold, Ink Smear, Inverted Overprint, … |
| `valuation_basis` | which value applies | melt, numismatic, manual |
| `image_role` | what a photo shows | obverse, reverse, edge, detail, slab, certificate, group, packaging, unassigned |
| `storage_location_kind` | where things live | safe_deposit_box, safe, home, in_transit, sold, unknown |
| `carrier` | shipping carrier | USPS, UPS, FedEx, DHL |
| `vendor_kind` | acquisition channel | marketplace, auction, mint, dealer |

### Errors

An error is a manufacturing defect that usually makes an item *more* desirable,
so it is worth recording on any kind — a coin struck off centre, a note miscut
or printed with an offset. Two fields on `inventory_item`, applicable
throughout:

```
error_type_id   fk null    the classified defect, from the reference table
error_details   text null  free-form notes: position, extent, direction,
                           attribution, whatever the piece needs
```

`error_type` carries an `applies_to` column (`coin | currency | any`), because
the vocabularies barely overlap — a coin is *struck*, a note is *printed* — and
the entry form should offer only what is relevant.

| applies_to | representative values |
|---|---|
| coin | Doubled Die, Off Center, Clipped Planchet, Wrong Planchet, Broadstrike, Brockage, Die Crack, Cud, Lamination, Repunched Mintmark, Struck Through, Overdate, Mule, Blank Planchet |
| currency | Miscut, Misaligned Print, Offset Printing, Gutter Fold, Ink Smear, Missing Overprint, Inverted Overprint, Insufficient Inking, Obstruction, Fold-over |
| any | Other |

**Never inferred from free text.** Description fields are seller-supplied prose,
and keyword matching against them is unreliable in both directions: scanning a
real collection for error terms matched auction boilerplate such as *"ITEM SHOWN
ON SCREEN"* and *"no cancellations"*, while errors described in other words were
missed entirely. The error type is set by a person, and `error_details` exists
precisely because the interesting part rarely fits a vocabulary.

**Why grade is not one column.** Condition strings in this domain routinely
combine a grade, a designation, a grading service and — for notes — attributes
that are not grades at all. `PR69DCAM PCGS` is four facts. Storing it as text
makes "all my MS65-and-better Morgans" unanswerable. Each fact gets its own
column or link table, with the original string retained.

---

## 5. Identification: four distinct concepts

Routinely conflated, kept separate here:

| Concept | Scope | Lives in |
|---|---|---|
| **Grading agency** | who certified it | `grading_service` fk |
| **Certificate serial** | unique to one holder | `item_certification.cert_number` |
| **Note serial** | printed on the banknote | `currency_detail.serial_number` |
| **Type number** | shared by all items of that type | `friedberg_number` / `pcgs_type` |

```
item_certification            -- one to many: a lot may hold several certificates
  id, inventory_item_id, grading_service_id, cert_number text, raw text

item_note_attribute           -- many to many
  inventory_item_id, note_attribute_id
```

`cert_number` and `serial_number` are **text**. Both may contain leading zeros,
letters, stars and separators.

### Type catalogues

Friedberg numbers (US currency) and PCGS numbers (coins) identify a *type*, not
an individual item. Both come from commercial catalogues with no bulk licence,
so both are **curated tables that grow with use** rather than seeded datasets.

```
friedberg_number
  id, fr_number text unique, base_number int, district_letter char(1) null
  note_type_id, denomination_id, series_year, series_letter
  seal_color_id, signature_combination_id
  size_class            -- large | small | fractional
  source, verified_by, verified_at

pcgs_type
  id, pcgs_number int unique, description
  denomination_id, series, variety, year, mint_id
  source, verified_by, verified_at
```

**Resolution is a proposal, never a derivation.** Given whatever subset of
identifying attributes is known, the lookup returns ranked candidates. Nulls are
tolerated on both sides — a supplied value never excludes a catalogue row silent
on that attribute, and vice versa.

| Candidates | Behaviour |
|---|---|
| exactly 1 | propose it; one action to confirm |
| 2–20 | show them with the *differentiating* columns highlighted |
| 0 | offer to record a new type, pre-filled with what is known |

Confirming stamps `verified_by` / `verified_at`, so the catalogue improves with
use: the first note of a given type costs a lookup, every later one resolves
immediately.

**Uniqueness must be partial.** A plain unique index on the identifying tuple
would reject two differently half-known types. Use a partial unique index that
applies only to fully-specified rows, plus an unconditional `unique (fr_number)`
so a licensed dataset could later be merged on the catalogue number without
duplicating.

---

## 6. Money and valuation

### Cost basis — stored

```
taxes      = round((price + shipping) * tax_rate, 2)     generated, stored
total_cost = price + shipping + taxes                    generated, stored
```

### Current value — computed

A common-date, low-grade silver coin is worth its metal, and that moves daily
with spot. A key date in high grade carries a collector premium unrelated to
spot. One stored number cannot represent both, and a stored melt figure is stale
the moment the market moves.

```
composition                -- public-fact lookup, seedable from mint specifications
  denomination_id, country_id, year_from, year_to
  metal_id, fineness              -- 0.900, 0.999, 0.400, 0.350
  fine_weight_ozt                 -- actual metal weight per piece
  source

metal_price                -- time series
  metal_id, quoted_at, price_per_ozt, source
```

Coinage composition is a matter of legislation and mint specification, so it
resolves from denomination, country and year without being recorded per item,
and remains overridable.

### Weight is its own field, and it is not a float

Weight is stored on `inventory_item`, never left inside a denomination string,
because it is an input to a calculation rather than a label. Three points:

**`NUMERIC(12,6)`, not a float.** Weight multiplies straight into money —
`weight x spot x quantity` — and a float cannot represent `0.1` exactly, so the
error compounds through every revaluation. The rest of the schema already
forbids floats for money; the multiplicand deserves the same treatment. Six
decimal places in troy ounces represents every real figure exactly, including a
silver dime at `0.072338` and a 1.5 g gold nugget at `0.048225`.

**Gross and fine are different numbers.** A Morgan dollar weighs `0.859370` ozt
in total but contains `0.773440` ozt of silver, because it is 90% fine. Melt
value uses **fine** weight. Where a `composition` row resolves, fine weight is
derived from denomination and year rather than entered; for bullion it is set
directly.

**Troy ounces are the stored unit**, with `weight_raw` preserving what was
actually written — grams, kilos and pounds all appear in practice and convert on
the way in.

> One ambiguity worth knowing: precious metals are sold by the **troy** ounce
> (31.1035 g) while copper rounds are commonly sold by the **avoirdupois** ounce
> (28.35 g) — a 9.7% difference. It barely moves a copper valuation in money
> terms, but the unit a source meant should be recorded rather than assumed.

```
melt_value     = fine_weight_ozt * latest_spot(metal) * storage_quantity
reported_value = CASE valuation_basis
                   WHEN melt       THEN melt_value
                   WHEN numismatic THEN numismatic_value
                   WHEN manual     THEN numismatic_value
                 END
profit         = reported_value - total_cost
profit_pct     = profit / nullif(total_cost, 0)
```

All four are **view columns, not stored columns**, because spot price is an
input and it changes. `valuation_basis` defaults to `melt` where a composition
resolves and no premium has been entered, otherwise `numismatic`.

### `valuation_snapshot`

```
valuation_snapshot
  inventory_item_id, captured_at
  valuation_basis_id, spot_price_used, fine_weight_ozt
  melt_value, numismatic_value, reported_value
```

Recording the spot price *used* makes each snapshot reproducible, which a bare
historical value column never would be. Written on a schedule and on demand,
giving a real portfolio history.

---

## 7. Lifecycle: two independent axes

How an item **came in** and how it **goes out** are different questions.
Collapsing them into one column makes "received and sold" unrepresentable.

```
inventory_item.status_id       ordered → received → (canceled | returned | missing)
inventory_item.disposition_id  held → listed → sold → shipped → delivered
                                                   ↘ returned_by_buyer
```

`missing` is distinct from `canceled` and `returned`: paid for, not cancelled,
never arrived.

```
item_status_history
  inventory_item_id, from_status_id, to_status_id, changed_at, changed_by, note
```

History is a table rather than overwritten columns so that "when did this
actually arrive" survives later corrections.

**Status is per item, not per order.** An order may contain many items and split
shipments are normal, so receiving works item by item with an order-level bulk
action over the top; partial receipt leaves the remainder `ordered`.

### Physical location

```
storage_location
  id, storage_location_kind_id, institution, identifier, notes

location_history
  inventory_item_id, storage_location_id, moved_at, moved_by, note
```

Items move between locations, and "where was this in March" is worth being able
to answer. A sale writes `location_history` — `in_transit`, then `sold`.

---

## 8. Images

The **file** and its **use** are separate things, because one photograph may
serve several purposes with different visibility.

```
image                       -- the file, stored once, content-addressed
  id, sha256 unique, storage_key, media_type, byte_size
  width, height, captured_at, source_ref

image_derivative            -- generated, public-safe renditions
  image_id, kind (thumb | web), storage_key, width, height

item_image      (inventory_item_id, image_id, image_role_id, is_primary, sort_order)
listing_image   (listing_id,        image_id, sort_order)
shipment_image  (shipment_id,       image_id, kind)
```

Three link tables rather than a polymorphic `subject_type`/`subject_id`: real
foreign keys, each independently constrained. A partial unique index enforces at
most one `is_primary` per item.

`item_image.inventory_item_id` is **nullable** — photographs exist before anyone
decides what they depict, and must be storable, browsable and searchable in that
state.

**Bytes live in object storage, never in the database.** A collection's
photographs run to gigabytes. The database holds metadata and keys; a
`StorageBackend` interface abstracts local filesystem from S3-compatible storage.

### EXIF is stripped at ingest

Photographs of valuables commonly carry GPS coordinates of where they were
taken, along with camera identifiers and timestamps. Metadata is removed when
the image enters the system, not at publish time:

```
1. read     capture DateTimeOriginal, dimensions, orientation into columns
2. rotate   apply the orientation transform to the pixels
3. strip    drop every metadata segment
4. verify   reopen and assert none remains — fail the ingest if any does
5. hash     sha256 of the cleansed file; that is the identity
6. store    object storage; database records metadata only
```

Step 2 is not optional: stripping orientation metadata without first applying it
leaves images displaying rotated. Step 4 is the actual guarantee — it comes from
re-reading the written file, not from trusting the library that wrote it, and is
enforced by a test.

Originals are never served. Public requests are answered only from
`image_derivative` rows.

---

## 9. Sales

```
listing                       -- what is offered, and at what price
  id, inventory_item_id, price, currency_id, is_active, listed_at, ended_at

customer
  id, user_id null, display_name, email, phone

address
  id, customer_id, address_kind (shipping | billing)
  line1, line2, city, region, postal_code, country_id
  is_default, valid_from, valid_to

order        id, customer_id, order_status_id, total_amount, placed_at
order_item   id, order_id, listing_id, quantity, unit_price

shipment
  id, order_id, carrier_id, tracking_number, service_level
  shipped_at, delivered_at, cost, insured_value, weight_oz, shipment_status_id
```

`order_item` references `listing`, which references `inventory_item` — a single
foreign key chain, which is what keeping one inventory table buys.

`unit_price` on `order_item` is captured at purchase so later price changes never
rewrite order history. Shipment sits on the order rather than the item, because
one parcel carries many lines; it is one-to-many so partial shipment works as
partial receipt does on the buying side.

Addresses carry validity dates because customers move, and historical orders
must still show where they were actually sent.

---

## 10. Views and access boundaries

```sql
create view coin_inventory     as select ... where item_kind in ('coin','bullion','set');
create view currency_inventory as select ... where item_kind = 'currency';
create view item_valuation     as select ... melt, numismatic, reported, profit, profit_pct;
create view public_catalog     as select ... from listing join inventory_item ...;
```

The API and UI consume the views, so coins and currency read as independent
inventories with their own columns, filters and type catalogues, while nothing
shared is implemented twice and field names cannot drift apart.

**`public_catalog` is the authorisation boundary.** It must never expose
`storage_location_id`, `local_catalog_number`, `location_history`,
`item_certification` internals, cost basis, or inventory photographs. Staff-facing
views must never leak customer PII into listings. Both directions belong in
authorisation tests.

---

## 11. Constraints, indexes and conventions

**Naming.** Tables singular, snake_case. Foreign keys `<table>_id`. Reference
tables named for what they classify. Boolean columns read as assertions
(`is_active`). Timestamps `*_at`, dates `*_on`.

**Constraints worth having:**

- `check (price >= 0)`, `check (shipping >= 0)`, `check (storage_quantity > 0)`
- `check (year_end >= year_start)` where both are present
- partial unique on `(inventory_item_id) where is_primary` in `item_image`
- `unique (vendor_id, order_number) where order_number is not null`
- foreign keys to reference tables are `on delete restrict` — a classifier in use
  cannot vanish

**Indexes:**

- every foreign key
- `inventory_item (item_kind_id, status_id)` — the primary browse path
- `inventory_item (disposition_id) where disposition_id = 'listed'` — partial,
  for the public catalogue
- GIN on `to_tsvector(title || description)` for full text
- GIN on `attributes` for the JSONB long tail
- `metal_price (metal_id, quoted_at desc)` — latest-quote lookup

**Concurrency.** Anything that decrements available quantity locks the affected
rows with `SELECT ... FOR UPDATE` taken in a stable id order, so concurrent
buyers can neither oversell nor deadlock. This is verified by tests that drive
the handler from real threads — a test that serialises its requests will pass
even with the lock removed and is worthless.

**Migrations.** Every schema change is an Alembic revision, with a test that
asserts autogenerate finds no difference between the migrations and the models.

---

## 12. Reference data is shipped, not typed

Most of the reference tables hold facts that are true everywhere: the Sheldon
grades, the US mints, the twelve Federal Reserve districts, the composition of
a pre-1965 dime. None of that is specific to one collection, and no new
installation should have to rebuild it.

So reference data lives in **versioned JSON** under `backend/data/reference/`,
never hardcoded, and moves in both directions:

```
python -m app.seeding load                        idempotent, safe to re-run
python -m app.seeding export --out <dir>          dump back to seed files
python -m app.seeding export --source seeded derived
```

**Foreign keys travel as codes, not ids.** A seed row says `"metal": "silver"`,
never `"metal_id": 3`. Ids are per-installation; codes are not. Resolution is
generic -- the key `metal` is matched to the column `metal_id` and looked up in
whatever table that column points at -- so a new classifier table needs no
loader changes.

**`source` is the safety valve.** Every reference row records whether it was
`seeded`, `derived` by a rule, or entered `manual`ly:

| source | meaning | exported by default |
|---|---|---|
| `seeded` | shipped vocabulary, or confirmed public fact | yes |
| `derived` | learned from real data by an importer | only if asked for |
| `manual` | one operator's own decision | never, unless asked for |

This is what makes the export safe to hand to someone else. A collection's
private judgements do not escape as though they were curated facts, and a
hand-edited row is never overwritten by a later `load` -- a person's correction
outranks a shipped default.

It also creates a feedback loop worth naming: classifiers discovered while
importing real data are written as `derived`, reviewed, and the good ones
promoted into the shipped files. The vocabulary improves with use rather than
being guessed at once.

**Consequence for importers.** Because derived rows are candidates for export,
an importer that invents junk classifiers is not merely untidy -- it
contaminates a shared catalogue. So a value that cannot be confidently
classified is *declined*: the original text is kept in its `*_raw` column and
flagged for review, rather than becoming a reference row. Declining is the
safe direction.

---

## 13. Implementation notes

Three places where the built schema departs from the description above, each
for a concrete reason.

**`order` is `sales_order`.** `order` is a reserved word in SQL, so every
reference to it in a view or hand-written query would need quoting. The tables
are `sales_order` and `sales_order_item`.

**The storefront scaffold has been removed.** The demo's flat `coins` table and
its `orders` / `order_items` tables were superseded by `inventory_item`,
`listing` and `sales_order`, and the API now runs on those. `users` remains --
it was never scaffold, and the target schema references it from `customer`,
`item_status_history`, `location_history` and both type catalogues.

The scaffold left one mark on the way out. Its PostgreSQL enum type was named
`item_kind`, and the target schema has a reference *table* of that name; in
PostgreSQL a table implicitly creates a composite type, so tables and types
share one namespace and the two genuinely collided. The enum was renamed to
`coin_kind` by the migration that created the new tables, and dropped with
`coins` by the one that retired them.

**The listed-items index is not partial.** The design asks for
`inventory_item (disposition_id) where disposition_id = 'listed'`, but
`disposition_id` is a surrogate key and its value is not knowable at migration
time; a partial index would have to hardcode an integer that differs per
installation. It is a plain index on `disposition_id`. The public catalogue's
own partial index lives on `listing (is_active)`, where the predicate is a
boolean and does hold.

**Views are owned by the migration, not the metadata.** Alembic autogenerate
reflects tables only, so views are created and dropped explicitly from the
definitions in `app/models/views.py`. Two consequences worth knowing: a test
database built with `create_all` needs the views created separately, and a
downgrade must drop the views before the tables they read.
