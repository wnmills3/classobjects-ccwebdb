# Orders entered and edited on a customer's behalf

The management console is a superset of the shop: anything a customer can do, a
manager can do from `/management`. For orders that means placing an order
for someone else -- a phone, walk-in or in-person sale of a web-store item --
changing an order after it is placed, and moving it through its statuses to
shipped or cancelled. All of it happens on the **Sales** page
(`/management/sales`), which lists every order: shop checkouts, orders
entered on a customer's behalf, and sales recorded from other platforms
(`selling-design.md`). Because a manager can act for a buyer and re-price a
paid order, every order records who placed it and every change to it.

## Rules

| Question | Rule |
|---|---|
| Who an order can be for | **Any customer record**, account holder or not. Choosing an account with no customer record creates one. |
| When contents may change | **While `pending` or `paid`.** Not from `packed` on, and not when `cancelled` or `refunded`. |
| Line prices | **The listing's price by default; a manager may override it** (not below zero). Existing lines keep the price they were bought at unless changed. |
| Accountability | **Who placed the order, plus a history row for every change.** |
| Shape | **One order-writing module (`app/order_writes.py`) behind separate manager endpoints.** The shop's `POST /api/orders` keeps its contract. |

Manager-only fields on the shop's endpoints are not used: one role check
would stand between a shopper and setting their own price. Line-by-line
endpoints are not used either: "swap this coin for that one" would be two
calls that can half-succeed.

A web-store item sold in person is entered here, not through the Listings
page's Record sale, which refuses store listings (`selling-design.md`).
Lines come from web-store listings only: the Add item search reads the shop
catalog, and a new or grown line must be an active store listing.

## Data model

**`sales_order`** carries:

| Column | Meaning |
|---|---|
| `placed_by_id` | FK `users`, `ON DELETE SET NULL`, nullable. The account that entered the order: the buyer, or the manager acting for them. |
| `version` | `version_id_col`. Two managers editing one order cannot overwrite each other silently. |

**`sales_order_change`**, one row per individual change:

| Column | Meaning |
|---|---|
| `sales_order_id` | FK `sales_order`, `ON DELETE CASCADE`, indexed |
| `changed_at`, `changed_by_id` | when, and which account (`ON DELETE SET NULL`) |
| `change` | enum `sales_order_change_kind`: `placed`, `line_added`, `line_removed`, `quantity`, `unit_price`, `customer`, `notes`, `status`, `total` |
| `listing_id` | FK `listing`, `ON DELETE RESTRICT`, nullable; set for line-level changes |
| `from_value`, `to_value` | text: `2` → `3`, `189.00` → `150.00`, a customer's display name, a status code |

One save writes several rows sharing `changed_at` and `changed_by_id`;
grouping on those reconstructs an edit. Values are text because they are of
mixed kinds; `change` is a closed vocabulary so the history stays
filterable. Money is stored as a plain decimal string and formatted only for
display.

## `order_writes`

The one place order stock moves. Functions run inside the caller's
transaction and leave committing to it. Every refusal goes through
`_refuse`, which rolls the transaction back before raising.

**`place_order(db, customer, lines, placed_by, notes=None, *, venue=None,
status_code="pending", external_order_id=None)`.** `lines` are
`Line(listing_id, quantity, unit_price or None)`.

1. Take the rows through `_lock_listings`, which goes through
   `offering_writes.lock_for_sale`: lot rows, then items, then listings
   (`lock-order-design.md`). Unknown listing: 404.
2. With no `venue` (a shop or on-behalf order): the listing must be the
   store's and `active`, else 409. Always: quantity above
   `quantity_available` is 409 naming the listing and what remains.
3. Reduce `quantity_available`; a listing reaching 0 marks its items `sold`.
   On a shop or on-behalf order, a lot listing bought outright is ended as
   sold with its lot (an item listing stays active at zero stock).
4. A line's `unit_price` is the given price, or the listing's.
5. Write one `sales_order_item_share` per item the line carries (one for an
   item listing, one per member for a lot), total the order, set
   `placed_by_id`, and write a `placed` change row naming the placing
   account.

`venue`, `status_code` and `external_order_id` are for sales recorded from
another platform (`sales_writes`); an on-behalf order leaves them unset.

**`revise_order(db, order, *, customer, lines, notes, version, by) -> bool`.**
`lines` is the order's complete desired contents.

1. Lock and re-read the `sales_order` row first. Status not `pending` or
   `paid`: 409. `version` differs: 409, "changed by someone else… reload".
2. Take the listings of the current and desired lines together through
   `_lock_listings`.
3. Per listing, `delta = desired - current`. `delta > 0` needs an active
   store listing with that much available, else 409. Shrinking or removing a
   line whose listing has **ended** is refused: its stock has nowhere to go
   back to. Every check passes before anything changes, so a refused save
   changes no line and no stock.
4. Move stock by the delta: reaching 0 marks the item `sold`, rising from 0
   on an active listing marks it `listed`.
5. Add, remove, re-quantity and re-price lines; replace `customer_id` and
   `notes`; recompute `total_amount` and the shares.
6. One change row per difference. No difference: no rows, no version bump,
   returns `False`.

**Status changes** (`PATCH /api/orders/{id}`, any `sales_order_status`
code: `pending`, `paid`, `packed`, `shipped`, `delivered`, `cancelled`,
`refunded`) lock the order row, write a `status` change row and bump
`version`.

- Cancelling an order that has not been packed returns its stock
  (`return_stock`, through the same lock path). Cancelling one whose listing
  has ended -- an outside sale, or a lot bought in the shop -- is refused
  while it is unpacked, because there is nothing to return the stock to.
- Cancelling a `packed`, `shipped` or `delivered` order moves no stock; it is
  how a refund is recorded, whatever the listing's state.
- A cancelled order cannot be moved to another status: its stock is back on
  sale. Re-sending `cancelled` is harmless.

## Endpoints

| Endpoint | Access | Behavior |
|---|---|---|
| `POST /api/orders` | signed in | Shop checkout: `place_order` for the caller's own customer record, listing prices only. |
| `GET /api/orders`, `GET /api/orders/{id}` | signed in | A manager sees every order (`mine=true` narrows to their own); a shopper only their own, and another customer's order is a 404. `notes`, `placed_by_email` and line snapshots are manager-only. |
| `POST /api/customers/{id}/orders` | manager | Place an order for that customer: `items` of `{listing_id, quantity, unit_price?}`, optional `notes`. Unknown customer: 404. |
| `POST /api/users/{id}/customer` | manager | Find or create the customer record behind an account. |
| `PUT /api/orders/{id}` | manager | `revise_order`. Body: `version`, `customer_id`, `items` of `{listing_id, quantity, unit_price}`, `notes`. |
| `GET /api/orders/{id}/changes` | manager | Change rows, newest first, with the changing account's email and each line's listing title. |
| `PATCH /api/orders/{id}` | manager | Status, as above. |

The manager endpoints' request schemas forbid extra fields. 422 for a
quantity below 1, a unit price below 0, a listing twice in one order, or no
lines -- an order is emptied by cancelling it. Shoppers cannot edit an order.

`OrderOut` carries `version`, `notes`, `placed_by_email`, each line's
`listing_ended`, and `payment_adjustment_due`: true when the order is `paid`
and a `total` change row is later than its latest `status` change to
`paid`. Payments are not recorded, so this prompts a person; it never
charges or refunds.

## Console

All on the Sales page (`/management/sales`).

**Order editor** (`orders/OrderEditor.jsx`), a modal for **New order** and
for **Edit** on a `pending` or `paid` order:

- **Customer**: a searchable picker over customers and accounts without a
  customer record ("account, no orders yet"); choosing an account calls
  `POST /api/users/{id}/customer`.
- **Lines**: title, quantity, unit price, available stock, remove. An
  overridden price is marked "listing price $189.00".
- **Add item**: searches the shop catalog (`listCatalog` with `q` and
  `in_stock`).
- **Notes**, and a running **total** computed in integer cents
  (`orders/cents.js`), never floating point; the server's total is the real
  one.
- A `paid` order shows "This order is paid. Changing its total will flag a
  payment adjustment."
- Saving sends the loaded `version`. A refusal is shown in the dialog with
  the edits kept.

**Order list**, newest first: customer, lines, total with a "payment
adjustment due" badge, "entered by <email>" when the placing account's email
is not the customer's, **Edit** on `pending` and `paid` orders, and
**History** (`orders/OrderHistory.jsx`, changes grouped by edit). Each row's
status is a select. On an unpacked order `cancelled` is disabled, with the
reason as its tooltip, exactly where the server would refuse it: an outside
sale, or a store order with a line whose listing has ended
(`listing_ended`). A cancelled order's select is disabled.

## Tests

- Stock: raising, lowering and removing lines move stock by the delta;
  swapping listings in one save; `listed`/`sold` as edits cross zero; an
  over-request refused with nothing changed.
- Concurrency (`tests/test_order_revision_race.py`): a manager's edit and a
  checkout contending for a last unit on real threads -- one succeeds, stock
  never negative; two edits at one version, the second 409; a cancel and an
  edit of one order neither deadlock nor corrupt stock; a concurrent edit to
  an item does not refuse a revision or a cancellation.
- Rules: editable statuses, version, price and quantity bounds, duplicate
  listings, at least one line, unknown customer and listing.
- Access: each manager endpoint is 401 signed out and 403 for a shopper,
  writing nothing.
- History: one row per difference; none for a no-op; `placed` on both paths;
  `status` rows; `payment_adjustment_due` only after a paid total changes.
- Console: editor prefill, payloads, exact-cents total, refusal keeps edits,
  paid banner, badge, "entered by", History.

## Not built

- Finding an item to add by inventory code (the catalog search covers
  the listing title, the item's source title and its description).
- Shipping or billing addresses on an order. `sales_order` has
  `shipping_address_id` and `billing_address_id`, and a customer's addresses
  can be recorded (`POST /api/customers/{id}/addresses`), but nothing sets
  an order's.
