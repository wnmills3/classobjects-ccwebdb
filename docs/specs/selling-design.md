# Selling: platforms, offers, sales lots and auctions

Where items are offered, how they are grouped for sale, how a sale on
another platform or at auction is recorded, and the rules that keep one coin
from being sold twice.

## Rules

| Question | Rule |
|---|---|
| "Resellers" | The **platforms we sell through** (web store, eBay, Whatnot, auction houses selling through an agent), not dealers who buy from us. |
| One item on several platforms at once | **Never.** At most one active offer per item, enforced by the database. |
| Outside sales | **Entered by hand**; platform report imports would fill the same records. |
| Buyers on outside platforms | **Customer records marked by platform** (`whatnot:coinfan88`); one "undisclosed buyer" per platform that does not name buyers. |
| Fees | **Defaults per platform for estimates; actual fees recorded per sale.** The actuals are what count for tax. |
| An auction lot that does not sell | Its items **go back into the store** if they were there before, at their old price; otherwise to `held`. |
| Consignment custody | **A storage location** ("Consigned: Heritage"), through the ordinary location history. |
| Where platforms live | **`sales_venue`, with an optional link to `vendor`**, so eBay is one partner whether buying or selling. |
| Grouping items for sale | A **sales lot**: a temporary group offered as one thing, which **dissolves back into its items** if it does not sell. |
| A web-store item sold in person | **An order on the customer's behalf**, from the Orders page (`order-on-behalf-design.md`). Record sale refuses store listings. |

Rejected, with reasons that still hold:

- *Renaming `vendor` to a partner table with both roles* -- it renames a table
  used by purchases, receiving, search and tests for no gain.
- *A platform list with no link to `vendor`* -- eBay, Whatnot and HiBid
  would exist twice, spelled differently.
- *A parallel "offering" table beside `listing`* -- checkout, the public
  catalogue, sale snapshots and the for-sale warning all read `listing`;
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

## Data model

### Reference vocabularies

- **`sales_venue_kind`**: `own_store`, `marketplace` (fixed price on eBay,
  Whatnot), `live_auction` (eBay Live, Whatnot shows), `auction_house`
  (Heritage, HiBid through an agent).
- **`sales_fee_kind`**: `commission`, `processing`, `listing`,
  `shipping_label`, `promotion`, `other`.

Both are small, closed vocabularies the product defines, so the migration
that creates each table seeds it with an `INSERT`; they are the only two
tables the schema seeds (`backend/app/models/__init__.py`).
**`storage_location_kind.consigned`** is a row in
`data/reference/operations.json`, loaded by `python -m app.seeding load`.

### `sales_venue` -- the platforms

Installation data, not shipped: another installation has different
accounts. Only the store platform is created by the migration; the rest are
entered on the Platforms page.

| Column | Notes |
|---|---|
| `id`, `code`, `name` | `code` unique and immutable |
| `sales_venue_kind_id` | FK, not null |
| `vendor_id` | FK `vendor`, nullable, **unique**: at most one platform per purchase source |
| `account_handle` | the owner's username or seller id there |
| `listing_url_template` | e.g. `https://www.ebay.com/itm/{external_id}` |
| `commission_rate`, `processing_rate` | `numeric(6,4)`, nullable, a fraction |
| `processing_fixed`, `listing_fee` | `numeric(12,2)`, nullable |
| `terms_as_of` | when the defaults were read; shown beside every estimate |
| `notes`, `is_active`, `version`, timestamps | retired, never deleted |

Exactly one `own_store` platform (partial unique index), whose kind cannot
change. **No fee figures are seeded**: terms change and differ by account.

Estimated fees for a price `p`: `p * commission_rate + p * processing_rate +
processing_fixed + listing_fee`, nulls as zero. Shown to staff only, never
stored on a sale.

### `listing` -- one offer of one thing on one platform

| Column | Meaning |
|---|---|
| `sales_venue_id` | FK, not null |
| `format` | `fixed_price` \| `auction` |
| `status` | `active` \| `paused` \| `ended` |
| `is_active` | `GENERATED ALWAYS AS (status = 'active') STORED`, so older readers keep working; nothing writes it |
| `inventory_item_id` / `sales_lot_id` | nullable FKs; `ck_listing_item_xor_lot` requires **exactly one** |
| `paused_by_listing_id` | FK `listing`: the offer this store listing is paused for, and so which ending resumes it |
| `external_id`, `external_url` | the platform's id and page; the URL is derived from the template when not given |
| `price` | for an auction listing, the **starting bid** (0 if none) |
| `quantity_available` | always 1 on a lot listing (`ck_listing_lot_quantity_one`) |
| `ended_at` | when it ended |

The shop and checkout accept only listings that are `own_store`,
`fixed_price` and `active`; `offering_writes.sellable_in_shop` and
`shop_listing_filters` are that rule's one home. A re-offer is always a
**new** listing row.

**`listing_status_history`** mirrors `item_status_history`: the opening row
and every pause, resumption and ending, written by `offering_writes` in the
same flush, with a note ("sold", "withdrawn", "paused for listing #12")
because status alone cannot tell a sale from a withdrawal.

### `sales_lot` and `sales_lot_item`

- **`sales_lot`**: `title`, `description` (both public), `status`
  (`assembling` → `offered` → `sold` | `dissolved`), `version`.
- **`sales_lot_item`**: `sales_lot_id`, `inventory_item_id`, `released_at`.
  A partial unique index on `inventory_item_id` where `released_at IS NULL`
  keeps an item in at most one open lot. `released_at` is set only when the
  lot is sold or dissolved; a member dropped during assembly is deleted.
- Membership is editable only while `assembling`. Once offered it is frozen:
  the buyer is looking at that exact group. A member of an offered lot
  cannot be offered on its own anywhere (`_refuse_grouped`), and cannot be
  split.
- An item cannot join a lot while it has an item listing with
  `quantity_available > 1` or sold units -- a claim covers a whole item.

### `offer_claim` -- the one-active-offer rule

`offer_claim(inventory_item_id, listing_id, state)`, `state` in `active` |
`paused` | `released`.

- An item listing has one claim; a lot listing one per member.
- **Partial unique index on `inventory_item_id` where `state = 'active'`** --
  the database guarantee that no item is offered twice. It lives here, not
  on `listing`, because a lot listing offers several items.
- Written only by `offering_writes`, in the same transaction as the listing
  it mirrors: a claim's state always equals its listing's status (`ended` →
  `released`).

### `auction` and `auction_lot`

- **`auction`**: `sales_venue_id` (`live_auction`, `auction_house`, or
  `marketplace` for a single timed eBay auction), `title`, `external_id`
  (sale number), `starts_at`, `ends_at`, `status` (`draft` → `scheduled` →
  [`consigned`] → `closed` → `settled`, or `cancelled`), `consigned_on`,
  `notes`, `version`. `consigned` applies only to auction houses.
- **`auction_lot`**: one row per auction listing (`listing_id` unique),
  `auction_id`, `lot_number` (text, unique within the auction), `reserve`,
  `result` (`sold` | `unsold` | `withdrawn`, null until settled),
  `hammer_price`, `buyer_customer_id`. Its listing offers a sales lot and has
  `format = auction`.

An auction-format listing need not belong to an auction: the Offer dialog can
put a coin on eBay by auction directly.

**Removing a lot from an auction deletes its `auction_lot` row** (ruling
R11), so its `lot_number` is free again at once; lot numbers stay editable
until the sale. Two consequences: a removed lot leaves no auction-side record
(the coin's `location_history` is the only trace, if it was consigned), and
an ended auction-format listing may have no `auction_lot` row. A lot pulled
*after* the sale closed is a settlement result (`withdrawn`), recorded on a
row settlement keeps -- which is why `remove_lot` refuses a `closed` auction
(R14).

**Custody is `consigned_on is not None`, not the status** (R13): `close`
accepts a `consigned` auction and keeps the date, so a `closed` auction may
still have coins at the house. `cancel`, `remove_lot` and `settle` then
require a location to return them to (R9), and `cancel` and `settle` clear
the date once every coin is back.

### Sales

- **`sales_order`** carries `sales_venue_id` (not null) and
  `external_order_id`.
- **`sales_order_fee`**: `sales_order_id`, `sales_fee_kind_id`, `amount`,
  `note` -- actual fees as the platform's statement shows them. Net payout is
  `total_amount - sum(fees)`, computed, never stored.
- **`sales_order_item_share`**: `sales_order_item_id`, `inventory_item_id`,
  `amount`, `fee_amount`. Every line is divided among the items it carried
  -- one row for an item listing, one per member for a lot -- with
  `app/allocation.py`, so shares sum to the line to the cent. The default
  weight is each item's `total_cost`; **equal** when asked for, or when every
  weight is zero. Fees divide the same way. **Every line gets shares, a
  single-item store sale included**: that makes this table the one permanent
  answer to "which items did this order carry", which `sale_state` and
  realised-gain reporting depend on.
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
  sales are created `paid` (the owner ships from the Orders page);
  `auction_house` sales `delivered` (the house ships).
  `sales_writes._STATUS_BY_VENUE_KIND` has no `own_store` key, so a store
  sale cannot be recorded this way even past the explicit refusal.

### Buyers

`customer` carries `sales_venue_id` and `venue_username`: unique per
platform where the username is set, and one **undisclosed buyer** per
platform (username null), shown as "Undisclosed buyer (Heritage)". A store
customer has neither.

### Consignment custody

A `consigned` storage location per auction-house platform (`institution` =
the platform's name), created on first use and upserted, so two requests
racing to create it read one row. Items move there and back only through
`lifecycle_writes.set_location`. Storage locations are never shown to
customers.

## Who writes what

| Module | Sole writer of |
|---|---|
| `offering_writes` | `listing.status`, `listing_status_history`, `offer_claim`, `sales_lot.status`, `sales_lot_item.released_at`, and the `inventory_item.disposition` changes these cause |
| `lot_writes` | assembling a lot: create, edit, delete, add and remove members |
| `order_writes` | `sales_order`, its lines, stock, and share rows with their `amount` |
| `sales_writes` | `sales_order_fee` and the `fee_amount` half of shares; orchestrates a sale through `order_writes.place_order` and `offering_writes.end_offer(sold=True)` |
| `auctions` | `auction`, `auction_lot`, and the consigned `storage_location` |

`sales_writes` has one implementation with two doors: `record_sale` (one
listing; the Listings page's Record sale) and `record_sale_lines` (several
listings on one order; auction settlement, one call per buyer). Shop checkout
calls `order_writes.place_order` directly and never touches either.

Every multi-row write takes its locks through
`offering_writes.lock_for_sale` -- lot rows, then items, then listings --
after any `auction` or `sales_order` row above them. See
`lock-order-design.md`.

## How things move

| Action | Effect |
|---|---|
| **Offer** an item or lot (platform, format, price, public text, external id) | Refused if any affected item is not `received`, is split or deleted, is already sold or held by an open order, is a member of an offered lot, has an active claim on a **non-store** listing ("end it first"), or the platform is retired. An active **store** listing of an affected item is **paused** with `paused_by_listing_id` set -- except that offering an item on the store while it is already active there is refused. The new listing and its claims are `active`; items become `listed`; a lot becomes `offered`. A batch is all or nothing and lists every refused item with its reason. |
| **End** a listing | `ended`, claims `released`. Store listings it paused **resume**. A lot listing's lot **dissolves** (members released). Items nothing else offers go to `held`. Ending an ended listing changes nothing. An auction lot's listing cannot be ended here -- only through its auction. |
| **Record a sale** on an outside listing | One order on that platform: buyer (matched or created), price, `external_order_id`, fee lines, shares, snapshot. The listing ends **sold**: claims released, the lot `sold`, members' paused store listings **ended** rather than resumed, items `sold`. Refused for a store listing and for a listing that is a lot of an auction (R25) -- that sells through settlement. |
| **Add to an auction** (`draft` or `scheduled`) | Offers the lot, or a new lot of one, with `format = auction`, and creates its `auction_lot`. Same refusals and pausing as Offer. |
| **Remove from an auction** (not once `closed`), or **Cancel** | Ends its listings as for End and deletes the `auction_lot` rows; consigned coins return to the chosen location. |
| **Schedule** | `draft` → `scheduled`. |
| **Mark consigned** (auction houses, from `scheduled`) | `consigned_on` set, status `consigned`; every member moves to the house's consigned location. |
| **Close** (from `scheduled` or `consigned`) | `closed`; lot results may now be entered. |
| **Settle** (every lot has a result; every sold lot a price and buyer) | One transaction. **Sold** lots: one order per buyer (usernames compared case-insensitively, R17) holding their lots, with fees and shares, as Record a sale. **Unsold / withdrawn**: as End -- lot dissolved, paused store listings resume at their old price, other members `held`. Consigned coins return to the chosen location. Status `settled`. |

**Concurrency.** Row locks as above; the `offer_claim` partial unique index
is the backstop if two requests still race. Platform, lot, auction and order
details are optimistic (`version`, 409 on mismatch). Checkout refuses a
listing that is not `active`, so a store listing paused while in a cart
cannot be bought.

**The for-sale warning** (`app.sale_state`) counts active *and* paused
claims and open orders through shares, so an item in an unsettled auction or
an offered lot warns like a listed one (`for-sale-guards-design.md`).

## Console

All pages use the console's patterns: modal dialogs, Alt-key accelerators,
the version token sent back on save. Cost basis, value, fees and margin are
staff-only. The menu has a **Selling** group: Listings, Lots, Auctions.

1. **Platforms** (`/owner/platforms`): add, edit, retire. Name, kind,
   purchase source (a `vendor` picker), account handle, URL template with a
   sample link, default fees and their as-of date. The store's kind is fixed.
2. **Offer from inventory.** The coin and currency pages' bulk bar has
   **Offer for sale...** and **Group into lot...**. The offer dialog takes
   platform and format, then per item: price, public title (suggested by
   `GET /api/offers/titles`) and description, external id or URL, with cost
   basis, value, estimated fees and net, and margin. The **item editor** has
   an **Offers** panel (current and past listings, claim state, Offer, End)
   beside its sales history.
3. **Listings** (`/owner/listings`): every listing, filterable by platform,
   format and status. **Edit** (price, public text, external id), **End**,
   and **Record sale...** on active non-store rows (buyer by platform
   username or undisclosed, sale price, order number, actual fees by kind,
   net and margin before confirming).
4. **Lots** (`/owner/lots`): assembling lots with add and remove, title,
   description, running cost basis and value; **Offer** or add to an
   auction. History of offered, sold and dissolved lots, newest page first,
   with **Re-offer as a lot** on a dissolved one, which pre-fills a new
   assembling lot.
5. **Auctions** (`/owner/auctions`): list by platform, date, status and lot
   count. Detail: platform, sale number, dates; **Schedule**, **Mark
   consigned**, **Close**, **Cancel**; a lot table with editable lot numbers
   and **Add lot** (an assembling lot, or one item as a lot of one). The
   settlement grid takes each lot's result, hammer price and buyer, fees per
   buyer order, and for an auction house the location for returned items,
   with gross, fees, net and cost-basis totals; **Settle** applies it all.

## API

Admin-only unless noted; the console's calls live in `owner/api.js`.

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/sales-venues`, `PATCH /api/sales-venues/{code}` | platforms |
| `GET /api/offers/titles` | suggested public titles for items about to be offered |
| `POST /api/offers` | offer one or many items, or a lot; all or nothing |
| `GET /api/listings` | listings by `venue`, `format`, `status` (`all` adds ended) and `item_id` (lot memberships included) |
| `PATCH /api/listings/{id}` | price, public text, external id |
| `POST /api/listings/{id}/end` | end |
| `POST /api/listings/{id}/sale` | record an outside sale |
| `GET/POST /api/sales-lots`, `GET/PATCH/DELETE /api/sales-lots/{id}` | lots; `PATCH` changes wording and membership together while assembling; the list is paged (`limit` ≤ 500, default 200; `offset`; `total`) |
| `GET/POST /api/auctions`, `PATCH /api/auctions/{id}` | auctions |
| `POST /api/auctions/{id}/lots`, `PATCH`/`DELETE .../lots/{lot_id}` | add, renumber, remove |
| `POST /api/auctions/{id}/{schedule\|consign\|close\|cancel\|settle}` | transitions |

`GET /api/catalog` (public) returns store lots as one entry with their
members' public descriptions. The catalogue has no write endpoints.

**Errors.** 409 for conflicts with other work, naming what is in the way or
reporting a stale version. 422 for bad input: unknown platform, format or
fee kind, an empty lot, a negative or sub-cent amount. Refusals from
`sales_writes` and `auctions` answer `{detail, refused: [...]}`. Settle
refuses while any lot lacks a result or a sold lot lacks a price or buyer.

## Invariants checked after every test

`backend/tests/conftest.py`'s autouse fixture runs five checks after every
test that has a database session, and the race tests' `committed` fixtures
call them by hand:

| Rule | Check | Raises |
|---|---|---|
| A claim's state equals its listing's status | `check_claim_invariant` | `ClaimInvariantViolation` |
| An open lot membership implies its lot is `assembling` or `offered` | `check_lot_invariant` | `LotInvariantViolation` |
| A held claim implies its item is `listed` or already sold away | `check_disposition_invariant` | `DispositionInvariantViolation` |
| A listing's latest history row names its current status | `check_listing_history_invariant` | `HistoryInvariantViolation` |
| An auction agrees with its lots, and each lot with its listing | `check_auction_invariant` | `AuctionInvariantViolation` |

The auction rules: until `settled`, no lot has a result and every lot's
listing is `active`; once `settled`, every lot has a result and an `ended`
listing; a `cancelled` auction has no lots; a `sold` lot has a hammer price
and a buyer and any other lot has neither; a lot details an auction-format
listing; `consigned_on` is set on every `consigned` auction, may be set on a
`closed` one, and on no other.

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
  (`TestClient` serialises requests and cannot show a race):
  `tests/test_offer_races.py`, `tests/test_settlement_race.py`,
  `tests/test_order_revision_race.py`.
- **Settlement**: shares sum to the line to the cent; resumed store listings
  keep their price; consignment moves appear in location history both ways;
  a sold lot's paused store listings end rather than resume.
- **Migrations**: `test_migrations_round_trip` and
  `test_migrations_match_models`.
- **Shop boundary**: the public catalogue never exposes cost, storage
  location or non-store listings; the bundle-isolation check stays green.

## Known limits

- **Cancelling an order that bought a lot is refused.** The lot is `sold`
  and its listing ended, so there is nothing to return the stock to. The
  coins come back by the owner's own correction and are regrouped as a
  **new** lot. The Orders page disables Cancel on such an order
  (`listing_ended`).
- **Ending a lot's offer dissolves the lot**; there is no "withdraw but keep
  the group". It is also the only way to sell one of its coins on its own.
- **A lot listing's `piece_count` is the sum of its members'**, since a
  member may itself be several pieces (a roll, a mint set).
- **No equal-shares control in the console.** `record_sale` takes
  `equal_shares`; the Record sale dialog always sends `false`.
- **A recorded sale's quantity can be raised but not lowered.**
  `revise_order` refuses to shrink a line whose listing has ended, and
  `record_sale` ends the listing. The remedy is cancel and re-record.
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
- Realised-gain reporting. The shares make it possible; it needs the owner's
  accounting decisions (specific identification or FIFO, how fees and
  outbound shipping count).
- Customers registering interest in an item held by an auction; needs email.
- Pushing listings to platforms through their APIs. Offers record what the
  owner listed by hand.
- Sales tax on outside sales: marketplaces collect it as facilitators, so it
  is not part of the recorded total.
- Price suggestions from a valuation feed.
