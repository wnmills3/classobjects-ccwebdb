# Selling: platforms, offers, sales lots and auctions

Design. Status: **agreed with the owner 2026-09-17**; phases 0-2 (offering)
built, phase 2R (record a sale) **built 2026-09-21**, phase 3 (sales lots)
**built 2026-09-21**. Phase 4 (auctions) follows, and **live is not migrated
until it is merged too**.
**Revised 2026-09-20** with the owner: the phase-2 remainder, phase 3 and phase
4 were scoped together and four points where this document and the built code
had drifted apart were reconciled. See *Revision, 2026-09-20* below.

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

Added 2026-09-20, scoping the rest of the work:

| Question | Decision |
|---|---|
| Order of the remaining work | **Record-a-sale, then sales lots, then auctions.** Settlement is defined in terms of record-a-sale, so any other order writes it twice. |
| When live is migrated | **Once, after all three are merged.** One migration per phase for testing; one backup and one apply. |
| Warning on a lot's pieces after it sells | **Through `sales_order_item_share`**, which names every item on every order line permanently. Closes the gap `sale_state.py` defers to phase 3. |

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

Seeded by an `INSERT` in the migration that creates the table, as phase 1 did
for `sales_venue_kind` in `d6a1f3b8c402`. These are small, closed vocabularies
that the product defines rather than the installation, and seeding them with
the schema keeps a freshly migrated database usable without a separate seed
step.

(This paragraph originally said the vocabularies were seeded from JSON in
`backend/data/reference/`, per `docs/database-design.md` section 12. The
implementation did not do that, and the migration `INSERT` is the better fit
here: the JSON files carry numismatic reference *data* with a `_comment`
naming its source, which these vocabularies are not. Corrected 2026-09-20 to
describe what phase 1 built.)

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

  **Every sold line gets shares, including a store sale of a single item** --
  one row carrying the whole line. A share of one looks redundant, and is
  deliberate: it makes `sales_order_item_share` the single, permanent answer to
  "which items did this order carry", with one query shape rather than two and
  an `IS NULL` branch between them. `app.sale_state` depends on that (below),
  and so will realised-gain reporting. Decided with the owner 2026-09-20.
- **The sale snapshot gains a second shape** (`app/sale_snapshot.py`).
  `sales_order_item.item_snapshot` keeps the item and the listing as they
  were when the line was made, and a lot line has no single item to keep. So
  `snapshot_version` moves from 1 to 2, and a reader must branch on the
  shape it finds rather than assume:

  | Version | Keys it may carry |
  |---|---|
  | **1** | always `item` and `listing`; never `lot` or `items` |
  | **2** | `listing`, plus **either** `item` (an item listing -- byte for byte the version-1 shape) **or** `lot` and `items` (a lot listing) |

  Never both, in either version. `lot` is id, title and description;
  `items` is the same per-item detail as `item`, one entry per member, in
  item id order. A snapshot is never rewritten, so version 1 copies stay in
  the database for ever and stay readable: ask for `lot` and branch on
  whether it is there. The lot half is the whole record of which coins the
  group held -- selling a lot releases every membership, so a reader that
  went back to `sales_lot_item` would find nothing.
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
| **Offer** an item or lot (platform, format, price, public text, external id) | Refused if any affected item has an active claim on a **non-store** listing ("CC-001234 is active on eBay, listing #12: end it first"), is not `received`, is split, has been deleted, or the platform is retired. An active **store** listing of an affected item is **paused** with `paused_by_listing_id` set -- except that offering an *item* on the store while it is already active on the store is simply refused. The new listing and its claims are `active`; items become `listed`; a lot becomes `offered`. |
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
- **Invariants after every write in the suite**, all three **built**, all
  three run by `tests/conftest.py`'s autouse `_claim_invariant` fixture:

  | Rule | Check | Exception it raises | Built |
  |---|---|---|---|
  | A claim's state equals its listing's status | `check_claim_invariant` | `ClaimInvariantViolation` | phase 2R |
  | An open lot membership implies its lot is `assembling` or `offered` | `check_lot_invariant` | `LotInvariantViolation` | **phase 3** |
  | A `HELD_BY` claim implies its item is `listed` or already sold away | `check_disposition_invariant` | `DispositionInvariantViolation` | **phase 3** |

  **Three separate checks with three separate exception types, and the
  separation is load-bearing.** Four tests carry the
  `claim_invariant_waiver` marker, which absorbs *any*
  `ClaimInvariantViolation`. Had the two new rules raised that same type --
  or had they been folded into `check_claim_invariant`, as the Revision note
  below originally said phase 3 would do -- those four tests would have been
  silently exempted from the new rules as well, which is the exact accident
  the checks exist to catch. So the waiver is consulted only by the claim
  half, the two new checks run before it and are never waived, and each
  `xfail(strict=True, raises=...)` proof in `tests/test_claim_invariant.py`
  narrows on its own type so no proof can pass on another's failure.

  Each rule is **one direction only**, deliberately. `check_lot_invariant`
  does not require an `assembling` lot to have members -- a lot is created
  empty and assembled a coin at a time. `check_disposition_invariant` does
  not require a `listed` item to have a claim (`build_listing` makes dozens
  of claimless listed items), and it allows `offering_writes.SOLD_AWAY`,
  because a shop checkout that takes the last unit sets the item to `sold`
  while its store listing stays active with an active claim.

  **What they found on their first run across the whole suite.** A real bug
  in `offering_writes.end_offer`: when an ending decided that nothing
  offered an item any more, it moved the item back to `held` but left behind
  a stray claim still reading `active` or `paused` -- for ever, on an item it
  had just decided nothing offers. Worth recording twice over, because that
  shape already violated the *pre-existing* claim invariant's own rule that
  an ended listing carries released claims. The new checks found something
  that was wrong under a rule that predated them.
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
   **Delivered without record-a-sale** -- see phase 2R.
2R. **Record a sale** (the phase-2 remainder, scoped 2026-09-20): **built
   2026-09-21.**
   `sales_fee_kind`, `sales_order_fee`, `sales_order_item_share`,
   `customer.sales_venue_id`/`venue_username` and the undisclosed buyer,
   `app/sales_writes.py`, `POST /api/listings/{id}/sale`, **Record sale...**
   on the Listings page. Phase 2 merged without any of it: `end_offer`'s
   `sold=True` branch has no caller on `main`.
3. **Sales lots**: **built 2026-09-21.** `sales_lot`, `sales_lot_item`,
   nullable `listing.inventory_item_id` with `ck_listing_item_xor_lot` and
   `ck_listing_lot_quantity_one`, `app/lot_writes.py`, lot listings offered
   and ended through `offering_writes`, `sale_snapshot` version 2, lot
   listings in the shop and at checkout, `/api/sales-lots`, and the Lots page
   (`/owner/lots`). Carries one **known defect** and one **known coverage
   hole**, both recorded below; neither is fixed on this phase's branch.
4. **Auctions**: `auction`, `auction_lot`, the `consigned` location kind, the
   Auctions page and settlement.

`paused_by_listing_id` is introduced in phase 2, the first phase that pauses a
store listing (offering a stored item on eBay pauses its store listing).

**Phase 2R comes before phase 3 and cannot be skipped.** Settlement in phase 4
is defined in this document as "one order per buyer holding their lots, fees,
shares; as Record a sale". Building auctions first would mean writing
settlement against a function that does not exist, and then writing it again.

## Revision, 2026-09-20

Scoping the remaining phases with the owner turned up four places where this
document and the code that implements it had drifted apart. All four are
reconciled as part of phases 2R-4 rather than left to disagree.

1. **Vocabulary seeding.** The document said JSON; phase 1 used a migration
   `INSERT`. The code is right and the document is corrected (*Reference
   vocabularies*, above).
2. **`app/seed.py` creates a listing with no `offer_claim`**, the one
   sanctioned exception to "every listing has a claim". `app.sale_state`
   silently compensates by asking both the claim and `listing.inventory_item_id`.
   Once a lot listing exists that compensation is load-bearing for a dev-only
   bug, so `seed.py` is fixed in phase 2R to go through `offering_writes.offer`.
3. **The suite-wide invariant test does not exist.** *Testing*, above, requires
   claim state to equal listing status after every write in the suite; it was
   never built, and it would have caught (2) by itself. Built in phase 2R.

   (Corrected 2026-09-21. This item said the claim invariant would be
   "extended in phase 3 to cover lot membership against lot status". It was
   not, and could not have been. Phase 3 built **two separate checks, each
   with its own exception type** -- `check_lot_invariant` raising
   `LotInvariantViolation` and `check_disposition_invariant` raising
   `DispositionInvariantViolation` -- because `claim_invariant_waiver`
   absorbs any `ClaimInvariantViolation`, so extending the claim check, or
   reusing its type, would have exempted the four waived tests from the new
   rules too. *Testing*, above, is the record of what was built.)
4. **`app.sale_state`'s order half.** It reaches an item only through
   `listing.inventory_item_id`, so a lot's members are invisible to it, and
   a sold lot's members are invisible through the claim too -- the claim is
   `released` at sale. The module's own comment defers the rule to phase 3.
   **Decided: the order half joins through `sales_order_item_share`**, which
   names every item on every line, lot or single, and is permanent. This is
   why a single-item store sale also gets a share row (*Sales*, above).

**Where record-a-sale lives.** *How things move*, above, says a sale is
recorded "via `order_writes`". That stays true and is made exact here:
`app/sales_writes.py` is a new module that **orchestrates** a sale and owns the
one fact nothing else writes, `sales_order_fee`, plus the **fee half** of
`sales_order_item_share`. The share row itself, and its `amount`, belong to
`order_writes._sync_shares`, which writes one for every line `place_order`
creates -- which is what gives a plain store checkout line a share too, and is
why `sale_state` can reach an order's items the same way whatever the order
came from. `sales_writes` fills in `fee_amount` on rows that already exist and
never constructs one. (Corrected 2026-09-21: this section previously gave the
whole share table to `sales_writes`. The code's split is the better one and is
what the document now describes.)

It creates the order and its snapshot through `order_writes.place_order`, and
ends the listing through `offering_writes.end_offer(sold=True)`.
`order_writes` remains the only writer of orders and `offering_writes` the only
writer of listings and claims; `sales_writes` adds no second path to either.
It has one entry point, `record_sale`, with **one production caller today**:
the Listings page's Record sale (`POST /api/listings/{id}/sale`). Store
checkout does **not** go through it -- it calls `order_writes.place_order`
directly, because a shop order has no fees to record and no listing to end.
The second caller the single entry point exists for is phase-4 auction
settlement, which is what keeps settlement from growing its own copy of fees
and shares. (Corrected 2026-09-21: this section previously named three
callers, including store checkout.)

**Open decision: record-a-sale on a store listing.** `_STATUS_BY_VENUE_KIND`
maps `own_store -> paid`, so `record_sale` will accept a store listing, and
until 2026-09-21 the Listings page offered **Record sale...** on store rows.
That is a second way to sell a shop item -- past the cart and past checkout --
and it mints an "Undisclosed buyer (store)" when the username is left blank.
It was never decided; it is what the dictionary key happens to allow. The
button is hidden on store rows for now, and the two options are:

- **Allow it**, as the way to enter an in-person or bourse-table sale of
  something that was also listed in the shop: the sale really did happen off
  the web store, and the alternative is an administrator placing an order for
  a walk-in customer through the Orders page.
- **Refuse it**, by dropping the `own_store` key so `record_sale` raises "no
  default order status for sales venue kind 'own_store'": a store item sells
  through checkout, and an in-person sale is entered as an order on behalf of
  a customer, which already exists.

The owner decides. Nothing in phases 3 or 4 depends on the answer.

**Delivery.** Three branches, merged in order: the phase-2 remainder, sales
lots, auctions. One migration each, so `test_migrations_round_trip` exercises
them separately, but **live is not migrated until all three are merged** -- one
backup, one `alembic upgrade head`, one verification pass.

**Out of scope, added 2026-09-20.** Relisting clears `ended_at` and there is no
listing history table, so a re-offer loses the previous ending's timestamp. A
`listing_status_history` mirroring `ItemStatusHistory` is the fix. Auction
settlement is the first thing that makes a listing's ending part of the
financial record, so phase 4 documents the limit where it starts to matter
rather than quietly inheriting it. Building the table is a separate decision.

## The lock order between `order_writes` and `offering_writes`

**Was a known defect. Fixed 2026-09-21** on `fix/lock-order`, in the
direction `docs/specs/lock-order-design.md` recommended and the owner chose.
Kept here as a record rather than deleted, because the shape it describes is
the one a future writer would recreate.

**The defect.** The two money-path modules took their two kinds of row in
opposite orders. `offering_writes` (`offer`, `end_offer`) took the lot row,
then the items ascending, then the listings ascending. `order_writes`
(`place_order`, `revise_order`, `return_stock`) took the **listings** first,
in `_lock_listings`, and reached the items afterwards -- through
`_after_stock_change`, and for a lot through `end_offer` inside
`_settle_sold_lots`. So a shopper checking out a lot while an administrator
offered one of its coins somewhere else could end up with each transaction
holding what the other waited for, and Postgres broke the tie by aborting
one: an **HTTP 500** instead of the clean refusal either path was built to
give, with either party the victim. Nothing was ever left half-written -- a
deadlock abort rolls the whole transaction back -- so the damage was a bad
error and a lost request, not corrupt data. It was **pre-existing on
`main`**; sales lots widened the exposure (a lot sale always crosses zero and
touches N member rows) without creating it.

**The fix: one owner for the acquisition order.** Not four call sites
brought into agreement by hand -- four hand-written sequences are four
chances to drift, which is the whole reason this design already has one
writer per fact. The canonical order is `offering_writes`', because it is
the one that carries a guarantee: that module's listing set is *derived*
from `offer_claim`, it is the only writer of claims, and the derivation is
only safe with the items held first, which is what claim uniqueness rests
on. `order_writes` took listings first for no reason but that `place_order`
is handed `Line(listing_id=...)`.

- **`offering_writes._acquire`** is the one place the order exists: lot
  rows, then items, then listings, each kind in one ascending statement.
- **`offering_writes.lock_for_sale`** is its public door and accepts either
  entry point -- items (what `offer` has) or listings (what checkout, a
  revision, a stock return, `record_sale` and receiving have), resolving
  listing → lot → member itself. `_lock_listings` keeps its own
  `populate_existing` and `selectinload(Listing.sales_venue)` behaviour,
  which `lock_for_sale` provides, and keeps its 404 for an unknown listing
  id.
- **Eight writers come through it**, enumerated in
  `docs/specs/lock-order-design.md` rather than asserted as "every writer" --
  that phrasing was used first and was wrong twice.
  `routers.inventory.receive_items` wrote its `inventory_item` status rows
  and flushed them before calling `end_offer`, so it took items before lots;
  an administrator marking a lot member `missing` while a shopper checked
  that lot out was the pair. Closed in fix round 1 by taking the pass before
  the first write. And `splitting.split_item` was filed as single-kind when
  it holds the parent item row across an `end_offer` call; it is safe, and
  round 2 made it safe by construction rather than by accident.

**Two related rules, both found in review and both about a lot describing
something untrue.** `offering_writes._end` leaves an already-`ended` listing
alone, so a second `POST /api/listings/{id}/end` cannot rewrite a `sold` lot
to `dissolved` -- one admin call, no concurrency needed. And
`splitting.split_item` refuses a coin that is an open member of an *offered*
lot, unconditionally, the sibling of the sold-inside-a-lot refusal: a lot
must not go on offering a coin that no longer exists as a whole item.
- **A confirming re-read**, because the member set has to be *read* before
  it can be locked. It holds the set frozen only while the listing is still
  on offer: an offered lot's membership cannot otherwise change
  (`lot_writes._refuse_unless_assembling`, `_refuse_grouped`), but
  `offering_writes._end` releases every membership when the lot is sold or
  dissolved, and the loser of two checkouts racing one lot sees exactly
  that. A change while the listing is still live is `LockSetChanged`, a 500
  by the same reasoning as `ShareMissing`; the offer's own ending is the
  ordinary case each caller already refuses cleanly.
- **Ordering only: no schema change and no migration.**

**The coverage hole is closed.** Nothing used to race `order_writes` against
`offering_writes` on a lot, because the only reachable cross-writer shape
was the deadlock and such a test would have been permanently red. It is now
`test_buying_a_lot_races_offering_one_of_its_coins`
(`tests/test_offer_races.py`): a checkout of a lot against an offer of one of
its coins, two real connections behind a barrier, asserting one winner, one
clean refusal and **no** `OperationalError` in either thread. Its companion
`test_a_checkout_takes_the_three_kinds_of_row_in_the_canonical_order`
(`tests/test_offering_writes.py`) measures the sequence of kinds on one
connection, which is what catches an inversion of the shared helper -- the
threaded test cannot, because inverting the single owner moves both writers
at once and they still agree.

**A false conflict went with it.** `_after_stock_change` writes
`inventory_item.disposition`, which carries a version column and used to go
unlocked, so an unrelated concurrent edit to a coin lost the race and a
person was told to reload their revision or their cancellation. Those items
are now locked and re-read before anything is written
(`test_a_concurrently_edited_item_no_longer_refuses_a_revision` and
`..._a_cancellation`). The `except StaleDataError` clauses in
`order_writes.revise_order` and `routers.orders.update_order_status` are
deliberately left in place as defence in depth.

## Known limits

Small, deliberate, and recorded so they read as choices.

- **Cancelling an order that bought a lot does not restore the lot.** The
  lot was sold, so `offering_writes._end` marked it `sold` and released
  every membership; a dissolved or sold lot never comes back. Cancelling
  such an order is refused outright (`routers/orders._no_stock_to_return`),
  because the lot's listing has ended and there is nothing to return its
  stock to. The coins come back individually, by the owner's own correction,
  and are grouped again as a **new** lot.
- **Ending a lot's offer dissolves the lot.** There is no "withdraw the
  offer but keep the group". The confirmation dialog says so outright, and
  it is the **only** route to selling one of a lot's coins on its own, since
  `offering_writes._refuse_grouped` refuses offering a member on every
  venue, the web store included.
- **A lot listing's `piece_count` is the sum of its members', not the count
  of them.** The column means how many objects the entry represents, and a
  member may itself be a multi-piece row -- a roll, a mint set -- so
  `len(members)` would be a second wrong number, and 1 would be
  indistinguishable from a genuine single piece.
- **No equal-shares control in the console.** `record_sale` takes
  `equal_shares`, and the Record sale dialog always sends `false`, the
  spec's cost-weighted default. Choosing an equal division of a lot's money
  is not reachable from the console today; it has not been asked for, and
  adding the control would put a question in front of every outside sale.
- **The Orders page's greyed-out cancel does not match the server's rule
  exactly.** `routers/orders._no_stock_to_return` refuses a cancel when the
  order is unshipped *and* either its platform is not the store or one of its
  listings has ended. `Orders.jsx` greys the option out on "not the store and
  not shipped", which is now right for every outside order -- but a **store
  order that bought a lot** is still offered the choice and then refused,
  showing the 409 as an error on the page. Closing that half honestly needs
  `OrderOut` to carry whether a line's listing has ended, which is a schema
  change; the 409 is clear and lands on the page, so it waits. The server is
  right in both directions; only the hint is approximate.
- **`revise_order` cannot correct an outside sale's quantity downward.**
  Shrinking or removing a line whose listing has **ended** is refused
  (`order_writes.revise_order`), and `record_sale` ends the listing when it
  records the sale -- so a recorded sale's quantity can be raised but never
  lowered. The refusal is right: the old behaviour "worked" by handing stock
  back to a listing nobody can see, stranding the coins `sold` and
  un-offerable. But it removes a workflow and nothing replaces it; the
  remedy today is to cancel and re-record.
- **A lot's snapshot costs one `item_detail` per member, inside the
  checkout's lock window.** `sale_snapshot.take` calls `_detail` once per
  member, and `_detail` calls `routers.inventory.item_detail`
  (`order_writes._line`, reached from `place_order` after `_lock_listings`
  has taken the listing locks). Harmless for the two- or three-coin lots this
  is built for; a very large lot would hold those locks for hundreds of round
  trips while it builds the snapshot.
- **`GET /api/sales-lots` has no pagination and eager-loads every member of
  every lot.** `routers/lots.list_sales_lots` defaults to `status=all` and
  returns the whole table, each lot with its items. Fine at today's numbers
  and for the console's Lots page, whose useful default is "everything" --
  but the history list only ever grows, and `SalesLotListOut` is an object
  rather than a bare list precisely "so a page count can be added".

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
