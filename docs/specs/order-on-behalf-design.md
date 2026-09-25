# Orders entered and edited on a customer's behalf

The management console is a superset of the shop: anything a customer can do, an
administrator can do from `/management`. For orders that means placing an order
for someone else -- a phone, walk-in or in-person sale -- and changing an
order after it is placed. Because an administrator can act for a buyer and
re-price a paid order, every order records who placed it and every change
to it.

## Rules

| Question | Rule |
|---|---|
| Who an order can be for | **Any customer record**, account holder or not. Choosing an account with no customer record creates one. |
| When contents may change | **While `pending` or `paid`.** Not from `packed` on, and not when `cancelled` or `refunded`. |
| Line prices | **The listing's price by default; an administrator may override it** (not below zero). Existing lines keep the price they were bought at unless changed. |
| Accountability | **Who placed the order, plus a history row for every change.** |
| Shape | **One order-writing module (`app/order_writes.py`) behind separate admin endpoints.** The shop's `POST /api/orders` keeps its contract. |

Admin-only fields on the shop's endpoints were rejected: one role check
would stand between a shopper and setting their own price. Line-by-line
endpoints were rejected: "swap this coin for that one" would be two calls
that can half-succeed.

A web-store item sold in person is entered here, not through the Listings
page's Record sale, which refuses store listings (`selling-design.md`).

## Data model

**`sales_order`** carries:

| Column | Meaning |
|---|---|
| `placed_by_id` | FK `users`, `ON DELETE SET NULL`, nullable. The account that entered the order: the buyer, or the administrator acting for them. |
| `version` | `version_id_col`. Two administrators editing one order cannot overwrite each other silently. |

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
3. Reduce `quantity_available`; a listing reaching 0 marks its item `sold`,
   and a lot listing bought outright ends as sold with its lot.
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

**Status changes** (`PATCH /api/orders/{id}`) lock the order row, write a
`status` change row and bump `version`. Cancelling an unshipped order
returns its stock (`return_stock`, through the same lock path); cancelling
one whose listing has ended -- an outside sale, or a lot bought in the
shop -- is refused, because there is nothing to return the stock to. A
cancelled order cannot be moved to another status.

## Endpoints

| Endpoint | Access | Behavior |
|---|---|---|
| `POST /api/orders` | signed in | Shop checkout: `place_order` for the caller's own customer record, listing prices only. |
| `POST /api/customers/{id}/orders` | admin | Place an order for that customer: `items` of `{listing_id, quantity, unit_price?}`, optional `notes`. Unknown customer: 404. |
| `POST /api/users/{id}/customer` | admin | Find or create the customer record behind an account. |
| `PUT /api/orders/{id}` | admin | `revise_order`. Body: `version`, `customer_id`, `items` of `{listing_id, quantity, unit_price}`, `notes`. |
| `GET /api/orders/{id}/changes` | admin | Change rows, newest first, with the changing account's email and each line's listing title. |
| `PATCH /api/orders/{id}` | admin | Status, as above. |

Request schemas forbid extra fields. 422 for a quantity below 1, a unit price
below 0, a listing twice in one order, or no lines -- an order is emptied by
cancelling it. Shoppers cannot edit an order.

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

**Order list**: a "payment adjustment due" badge, "entered by <email>" when
the placing account is not the customer's own, **History**
(`orders/OrderHistory.jsx`, changes grouped by edit), and Cancel disabled
exactly where the server would refuse it (`listing_ended`).

## Tests

- Stock: raising, lowering and removing lines move stock by the delta;
  swapping listings in one save; `listed`/`sold` as edits cross zero; an
  over-request refused with nothing changed.
- Concurrency (`tests/test_order_revision_race.py`): an admin edit and a
  checkout contending for a last unit on real threads -- one succeeds, stock
  never negative; two edits at one version, the second 409; a concurrent
  edit to an item does not refuse a revision or a cancellation.
- Rules: editable statuses, version, price and quantity bounds, duplicate
  listings, at least one line, unknown customer and listing.
- Access: each admin endpoint is 401 signed out and 403 for a shopper,
  writing nothing.
- History: one row per difference; none for a no-op; `placed` on both paths;
  `status` rows; `payment_adjustment_due` only after a paid total changes.
- Console: editor prefill, payloads, exact-cents total, refusal keeps edits,
  paid banner, badge, "entered by", History.

## Not built

- Finding an item to add by inventory code (the catalog search covers
  title and description).
- Shipping or billing addresses on an order.
