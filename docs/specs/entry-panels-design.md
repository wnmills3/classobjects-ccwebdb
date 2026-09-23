# Entry panels: New purchase and New item

The database is the system of record, so acquisitions are entered in the
console: **New purchase** records a vendor and a purchase order, and **New
item** records a coin, banknote or lot bought on it. Splitting a lot,
attributing its pieces, editing a vendor and editing a purchase order happen
elsewhere; a Friedberg number is attached afterwards from Receiving or the item
editor.

## Rules

| Question | Rule |
|---|---|
| Can an item exist without a purchase? | **No.** A standalone buy is a purchase holding one item. The order number is optional, so a walk-in or show purchase needs only a vendor. |
| Where do shipping and tax live? | **On each item** (`shipping_cost`, `tax_rate`, `tax_includes_shipping`). The page's purchase-wide values pre-fill each item. |
| A lot? | An item with `piece_count > 1`. Splitting is a later step. |
| Status on entry | `ordered` (default) or `received` for things already in hand. The opening status-history row is written either way. |
| Other defaults | disposition `held`, authenticity `unverified` unless given, valuation basis `numismatic`, source `manual`. |
| Vendors | Picked from a list; a missing one is added inline. Names are unique, case-insensitively. |
| Web addresses | A purchase's `source_url` must start with `http://` or `https://` (422 otherwise), the rule Receiving applies when showing it. |

**Tax fields are three-state.** The tax-rate box starts empty, meaning the
configured rate (sent as `tax_rate: null`); "No sales tax charged" sends 0 and
disables the box; an explicit rate is validated as 0-1 with up to 4 places (a
leading-dot `.0635` accepted), and while it is invalid the item form refuses to
save, with a visible reason. "Tax on shipping" is "As configured" (`null`),
"Taxed" (`true`) or "Not taxed" (`false`): a checkbox cannot tell "not taxed"
from "use the default", and the default is `true`.

## API

All endpoints are administrator-only (401 signed out, 403 for a customer).
Request bodies forbid unknown fields (422). Money crosses as decimal strings.

### Vendors (`routers/acquisitions.py`)

- `GET /api/vendors` -- ordered by name: `id`, `name`, `url`, `vendor_kind`.
- `POST /api/vendors` -- `name` (1-255, trimmed), `url` (http(s) only, or
  null), `vendor_kind` (code; null -> `unknown`). 201 with the vendor; 409 on a
  duplicate name; 422 for an unknown kind or non-http url.

### Purchase orders

- `POST /api/purchase-orders` -- `vendor_id`, `order_number` (trimmed; "" ->
  null), `ordered_on` (not after tomorrow), `source_url` (http(s) only),
  `notes`. 201 with `PurchaseOrderDetailOut`. 404 for an unknown vendor; 409
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
| `year_start`, `year_end` | a start with no end is a single year; end before start is a 422 |
| `piece_count` | default 1, >= 1 |
| `item_cost`, `shipping_cost` | default 0.00, >= 0, 2 places |
| `tax_rate`, `tax_includes_shipping` | null -> the configured default |
| `status` | `ordered` or `received` |
| `country`, `denomination`, `grade`, `strike_type`, `grade_designation`, `grading_service`, `metal`, `series`, `bullion_form` | codes; unknown -> 422 naming the field. `grade` may be compound (`MS65`), split into number and strike type. |
| `storage_form` | null -> `single` |
| `authenticity` | null -> `unverified` |
| `cert_number` | creates one `item_certification`, graded by `grading_service` |
| `mint`, `variety` | coin detail; refused for `currency` |
| `serial_number`, `series_year`, `series_letter` (<= 4), `seal_color`, `fed_district`, `note_type`, `signature_combination` | currency detail; refused for any other kind |
| `suggested` | field names whose value the form filled from the facts and the person left alone; recorded as derived defaults |

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

Route `/purchases/new`, nav link **New purchase**
(`owner/pages/NewPurchase.jsx`), in two steps on one page:

1. **The purchase.** Either *Add to an existing purchase* -- a list filterable
   by order number or vendor, as keyboard-reachable buttons -- or a new one:
   vendor (with "+ Add a vendor..." opening an inline name / kind / web address
   form that never submits the outer form), order number, order date, web
   address, notes, **Create purchase**. A refusal is shown in place with the
   input kept.
2. **Items on this purchase.** The purchase heading, the purchase-wide tax
   values, a table of items entered so far (code, title, kind, cost, status),
   the New item form, a **Receive these** link to `/receiving?order=<id>`, and
   **Start another purchase**.

**New item** (`owner/pages/entry/NewItemForm.jsx`):

- Kind, title, description, year (with "Range of years"), piece count, cost,
  shipping, status (ordered / received), country, denomination, strike type
  (not for a note), grade, grade designation, grading service, certificate
  number, metal (not for a note), series.
- Coin block (not currency): mint, variety. Banknote block (currency): serial
  number, series year and letter, note class, seal, signatures, Reserve Bank.
- Pickers are `ReferenceSelect`, filtered to the item's kind
  (`vocabulary-and-errors-design.md`). The grade picker offers the note scale
  for currency and the coin scales otherwise; changing kind across that
  boundary clears a picked grade, a change within one side keeps it.
- **Suggestions.** As facts are entered the form asks `GET /api/defaults/note`
  or `/coin` and fills what the facts decide, marked *suggested*
  (`classifier-defaults-design.md`).
- **Errors** are recorded with `ErrorsPanel`, saved after the item is created,
  with a Retry if that second step fails.
- Money is validated with `isMoney` before sending.
- **Save** clears the whole form. **Save and add another** keeps
  `SHARED_ON_REPEAT` -- kind, status, country, denomination, series, series
  year and letter, seal, district, note class, signatures, grading service,
  metal, mint -- clears everything that varies piece to piece (title,
  description, years, grade, designation, serial, certificate, variety, cost,
  shipping, piece count back to 1) and focuses the title.
- Keyboard accelerators via `accel` / `AccessLabel` (Alt+letter, avoiding D,
  E and F, which the browser claims) and `useSaveShortcut` (Ctrl+S /
  Ctrl+Enter saves).

## Field help

Whichever field has focus is explained in the console's **help band**, fixed
at the bottom of the window. The console is laid out as a column the height
of the window -- menu, page, band -- and only the page scrolls, so a form
always fits between the menu and the band and the explanation never scrolls
out of sight. `owner/HelpBar.jsx` holds the band (`HelpProvider`, `HelpBar`)
and clears it on moving to another page; `owner/HelpScope.jsx` wraps a form
and publishes to the band (drawing an area of its own only outside the
console shell, as in a component test). A field opts in with
`data-help="<key>"` on its label -- or on the element wrapping a radio group
or a box named through `htmlFor` -- and `owner/fieldHelp.js` holds the text,
keyed by the field's API name, so every form explains a field the same way.
Nothing is placed inside a label: a control in a label with no `for` takes
the label from its field. The last field explained stays shown when focus
moves to one with none; scopes nest, and the innermost one with help for the
focused field shows it. The same mechanism serves the item editor,
Receiving's search form and the Friedberg lookup. A test scans the console
source and fails on any `data-help` key with no text.

Help text is public numismatic fact only -- what is printed where on a note
and what it means -- never a catalogue's numbering or a price guide's values.
