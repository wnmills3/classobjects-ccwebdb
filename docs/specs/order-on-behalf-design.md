# Orders entered and edited on a customer's behalf

Design. Status: approved in conversation, not yet implemented (2026-09-15).

## The problem

The owner console is to be a superset of the shop: anything a customer can do,
an administrator can do from `/owner`. Two order capabilities are missing.

- **Placing an order for someone else.** `POST /api/orders` always records the
  order against the caller's own customer record, so an administrator taking a
  phone or walk-in order can only order for themselves.
- **Changing an order after it is placed.** Nothing can add, remove,
  re-quantity or re-price a line, or move an order to another customer. Only
  the status can change.

Nothing records who placed or changed an order. That was harmless while the
buyer was always the person signed in; it stops being harmless once an
administrator acts for them and can re-price a paid order.

## Decisions

| Question | Decision |
|---|---|
| Who an order can be for | **Any customer record**: account holders and buyers without an account. An account with no customer record gets one when chosen. |
| When contents may change | **While `pending` or `paid`.** Locked from `packed` on, and when `cancelled` or `refunded`. |
| Line prices | **The listing's price by default; an administrator may override** it (not below zero). Existing lines keep the price they were bought at unless changed. |
| Accountability | **Who placed the order, plus a history row for every change.** |
| Shape | **A shared order-writing module with separate admin endpoints.** The shop's `POST /api/orders` keeps its contract exactly. |

Rejected: admin-only fields on the existing endpoints, which leaves one role
check between a shopper and setting their own price; and line-by-line
endpoints, which turn "swap this coin for that one" into two calls that can
half-succeed.

## Data model

One migration.

**`sales_order` gains:**

| Column | Meaning |
|---|---|
| `placed_by_id` | FK `users`, `ON DELETE SET NULL`, nullable. The account that entered the order: the buyer, or the administrator acting for them. Null for orders placed before this change (the live database has none). |
| `version` | Integer, `version_id_col`, as `inventory_item` and `listing` use. Two administrators editing one order cannot overwrite each other silently. |

**New table `sales_order_change`**, one row per individual change, following
`item_status_history`:

| Column | Meaning |
|---|---|
| `id` | |
| `sales_order_id` | FK `sales_order`, `ON DELETE CASCADE`, indexed |
| `changed_at` | timestamptz, not null |
| `changed_by_id` | FK `users`, `ON DELETE SET NULL`, nullable |
| `change` | Enum `sales_order_change_kind`: `placed`, `line_added`, `line_removed`, `quantity`, `unit_price`, `customer`, `notes`, `status`, `total` |
| `listing_id` | FK `listing`, `ON DELETE RESTRICT`, nullable; set for line-level changes |
| `from_value`, `to_value` | Text, nullable. `2` -> `3`; `189.00` -> `150.00`; a customer's display name; a status code |

One save writes several rows sharing `changed_at` and `changed_by_id`; grouping
on those reconstructs an edit. `from_value`/`to_value` are text because the
values are of mixed kinds; `change` is a closed vocabulary, so the history stays
filterable. Money is written as a plain decimal string (`150.00`), formatted
only for display.

## Server

### `app/order_writes.py`

The one place order stock moves. Both functions run inside the caller's
transaction and leave committing to it.

**`place_order(db, customer, lines, placed_by) -> SalesOrder`.** `lines` is a
list of (listing id, quantity, unit price or None). Today's checkout, moved:

1. Lock every named listing `SELECT ... FOR UPDATE`, ordered by id.
2. Unknown listing: 404. Inactive listing, or quantity above
   `quantity_available`: 409 naming the listing and what remains.
3. Reduce `quantity_available`; a listing reaching 0 marks its item's
   disposition `sold`.
4. A line's `unit_price` is the given price, or the listing's current price.
5. Total the order, set `placed_by_id`, write a `placed` change row whose
   `to_value` records the placing account's email.

**`revise_order(db, order, *, customer, lines, notes, version, by) ->
SalesOrder`.** `lines` is the order's complete desired contents.

1. Status not `pending` or `paid`: 409.
2. `version` differs from the order's: 409, "changed by someone else; reload".
3. Lock the listings of the current and desired lines together, ordered by id
   -- the same deadlock-safe order as checkout.
4. Per listing, `delta = desired quantity - current quantity`:
   - `delta > 0` requires an active listing with at least `delta` available,
     else 409 naming it and what remains. Every check passes before anything is
     changed, so a refused save changes no line.
   - `quantity_available -= delta`. Reaching 0 marks the item `sold`; rising
     from 0 on an active listing marks it `listed`.
5. Add, remove, re-quantity and re-price lines to match; replace `customer_id`
   and `notes`; recompute `total_amount`.
6. Write one change row per difference: `line_added` (to: quantity @ price),
   `line_removed` (from: quantity @ price), `quantity`, `unit_price`,
   `customer`, `notes`, `total`. No difference, no rows, no version bump.

### Endpoints

| Endpoint | Access | Behaviour |
|---|---|---|
| `POST /api/orders` | signed in | Contract unchanged. Calls `place_order` for the caller's customer record, listing prices only. |
| `POST /api/customers/{id}/orders` | admin | Create for that customer: `items` of `{listing_id, quantity, unit_price?}`, optional `notes`. Unknown customer: 404. |
| `POST /api/users/{id}/customer` | admin | Find or create the customer record behind an account; returns it. |
| `PUT /api/orders/{id}` | admin | `revise_order`. Body: `version`, `customer_id`, `items` of `{listing_id, quantity, unit_price}`, `notes`. |
| `GET /api/orders/{id}/changes` | admin | Change rows, newest first, with the changing account's email and each line's listing title. |
| `PATCH /api/orders/{id}` | admin | Status, as now; additionally writes a `status` change row and bumps `version`. The cancelled-order lock is unchanged. |

Request schemas forbid extra fields. Validation (422): quantity at least 1,
unit price at least 0, a listing at most once per order, at least one line --
an order is emptied by cancelling it.

`OrderOut` additionally carries `version`, `notes`, `placed_by_email`, and
`payment_adjustment_due`: true when the order is `paid` and a `total` change row
is later than its most recent `status` row to `paid`. Payments are not
recorded, so this is a prompt for a person, never an automatic charge or refund.

Shoppers still cannot edit an order; every new endpoint is administrator-only.

## Console

All on the Orders page.

**Order editor**, a modal `<dialog>` like the item editor, for **New order**
and for **Edit** on a `pending` or `paid` row:

- **Customer**: a searchable picker over customers (name, email) and accounts
  without a customer record, marked "account, no orders yet". Choosing an
  account calls `POST /api/users/{id}/customer`.
- **Lines**: title, quantity, unit price, available stock, remove. The price
  shows the listing's price until changed; an overridden price is marked
  "listing price $189.00".
- **Add item**: searches `listCatalog` with `q` and `in_stock`, listing title,
  price and stock.
- **Notes**.
- **Total**: a running display total, computed in integer cents, never
  floating point. The server's total is the real one.
- A `paid` order shows: "This order is paid. Changing its total will flag a
  payment adjustment."
- Saving sends the loaded `version`. A refusal is shown inside the dialog with
  the edits kept; success closes it and refreshes the list.

**Order list** gains a "payment adjustment due" badge, "entered by
<email>" when the placing account is not the customer's own, and a
**History** link showing changes grouped by edit.

Out of scope: finding an item by inventory code (the catalogue search covers
title and description), and shipping or billing addresses on an order.

## Testing

**Server**, test first:

- Stock: raising, lowering and removing lines move stock by the delta;
  swapping one listing for another in one save; items marked `listed` and
  `sold` as edits cross zero; an over-request refused with no line or stock
  changed.
- Concurrency: an admin edit and a checkout contending for a last unit from
  real threads -- exactly one succeeds, stock never negative. Two edits at the
  same version: the second is a 409.
- Rules: editable statuses only; version; price and quantity bounds; duplicate
  listings; at least one line; unknown customer and listing.
- Access: each new endpoint is 401 signed out and 403 for a shopper, writing
  nothing. The existing `POST /api/orders` tests pass unmodified.
- History: one row per difference with correct values; none for a no-op;
  `placed` for both paths; `status` rows; `payment_adjustment_due` only on a
  paid order whose total changed after it was paid.
- Migration: upgrade and downgrade; models match the migrated schema.

**Console**: editor prefill; payloads for add, remove, re-quantity and
re-price; exact cents total; refusal keeps edits; paid banner, adjustment badge,
"entered by", History.

**Mutation checks**, with files backed up to disk first: remove the stock
delta, editable-status check, version check, admin guard, history writes,
payment flag and cents arithmetic in turn; each must fail a test.

**Live check** against a copy of the database on a second backend port, since
orders are never deleted and the working database has none.

`scripts\ccweb_check.cmd` passes before every commit.
