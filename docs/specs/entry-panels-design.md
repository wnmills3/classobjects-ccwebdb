# Entry panels: Purchases and New item

The database is the system of record, so acquisitions are entered in the
console: **Purchases** records a vendor and a purchase order, and **New
item** records a coin, banknote or lot bought on it. Splitting a lot,
attributing its pieces, editing a vendor and editing a purchase order happen
elsewhere; a Friedberg number is attached afterwards from Receiving or the item
editor, where -- like a photograph added there -- it is held until the editor's
Save.

## Rules

| Question | Rule |
|---|---|
| Can an item exist without a purchase? | **No.** A standalone buy is a purchase holding one item. The order number is optional, so a walk-in or show purchase needs only a vendor. |
| Where do shipping and tax live? | **On each item** (`shipping_cost`, `tax_rate`, `tax_includes_shipping`). The page's purchase-wide values pre-fill each item. |
| A lot? | An item with `piece_count > 1`. Splitting is a later step. |
| Status on entry | `ordered` (default) or `received` for things already in hand. The opening status-history row is written either way. |
| Other defaults | disposition `held`, authenticity `unverified` unless given, valuation basis `numismatic`, source `manual`. The New item form starts with country United States (`US`), which the person may change or empty. |
| Vendors | Picked from a list; a missing one is added inline. Names are unique, case-insensitively. |
| Web addresses | A purchase's `source_url`, and a seller's `store_url`, must start with `http://` or `https://` (422 otherwise), the rule Receiving applies when showing them. |
| Who sold it? | The vendor is often the marketplace (ebay.com, whatnot.com); the seller on it is a row of its own (`seller`: a unique name and an optional store link) that the purchase names by `seller_id` -- one seller per purchase, since a marketplace order comes from one, and many purchases per seller. |
| Repeated entry | **Save and add another** keeps exactly what the next piece of one purchase shares (`SHARED_ON_REPEAT` in `NewItemForm.jsx`, listed under *New item* below) and clears the rest. Grade, grade designation, serial number, certificate, variety, cost, shipping and piece count are per piece and always clear, even when they often repeat. |

**Tax fields are three-state.** The tax-rate box starts empty, meaning the
configured rate (sent as `tax_rate: null`); "No sales tax charged" sends 0 and
disables the box; an explicit rate is validated as 0-1 with up to 4 places (a
leading-dot `.0635` accepted), and while it is invalid the item form refuses to
save, with a visible reason. "Tax on shipping" is "As configured" (`null`),
"Taxed" (`true`) or "Not taxed" (`false`): a checkbox cannot tell "not taxed"
from "use the default", and the default is `true`.

## API

All endpoints are manager-only (401 signed out, 403 for a customer).
Request bodies forbid unknown fields (422). Money crosses as decimal strings.

### Vendors (`routers/acquisitions.py`)

- `GET /api/vendors` -- ordered by name: `id`, `name`, `url`, `vendor_kind`.
- `POST /api/vendors` -- `name` (1-255, trimmed), `url` (http(s) only, or
  null), `vendor_kind` (code; null -> `unknown`). 201 with the vendor; 409 on a
  duplicate name; 422 for an unknown kind or non-http url.

### Storage locations

- `GET /api/storage-locations` -- every location: `id`, `label` (institution and
  box, else the kind), `kind`.
- `POST /api/storage-locations` -- `kind` (a `storage_location_kind` code),
  `institution` and `identifier` (trimmed; blank -> null), `notes`. 201 with
  the location; 409 for one that exists (same kind, institution and
  identifier, case aside); 422 for an unknown kind, or `consigned` / `sold`,
  which the auction and sale code make.
- Every location picker -- New item, the item editor, Receiving -- is
  `LocationSelect`: the locations, "--" for not recorded, and "+ Add a
  location..." opening an inline kind / bank or place / box form.

### Sellers

- `GET /api/sellers` -- ordered by name, case aside: `id`, `name`, `store_url`.
- `POST /api/sellers` -- `name` (1-255, trimmed), `store_url` (http(s) only;
  blank or absent -> null). 201 with the seller; 409 on a name already taken,
  whatever its case; 422 for a store that is not a web address.
- `PATCH /api/sellers/{id}` -- `name` and/or `store_url`; only what is sent
  changes, blank clears the store. 409 for a name another seller has.

### Purchase orders

- `POST /api/purchase-orders` -- `vendor_id`, `order_number` (trimmed; "" ->
  null), `ordered_on` (not after tomorrow), `source_url` (http(s) only),
  `seller_id` (404 for an unknown one), `notes`. The detail names the seller:
  `seller_id`, `seller` (the name) and `seller_url` (their store). 201 with `PurchaseOrderDetailOut`. 404 for an unknown vendor; 409
  when that vendor already has that order number (a partial unique index, so
  several unnumbered purchases from one vendor are allowed).
- `GET /api/purchase-orders` and `GET /api/purchase-orders/{id}`, whose lines
  carry `source_title` and `item_kind` for the page's items table.

### Items: `POST /api/inventory` (`ItemCreate`)

| Field | Rule |
|---|---|
| `purchase_order_id` | required; 404 if unknown |
| `item_kind` | required code |
| `source_title` | required, 1-500 |
| `description` | default "" |
| `year_start`, `year_end` | a start with no end is a single year; end before start is a 422; refused for `currency` (a note's year is its `series_year`) |
| `piece_count` | default 1, >= 1 |
| `item_cost`, `shipping_cost` | default 0.00, >= 0, 2 places |
| `tax_rate`, `tax_includes_shipping` | null -> the configured default |
| `status` | `ordered` or `received` |
| `country`, `denomination`, `grade`, `strike_type`, `grade_designation`, `grading_service`, `metal`, `series`, `bullion_form`, `set_form` | codes; unknown -> 422 naming the field. `grade` may be compound (`MS65`), split into number and strike type. |
| `storage_form` | null -> `single` |
| `authenticity` | null -> `unverified` |
| `cert_number` | creates one `item_certification`, graded by `grading_service` |
| `sellers_item_id` | optional, <= 64; the seller's id for the listing it was bought from (eBay's item number); trimmed, blank -> null |
| `listing_url` | optional, <= 1000; the listing's web address; trimmed, blank -> null, otherwise http(s) only (422). Editable with `PATCH` too, and offered as a "Listing" link in the item editor only when it is a web address |
| `storage_location_id` | optional; where it is kept (422 for an unknown one). Set through `set_location`, so it is the item's first location-history row. `PATCH` moves it the same way (null: not recorded), without the for-sale acknowledgement -- no buyer sees where a piece is kept -- and a move is kept in the location history, not also as a field change |
| `mint`, `variety` | coin detail; refused for `currency` |
| `serial_number`, `series_year`, `series_letter` (<= 4), `seal_color`, `fed_district`, `note_type`, `signature_combination`, `face_plate_number`, `back_plate_number`, `printing_facility` | currency detail; refused for any other kind. A face plate is a check letter and digits (`E82`), digits alone, or either with `FW` before it; a back plate is digits; `printing_facility` is `dc` or `fw`, read from the face plate when one is sent |
| `suggested` | field names whose value the form filled from the facts and the person left alone; recorded as derived defaults |
| `attributes` | codes (Binary, Star, CAC), set as an edit sets them (`app.item_attributes`); one unknown or of the other kind is a 422 naming it, and nothing is created |

A detail field for the other kind is a 422 naming the field: a silently
dropped serial number is data loss. A denomination whose `kind` contradicts
the item kind is a 422.

In one transaction: resolve every code, build the item, add exactly one detail
row (`CurrencyDetail` for currency, otherwise `CoinDetail`), add the
certification, record the `suggested` fields in `item_field_source`, write the
opening status row ("entered in the console"), bring the item's classifier
defaults up to date (`classifier-defaults-design.md`), commit. The response is
the same body `GET /api/inventory/{id}` returns, so the console can show the
new `item_code`.

## Console

Route `/purchases` (`/purchases/new` also opens it), nav link **Purchases**
(`management/pages/NewPurchase.jsx`), in two steps on one page:

1. **The purchase.** Either *Add to an existing purchase* -- a table of order
   number, date, vendor and purchase number (`#3974`, the purchase's own id,
   which the reports and Receiving's heading show too), filterable by part of
   an order number or vendor, or by an exact purchase number, and sorted by
   any column from a button in its header (date, newest first, until another
   is chosen; an undated purchase always last; ties newest first). A row or its order-number
   button picks the purchase -- or a new one:
   vendor (with "+ Add a vendor..." opening an inline name / kind / web address
   form that never submits the outer form), order number, order date, web
   address, seller (with "+ Add a seller..." opening an inline name / store
   form, as the vendor picker does), notes, **Create purchase**. **Edit
   details** changes the same fields; the heading links "Vendor page" and
   names the seller, linked to their store, as Receiving's does. A refusal is shown in place with the
   input kept.
2. **Items on this purchase.** The purchase heading, the purchase-wide tax
   values, a table of items entered so far (code, title, kind, cost, status) whose
   item code opens the item editor over the page, to fix an entry where it
   was made (the purchase is read again when the editor closes),
   the New item form, a **Receive these** link to `/receiving?order=<id>`, and
   **Start another purchase**.

**New item** (`management/pages/entry/NewItemForm.jsx`):

- **The listing and its price first**, known before the piece is in hand:
  listing web address, seller's item id, item cost, shipping. The listing's
  address suggests the seller's item id when it carries one -- eBay's
  `/itm/<id>`, a HiBid, LiveAuctioneers or Proxibid lot (`management/listing.js`);
  an order page carries none. The id is marked *suggested* until the person
  changes it, taken back if the address stops carrying one, and never
  replaces an id the person typed.
- **Then the facts** (`identify-first-entry-design.md`). Kind, then what
  identifies the piece: a note's series year, series letter, denomination,
  serial number, face plate, back plate and printing location ("Printed
  at"); anything else's year (with "Range of years"), mint and denomination.
- Then what those facts decide, marked *suggested* while it is the form's:
  series, and a note's class, seal, signatures and Reserve Bank or anything
  else's metal. Then country; strike type (not for a note), grade, grade
  designation, grading service, certificate number, set form and variety
  (not for a note), attributes (the editor's `AttributesField`, offering
  the kind's own; changing kind across note and coin drops them).
- Then the rest of the purchase line: title, piece count.
- Last, in this order: errors, then the description with **Suggest
  description** -- which writes it from what is entered, errors included, so
  they come first -- then the storage location (optional) and status
  (ordered / received). The item editor offers the storage location too.
- Pickers are `ReferenceSelect`, filtered to the item's kind
  (`vocabulary-and-errors-design.md`). The grade picker offers the note scale
  for currency and the coin scales otherwise; changing kind across that
  boundary clears a picked grade, a change within one side keeps it.
- **Suggestions.** As facts are entered the form asks `GET /api/defaults/note`
  or `/coin` and fills what the facts decide -- the design series included --
  marked *suggested* (`classifier-defaults-design.md`). The series is never
  sent as a fact.
- **Errors** are recorded with `ErrorsPanel`, saved after the item is created,
  with a Retry if that second step fails.
- Money is validated with `isMoney` before sending.
- **Save** clears the whole form. **Save and add another** keeps
  `SHARED_ON_REPEAT` -- kind, seller's item id, listing web address,
  status, storage location (a parcel is put away in one place), country,
  denomination, series, series year and letter, seal, district, note class,
  signatures, grading service, metal, mint -- clears everything that varies
  piece to piece (attributes, title,
  description, years, grade, designation, serial, certificate, variety, cost,
  shipping, piece count back to 1) and focuses the first identifying field
  it cleared: a note's serial number, anything else's year.
- Keyboard accelerators via `accel` / `AccessLabel` (Alt+letter, avoiding D,
  E and F, which the browser claims) and `useSaveShortcut` (Ctrl+S /
  Ctrl+Enter saves; the Save button, a `SaveButton`, shows Ctrl+S and has no
  Alt letter).

## Listing links

An item names the listing it was bought from twice: its web address
(`listing_url`) and the seller's own id for it (`sellers_item_id`). Each can
recover the other, and at most vendors a purchase's web address is its lot's.

| Rule | Where it applies |
|---|---|
| The id is read from the address where the site puts it: eBay `/itm/<id>`, a HiBid, LiveAuctioneers or Proxibid lot. An order page (Whatnot's `/order/`, order.ebay.com) carries none. | New item suggests it as the address is typed (`management/listing.js`); the pass fills it (`app/listing_links.py`). |
| An eBay id rebuilds its address, `https://www.ebay.com/itm/<id>`. | The pass. |
| At any vendor but eBay and Whatnot -- whose orders hold many listings -- the purchase's web address is its lot's page. | New item starts with it as the listing, suggested (with the id it carries); a purchase with no web address takes the first item's listing when the item is entered (`create_item`); the pass fills an item with no address from its purchase only when that address carries a lot id (a shop's location page is no listing), and a purchase with none from the one address its items share. |
| A purchase whose items name several lots gives no address to its items and takes none from them. | The pass. |

Nothing already recorded is replaced. The pass reports by default;
`python -m app.listing_links --commit --by EMAIL` fills the gaps and logs
each item's change in its History under that person.

## Field help

Whichever field has focus is explained in the console's **help band**, fixed
at the bottom of the window. The console is laid out as a column the height
of the window -- menu, page, band -- and only the page scrolls, so a form
always fits between the menu and the band and the explanation never scrolls
out of sight. `management/HelpBar.jsx` holds the band (`HelpProvider`, `HelpBar`)
and clears it on moving to another page; `management/HelpScope.jsx` wraps a form
and publishes to the band (drawing an area of its own only outside the
console shell, as in a component test). A field opts in with
`data-help="<key>"` on its label -- or on the element wrapping a radio group
or a box named through `htmlFor` -- and `management/fieldHelp.js` holds the text,
keyed by the field's API name, so every form explains a field the same way.
Nothing is placed inside a label: a control in a label with no `for` takes
the label from its field. The last field explained stays shown when focus
moves to one with none; scopes nest, and the innermost one with help for the
focused field shows it. The same mechanism serves the item editor,
Receiving's search form and the Friedberg lookup. A test scans the console
source and fails on any `data-help` key with no text.

Help text is public numismatic fact only -- what is printed where on a note
and what it means -- never a catalog's numbering or a price guide's values.
