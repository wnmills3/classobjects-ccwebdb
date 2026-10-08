# Selling: platforms, offers, sales lots and auctions

The business sells through its own web store and through outside platforms:
marketplaces (eBay, Whatnot), live auction shows, and auction houses (Heritage,
HiBid) through an agent. Those venues run the bidding and the checkout; this
system never does. What it does is keep the owner's side of the ledger
straight:

- **where each coin is offered right now**, on which platform, at what price,
  with the guarantee that no coin is ever offered in two places at once or
  sold twice;
- **groups of coins sold as one thing** (sales lots), which fall back into
  their coins if they do not sell;
- **an auction's bookkeeping**: which lots are in it, consignment of coins to
  an auction house and their return, and settlement of results into orders;
- **what an outside sale actually brought in**: buyer, price, the platform's
  actual fees, and each coin's share of the money, which is what gain
  reporting is computed from.

The owner and managers work it in the management console: the **Platforms**
page (`/management/platforms`); **Offer for sale...** and **Group into
lot...** on the coin and currency pages; and the **Selling** menu group,
**Listings**, **Lots** and **Auctions**. The orders a sale produces appear on
the **Sales** page (`/management/sales`, `order-on-behalf-design.md`). The
public shop sees only web-store, fixed-price, active listings.

## Rules

| Question | Rule |
|---|---|
| "Resellers" | The **platforms we sell through** (web store, eBay, Whatnot, auction houses selling through an agent), not dealers who buy from us. |
| One item on several platforms at once | **Never.** At most one active offer per item, enforced by the database. |
| Outside sales | **Entered by hand**, from the platform's own record of the sale. |
| Buyers on outside platforms | **Customer records marked by platform** (`whatnot:coinfan88`); one "undisclosed buyer" per platform that does not name buyers. |
| Fees | **Defaults per platform for estimates; actual fees recorded per sale.** The actuals are what count for tax. |
| An auction lot that does not sell | Its items **go back into the store** if they were there before, at their old price; otherwise to `held`. |
| Consignment custody | **A storage location** ("Consigned: Heritage"), through the ordinary location history. |
| Where platforms live | **`sales_venue`, with an optional link to `vendor`**, so eBay is one partner whether buying or selling. |
| Grouping items for sale | A **sales lot**: a temporary group offered as one thing, which **dissolves back into its items** if it does not sell. |
| A web-store item sold in person | **An order on the customer's behalf**, from the Sales page. Record sale refuses store listings: it would be a second way to sell a shop item, past the cart and checkout. |

Alternatives rejected, for reasons that still hold:

- *Renaming `vendor` to a partner table with both roles* -- it renames a table
  used by purchases, receiving, search and tests for no gain.
- *A platform list with no link to `vendor`* -- eBay, Whatnot and HiBid
  would exist twice, spelled differently.
- *A parallel "offering" table beside `listing`* -- checkout, the public
  catalog, sale snapshots and the for-sale warning all read `listing`;
  widening it keeps them working.
- *A sales lot as an inventory item* -- it would be counted in inventory and
  cost basis beside its own members.
- *Cross-listing with manual delisting* -- the first sale wins and the second
  platform sells it again.

## Three kinds of lot

| Term | Direction | Lifetime | Relationship |
|---|---|---|---|
| **Purchase lot** | how it came in | permanent; decomposed by splitting | `inventory_item.parent_item_id` |
| **Sales lot** | how it goes out | while offered; then sold or dissolved | `sales_lot_item` |
| **Auction lot** | a sales lot in an auction | the auction | `auction_lot`, detail of a lot's auction listing |

An item can be in one purchase lot and one open sales lot at once, and they
say nothing about each other. A single item offered at auction is a sales lot
of one.

## How things move

| Action | Effect |
|---|---|
| **Offer** an item or lot (platform, format, price, public text, external id) | Refused if any affected item is deleted, split, or not `received`; is already sold (`sold`, `shipped`, `delivered`) or held by an unshipped order; is a member of an offered lot; is on offer on a **non-store** platform ("end it first"); or the platform is retired. An active **store** listing of an affected item is **paused**, with `paused_by_listing_id` set -- except that offering an item on the store while it is already there is refused. The new listing and its claims are `active`; items become `listed`; a lot becomes `offered`. A batch is all or nothing, lists every refused item with its reason, and refuses an item named twice. |
| **Edit** a listing | Price, public title and description, external id. Once a listing has ended only its external id may change: its terms are part of the sale's record. |
| **End** a listing | `ended`, claims `released`. Store listings it paused **resume**. A lot listing's lot **dissolves** (members released). Items nothing else offers go back to `held`. Ending an ended listing changes nothing. A listing that is a lot of an auction is refused: it ends only through its auction. |
| **Record a sale** on an outside listing | One order on that platform: buyer (matched case-insensitively, or created; blank is the platform's undisclosed buyer), price, `external_order_id`, fee lines, shares, snapshot. The listing ends **sold**: claims released, the lot `sold`, members' paused store listings **ended** rather than resumed, items `sold`. Refused for a store listing and for a listing that is a lot of an auction, which sells through settlement. |
| **Add to an auction** (`draft` or `scheduled`) | Offers the lot, or wraps one item in a new lot of one titled by the item's source title, with `format = auction`, and creates its `auction_lot`. Same refusals and pausing as Offer. |
| **Remove from an auction** (`draft`, `scheduled` or `consigned`) | Ends the listing as for End and deletes the `auction_lot` row; if the house holds coins, they return to the chosen location first. |
| **Schedule** | `draft` → `scheduled`. |
| **Mark consigned** (auction houses only, from `scheduled`) | `consigned_on` set, status `consigned`; every member moves to the house's consigned location. |
| **Close** (from `scheduled` or `consigned`) | `closed`; lot results may now be entered. A `draft` auction is cancelled, not closed. |
| **Cancel** (any status but `settled` or `cancelled`) | Removes every lot as above, in lot id order; consigned coins return to the chosen location; `consigned_on` cleared; status `cancelled`. |
| **Settle** (from `closed`) | One transaction. **Sold** lots: one order per buyer (usernames compared case-insensitively; the first spelling is kept) holding that buyer's lots, with that buyer's fees and the shares, as Record a sale. Every buyer's order carries the auction's sale number as `external_order_id`, which is what the house's statement is reconciled against. **Unsold / withdrawn**: coins return from the house first if it holds them, then as End -- lot dissolved, paused store listings resume at their old price, other members `held`. `consigned_on` cleared, status `settled`. |

**Custody is `consigned_on is not None`, not the status.** `close` accepts a
`consigned` auction and keeps the date, so a `closed` auction may still have
coins at the house. `remove_lot` and `cancel` then require a location to
return them to, and `settle` requires one when any lot is coming back.
`cancel` and `settle` clear the date once every coin is back; removing one lot
never does.

**Removing a lot deletes its `auction_lot` row**, so its `lot_number` is free
again at once; lot numbers and reserves stay editable until the auction
closes. A removed lot leaves no auction-side record (the coins'
`location_history` is the only trace, if they were consigned), and an ended
auction-format listing may have no `auction_lot` row. A lot pulled *after*
the sale closed is the settlement result `withdrawn`, recorded on a row
settlement keeps -- which is why `remove_lot` refuses a `closed` auction.

An auction-format listing need not belong to an auction: the Offer dialog can
put a coin on eBay by auction directly, and it is ended and sold like any
other outside listing.

## Data model

### Reference vocabularies

- **`sales_venue_kind`**: `own_store`, `marketplace` (fixed price on eBay,
  Whatnot), `live_auction` (eBay Live, Whatnot shows), `auction_house`
  (Heritage, HiBid through an agent).
- **`sales_fee_kind`**: `commission`, `processing`, `listing`,
  `shipping_label`, `promotion`, `other`.

Both are small, closed vocabularies the product defines, so the baseline
migration (`backend/alembic/baseline.sql`) seeds them with `INSERT`s. The
only other rows it inserts are the own-store `sales_venue` and the six
`strike_type` rows. **`storage_location_kind.consigned`** is a row in
`backend/data/reference/operations.json`, loaded by
`python -m app.seeding load`; marking an auction consigned on a database
without it fails with a message naming that command.

### `sales_venue` -- the platforms

Installation data, not shipped: another installation has different
accounts. Only the web store is created by the migration; the rest are
entered on the Platforms page.

| Column | Notes |
|---|---|
| `id`, `code`, `name` | `code` unique and immutable |
| `sales_venue_kind_id` | FK, not null |
| `is_own_store` | true on the web store only; `uq_sales_venue_own_store` allows one |
| `vendor_id` | FK `vendor`, nullable, **unique**: at most one platform per purchase source |
| `account_handle` | the owner's username or seller id there |
| `listing_url_template` | e.g. `https://www.ebay.com/itm/{external_id}` |
| `commission_rate`, `processing_rate` | `numeric(6,4)`, nullable, a fraction between 0 and 1 |
| `processing_fixed`, `listing_fee` | `numeric(12,2)`, nullable, not negative |
| `terms_as_of` | when the defaults were read; shown beside every estimate |
| `notes`, `is_active`, `version`, timestamps | retired, never deleted |

The web store's kind cannot change and it cannot be retired, and no other
platform can be given the `own_store` kind. **No fee figures are seeded**:
terms change and differ by account.

Estimated fees for a price `p`: `p * commission_rate + p * processing_rate +
processing_fixed + listing_fee`, nulls as zero. Shown to staff only, never
stored on a sale.

### `listing` -- one offer of one thing on one platform

| Column | Meaning |
|---|---|
| `sales_venue_id` | FK, not null |
| `format` | `fixed_price` \| `auction` |
| `status` | `active` \| `paused` \| `ended` |
| `is_active` | `GENERATED ALWAYS AS (status = 'active') STORED`, read by `ix_listing_active` and the `public_catalog` view; nothing writes it |
| `inventory_item_id` / `sales_lot_id` | nullable FKs; `ck_listing_item_xor_lot` requires **exactly one** |
| `paused_by_listing_id` | FK `listing`: the offer this store listing is paused for, and so which ending resumes it |
| `external_id`, `external_url` | the platform's id and page. Nothing writes `external_url`: no request field and no pass sets it, so the page shown is always derived from the platform's URL template and `external_id` |
| `price` | for an auction listing, the **starting bid** (0 if none) |
| `quantity_available` | always 1 on a lot listing (`ck_listing_lot_quantity_one`) |
| `title`, `description` | the public wording of the offer |
| `listed_at`, `ended_at` | when it opened and ended |

The shop and checkout accept only listings that are `own_store`,
`fixed_price` and `active`; `offering_writes.sellable_in_shop` and
`shop_listing_filters` are that rule's one home. A re-offer is always a
**new** listing row.

**`listing_status_history`** mirrors `item_status_history`: the opening row
and every pause, resumption and ending, written by `offering_writes` in the
same flush, with a note ("offered", "sold", "withdrawn", "paused for listing
#12", "unsold at auction #3") because status alone cannot tell a sale from a
withdrawal.

### `sales_lot` and `sales_lot_item`

- **`sales_lot`**: `title`, `description` (the working name and text, shown
  in the office; the shop shows the listing's own wording), `status`
  (`assembling` → `offered` → `sold` | `dissolved`), `version`.
- **`sales_lot_item`**: `sales_lot_id`, `inventory_item_id`, `released_at`.
  The partial unique index `uq_sales_lot_item_open` on `inventory_item_id`
  where `released_at IS NULL` keeps an item in at most one open lot.
  `released_at` is set only when the lot is sold or dissolved; a member
  dropped during assembly is deleted.
- Wording and membership are editable, and the lot deletable, only while
  `assembling`. Once offered it is frozen: the buyer is looking at that exact
  group. A member of an offered lot cannot be offered on its own anywhere
  (`offering_writes._refuse_grouped`) and cannot be split; the owner ends the
  lot's offer first. A dissolved lot never comes back: re-offering its coins
  starts a new lot.
- An item cannot join a lot while it is on an item listing with
  `quantity_available > 1` or an unshipped order holds units of it -- a claim
  covers a whole item. The same refusals as Offer apply (deleted, split, not
  received, already sold).

### `offer_claim` -- the one-active-offer rule

`offer_claim(inventory_item_id, listing_id, state)`, `state` in `active` |
`paused` | `released`.

- An item listing has one claim; a lot listing one per member.
- **Partial unique index `uq_offer_claim_active` on `inventory_item_id`
  where `state = 'active'`** -- the database guarantee that no item is
  offered twice. It lives here, not on `listing`, because a lot listing
  offers several items. An offer that loses a race to it is answered 409
  "was offered somewhere else a moment ago".
- Written only by `offering_writes`, in the same transaction as the listing
  it mirrors: a claim's state always equals its listing's status (`ended` →
  `released`).

### `auction` and `auction_lot`

- **`auction`**: `sales_venue_id` (`live_auction`, `auction_house`, or
  `marketplace` for a single timed eBay auction), `title`, `external_id`
  (sale number), `starts_at`, `ends_at`, `status` (`draft` → `scheduled` →
  [`consigned`] → `closed` → `settled`, or `cancelled`), `consigned_on`,
  `notes`, `version`. `consigned` applies only to auction houses: an eBay or
  Whatnot auction never leaves the premises.
- **`auction_lot`**: one row per auction listing (`listing_id` unique),
  `auction_id`, `lot_number` (text, unique within the auction), `reserve`,
  `result` (`sold` | `unsold` | `withdrawn`, null until settled),
  `hammer_price`, `buyer_customer_id`. Its listing offers a sales lot and has
  `format = auction`.

### Sales

- **`sales_order`** carries `sales_venue_id` (not null) and
  `external_order_id`.
- **`sales_order_fee`**: `sales_order_id`, `sales_fee_kind_id`, `amount`,
  `note` -- actual fees as the platform's statement shows them. Net payout is
  `total_amount - sum(fees)`, computed, never stored.
- **`sales_order_item_share`**: `sales_order_item_id`, `inventory_item_id`,
  `amount`, `fee_amount`. Every line is divided among the items it carried
  -- one row for an item listing, one per member for a lot -- with
  `app/allocation.py`, so shares sum to the line to the cent. `amount` is
  weighted by each item's `total_cost` (equally when every cost is zero).
  An order's fees are divided once across every item on every line, by cost
  or, when `equal_shares` is asked for, equally. **Every line gets shares, a
  single-item store sale included**: this table is the one permanent answer
  to "which items did this order carry", which `sale_state` and gain
  reporting (`sl_sales`, `reporting-design.md`) depend on.
- **Sale snapshots** (`app/sale_snapshot.py`) have two shapes, by
  `snapshot_version`:

  | Version | Keys |
  |---|---|
  | **1** | `item` and `listing` |
  | **2** | `listing`, plus **either** `item` (as version 1) **or** `lot` and `items` |

  Never both. `lot` is id, title and description; `items` is the per-item
  detail, one per member, in item id order. Snapshots are never rewritten,
  so a reader asks for `lot` and branches. The lot half is the whole record of
  which coins the group held -- selling a lot releases every membership.
- **Order status on an outside sale**: `marketplace` and `live_auction`
  sales are created `paid` (the owner ships from the Sales page);
  `auction_house` sales `delivered` (the house ships).
  `sales_writes._STATUS_BY_VENUE_KIND` has no `own_store` key, so a store
  sale cannot be recorded this way even past the explicit refusal.

### Buyers

`customer` carries `sales_venue_id` and `venue_username`: unique per
platform, case-insensitively, where the username is set
(`uq_customer_venue_username`), and one **undisclosed buyer** per platform
(username null, `uq_customer_venue_undisclosed`), shown as "Undisclosed
buyer (Heritage)". The customer record keeps the spelling the owner typed. A
store customer has neither column.

### Consignment custody

A `consigned` storage location per auction-house platform (`institution` =
the platform's name, no identifier), created on first use with an upsert, so
two first consignments racing read one row. Items move there and back only
through `lifecycle_writes.set_location`, noted with the auction. Storage
locations are never shown to customers.

## Who writes what

| Module | Sole writer of |
|---|---|
| `offering_writes` | `listing.status`, `listing_status_history`, `offer_claim`, `sales_lot.status`, `sales_lot_item.released_at`, and the `inventory_item.disposition` changes these cause |
| `lot_writes` | assembling a lot: create, edit, delete, add and remove members |
| `order_writes` | `sales_order`, its lines, stock, and share rows with their `amount` |
| `sales_writes` | `sales_order_fee` and the `fee_amount` half of shares; orchestrates a sale through `order_writes.place_order` and `offering_writes.end_offer(sold=True)` |
| `auctions` | auction transitions, which `auction_lot` rows exist, and the consigned `storage_location` |

`routers.auctions` itself creates a `draft` auction and edits its wording and
dates, and a lot's number and reserve (after
`auctions.refuse_unless_lot_editable`): fields with no consequence for the
writers above.

`sales_writes` has one implementation with two doors: `record_sale` (one
listing; the Listings page's Record sale) and `record_sale_lines` (several
listings on one order; auction settlement, one call per buyer, because an
auction house bills per buyer, not per lot). Shop checkout calls
`order_writes.place_order` directly and never touches either.

**Concurrency.** Every multi-row write takes its locks through
`offering_writes.lock_for_sale` -- lot rows, then items, then listings --
after any `auction` or `sales_order` row above them
(`lock-order-design.md`). The `offer_claim` partial unique index is the
backstop if two requests still race. Platform, listing, lot, auction and
order details are optimistic (`version`, 409 on mismatch). Checkout refuses a
listing that is not `active`, so a store listing paused while in a cart
cannot be bought.

**The for-sale warning** (`app.sale_state`) counts active *and* paused
listings and open orders through shares, so an item in an unsettled auction
or an offered lot warns like a listed one (`for-sale-guards-design.md`).

## Console

All pages use the console's patterns: modal dialogs, Alt-key accelerators,
the version token sent back on save. Cost basis, value, fees and margin are
staff-only.

1. **Platforms** (`/management/platforms`): add, edit, retire. Name, kind,
   purchase source (a `vendor` picker), account handle, URL template with an
   example link, default fees and their as-of date. The store's kind is
   fixed.
2. **Offer from inventory.** The coin and currency pages' bulk bar has
   **Offer for sale...** and **Group into lot...** (the selection becomes a
   new lot, or joins an assembling one). The offer dialog takes platform and
   format, then per item: price, public title (suggested by
   `GET /api/offers/titles`) and description, external id, with cost basis,
   value, estimated fees and net, and margin. **Fill blank prices** sets
   each price not yet typed to the lowest that leaves the margin entered
   beside it (20% to begin with) after the chosen platform's fees and the
   item's cost (`priceForMargin` in `platform-rates.js`, the inverse of the
   margin the row shows); a typed price is never replaced, and an item with
   no cost recorded is left blank and counted. The **item editor** has an
   **Offers** panel (current and past listings, lot memberships included,
   with Offer and End) beside its sales history.
3. **Listings** (`/management/listings`): every listing, filterable by
   platform, format and status. **Edit** on active rows (price, public text,
   external id), **End** on any row not yet ended, and **Record sale...** on
   active non-store rows (buyer by platform username or undisclosed, sale
   price, order number, actual fees by kind, and net and margin before
   confirming). A paused row names the listing it is paused for.
4. **Lots** (`/management/lots`): **New lot...**, and each assembling lot
   with its members and a remove for each (coins go in from the coin and
   currency pages' **Group into lot...**, not from here), running cost basis
   and value, **Offer for sale...**, **Edit wording...** (title and
   description) and **Discard...**. An assembling lot enters an auction from
   the Auctions page's **Add lot...**. History of offered, sold and dissolved
   lots, newest page first, with **Re-offer as a lot** on a dissolved one,
   which pre-fills a new assembling lot.
5. **Auctions** (`/management/auctions`): list by platform, date, status and
   lot count. Detail: platform, sale number, dates; **Schedule**, **Mark
   consigned...**, **Close**, **Cancel auction**; a lot table with editable
   lot numbers and reserves, and **Add lot...** (an assembling lot, or one
   item as a lot of one). The settlement grid takes each lot's result, hammer
   price and buyer, fees per buyer order, and -- when the house holds coins
   -- the location to return unsold and withdrawn items to, with gross, fees,
   net and cost-basis totals; **Settle** applies it all, and a refused grid
   marks every offending lot at once.

## API

Admin-only unless noted; the console's calls live in `management/api.js`.

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/sales-venues`, `PATCH /api/sales-venues/{code}` | platforms; the list puts the store first and includes retired ones |
| `GET /api/offers/titles?item_ids=...` | suggested public titles for items about to be offered |
| `POST /api/offers` | offer one or many items (`items`), or one lot (`lot_id`); all or nothing |
| `GET /api/listings` | listings by `venue`, `format`, `status` (default active and paused; `all` adds ended) and `item_id` (lot memberships and ended offers included) |
| `PATCH /api/listings/{id}` | price, title, description, external id; `version` |
| `POST /api/listings/{id}/end` | end (withdraw) |
| `POST /api/listings/{id}/sale` | record an outside sale: price, buyer username, order number, fees, `equal_shares` |
| `GET/POST /api/sales-lots`, `GET/PATCH/DELETE /api/sales-lots/{id}` | lots; `PATCH` changes wording and membership (`add_item_ids`, `remove_item_ids`) together while assembling; the list is paged (`limit` ≤ 500, default 200; `offset`; `total`) and filterable by `status` |
| `GET/POST /api/auctions`, `PATCH /api/auctions/{id}` | auctions; the list filters by `venue` and `status`; `PATCH` never changes platform or status |
| `POST /api/auctions/{id}/lots`, `PATCH`/`DELETE .../lots/{lot_id}` | add, renumber or re-reserve, remove (`returned_to_location_id` as a query parameter) |
| `POST /api/auctions/{id}/{schedule\|consign\|close\|cancel\|settle}` | transitions; `consign` takes `on_date`, `cancel` and `settle` take `returned_to_location_id` |

`GET /api/catalog` (public) returns a store lot as one entry whose members
carry their public descriptions. The catalog has no write endpoints.

**Errors.** 409 for conflicts with other work, naming what is in the way or
reporting a stale version. 422 for bad input: unknown platform, format or
fee kind, an empty lot, a negative or sub-cent amount. An offer refusal
answers `{detail, refused: [{item_code, reason}]}`; refusals from
`sales_writes` and `auctions` answer `{detail, refused: [{reason,
lot_number}]}`. Settle reports every problem at once: an auction not
`closed`, a lot with no result or two, a sold lot with no hammer price or --
outside an auction house, where a blank buyer is the undisclosed buyer --
no buyer, a price on a lot that did not sell, fees given twice or for someone
who bought nothing, or coins with nowhere to return to. It answers 422 only
when every problem is a bad amount, 409 otherwise.

## Invariants checked after every test

`backend/tests/conftest.py`'s autouse fixture runs five checks after every
test that has a database session, and the race tests' `committed` fixtures
call them by hand (`check_all_invariants`):

| Rule | Check | Raises |
|---|---|---|
| A claim's state equals its listing's status | `check_claim_invariant` | `ClaimInvariantViolation` |
| An open lot membership implies its lot is `assembling` or `offered` | `check_lot_invariant` | `LotInvariantViolation` |
| A held claim implies its item is `listed` or already sold away | `check_disposition_invariant` | `DispositionInvariantViolation` |
| A listing's latest history row names its current status | `check_listing_history_invariant` | `HistoryInvariantViolation` |
| An auction agrees with its lots, and each lot with its listing | `check_auction_invariant` | `AuctionInvariantViolation` |

The auction rules: from `draft` through `closed`, no lot has a result and
every lot's listing is `active`; once `settled`, every lot has a result and
an `ended` listing; a `cancelled` auction has no lots; a `sold` lot has a
hammer price and a buyer and any other lot has neither; a lot details an
auction-format listing; `consigned_on` is set on every `consigned` auction,
may be set on a `closed` one, and on no other.

**Five exception types, and the separation is load-bearing.** A few tests
build deliberately inconsistent claim scaffolding and carry the
`claim_invariant_waiver` marker, which absorbs any `ClaimInvariantViolation`.
Had another rule raised that type, or been folded into the claim check,
those tests would be silently exempt from it too. So only the claim half
consults that waiver; the auction check has its own
`auction_invariant_waiver`; both require a `reason=` and fail when they stop
biting; and each `xfail(strict=True, raises=...)` proof in
`tests/test_claim_invariant.py` narrows on its own type.

Each rule is **one direction only**. An `assembling` lot need not have
members (lots are built a coin at a time); a `listed` item need not have a
claim (`build_listing` makes claimless listings); and the disposition check
allows `offering_writes.SOLD_AWAY`, because a checkout that takes the last
unit marks the item `sold` while its store listing stays active.

## Other tests

- **Races** on real threads behind a barrier, a session each
  (`TestClient` serializes requests and cannot show a race):
  `tests/test_offer_races.py`, `tests/test_settlement_race.py`,
  `tests/test_order_revision_race.py`.
- **Settlement** (`tests/test_auction_settlement.py`): shares sum to the line
  to the cent; resumed store listings keep their price; a settlement run
  without autoflush leaves each returned coin at the chosen location, with
  the move out to the house and the move back both in its location history;
  a sold lot's paused store listings end rather than resume; a failure part
  way leaves nothing written.
- **Migrations**: `test_migrations_match_models` and
  `test_the_baseline_views_and_functions_are_the_apps`.
- **Shop boundary**: the public catalog never exposes cost, storage
  location or non-store listings; the bundle-isolation check stays green.

## Known limits

- **An unshipped order holding an ended listing cannot be cancelled** -- an
  outside sale, or a lot bought in the shop. There is nothing to return the
  stock to. Once shipped it can be cancelled (no stock moves), which is how a
  refund is recorded. The Sales page disables the option where the server
  would refuse it (`listing_ended`).
- **A recorded outside sale's quantities cannot change.** Raising a line
  needs a store listing on sale, and lowering one hands stock back to a
  listing that ended with the sale; both are refused. Its prices, customer
  and notes can still be revised. There is no console path that re-opens
  such a sale.
- **Ending a lot's offer dissolves the lot**; there is no "withdraw but keep
  the group". It is also the only way to sell one of its coins on its own.
- **A lot listing's `piece_count` is the sum of its members'**, since a
  member may itself be several pieces (a roll, a mint set).
- **No equal-shares control in the console.** `record_sale` takes
  `equal_shares`; the Record sale dialog always sends `false`.
- **The console offers End and Record sale on an auction lot's listing**,
  which the server refuses with a message pointing at the auction.
- **A lot's snapshot costs one `item_detail` per member inside checkout's
  lock window.** Harmless for two- or three-coin lots; a very large lot would
  hold its locks while the snapshot is built.
- **`SettlementInputInvalid` (422) is unreachable through the settle
  endpoint**: the request schema already refuses negative or sub-cent money.
  It guards callers that bypass the schema.
- **`order_writes._refuse` rolls back the whole session**, even inside
  `auctions.settle`'s savepoint. It errs safe and is unreached: settlement
  always passes a `venue` and asks for exactly the quantity each lot listing
  offers.

## Not built

- Importing platform sales reports (the Whatnot CSV, an eBay export) into the
  same order, fee and customer rows.
- A tax-return form of realized gain. Gain per item is reported by `sl_sales`
  (specific identification); how fees and shipping are presented on a return
  is outside it (`reporting-design.md`).
- Customers registering interest in an item held by an auction; needs email.
- Pushing listings to platforms through their APIs. Offers record what the
  owner listed by hand.
- Sales tax on outside sales: marketplaces collect it as facilitators, so it
  is not part of the recorded total.
- Price suggestions from a valuation feed.
