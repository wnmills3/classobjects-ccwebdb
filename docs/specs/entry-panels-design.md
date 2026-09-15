# Entry panels: New purchase and New item

Design. Status: approved by the user in advance ("design and build the entry
panels ... don't ask for my input"), not yet implemented (2026-09-15).

## The problem

The database has been the system of record since 2026-09-08
(`docs/workflow-import-and-cleanup.md`): rebuilding from the spreadsheet destroys
work that exists nowhere else. But nothing in the application can add an
acquisition. `docs/workflow-new-collection.md` specifies the panels and says
"the entry panels described here do not yet [exist]". The only creation paths
are the importer, the demo seed, and `POST /api/catalog`, which always creates a
shop listing, marks the item `listed`, and records no vendor, purchase order,
coin or banknote detail.

The owner has banknotes to add now.

## Scope

In: **New purchase** (a vendor and a purchase order) and **New item** (a coin,
banknote or other item bought on that purchase, standalone or as a lot).

Out: Split, Attribute and Group panels (the split endpoint exists; attribution
has the review pane); vendor editing and merging; purchase-order editing;
Friedberg attachment at entry (done afterwards from Receiving, which already
has `FriedbergLookup` for a currency item).

## Decisions

| Question | Decision |
|---|---|
| Can an item exist without a purchase? | **No new item is entered outside a purchase.** A standalone buy is a purchase holding one item. The purchase's order number is optional, so a walk-in or show purchase needs only a vendor. |
| Where do shipping and tax live? | **On each item, as the schema already has them** (`shipping_cost`, `tax_rate`, `tax_includes_shipping`). The purchase page holds a tax-rate default, "no sales tax charged" and "tax includes shipping", and pre-fills each new item form. No migration. |
| A lot? | **An item with `piece_count > 1`**, as the workflow doc defines. Splitting stays a later step. |
| Status on entry | **`ordered` or `received`**, default `ordered`; `received` for things already in hand. The opening status history row is written either way. |
| Disposition, authenticity, valuation, source | `held`, `unverified` unless given, `numismatic`, `manual`. |
| Vendors | Picked from a list; a missing vendor is added inline (name, kind, web address). Names are unique. |
| Repeated entry | **"Save and add another"** keeps the kind and the shared fields (country, denomination, series, seal, district, note type, grade scale, tax fields) and clears the per-note fields (title, serial, cost, certificate). |
| Web addresses | A purchase's `source_url` must start with `http://` or `https://` (422 otherwise), the same rule Receiving applies when showing it. |

## API contract (binding for backend and frontend)

All endpoints are administrator-only: 401 signed out, 403 for a customer. Request
bodies forbid unknown fields (422). Money crosses as decimal strings.

### Vendors -- `backend/app/routers/acquisitions.py`, new `vendors_router`, prefix `/vendors`

`GET /api/vendors` -> 200, list ordered by name:

```json
[{"id": 3, "name": "ebay.com", "url": "https://www.ebay.com", "vendor_kind": "marketplace"}]
```

`POST /api/vendors` body `{"name": str (1-255, trimmed, required), "url": str | null (http(s) only), "vendor_kind": str | null (vendor_kind code; null -> "unknown")}`
-> 201 with the vendor as above. 409 `"A vendor named <name> already exists"` on a
duplicate name (case-insensitive). 422 unknown kind or non-http url.

### Purchase orders -- `purchase_orders_router`, prefix `/purchase-orders`

`POST /api/purchase-orders` body `{"vendor_id": int, "order_number": str | null (trimmed; "" -> null), "ordered_on": date | null (not in the future beyond today+1 day, as receiving), "source_url": str | null (http(s) only), "notes": str | null}`
-> 201 with the existing `PurchaseOrderDetailOut` (`id, order_number, vendor, ordered_on, source_url, lines: []`).
404 unknown vendor. 409 `"<vendor> order <number> is already recorded"` when that
vendor already has that order number (the partial unique index).

The existing `GET /api/purchase-orders` and `GET /api/purchase-orders/{id}` are unchanged.

### Items -- `backend/app/routers/inventory.py`, `POST /api/inventory`

Body (`ItemCreate`, extra forbidden):

| Field | Type | Rule |
|---|---|---|
| `purchase_order_id` | int | required; 404 if unknown |
| `item_kind` | str | required item_kind code |
| `source_title` | str | required, 1-500 |
| `description` | str | default "" |
| `year_start`, `year_end` | int \| null | `year_end` null with a start -> equal to start (single year); end before start -> 422 |
| `piece_count` | int | default 1, >= 1 |
| `item_cost`, `shipping_cost` | Decimal | default "0.00", >= 0, 2 places |
| `tax_rate` | Decimal \| null | 0..1, 4 places; null -> configured default |
| `tax_includes_shipping` | bool \| null | null -> configured default |
| `status` | str | `ordered` (default) or `received`; anything else 422 |
| `country`, `denomination`, `grade`, `grade_designation`, `grading_service`, `metal`, `series`, `bullion_form` | str \| null | codes; unknown -> 422 naming the field |
| `storage_form` | str \| null | code; null -> `single` |
| `authenticity` | str \| null | code; null -> `unverified` |
| `cert_number` | str \| null | creates one `item_certification` (with `grading_service`) |
| `mint`, `variety` | str \| null | coin detail; only when the kind is not `currency` |
| `serial_number`, `series_year`, `series_letter`, `seal_color`, `fed_district`, `note_type` | str/int \| null | currency detail; only when the kind is `currency`; `series_letter` <= 4 chars |

Sending currency-detail fields for a non-currency kind, or coin-detail fields for
`currency`, is a 422 naming the field -- a silently dropped serial number is data
loss.

Behaviour, in one transaction, mirroring `SchemaLoader.load()`:
resolve every code (422 on the first unknown, naming the field) -> build the
`InventoryItem` (`source=manual`, `disposition=held`, `valuation_basis=numismatic`,
tax fields set only when given so the model defaults apply otherwise) -> flush ->
add exactly one detail row: `CurrencyDetail` for `currency`, otherwise `CoinDetail`
-> add the certification if `cert_number` -> `record_initial_status(db, item,
user_id=admin.id, note="entered in the console")` -> commit.

Response 201: the same body `GET /api/inventory/{id}` returns (`ItemDetailOut`),
so the console can show the generated `item_code`.

## Console

New route `/purchases/new`, nav link **New purchase** (after Receive).

**`frontend/src/owner/pages/NewPurchase.jsx`** -- two steps on one page:

1. *The purchase.* Either "Add to an existing purchase" (a picker over
   `listPurchaseOrders()`, showing number, vendor, date) or a new one: vendor
   select (from `listVendors()`, with "+ Add a vendor..." opening an inline
   name / kind / web address form that calls `createVendor`), order number,
   order date, web address, notes, **Create purchase**.
   Refusals (409 duplicate order, 422) are shown in place, keeping what was typed.
2. *Items on this purchase*, once a purchase is chosen or created: the purchase
   heading (vendor, number, date), purchase-wide defaults (tax rate pre-filled
   with the configured default, "No sales tax charged", "Tax includes shipping"),
   a table of items entered so far (item code, title, kind, cost, status) built
   from `getPurchaseOrder(id).lines`, the **New item** form below it, and a link
   **Receive these** to `/receiving?order=<id>`.

**`frontend/src/owner/pages/entry/NewItemForm.jsx`** -- props
`{ purchaseOrderId, defaults, onSaved }`:
- Kind (select over the `item_kind` vocabulary), title, description, year (with
  "Range of years" like the item editor), piece count, cost, shipping, status
  (ordered / received radio), country, denomination, grade (note-grade scale for
  currency, coin scales otherwise, as the item editor filters), grade designation,
  grading service, certificate number, metal (non-currency), series.
- Coin block (kind not currency): mint, variety.
- Banknote block (kind currency): serial number, series year, series letter, seal
  colour, Federal Reserve district, note type.
- Classifier pickers are `ReferenceSelect`, so a missing value is added in place.
- Money validated with `isMoney` from `pages/orders/cents.js` before sending.
- **Save** and **Save and add another** (keeps the shared fields listed in
  Decisions, clears the rest, focuses the title). A refusal is shown in the form
  with the input kept.
- Keyboard accelerators via `accel` / `AccessLabel` (Alt+letter, avoiding D, E, F)
  and `useSaveShortcut` (Ctrl+S / Ctrl+Enter = Save).

Console API additions in `frontend/src/owner/api.js`: `listVendors()`,
`createVendor(payload)`, `createPurchaseOrder(payload)`, `createInventoryItem(payload)`.

Docs: `docs/workflow-new-collection.md` status updated (New purchase, New item and
New lot exist; Split panel, Attribute walk and Group still do not);
`docs/system-administration.md` gains an "Entering a purchase" section.

## Testing

Backend (`backend/tests/test_entry_vendors_purchases.py`, `backend/tests/test_inventory_create.py`):
vendor list/create, duplicate name 409 (case-insensitive), unknown kind 422,
non-http url 422; purchase create, unknown vendor 404, duplicate vendor+number 409,
two purchases with no number for one vendor allowed, future date 422, bad url 422;
item create for a coin (coin detail, mint) and a banknote (currency detail with
serial, series year/letter, seal, district, note type; generated
`series_designation`), a lot (`piece_count` 5), `status` ordered vs received with
one opening status-history row, tax defaults vs explicit zero, certification row,
single-year normalisation, unknown code 422 naming the field, cross-kind detail
fields 422, unknown purchase order 404, extra field 422, and 401/403 on every new
endpoint with nothing written. The response carries a generated `CC-` item code.

Frontend (`NewPurchase.test.jsx`, `entry/NewItemForm.test.jsx`): creating a
vendor inline and a purchase; picking an existing purchase; 409 kept in place;
item payload for a coin and a banknote (banknote fields shown only for currency);
money validation; Save and add another keeps shared fields and clears per-note
fields; accelerators present; Receive these link.

Gates: `scripts\ccweb_check.cmd` exit 0 before merge; a final whole-branch review.
