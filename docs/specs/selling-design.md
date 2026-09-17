# Selling: platforms, offers, sales lots and auctions

Design. Status: **agreed with the owner 2026-09-17**; not yet built.

## The problem

The collection is catalogued and cleaned up enough to start selling
(`docs/project-purpose.md`: catalogue -> clean up -> **list** -> sell), and the
system cannot yet say where anything is offered.

- `vendor` records where items were **bought**. Nothing records the platforms
  items are **sold** through: the web store, eBay, Whatnot, and auction houses
  such as Heritage and HiBid that sell through an agent.
- `listing` has a price, a quantity and an active flag, and nothing else. It
  implicitly means "in our web store". It cannot say "on eBay as listing
  123456", and nothing stops an item being offered in two places at once --
  which `project-purpose.md` names as one of the three things that must be true
  before the first sale.
- The only console page that creates a listing, **Manage**
  (`/owner/manage/coins`), creates a *new item* with it. It cannot offer an item
  that is already owned, which is all 7,660 of them, and it contradicts the
  ruling that an item only ever enters through a purchase.
- Auctions are described in `project-purpose.md` and do not exist.
- On live data, 2026-09-17: 0 sales orders, 0 customers, and 5 active listings,
  all of them demo items (CC-007657..CC-007661) left behind by `app.seed`.

## Decisions

Made by the owner during design, 2026-09-17.

| Question | Decision |
|---|---|
| "Resellers" | The **platforms we sell through**, not dealers who buy from us. |
| One item on several platforms at once | **Never.** At most one active offer per item, enforced by the database. |
| Scope | **Platforms, offers, sales lots and auctions together**, delivered in phases. |
| Outside sales | **Entered by hand first**; platform report imports come later and fill the same records. |
| Buyers on outside platforms | **Customer records marked by platform** (`whatnot:coinfan88`); one "undisclosed buyer" per auction house that does not name buyers. |
| Fees | **Defaults per platform for estimates; actual fees recorded per sale.** The actuals are what count for tax. |
| An auction lot that does not sell | Its items **go back into the store automatically** if they were there before, at their old price; otherwise to `held`. |
| Consignment custody | **Tracked as a storage location** ("Consigned: Heritage") through the existing location history. |
| Where platforms live | **A `sales_venue` table with an optional link to `vendor`**, so eBay is one partner whether buying or selling. The purchase side is unchanged. |
| Grouping items for sale | A **sales lot**: a temporary grouping offered as one sale item in any format on any platform, which **dissolves back into its items** if it does not sell. |
| The Manage page | **Retired**, replaced by the Listings page. |

Rejected:

- *Renaming `vendor` to a partner table with both roles.* Cleanest on paper,
  but it renames a table used by purchases, receiving, the importer, search
  and tests for no feature gain today.
- *A separate platform list with no link to `vendor`.* eBay, Whatnot and HiBid
  would exist twice, spelled differently -- the duplication `vendor` already
  shows.
- *A parallel "offering" table beside `listing`.* Checkout, the public
  catalogue, sale snapshots and the for-sale edit warning all read `listing`;
  widening it keeps them working.
- *A sales lot as an inventory item.* It would be counted in inventory and
  cost basis beside its own members -- the problem split purchase lots already
  had to be excluded from every view to avoid.
- *Cross-listing with manual delisting.* The first sale wins and the second
  platform sells it again.

## Three kinds of lot

`project-purpose.md` distinguishes two meanings of "lot". This design uses the
second one more generally and names it.

| Term | Direction | Lifetime | Relationship |
|---|---|---|---|
| **Purchase lot** | how it came in | permanent; decomposed by splitting | `inventory_item.parent_item_id` |
| **Sales lot** | how it goes out | while offered; sold or dissolved | `sales_lot_item` |
| **Auction lot** | a sales lot in an auction | the auction | `auction_lot`, detail of a lot's auction listing |

An item can be in one purchase lot and one open sales lot at the same time, and
they say nothing about each other. A single item offered in an auction is a
sales lot of one.

## Data model

### Reference vocabularies (shipped with the product)

Seeded JSON in `backend/data/reference/`, each with a `_comment` naming its
source, per `docs/database-design.md` section 12.

- **`sales_venue_kind`**: `own_store`, `marketplace` (fixed-price listings on
  eBay, Whatnot), `live_auction` (eBay Live, Whatnot shows), `auction_house`
  (Heritage, HiBid through an agent).
- **`sales_fee_kind`**: `commission`, `processing`, `listing`, `shipping_label`,
  `promotion`, `other`.
- **`storage_location_kind`** gains `consigned`.

### `sales_venue` -- the platforms (installation data, not shipped)

These are the owner's own accounts, so they are not reference data: another
installation has different ones. Only the store platform is created by the
migration; the owner enters the rest on the Platforms page.

| Column | Notes |
|---|---|
| `id`, `code`, `name` | `code` unique and immutable, like reference codes |
| `sales_venue_kind_id` | FK, not null |
| `vendor_id` | FK `vendor`, nullable, **unique**: at most one platform per purchase source |
| `account_handle` | the owner's username or seller id there |
| `listing_url_template` | e.g. `https://www.ebay.com/itm/{external_id}` |
| `commission_rate`, `processing_rate` | `numeric(6,4)`, nullable -- a fraction, `0.1325` |
| `processing_fixed`, `listing_fee` | `numeric(12,2)`, nullable |
| `terms_as_of` | date the defaults were read; shown beside every estimate |
| `notes`, `is_active`, `version`, timestamps | retired, never deleted |

Exactly one `own_store` platform: a partial unique index on the kind. Its kind
cannot be changed. **No fee figures are seeded** -- platform terms change and
differ by account and category, so the owner enters them from their own
account.

Estimated fees for a price `p`:
`p * commission_rate + p * processing_rate + processing_fixed + listing_fee`,
nulls counting as zero. The estimate is shown to staff only and never stored on
a sale.

### `listing` -- any offer of one thing on one platform

Widened, not replaced.

| Column | Change |
|---|---|
| `sales_venue_id` | **new**, FK, not null; existing rows get the store platform |
| `format` | **new**, `fixed_price` \| `auction`; existing rows `fixed_price` |
| `status` | **new**, `active` \| `paused` \| `ended`; from existing `is_active` |
| `is_active` | becomes `GENERATED ALWAYS AS (status = 'active') STORED`, so every reader keeps working; **every writer changes** to go through `offering_writes` |
| `inventory_item_id` | becomes **nullable** |
| `sales_lot_id` | **new**, FK, nullable; a check requires **exactly one** of the two |
| `paused_by_listing_id` | **new**, FK `listing`, nullable: the offer this store listing is paused for, and so which settlement resumes it |
| `external_id`, `external_url` | **new**, text, nullable; the URL is derived from the template when not given |
| `price` | unchanged; for an auction listing it holds the **starting bid** (0 if none) |
| `quantity_available` | unchanged; always 1 for a lot listing (check) |

The public catalogue and checkout accept only listings with
`sales_venue.kind = own_store`, `format = fixed_price` and `status = active`.

### `sales_lot` and `sales_lot_item`

- **`sales_lot`**: `id`, `title`, `description` (both public), `status`
  (`assembling` -> `offered` -> `sold` | `dissolved`), `version`, timestamps.
- **`sales_lot_item`**: `sales_lot_id`, `inventory_item_id`, `released_at`.
  Unique per pair, and a **partial unique index on `inventory_item_id` where
  `released_at IS NULL`**: an item is in at most one open lot. `released_at` is
  set when the lot is sold or dissolved.
- Membership is editable only while `assembling`. Once offered it is frozen:
  the buyer is looking at that exact group.
- An item may not join a lot while it has an item listing with
  `quantity_available > 1` or with any sold units -- a claim covers the whole
  item, and a partly sold broken-up lot is not a whole item. Refused with a
  message saying so.

### `offer_claim` -- the one-active-offer rule

`offer_claim(id, inventory_item_id, listing_id, state)`, `state` in `active` |
`paused` | `released`.

- An item listing has one claim; a lot listing has one per member.
- **Partial unique index on `inventory_item_id` where `state = 'active'`.**
  This is the database guarantee that no item is offered twice. It lives here,
  not on `listing`, because a lot listing offers several items.
- Written **only** by `app/offering_writes.py`, in the same transaction as the
  listing it mirrors: a claim's state always equals its listing's status
  (`ended` -> `released`). An invariant test checks this after every write in
  the suite.

### `auction` and `auction_lot`

- **`auction`**: `id`, `sales_venue_id` (kind `live_auction`, `auction_house`,
  or `marketplace` for a single timed eBay auction), `title`,
  `external_id` (sale number), `starts_at`, `ends_at`, `status`
  (`draft` -> `scheduled` -> [`consigned`] -> `closed` -> `settled`, or
  `cancelled`), `consigned_on`, `notes`, `version`, timestamps. `consigned`
  applies only to `auction_house` platforms.
- **`auction_lot`**: one row per auction listing (`listing_id` unique),
  `auction_id`, `lot_number` (text, unique within the auction), `reserve`,
  `result` (`sold` | `unsold` | `withdrawn`, null until settled),
  `hammer_price`, `buyer_customer_id`. The listing it details always offers a
  sales lot, and has `format = auction`.

An auction-format listing always belongs to an auction; a timed eBay auction is
an auction with one lot.

### Sales

- **`sales_order`** gains `sales_venue_id` (not null; existing rows get the
  store) and `external_order_id`.
- **`sales_order_fee`**: `sales_order_id`, `sales_fee_kind_id`, `amount`,
  `note`. Actual fees, as the platform's statement shows them. Net payout is
  `total_amount - sum(fees)`, computed, never stored.
- **`sales_order_item_share`**: `sales_order_item_id`, `inventory_item_id`,
  `amount`, `fee_amount`. Every sold line is divided among the items it
  carried -- one row for an item listing, one per member for a lot -- with
  `app/allocation.py`, so shares sum to the line to the cent. The default
  weight is each item's `total_cost`; **equal** is chosen explicitly, and used
  when every weight is zero. Fees are divided the same way. This is what keeps
  realised gain answerable per item.
- **Order status on an outside sale.** `marketplace` and `live_auction` sales
  are created `paid` -- the platform collected payment and the owner ships,
  through the existing Orders page. `auction_house` sales are created
  `delivered`, since the house holds and ships the item; their sale snapshot is
  taken at settlement.

### Buyers

`customer` gains `sales_venue_id` and `venue_username`.

- Unique `(sales_venue_id, venue_username)` where the username is not null.
- One **undisclosed buyer** per platform: unique `sales_venue_id` where
  `venue_username IS NULL AND sales_venue_id IS NOT NULL`, display name
  "Undisclosed buyer (Heritage)".
- A store customer has neither column set, as today.

### Consignment custody

A storage location of kind `consigned` per auction-house platform
(`institution` = the platform's name), created the first time that platform
is consigned to. Items move there, and back, only through
`lifecycle_writes.set_location`, so the location history stays complete.
Storage locations stay invisible to customers (`StorageLocation` docstring).

## How things move

`app/offering_writes.py` is the **only writer** of `listing.status`,
`offer_claim`, `sales_lot.status`, `sales_lot_item.released_at` and the
`inventory_item.disposition` changes these cause -- the same pattern as
`lifecycle_writes.py`. `order_writes._after_stock_change` keeps handling store
stock.

| Action | Effect |
|---|---|
| **Offer** an item or lot (platform, format, price, public text, external id) | Refused if any affected item has an active claim on a **non-store** listing ("CC-001234 is active on eBay, listing #12: end it first"), is not `received`, is split, or the platform is retired. An active **store** listing of an affected item is **paused** with `paused_by_listing_id` set -- except that offering an *item* on the store while it is already active on the store is simply refused. The new listing and its claims are `active`; items become `listed`; a lot becomes `offered`. |
| **End** a listing | `ended`, `ended_at` set, claims `released`. Store listings it paused **resume**. A lot listing's lot is **dissolved** (members released). Items with no remaining active or paused claim go to `held`. |
| **Record a sale** on a fixed-price outside listing | One sales order on that platform: buyer (matched or created), price, `external_order_id`, fee lines, shares. Via `order_writes`, so the sold item is snapshotted as today. The listing ends as **sold**: claims released, the lot `sold`, members' paused store listings **ended** rather than resumed, items `sold`. |
| **Add to an auction** (auction `draft` or `scheduled`) | Offers the lot (or a new lot of one) with `format = auction`, creating its `auction_lot`. Same refusals and pausing as Offer. |
| **Remove from an auction**, or **cancel** the auction | Ends its listings as for End: paused store listings resume, lots dissolve. |
| **Mark consigned** (auction houses) | `consigned_on` set, status `consigned`; every member item moves to the house's consigned location. |
| **Close** | Status `closed`; lot results may now be entered. |
| **Settle** (every lot has a result; every sold lot has a price and buyer) | One transaction. **Sold** lots: one order per buyer holding their lots, fees, shares; as Record a sale. **Unsold / withdrawn** lots: as End -- lot dissolved, paused store listings resume at their old price, other members `held`. For an auction house, returned items move to the location the owner chose. Status `settled`. |

**Concurrency** follows the project's rules. Offer, add-to-auction, end,
record-a-sale and settle take `FOR UPDATE` on the affected items' rows, in item
id order, before reading their claims; the partial unique index is the backstop
if two requests still race. Platform, lot and auction details are optimistic
(`version`, 409 on mismatch). Checkout already locks the listing row, and now
also refuses a listing that is not `active` -- a store listing paused while in
a cart cannot be bought.

**The for-sale edit warning** (`app.sale_state`) counts active *and* paused
claims, so an item in an unsettled auction or an offered lot warns like a
listed one.

## Console

All pages follow existing console patterns: modal dialogs, Alt-key
accelerators, the version token sent back on save. Cost basis, value, fees and
margin are shown to staff only.

1. **Platforms** (`/owner/platforms`, beside Vocabularies). Table with add,
   edit, retire. Form: name, kind, purchase source (a picker over `vendor`),
   account handle, URL template with a sample link, default fees with their
   as-of date. The store platform's kind is fixed.
2. **Offer from inventory.** The coin and currency search pages' bulk bar gains
   **Offer for sale...** and **Group into lot...**. The offer dialog takes
   platform and format, then a row per item: price, public title and
   description (pre-filled from the item), external id or URL, with cost
   basis, value, **estimated fees and net** and margin beside it. Items that
   cannot be offered are listed with their reason, never dropped silently. The
   **item editor** gains an **Offers** panel (current and past listings, claim
   state, Offer and End) beside its Sales history.
3. **Listings** (`/owner/listings`). Every active and paused listing on every
   platform, filterable by platform, format and age. Row actions: **Edit**
   (price, public text, external id), **End**, and **Record sale...** (buyer by
   platform username or new, sale price, order number, actual fees by kind,
   net and margin before confirming). **Replaces Manage.**
4. **Sales lots** (`/owner/lots`). Assembling lots with item add and remove,
   title, description, and running cost basis and value; **Offer** (the same
   dialog, one row) or add to an auction. History of offered, sold and
   dissolved lots, with **Re-offer as a lot** on a dissolved one, which
   pre-fills a new assembling lot.
5. **Auctions** (`/owner/auctions`). List by platform, date, status and lot
   count. Detail: platform, sale number, dates; **Schedule**, **Mark
   consigned**, **Close**, **Cancel**; a lot table with editable lot numbers
   and **Add lot** (an assembling lot, or a single item as a lot of one).
   Settlement grid: per lot result, hammer price and buyer; fees per buyer
   order; gross, fees, net and cost-basis totals; for an auction house, the
   location for returned items. **Settle** confirms and applies all of it.

The console menu gains a **Selling** group: Listings, Lots, Auctions.

## API

Admin-only; the frontend calls live only in `owner/api.js`.

| Endpoint | Purpose |
|---|---|
| `GET/POST /api/sales-venues`, `PATCH /api/sales-venues/{code}` | platforms |
| `POST /api/offers` | offer one or many items, or a lot; all or nothing |
| `GET /api/listings` | filtered listing table |
| `PATCH /api/listings/{id}` | price, public text, external id |
| `POST /api/listings/{id}/end` | end |
| `POST /api/listings/{id}/sale` | record an outside sale |
| `GET/POST /api/sales-lots`, `PATCH /api/sales-lots/{id}` | lots and their membership while assembling |
| `GET/POST /api/auctions`, `PATCH /api/auctions/{id}` | auctions |
| `POST /api/auctions/{id}/lots`, `DELETE /api/auctions/{id}/lots/{lot_id}`, `PATCH .../lots/{lot_id}` | add, remove, renumber |
| `POST /api/auctions/{id}/{schedule\|consign\|close\|cancel\|settle}` | transitions |

`GET /api/catalog` stays public and now also returns store lots, as one entry
with its members' public descriptions. **`POST`, `PATCH` and `DELETE
/api/catalog` are removed** with the Manage page. The shop's checkout contract
is otherwise unchanged.

### Errors

- **409** for conflicts with other work, naming what is in the way (the
  listing, lot or auction and its platform) or reporting a stale version.
- **422** for bad input: unknown platform, format or fee-kind code, an empty
  lot, a lot listing with quantity other than 1.
- Batch offers refuse as a whole and list every refused item with its reason.
- Settle refuses while any lot lacks a result, any sold lot lacks a price or
  buyer, or any fee line is negative.

## Testing

- **Races**, on real threads behind a barrier with a session each, as in
  `test_order_revision_race.py` (`TestClient` serialises requests and cannot
  see a race):
  two offers of one item; an offer against an add-to-auction; checkout against
  a pause; two settles of one auction.
- **Mutation checks**: drop the `offer_claim` partial unique index, and
  separately the `FOR UPDATE` in offer and settle, and confirm the race tests
  fail; restore and confirm they pass.
- **Invariants after every write in the suite**: claim state equals listing
  status; an item's disposition agrees with its claims; open lot membership
  agrees with lot status.
- **Settlement**: shares sum to the line to the cent (a lot of three at
  $100.00); resumed store listings keep their price; consignment moves appear
  in location history in both directions; a sold lot's paused store listings
  end rather than resume.
- **Migration**: `test_migrations_round_trip` and
  `test_migrations_match_models` stay green; existing listings map to the store
  platform with `is_active` unchanged in meaning; `is_active` is generated.
- **Shop boundary**: the public catalogue never exposes cost, storage location
  or non-store listings; the bundle-isolation check stays green.
- **Before live**: each phase is applied to a scratch copy of `ccwebdb` first,
  with counts shown and the owner's approval, backup first (the database is
  the record: `docs/data-import-plan.md`, Amendment K). The five demo listings and their items
  (CC-007657..CC-007661) are removed at phase 1, after the owner confirms.

## Phases

Each phase is merged and applied on its own.

0. **Purchase-source clean-up** (data pass, no schema): merge
   `builionsharks.com`/`bullionshark.com`, `usming.gov`/`usmint.gov`,
   `hibid.co`/`hibid.com` if they are the same business, resolve the vendor
   named `.`, and set `vendor_kind` on all 21. Dry run with counts first.
1. **Platforms**: `sales_venue_kind`, `sales_venue`, the store platform,
   `listing.sales_venue_id`/`format`/`status`/generated `is_active`,
   `sales_order.sales_venue_id`; the Platforms page; demo listing removal.
2. **Offering**: `offer_claim`, `listing.paused_by_listing_id`,
   `offering_writes`, offer and end, the Listings
   page and item Offers panel, record-a-sale with `sales_fee_kind`,
   `sales_order_fee`, `sales_order_item_share` and platform buyers. Manage and
   the catalogue write endpoints are retired.
3. **Sales lots**: `sales_lot`, `sales_lot_item`, nullable
   `listing.inventory_item_id`, lot listings in the shop, the Lots page.
4. **Auctions**: `auction`, `auction_lot`, the `consigned` location kind, the
   Auctions page and settlement.

`paused_by_listing_id` is introduced in phase 2, the first phase that pauses a
store listing (offering a stored item on eBay pauses its store listing).

## Not in this design

- Importing platform sales reports (the Whatnot CSV, an eBay dump). A later
  phase, filling the same `sales_order`, `sales_order_fee` and customer rows.
- Realised-gain reporting. The per-item shares make it possible; it still
  needs the owner's accounting decisions (specific identification vs FIFO,
  how fees and outbound shipping count).
- Customers registering interest in an item held by an auction
  (`project-purpose.md`); needs email delivery.
- Pushing listings to platforms through their APIs. Offers here record what
  the owner listed by hand.
- Sales tax on outside sales: marketplaces collect it as facilitators, so it
  is not part of the order total recorded here. Store sales are unchanged.
- Price suggestions from a valuation feed (the CDN API was checked against
  `no-proprietary-reference-data`; not chosen yet).
