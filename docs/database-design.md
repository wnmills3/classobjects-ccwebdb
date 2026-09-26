# Database design

The schema of the coin and banknote inventory and sales platform, as built:
PostgreSQL, mapped by SQLAlchemy 2 in `backend/app/models/`, migrated by
Alembic. The models are the source of truth; this document explains their
shape and the reasons behind the constraints that are easy to break.

---

## 1. Principles

**Classifiers are foreign keys, never free text.** Anything used to search,
filter, group or report is a reference table with a stable code. `$20 Bill`,
`$20 Blll` and `$20 B` are three strings and one concept. Labels are not
copied onto the rows that use them: there is one copy of each, so a rename
takes effect everywhere the moment it commits. Search speed comes from
reading the base tables with only the joins a query needs, plus partial
indexes on the facet columns (§11), not from denormalising.

**The owner's own words are kept.** `rating` holds the owner's rating as
written and `weight_note` a weight that is not a single number, beside the
classified values; the passes and search read them as evidence.

**Money is `NUMERIC(12,2)` mapped to `Decimal`.** No floats anywhere in the
stack. Weights, which multiply into money, are `NUMERIC(12,6)`.

**Cost basis is fixed; value is time-varying.** What was paid is stored.
What an item is worth moves with spot price and is computed at read time.

**Machine guesses never masquerade as curated facts.** Reference rows, type
catalog rows, attribute links and errors record whether they were
`seeded`, `derived` by a rule, or entered `manual`ly (the `provenance_source`
enum). Per-field provenance on items is `item_field_source` (§7).

**Storage location, cost basis and inventory photographs are never
customer-visible.** This is an authorization boundary, enforced and tested
(§10).

**One writer per invariant.** Where a column and a history or guard table
must change together, exactly one module writes them (§11).

---

## 2. Entity overview

```
  vendor ── purchase_order ── inventory_item ──┬── coin_detail ── pcgs_type
                                  │  │         └── currency_detail ── friedberg_number
                                  │  └── parent_item_id (lot lineage)
                                  │
      item_certification, item_error, item_attribute_link   (identification)
      item_status_history, location_history ── storage_location
      item_field_review, item_field_source, item_field_change  (per-field state)
      item_image ── image ── image_derivative
      valuation_snapshot                    composition, metal_price

  inventory_item ─┬─ listing ──────────── sales_order_item ── sales_order ── customer ── users
  sales_lot ──────┘   │  ├─ offer_claim         │                 ├─ sales_order_fee
   └─ sales_lot_item  │  ├─ listing_status_history                ├─ sales_order_change
                      │  └─ auction_lot ── auction                └─ shipment ── shipment_image
                      └─ sales_venue          sales_order_item_share ── inventory_item
```

Everything hangs off `inventory_item`. Coins, notes, bullion and sets share
one table: they share purchase, cost, grade, certification, status, location
and images; acquisitions routinely contain several kinds; sales reference any
of them through one foreign key chain; and an item whose kind is not yet known
still needs a row. What differs lives in 1:1 detail tables.

---

## 3. Items and acquisition

### `inventory_item`

One row per acquired item or lot (`models/core.py`).

| Column | Type | Notes |
|---|---|---|
| `id` | int pk | internal surrogate key |
| `item_code` | varchar(32), unique | **permanent identifier of the physical object**, `CC-000123`; server default from `item_code_seq` |
| `version` | int | optimistic concurrency (§11) |
| `purchase_order_id` | fk null | null when the acquisition is not recorded |
| `item_kind_id` | fk | coin, currency, bullion, set, medal, token, other, unknown |
| `denomination_id` | fk null | face value in a currency, coin or note |
| `bullion_form_id`, `set_form_id` | fk null | Silver Eagle, Mint Set, … |
| `storage_form_id` | fk | single, roll, tube, box, bag, album, … |
| `piece_count` | int, default 1 | how many objects the row stands for; every weight and value multiplies by it |
| `country_id` | fk null | issuer |
| `year_start`, `year_end` | int null | a coin's year, or a range for sets and rolls; always empty on a banknote, whose year is `currency_detail.series_year` |
| `series_id` | fk null | design series (Morgan Dollar); on the item so facets group on an indexed column of the scanned table |
| `strike_type_id` | fk null | business, proof, specimen, … — the "PR" of PR69 |
| `grade_id` | fk null | the number (`65`, `64+`), or a non-numeric grade |
| `grade_designation_id` | fk null | DCAM, CAM, RD, RB, BN, FS, FB on a coin; EPQ, PPQ on a note -- one per grade, part of it |
| `grading_service_id` | fk null | who graded it |
| `authenticity_id` | fk | unverified, genuine, counterfeit, questionable |
| `status_id` | fk | acquisition axis (§7) |
| `disposition_id` | fk | sales axis (§7) |
| `storage_location_id` | fk null | where it physically is (§7) |
| `parent_item_id` | fk null → `inventory_item` | the lot this piece was split from |
| `split_at` | timestamptz null | set on a lot when it is broken into pieces |
| `deleted_at` | timestamptz null | soft delete: the row should never have existed |
| `local_catalog_number` | varchar null | the owner's own earlier numbering; not unique |
| `source_title` | varchar(500) | what the seller called the item, kept verbatim |
| `description` | text | what a person recognizes the item by |
| `listing_url` | varchar null | where it was bought |
| `sellers_item_id` | varchar(64) null, indexed | the seller's own id for the listing it was bought from -- eBay's item number; every piece of one listing carries it, and one listing can be bought in several orders, so **not unique** |
| `rating` | text null | the owner's rating in their own words ("66EPQ Double Quad"); searched and read as evidence, never shown to a buyer |
| `weight_note` | text null | a weight as written where it is not a single number ("1 oz each") |
| `item_cost`, `shipping_cost` | numeric(12,2) | cost basis inputs (§6) |
| `tax_rate` | numeric(6,4) | stamped on insert (§6) |
| `tax_includes_shipping` | boolean | stamped on insert (§6) |
| `sales_tax`, `total_cost` | numeric(12,2), **generated** | §6 |
| `numismatic_value` | numeric(12,2) null | hand-entered collector value |
| `valuation_basis_id` | fk | melt, numismatic, manual |
| `composition_id`, `metal_id` | fk null | metal content (§6) |
| `fineness` | numeric(6,4) null | 0.9000, 0.9990 |
| `gross_weight_ozt`, `fine_weight_ozt` | numeric(12,6) null | troy ounces |
| `attributes` | jsonb | the long tail only |
| `source` | provenance_source | default `manual` |
| `created_at`, `updated_at` | timestamptz | |

**`item_code` never changes and is never reused.** It survives listing, sale,
return and relisting, so one object has one history. It comes from a sequence,
not from `id`, so a gap left by a deletion is never filled by a new item
wearing a dead item's code. The expression is written exactly as PostgreSQL
stores it so the migration drift test stays quiet.

**Split lots.** A lot bought as one thing and sold as many is split: each piece
becomes a child with `parent_item_id` pointing at the lot, and the lot gets
`split_at`. The parent is kept, because it holds the purchase order, the
original cost and the code a receipt refers to; but anything that counts
inventory or money must exclude rows with `split_at` set, or the collection
appears to cost twice what it did. Every view and every facet index does.

**Soft delete is not a disposition.** Disposition records what happened to an
object; "created by mistake" did not happen to one. `deleted_at IS NULL` sits
beside `split_at IS NULL` in every view.

**`attributes` promotion rule:** anything filtered, sorted, joined or
aggregated on earns a real column or reference table.

Errors, certificates and attributes such as Star Note are not columns on the
item; they are the link tables in §5.

### `coin_detail` and `currency_detail`

1:1 with `inventory_item` (primary key = `inventory_item_id`, cascade on
delete), present only for the relevant kind, every classifier nullable:
`currency_detail` for a banknote, `coin_detail` for every other kind. A kind
change swaps the row (`app.item_kinds.match_detail_to_kind`); leaving
banknote is refused while the note row holds a value.

| `coin_detail` | Notes |
|---|---|
| `mint_id` | fk `mint` |
| `variety` | free text |
| `pcgs_type_id` | fk `pcgs_type` (§5) |
| `pcgs_status` | `unknown` \| `proposed` \| `confirmed` \| `conflicting` (check constraint) |

| `currency_detail` | Notes |
|---|---|
| `note_type_id` | Federal Reserve Note, Silver Certificate, … |
| `series_year`, `series_letter` | a series letter is **not** a mint mark |
| `series_designation` | **generated**: `1957B`, or `1935` with no letter |
| `seal_color_id`, `signature_combination_id`, `fed_district_id` | fks |
| `serial_number` | text, indexed; leading zeros and star suffixes are meaning |
| `friedberg_id` | fk `friedberg_number` (§5) |
| `friedberg_status` | same four values as `pcgs_status` |
| `face_plate_number`, `back_plate_number`, `plate_position` | text; needed to identify a mule, and carry check letters |
| `printing_facility` | `dc` \| `fw` (check constraint), null when not known; read from the face plate when there is one -- `FW` before it is Fort Worth (`app.plates`) |

A series letter (`1957-B`) and a mint mark (`1921-D`) look alike and mean
different things, so they are separate columns on separate tables.

### `vendor` and `purchase_order`

| `vendor` | Notes |
|---|---|
| `name` | unique (`uq_vendor_name`) |
| `host`, `url` | the site, when there is one |
| `vendor_kind_id` | fk null: marketplace, auction, mint, dealer |

| `purchase_order` | Notes |
|---|---|
| `vendor_id` | fk, not null |
| `order_number` | **text**, nullable |
| `ordered_on` | date |
| `source_url`, `notes` | |

`order_number` is text because marketplace and auction identifiers carry
leading zeros, letters and separators. Many channels issue none, so it is
nullable with a partial unique index, `uq_purchase_order_vendor_number` on
`(vendor_id, order_number) WHERE order_number IS NOT NULL`.

---

## 4. Reference data and provenance

Every classifier table uses `ReferenceMixin` (`models/base.py`): `id`, `code`
(unique, `uq_<table>_code`), `label`, `sort_order`, `is_active`, `source`.
`code` is machine-facing and must not change once referenced; `label` is
display text and may be reworded. Foreign keys to them are `ON DELETE
RESTRICT`, so a classifier in use cannot vanish.

| Table | Classifies | Extra columns |
|---|---|---|
| `item_kind` | coin, currency, bullion, set, medal, token, other, unknown | |
| `bullion_form` | Silver Eagle, Silver Bar, Krugerrand, … | `metal_id`, `typical_fine_weight_ozt`, `typical_fineness` |
| `set_form` | Mint Set, Proof Set, … | |
| `storage_form` | single, roll, tube, box, bag, album, … | `default_quantity` (a data-entry default only) |
| `currency` | USD, … | `symbol`, `minor_units` |
| `country` | issuer | `iso_alpha2` |
| `denomination` | a face value | `currency_id`, `face_value` numeric(12,4), `kind` (`coin` \| `note`); unique `(currency_id, face_value, kind)` |
| `series` | design series | `year_start`, `year_end`, `applies_to`, `denomination_id`, `needs_evidence`, `seal_color_id`, `note_type_id` |
| `mint` | coin mint | `mark` (may be blank), `country_id` |
| `grade_scale` | Sheldon, adjectival, note scale | |
| `strike_type` | business, proof, specimen, reverse proof, … | `prefix`, `suffix` |
| `grade` | condition | `grade_scale_id`, `numeric_value`, `is_plus`, `grade_rank` (**generated**: number + 0.5 for plus) |
| `grade_designation` | DCAM, CAM, RD, RB, BN, FS, FB, ... ; EPQ, PPQ | `applies_to` (`coin` \| `currency`): the API refuses the other kind's |
| `grading_service` | PCGS, NGC, ANACS, ICG, PMG, SEGS | |
| `authenticity` | unverified, genuine, counterfeit, questionable | |
| `item_attribute` | Star Note, No Motto, First Strike, CAC, Details, … | `applies_to`, `attribute_group` (`serial` \| `variety` \| `release` \| `verification` \| `qualifier`) |
| `note_type` | banknote class | |
| `seal_color` | blue, red, brown, green, gold, … | |
| `fed_district` | A Boston … L San Francisco | `letter`, `city`, `number` |
| `signature_combination` | Treasurer and Secretary | `treasurer`, `secretary`, `term_from`, `term_to` |
| `metal` | silver, gold, copper, platinum, palladium | `symbol`, `is_precious` |
| `valuation_basis` | melt, numismatic, manual | |
| `error_type` | mint or printing error | `applies_to` (`coin` \| `currency` \| `any`) |
| `item_status` | acquisition axis | |
| `disposition` | sales axis | |
| `storage_location_kind` | safe_deposit_box, safe, home, in_transit, sold, … | |
| `image_role` | obverse, reverse, edge, slab, … | |
| `vendor_kind` | marketplace, auction, mint, dealer | |
| `sales_venue_kind` | own_store, marketplace, live_auction, auction_house | |
| `carrier` | USPS, UPS, FedEx, DHL | `tracking_url_template` |
| `sales_order_status` | order lifecycle | |
| `sales_fee_kind` | fee kinds on a sale | |
| `shipment_status` | label_created, in_transit, delivered, … | |

`REFERENCE_MODELS` in `models/__init__.py` lists them in dependency order;
seeding, export and the `/api/reference/<table>` endpoints all walk it, and a
test fails if a classifier table is left out.

**Grade is several facts, not one string.** `PR69DCAM PCGS` is a strike type,
a number, a designation and a service; stored as text it makes "all my
MS65-and-better Morgans" unanswerable. The display form is composed by the SQL
function `grade_display(strike_prefix, strike_suffix, numeric_value, is_plus,
label, sheldon)`, which the views call; a strike with no prefix takes MS, AU,
XF … from the number.

**Series.** `series.label` is the formal name. `series_alias` (`series_id`,
`alias`, `is_active`, `source`; unique per series) carries what people say —
"Mercury" for the Winged Liberty Head Dime — and an item inherits aliases
through its series. `series_year_range` (`series_id`, `denomination_id`,
`year_start`, `year_end`, `letters`) holds the runs of years a design was
issued in, since one span would make every dollar since 1878 a Morgan;
`letters` narrows a note series by letter, with `*` meaning no letter. Its
unique constraint is `NULLS NOT DISTINCT`, because most ranges have no
denomination of their own and a re-load would otherwise duplicate them. Both
child tables cascade on delete from `series`, the only reference foreign keys
that do.

**`note_issue`** is not a classifier but a table of facts: one row per
small-size issue — `denomination_id`, `series_year`, `series_letter`,
`note_type_id`, `seal_color_id`, `signature_combination_id`, `variant`
(Hawaii, North Africa), `serial_prefix`. `app.classifier_defaults` looks
notes up in it instead of each note being typed by hand. A series issued in
two classes has two rows; unique (`uq_note_issue`, `NULLS NOT DISTINCT`) over
denomination, year, letter, class and seal. The seed file owns it: a load
makes the rows match the file exactly.

**`reference_alias`** gives another name to a row of any classifier
(`table_name`, `row_id`, `alias`, `is_active`, `source`) — "Legal Tender
Note" for United States Note. It names the row by table and id because a
foreign key cannot point at any table. A removed shipped alias is kept with
`is_active = false`, because seed loads only add.

**`reference_merge`** records a value merged into another and removed
(`table_name`, `code`, `label`, `merged_into`, `merged_at`, `merged_by_id`),
so a later seed load skips the merged code rather than resurrecting it.

**Errors** are recorded per item in `item_error` (§5), classified by
`error_type`. `applies_to` exists because coin and note vocabularies barely
overlap — a coin is struck, a note is printed. Errors are never inferred from
description text: seller prose matches error keywords both falsely and not at
all.

---

## 5. Identification

Four concepts that are routinely conflated, kept apart:

| Concept | Scope | Lives in |
|---|---|---|
| Grading agency | who certified it | `inventory_item.grading_service_id` |
| Certificate serial | one holder | `item_certification.cert_number` |
| Note serial | printed on the note | `currency_detail.serial_number` |
| Type number | every item of the type | `friedberg_number`, `pcgs_type` |

All serials and numbers are text.

### Per-item link tables

| Table | Key and columns | Notes |
|---|---|---|
| `item_certification` | `id`; `inventory_item_id`, `grading_service_id`, `cert_number` | one to many: a lot may hold several certified pieces |
| `item_error` | `id`; `inventory_item_id`, `error_type_id`, `details`, `source`, `noted_by_id`, `noted_at` | unique `(inventory_item_id, error_type_id)`: a miscut and an overprint on one bill are two rows, the same error twice is one |
| `item_attribute_link` | pk `(inventory_item_id, item_attribute_id)`; `source`, `derived_by`, `noted_by_id`, `noted_at`, `removed_at` | many to many |

**A removed attribute stays removed.** A rule reading the serial would put a
deleted link straight back, so a person removing one sets `removed_at`; every
reader skips such rows and every rule leaves them alone.

### Type catalogs

A Friedberg or PCGS number identifies a *type*, not an object. Both are
commercial catalogs, so both tables are **curated as notes and coins
arrive**, not seeded. Resolution against them is a proposal, never a
derivation: the lookup returns ranked candidates, a person confirms, and
confirmation stamps `verified_by_id` and `verified_at` so the next lookup can
trust the row.

| `friedberg_number` | Notes |
|---|---|
| `fr_number` | text, unconditionally unique (`uq_friedberg_number_fr_number`) |
| `base_number`, `district_letter` | |
| `note_type_id`, `denomination_id`, `series_year`, `series_letter`, `seal_color_id`, `signature_combination_id` | |
| `size_class` | `large` \| `small` \| `fractional` (check constraint) |
| `web_press` | boolean null: web-press and sheet-fed printings are different types |
| `printing_facility` | `dc` \| `fw` (check constraint), null when not known: a 2017-A $1 printed in Washington and one printed in Fort Worth are different types |
| `description`, `source`, `verified_by_id`, `verified_at` | |

**Identity is partially unique.** `uq_friedberg_number_identity` is unique on
`(denomination_id, series_year, series_letter, note_type_id, district_letter,
web_press, signature_combination_id, seal_color_id, printing_facility)`
**`NULLS NOT DISTINCT`**,
only where denomination, year and note type are known. Partial, because a
plain unique index would reject two differently half-known types, which is
normal in a catalog built by hand. `NULLS NOT DISTINCT`, because most series
have no letter and PostgreSQL otherwise treats two NULLs as different, so the
index would never fire and one type could be recorded twice under two numbers.
That makes every column that tells two types apart a member: many series
differ only by signatures, and wartime issues only by seal color.

| `pcgs_type` | Notes |
|---|---|
| `pcgs_number` | int, unique (`uq_pcgs_type_number`) |
| `description`, `denomination_id`, `series`, `variety`, `year`, `mint_id` | |
| `source`, `verified_by_id`, `verified_at` | |

`uq_pcgs_type_identity` is unique on `(denomination_id, year, mint_id,
variety)` where denomination, year and mint are known.

---

## 6. Money and valuation

### Cost basis: stored

```
sales_tax  = round((item_cost + CASE WHEN tax_includes_shipping
                                     THEN shipping_cost ELSE 0 END) * tax_rate, 2)
total_cost = item_cost + shipping_cost + sales_tax
```

Both are `GENERATED ALWAYS AS … STORED`, so they cannot drift or be written.
PostgreSQL forbids one generated column referencing another, so the tax
expression is repeated inside `total_cost` from one constant in `core.py`.

`tax_rate` and `tax_includes_shipping` are columns, stamped from the
`SALES_TAX_RATE` and `SALES_TAX_INCLUDES_SHIPPING` settings when the row is
inserted and never read from settings again. Tax paid is a historical fact:
changing the setting affects later purchases only. `tax_rate` has
deliberately no server default, so a raw `INSERT` that omits it fails rather
than using a stale rate. `0` records a purchase charged no tax.

The names are `item_cost` (not "price", which is `listing.price`, the asking
side) and `sales_tax` (not "taxes", since income tax on a gain is another
thing). Profit is not a column.

### Weight

Weight is on the item because it is an input to a calculation. It is
`NUMERIC(12,6)` troy ounces, never a float: six places represent every real
figure exactly, down to a silver dime at `0.072338`. **Gross and fine differ**
— a Morgan dollar weighs `0.859370` ozt and contains `0.773440` ozt of silver
— and melt uses fine weight. `weight_note` keeps a weight written as something
other than a single number. Check constraints keep fineness in `(0, 1]`,
weights non-negative, and fine within gross.

Precious metals are sold by the troy ounce (31.1035 g), copper rounds often by
the avoirdupois ounce (28.35 g); the unit a source meant should be recorded,
not assumed. `shipment.weight_oz` is a postal weight in avoirdupois ounces and
is unrelated.

### `composition`

A public-fact lookup from legislation and mint specifications: a 1963 US dime
is 90% silver because the law said so. `denomination_id`, `country_id`,
`year_from`, `year_to` (null = current), `metal_id`, `fineness`,
`gross_weight_ozt`, `fine_weight_ozt`, `note`, `source`. Unique
`(denomination_id, country_id, year_from, metal_id)`. The item's own metal,
fineness and weights override it where the table cannot know better.

### Value: computed

```
melt_value     = round(fine_weight_ozt * latest spot * piece_count, 2)
reported_value = melt_value        when valuation_basis = melt
                 numismatic_value  when numismatic or manual
profit         = reported_value - total_cost
profit_pct     = profit / nullif(total_cost, 0)
```

These are columns of the `item_valuation` view, not of any table, because spot
price is an input and it moves. `metal_price` (`metal_id`, `quoted_at`,
`price_per_ozt` numeric(12,4), `source`; unique per metal and time) is a time
series, so "as of" questions are answerable; `ix_metal_price_latest` on
`(metal_id, quoted_at DESC)` serves the latest-quote lookup.

### `valuation_snapshot`

`inventory_item_id`, `captured_at`, `valuation_basis_id`, `spot_price_used`,
`fine_weight_ozt`, `piece_count`, `melt_value`, `numismatic_value`,
`reported_value`. Recording the inputs makes each figure reproducible;
`piece_count` is copied rather than joined so a later correction to the item
does not restate a past valuation.

---

## 7. Lifecycle, location and per-field state

### Two axes

How an item **came in** and how it **goes out** are different questions;
one column would make "received and sold" unrepresentable.

```
inventory_item.status_id       ordered → received → (canceled | returned | missing)
inventory_item.disposition_id  held → listed → sold → shipped → delivered
```

`missing` means paid for, not cancelled, never arrived. Status is per item,
not per order: split shipments are normal and partial receipt leaves the rest
`ordered`.

| `item_status_history` | Notes |
|---|---|
| `inventory_item_id`, `from_status_id`, `to_status_id` | `from_status_id` is null on an item's opening row |
| `changed_at`, `changed_by_id`, `note` | |
| `arrived_on` | date the parcel actually arrived, when that differs from when it was recorded; null on non-arrivals |

History is a table so "when did this arrive" survives a later correction.
That holds only because **`app.lifecycle_writes` is the only writer of
`status_id` and `storage_location_id`**, writing the history row with every
change, and every path that creates an item records its opening row.
Disposition changes caused by offering and selling belong to
`app.offering_writes` (§9).

### Physical location

| `storage_location` | Notes |
|---|---|
| `storage_location_kind_id`, `institution`, `identifier`, `notes` | |

Unique `(storage_location_kind_id, institution, identifier)`, plus
`uq_storage_location_identity_no_identifier` on `(storage_location_kind_id,
institution) WHERE identifier IS NULL`: the constraint alone never fires when
`identifier` is null, and two concurrent first consignments to one auction
house would otherwise each create a location.

`location_history` (`inventory_item_id`, `storage_location_id`, `moved_at`,
`moved_by_id`, `note`) answers "where was this in March".

### Per-field state

| Table | A row means | Unique |
|---|---|---|
| `item_field_review` (`field_name`, `reviewed_at`, `reviewed_by_id`) | a person confirmed this field by looking at the object | `(inventory_item_id, field_name)` |
| `item_field_source` (`field_name`, `derived_by`, `derived_at`) | a pass filled this field from known facts and may refresh it; `derived_by = 'held'` means a person emptied it on purpose and no pass may fill it | `(inventory_item_id, field_name)` |
| `item_field_change` (`field_name`, `old_value`, `new_value` JSONB, `changed_by_id`, `changed_at`) | a person's edit changed this field -- one row per change, never updated | none; indexed on `(inventory_item_id, field_name, changed_at)` |

`item_field_change` is written by the item edit and the bulk edit
(`app.field_changes`), in the same transaction as the change, only for a field
whose value actually moved. Values are stored as the item editor sees them
(codes for classifiers, strings for money). It is what the editor reads to say
*who* changed a field it warns about; the passes, receiving and offering do
not write it, so a field changed that way has no entry.

`PUT /api/inventory/{id}/errors` logs the item's error set too, as one
`errors` row holding the whole set before and after (`[{error_type,
details}]`), since that endpoint replaces the set; saving an unchanged set
logs nothing.

The item editor's **History** panel reads it together with
`item_status_history` and `location_history` (`app.item_history`,
`GET /api/inventory/{id}/history`, admin-only): one list, newest first, with
classifier codes shown by their labels and a move's origin taken from the
previous move's destination.

Per field, because attribution works field by field and a half-done item is
the normal state. No `item_field_source` row means the value was recorded
rather than filled by a pass, and no pass touches it; saving a field by hand
deletes its row. `field_name` is checked against a whitelist in the API, not a
constraint, since which fields matter will change. The passes are listed in
[system-administration.md](system-administration.md), *The passes over stored
items*.

---

## 8. Images

The **file** and its **use** are separate, because one photograph may serve
several purposes with different visibility (`models/images.py`).

| Table | Columns | Notes |
|---|---|---|
| `image` | `sha256` (unique), `storage_key`, `media_type`, `byte_size`, `width`, `height`, `captured_at`, `source_ref` | the file, stored once, content-addressed |
| `image_derivative` | `image_id`, `kind` (`thumb` \| `web`), `storage_key`, `width`, `height` | unique `(image_id, kind)` |
| `item_image` | `inventory_item_id` (**nullable**), `image_id`, `image_role_id`, `is_primary`, `sort_order`, `note` | unique `(inventory_item_id, image_id)` |
| `listing_image` | `listing_id`, `image_id`, `sort_order` | unique pair |
| `shipment_image` | `shipment_id`, `image_id`, `kind` (`packed` \| `label` \| `handover` \| `damage`) | unique triple |

Three link tables rather than a polymorphic subject: real foreign keys, each
constrained on its own. `item_image.inventory_item_id` is nullable because
photographs exist before anyone decides what they show. `uq_item_image_primary`
allows at most one `is_primary` per item; **`app.image_links` is the only
writer of `item_image`**, because promoting a photograph means demoting the
incumbent first in the same transaction.

Bytes live in object storage, never in the database; a `StorageBackend`
abstracts local disk from S3-compatible storage.

### Metadata is stripped at ingest

Photographs of valuables carry GPS coordinates of where the valuables are
kept. `app.imaging` removes metadata when an image enters the system:

```
1. read     capture DateTimeOriginal, dimensions, orientation into columns
2. rotate   apply the orientation transform to the pixels
3. strip    drop every metadata segment
4. verify   reopen and assert none remains -- fail the ingest if any does
5. hash     sha256 of the cleansed file; that is the identity
6. store    object storage; database records metadata only
```

Rotation comes before stripping because orientation is itself metadata. Step 4
re-reads the written bytes rather than trusting the library that wrote them.
Originals are never served; public requests are answered only from
`image_derivative`.

---

## 9. Selling

`sales_order_item → listing → inventory_item` is one foreign key chain to any
kind of item, which is what the single inventory table buys. The order tables
are `sales_order` and `sales_order_item` because `order` is a reserved word.

### `sales_venue`

A platform the business sells through: the web store, eBay, an auction house.
Installation data, not shipped reference data.

| Column | Notes |
|---|---|
| `code` (unique), `name`, `sales_venue_kind_id` | |
| `is_own_store` | true on exactly one row (`uq_sales_venue_own_store`, partial on `is_own_store`); a migration creates it |
| `vendor_id` | fk null, unique: the same business as a purchase source, so eBay is one partner either way |
| `account_handle`, `listing_url_template` | |
| `commission_rate`, `processing_rate` | numeric(6,4) fractions in `[0, 1]` |
| `processing_fixed`, `listing_fee` | numeric(12,2), non-negative |
| `terms_as_of` | date the default fees were read |
| `notes`, `is_active`, `version` | |

The fee columns are **defaults for estimating** a sale's net and are never
stored per sale; what was actually charged is `sales_order_fee`.

### `listing`

What is offered, where, and at what price.

| Column | Notes |
|---|---|
| `inventory_item_id` / `sales_lot_id` | exactly one is set (`ck_listing_item_xor_lot`) |
| `price`, `currency_id`, `quantity_available` | a lot listing has quantity ≤ 1 |
| `sales_venue_id` | not null: every offer is attributable to a platform |
| `format` | `fixed_price` \| `auction` |
| `status` | `active` \| `paused` \| `ended` |
| `is_active` | **generated**: `status = 'active'`; cannot be written |
| `external_id`, `external_url` | the platform's listing number and page |
| `paused_by_listing_id` | fk `listing`: the offer this store listing was set aside for; null unless paused |
| `title`, `description` | public text; blank falls back to the item's |
| `listed_at`, `ended_at`, `version` | |

The shop and checkout accept only listings on the own-store venue, with
`format = fixed_price` and `status = active`. `ix_listing_active` indexes
`inventory_item_id WHERE is_active`. Relisting is always a **new** row, never a
reuse of an ended one, and `ended_at` is set once.

`listing_status_history` (`listing_id`, `from_status`, `to_status`,
`changed_at`, `note`) is the offer timeline — the opening row, each pause,
resumption and ending. `note` says "sold" or "withdrawn", which status alone
cannot.

### `offer_claim`: an item is offered in one place at a time

One row per listing that holds an item: an item listing has one claim, a lot
listing one per member.

| Column | Notes |
|---|---|
| `inventory_item_id`, `listing_id` | unique pair |
| `state` | `active` \| `paused` \| `released`, following the listing's status one for one |

**`uq_offer_claim_active`** is a partial unique index on `inventory_item_id
WHERE state = 'active'`. Released claims accumulate as history; two active
claims on one item are impossible, and the index is the backstop if two
requests race. A paused claim is a store listing set aside while the item is
offered elsewhere: it keeps its listing and price and resumes if that offer
ends unsold.

**`app.offering_writes` is the only writer of `listing.status`,
`listing.ended_at`, `offer_claim`, `listing_status_history`,
`sales_lot.status`, `sales_lot_item.released_at` and the disposition changes
they cause**, each in the same transaction as the listing it mirrors. Demo
seeding goes through it too.

### Sales lots

A temporary grouping offered and sold as one thing. Deliberately not an
inventory item, which would count it beside its own members.

| Table | Columns | Notes |
|---|---|---|
| `sales_lot` | `title`, `description`, `status` (`assembling` \| `offered` \| `sold` \| `dissolved`), `version` | editable only while `assembling`; never reopened, a re-offer is a new lot |
| `sales_lot_item` | `sales_lot_id`, `inventory_item_id`, `released_at`, timestamps | unique pair; `uq_sales_lot_item_open` on `inventory_item_id WHERE released_at IS NULL` keeps an item in one open lot |

Membership is released, not deleted: which coins were in a lot that sold is
part of the sale's record. `app.lot_writes` assembles lots; once offered they
belong to `app.offering_writes`.

### Auctions

| Table | Columns | Notes |
|---|---|---|
| `auction` | `sales_venue_id`, `title`, `external_id`, `starts_at`, `ends_at`, `status`, `consigned_on`, `notes`, `version` | status `draft` \| `scheduled` \| `consigned` \| `closed` \| `settled` \| `cancelled` |
| `auction_lot` | `auction_id`, `listing_id` (unique), `lot_number` (text), `reserve`, `result`, `hammer_price`, `buyer_customer_id` | unique `(auction_id, lot_number)`; `result` (`sold` \| `unsold` \| `withdrawn`) null until settled |

An auction-format listing belongs to an auction; a timed eBay auction is an
auction with one lot. `consigned` and `consigned_on` apply to auction houses,
which take physical custody; custody is tracked by `consigned_on IS NOT NULL`,
not by status. **`app.auctions` is the only writer of both tables**; it offers
and ends through `offering_writes`, moves items through
`lifecycle_writes.set_location`, and settles through `sales_writes`.

### Customers and addresses

| `customer` | Notes |
|---|---|
| `user_id` | fk `users` null, unique: a login has at most one customer |
| `display_name`, `email`, `phone`, `notes` | |
| `sales_venue_id`, `venue_username` | a buyer known on a platform; null for a store customer |

`uq_customer_venue_username` is unique on `(sales_venue_id,
lower(venue_username))`, because platforms display one account's name in
varying case. `uq_customer_venue_undisclosed` allows one row per venue with no
username, for houses that do not name buyers.

`address`: `customer_id`, `address_kind` (`shipping` \| `billing`), `line1`,
`line2`, `city`, `region`, `postal_code`, `country_id`, `is_default`,
`valid_from`, `valid_to`. Addresses are superseded rather than edited, so a
past order still shows where it went; `uq_address_default` allows one default
per customer and kind.

### Orders

| Table | Columns | Notes |
|---|---|---|
| `sales_order` | `customer_id`, `sales_venue_id`, `external_order_id`, `sales_order_status_id`, `shipping_address_id`, `billing_address_id`, `total_amount`, `placed_at`, `notes`, `placed_by_id`, `version` | `placed_by_id` is the buyer or an administrator acting for them |
| `sales_order_item` | `sales_order_id`, `listing_id`, `quantity` (> 0), `unit_price`, `item_snapshot` (jsonb), `snapshot_at` | price and item description captured at sale, so later edits never rewrite history |
| `sales_order_item_share` | `sales_order_item_id`, `inventory_item_id`, `amount`, `fee_amount` | unique pair; one row per item on every line |
| `sales_order_fee` | `sales_order_id`, `sales_fee_kind_id`, `amount`, `note` | actual fees from the platform's statement |
| `sales_order_change` | `sales_order_id`, `changed_at`, `changed_by_id`, `change`, `listing_id`, `from_value`, `to_value` | the order's edit history |
| `shipment` | `sales_order_id`, `carrier_id`, `shipment_status_id`, `tracking_number`, `service_level`, `shipped_at`, `delivered_at`, `cost`, `insured_value`, `weight_oz` | on the order, one to many, so partial shipment works |

**Shares are the one answer to "which items did this order carry".** A
single-item line has one share, a lot line one per member; shares sum exactly
to their line (`app.allocation`), so no cent is lost. Net payout is
`total_amount − sum(sales_order_fee.amount)`, computed when asked, never
stored. `app.order_writes` writes orders, lines and share amounts, and is the
one place stock moves; `app.sales_writes` writes `sales_order_fee` and
`fee_amount`.

---

## 10. Views and access boundaries

Four views, defined in `models/views.py` and created by migrations (Alembic
autogenerate reflects tables only). All exclude split and soft-deleted items.

| View | Shows |
|---|---|
| `coin_inventory` | kinds coin, bullion, set, medal, token, with coin detail and the composed grade |
| `currency_inventory` | kind currency, with note detail and the Friedberg number |
| `item_valuation` | melt, reported value, profit and profit % from the latest spot price (§6) |
| `public_catalog` | active, fixed-price, own-store listings with stock, and the public item fields |

**Nothing in the application reads them.** `app.inventory_search` queries the
base tables with only the joins each query needs, which is far faster than
paying for every join in a view. The views remain the schema's statement of
what each inventory and the public catalog contain.

**`public_catalog` describes the authorization boundary; code enforces it.**
The view must never expose storage location, `local_catalog_number`, cost
basis, purchase details, lineage or inventory photographs, and
`test_public_catalog_never_exposes_private_columns` asserts its columns
against `PUBLIC_CATALOG_FORBIDDEN_COLUMNS`. What actually keeps those fields
from a buyer is `routers/catalog.py:to_catalog_item`, which builds the public
shape field by field, with `CatalogItemOut` and
`test_catalogue_never_exposes_cost_basis_or_location`. Anything later built on
the view inherits the view's rules, which is why it is kept honest.

---

## 11. Constraints, indexes and conventions

**Naming.** Tables singular and snake_case (the login table, `users`, is the
exception). Foreign keys `<table>_id`. Booleans read as assertions
(`is_active`). Timestamps `*_at`, dates `*_on`. Constraints `ck_…`, unique
constraints and indexes `uq_…`, other indexes `ix_…`. Money
`NUMERIC(12,2)`, rates `NUMERIC(6,4)`, weights `NUMERIC(12,6)`, all
timestamps `timestamptz`.

**Foreign keys.** To reference tables: `ON DELETE RESTRICT`. Detail, history
and link rows owned by an item: `CASCADE`. References to `users` from audit
columns (`changed_by_id`, `noted_by_id`, `verified_by_id`, …): `SET NULL`, so
removing a member of staff never erases the record of what they did.

**Check constraints worth knowing.** Non-negative `item_cost`,
`shipping_cost`, prices, fees, share amounts and shipment costs;
`piece_count > 0`; year ranges ordered; fineness a fraction; fine weight
within gross; `pcgs_status`, `friedberg_status` and `size_class`
vocabularies; `printing_facility` (`dc` or `fw`) on `currency_detail` and
`friedberg_number`; `ck_listing_item_xor_lot`; address validity ordered.

**Partial and special unique indexes.**

| Index | Guarantees |
|---|---|
| `uq_offer_claim_active` | one active claim per item |
| `uq_sales_lot_item_open` | one open lot per item |
| `uq_item_image_primary` | one primary photograph per item |
| `uq_sales_venue_own_store` | one own store |
| `uq_address_default` | one default address per customer and kind |
| `uq_purchase_order_vendor_number` | order numbers unique per vendor when present |
| `uq_friedberg_number_identity`, `uq_pcgs_type_identity` | one catalog row per fully known type |
| `uq_customer_venue_username`, `uq_customer_venue_undisclosed` | one buyer per platform account |
| `uq_storage_location_identity_no_identifier` | one location per institution with no box number |
| `uq_note_issue`, `uq_series_year_range` | `NULLS NOT DISTINCT` identities for seeded facts |

**Indexes.** Every foreign key that is filtered on. On `inventory_item`:
`ix_inventory_item_kind_status`; GIN full text `ix_inventory_item_fts` over
`source_title || ' ' || description` with an explicit `english` regconfig;
GIN on `attributes`; and partial facet indexes on kind, grade, metal, country,
status, disposition and bullion form `WHERE split_at IS NULL`, which let the
planner answer a facet `GROUP BY` with an index-only scan. Time-ordered
history indexes on `item_status_history`, `location_history`,
`listing_status_history` and `valuation_snapshot`; `ix_metal_price_latest`.
Expression indexes are written exactly as PostgreSQL stores them, casts
included, or the drift test reports them changed on every run.

**Optimistic concurrency.** `inventory_item`, `listing`, `sales_venue`,
`sales_lot`, `sales_order` and `auction` carry `version`, used as
SQLAlchemy's `version_id_col`: every UPDATE checks the version it read, so two
people editing one record cannot silently overwrite each other. Editing is a
document edit where a person can resolve a conflict, so a row lock held across
a form would be wrong. `listing.quantity_available` is excluded: it is a
counter decremented under a row lock.

**Row locks.** Anything that moves stock or claims locks the affected rows
`FOR UPDATE` in one fixed order — lot row, then items by ascending id, then
listings by ascending id, each kind in one statement — through
`offering_writes.lock_for_sale`, so concurrent buyers can neither oversell nor
deadlock. See [lock-order-design.md](specs/lock-order-design.md). Tests drive
these paths from real threads; a test that serializes its requests passes
with the lock removed.

**Single writers.**

| Module | Only writer of |
|---|---|
| `app.lifecycle_writes` | `inventory_item.status_id`, `storage_location_id`, `item_status_history`, `location_history` |
| `app.offering_writes` | `listing.status`, `listing.ended_at`, `offer_claim`, `listing_status_history`, `sales_lot.status`, `sales_lot_item.released_at`, and the dispositions they cause |
| `app.lot_writes` | lot membership while `assembling` |
| `app.image_links` | `item_image` |
| `app.auctions` | `auction`, `auction_lot` |
| `app.order_writes` | order lines and share amounts; stock decrements |
| `app.sales_writes` | `sales_order_fee`, share `fee_amount` |
| `app.field_sources` | `item_field_source` |
| `app.field_changes` | `item_field_change` |

**Migrations.** The first Alembic revision is a baseline:
`backend/alembic/baseline.sql`, generated by `pg_dump` from the schema itself.
Every later schema change is a revision on top of it. `tests/test_migrations.py`
builds a database by running the migrations alone and asserts that
autogenerate finds no difference from the models, and that its views and
`grade_display()` equal the definitions in `app.models.views` and
`app.grades`, from which the `create_all` test database is built. A
migration that changes a view runs `DROP_VIEWS` and then `CREATE_VIEWS`. The
baseline cannot be downgraded; to go back, restore a backup.

---

## 12. Reference data is shipped, not typed

Most reference rows are facts true everywhere — Sheldon grades, US mints, the
twelve Federal Reserve districts, the composition of a pre-1965 dime — so they
live in versioned JSON under `backend/data/reference/` and load into any
installation:

```
python -m app.seeding load [--only <table> ...]              idempotent
python -m app.seeding export --out <dir> [--source seeded derived] [--include-inactive]
```

**Foreign keys travel as codes, not ids.** A seed row says `"metal": "silver"`;
the key `metal` resolves through the column `metal_id` to whatever table it
references, so a new classifier needs no loader change.

**`source` decides what is exported.**

| source | meaning | exported by default |
|---|---|---|
| `seeded` | shipped vocabulary or confirmed public fact | yes |
| `derived` | learned from real data by a rule | only if asked for |
| `manual` | one operator's own decision | only if asked for |

A load never overwrites a `manual` row — a person's correction outranks a
shipped default — and a merged code (`reference_merge`) is skipped.

Closed vocabularies the product defines rather than the world —
`sales_venue_kind`, `sales_fee_kind` — are seeded by the baseline
migration, as are the own-store `sales_venue` row and the six `strike_type`
rows (business, proof, specimen, reverse proof, enhanced reverse proof,
special mint set).

Seed files hold facts only, never a catalog publisher's numbering or prices
(`CLAUDE.md`, *Reference data*).

---

## 13. Users

`users` is the login table: `email` (unique), `full_name`, `hashed_password`,
`role` (`manager` \| `customer`), `is_active`, `token_version`, `created_at`.
Managers see cost basis; customers do not. Tokens are stateless JWTs
carrying `token_version`, which is bumped on every password change so that a
reset revokes every token issued before it.

It is referenced by `customer.user_id` and by the audit columns on history,
review, catalog, error, attribute, merge and order-change rows, all
`ON DELETE SET NULL`.

---

## 14. Not built

- No per-sale record of a platform's fee *estimate*; estimates are computed
  from `sales_venue` when shown.
- No stored net payout or realized gain; both are computed from shares and
  fees when asked.
