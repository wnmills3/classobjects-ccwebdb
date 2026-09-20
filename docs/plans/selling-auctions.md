# Selling, phase 4: auctions and settlement — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Consign lots to an auction, record what each lot did, and settle the
whole sale in one transaction — sold lots becoming orders with fees and shares,
unsold lots dissolving back into items that return to the shop at their old
price.

**Architecture:** An `auction` groups `auction_lot` rows, each detailing one
auction-format listing of one sales lot. The auction owns only its own
transitions; every listing, claim and lot write goes through
`offering_writes`, and every order, fee and share through
`sales_writes.record_sale`. Settlement is therefore an orchestration — a loop
over lots calling two existing modules — not a third implementation of selling.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL 16,
pytest; React + Vite for the console.

**Spec:** `docs/specs/selling-design.md` (revised 2026-09-20). Read `auction`
and `auction_lot`, *Consignment custody*, the **Settle** row of *How things
move*, and *Console* item 5.

**Depends on:** `docs/plans/selling-record-a-sale.md` and
`docs/plans/selling-sales-lots.md`, both merged. Settlement is defined in the
spec as "one order per buyer holding their lots, fees, shares; **as Record a
sale**", and every auction lot is a sales lot.

## Global Constraints

Every task's requirements implicitly include this section.

- **Branch:** `feat/auctions`, cut from `main` after phase 3 merges.
- **The gate:** `scripts\ccweb_check.cmd` exits zero before every commit.
  Never pipe it.
- **One pytest session at a time**, against `ccwebdb_test`.
- **Money is `Decimal`.** Hammer prices, fees and shares all reconcile to the
  cent or the settlement is wrong.
- **Docstrings and annotations enforced**, tests included.
- **Writers stay single:** `offering_writes` for listings, claims and lots;
  `sales_writes` for orders, fees and shares; `lifecycle_writes` for status and
  location. `app/auctions.py` writes only `auction` and `auction_lot`.
- **Settlement is one transaction.** A half-settled auction is unrecoverable
  by hand: some coins sold, some still claimed, some orders written.
- **Write tool for new files, Edit for changes.** No heredocs. cmd/batch only.
- **After this branch merges, the owner migrates live** — all three phases at
  once, backup first.

---

### Task 1: Schema — auctions, auction lots, and consigned custody

**Files:**
- Create: `backend/app/models/auctions.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/<generated>_auctions.py`
- Test: `backend/tests/test_auction_schema.py`

**Interfaces:**
- Produces: `Auction`, `AuctionLot`, `AuctionStatus`, `AuctionLotResult`; a
  `consigned` row in `storage_location_kind`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_lot_number_is_unique_within_its_auction(db, auction) -> None:
    """Two lot 14s in one sale is an unresolvable record."""


def test_the_same_lot_number_in_two_auctions_is_fine(db, auction, other_auction) -> None:
    """Lot numbers restart every sale."""


def test_one_auction_lot_per_listing(db, auction, lot_listing) -> None:
    """`listing_id` is unique: an auction lot *is* the detail of one listing."""


def test_consigned_is_a_storage_location_kind(db) -> None:
    """Custody at an auction house is tracked as a location, per the spec."""
    assert db.scalar(
        select(StorageLocationKind).where(StorageLocationKind.code == "consigned")
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest backend/tests/test_auction_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'Auction'`.

- [ ] **Step 3: Add the models**

```python
class AuctionStatus(enum.StrEnum):
    """An auction's life. `consigned` applies only to auction houses.

    A house takes physical custody before the sale, which the owner has to be
    able to see; an eBay or Whatnot auction never leaves the premises, so it
    goes straight from `scheduled` to `closed`.
    """

    draft = "draft"
    scheduled = "scheduled"
    consigned = "consigned"
    closed = "closed"
    settled = "settled"
    cancelled = "cancelled"


class AuctionLotResult(enum.StrEnum):
    """What one lot did, entered after the sale closes."""

    sold = "sold"
    unsold = "unsold"
    withdrawn = "withdrawn"
```

`Auction`: `id`, `sales_venue_id` (FK, not null), `title`, `external_id` (the
sale number), `starts_at`, `ends_at`, `status`, `consigned_on`, `notes`,
`version`, timestamps.

`AuctionLot`: `id`, `auction_id` (FK, not null), `listing_id` (FK, **unique**),
`lot_number` (text, unique within the auction), `reserve`, `result` (nullable
until settled), `hammer_price`, `buyer_customer_id`.

Seed the `consigned` storage-location kind in the migration, as phase 1 seeded
`sales_venue_kind`.

- [ ] **Step 4: Write the migration**

Generate, then correct:

- **Name every FK, index and constraint.**
- **`downgrade()` must `DROP TYPE IF EXISTS auction_status, auction_lot_result`**
  — autogenerate never drops enum types, and the next `upgrade` then fails.
- **Check the drop order:** `auction_lot` before `auction`.
- The `consigned` seed row must be removed in `downgrade()` too, or a round
  trip leaves it behind.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_auction_schema.py backend/tests/test_migrations.py -v`
Expected: PASS, round trip included.

- [ ] **Step 6: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/models backend/alembic/versions backend/tests
git commit -m "Add auctions, auction lots and consigned custody"
```

---

### Task 2: Auction transitions

**Files:**
- Create: `backend/app/auctions.py`
- Test: `backend/tests/test_auctions.py`

**Interfaces:**
- Produces: `AuctionRefused(Exception)`; `add_lot(db, auction, lot, *,
  lot_number, reserve, price) -> AuctionLot`; `remove_lot(db, auction_lot)`;
  `schedule(db, auction)`; `consign(db, auction, *, on_date)`;
  `close(db, auction)`; `cancel(db, auction)`.

- [ ] **Step 1: Write the failing test**

```python
def test_adding_a_lot_offers_it_with_auction_format(db, auction, assembled_lot) -> None:
    """Same refusals and pausing as any offer -- it *is* an offer."""
    auction_lot = add_lot(db, auction, assembled_lot, lot_number="14", reserve=None, price=Decimal("50.00"))
    assert auction_lot.listing.format is ListingFormat.auction
    assert auction_lot.listing.status is ListingStatus.active


def test_a_single_item_becomes_a_lot_of_one(db, auction, received_item) -> None:
    """The spec: a single item offered in an auction is a sales lot of one."""


def test_removing_a_lot_ends_its_listing_and_dissolves_it(db, auction_lot) -> None:
    """As End: paused store listings resume, members go back to held."""


def test_cancelling_removes_every_lot(db, auction_with_three_lots) -> None:
    """Nothing is left claimed by a sale that is not happening."""


def test_consigning_moves_every_item_to_the_house(db, house_auction) -> None:
    """Through `lifecycle_writes.set_location`, so history stays complete."""
    consign(db, house_auction, on_date=date(2026, 10, 1))
    for item in items_of(house_auction):
        assert item.storage_location.kind.code == "consigned"
        assert location_history(db, item)[-1].location.institution == "Heritage"


def test_consigning_a_marketplace_auction_is_refused(db, ebay_auction) -> None:
    """Nothing leaves the premises for an eBay auction."""
    with pytest.raises(AuctionRefused, match="auction house"):
        consign(db, ebay_auction, on_date=date(2026, 10, 1))


def test_lots_cannot_be_added_after_closing(db, closed_auction, assembled_lot) -> None:
    """The sale has happened."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_auctions.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write the module**

Each transition validates the current status first and refuses with a message
naming what is in the way. `add_lot` calls `offering_writes.offer(lot=...)`
with `listing_format=ListingFormat.auction` and `price` as the starting bid (0
if none); `remove_lot` and `cancel` call `offering_writes.end_offer`. The
module writes `auction` and `auction_lot` and nothing else.

`consign` moves every member item through `lifecycle_writes.set_location` to a
`consigned` location for that house, creating it on first use with the
platform's name as `institution`. **Only `lifecycle_writes` may write
`storage_location_id`** — assigning it anywhere else silently stops item
history being true.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_auctions.py -v`
Expected: PASS.

- [ ] **Step 5: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/auctions.py backend/tests/test_auctions.py
git commit -m "Consign lots to an auction, and take them back"
```

---

### Task 3: Settlement

The largest single piece of this branch. One transaction; either all of it
happens or none of it does.

**Files:**
- Modify: `backend/app/auctions.py` (`settle`, `SettlementLine`)
- Test: `backend/tests/test_auction_settlement.py`

**Interfaces:**
- Produces:

```python
@dataclass(frozen=True)
class SettlementLine:
    """One lot's outcome, as the settlement grid collected it."""

    auction_lot_id: int
    result: AuctionLotResult
    hammer_price: Decimal | None = None
    buyer_username: str | None = None


def settle(
    db: Session,
    auction: Auction,
    lines: Sequence[SettlementLine],
    fees: Mapping[str, Sequence[FeeLine]],
    *,
    settled_by: User,
    returned_to_location_id: int | None = None,
) -> list[SalesOrder]:
```

`fees` is keyed by buyer username (`None` for the undisclosed buyer), because
an auction house bills per buyer order, not per lot.

- [ ] **Step 1: Write the failing test**

```python
def test_settling_creates_one_order_per_buyer(db, closed_auction, admin_user) -> None:
    """Two lots to one buyer is one order with two lines, not two orders."""
    orders = settle(db, closed_auction, lines=[...], fees={...}, settled_by=admin_user)
    assert len(orders) == 2  # two distinct buyers across four lots


def test_an_unsold_lot_resumes_its_members_store_listings_at_the_old_price(
    db, closed_auction_with_stored_members, admin_user
) -> None:
    """The coin goes back in the shop exactly as it was, not repriced."""
    before = store_listing.price
    settle(db, closed_auction_with_stored_members, ...)
    assert store_listing.status is ListingStatus.active
    assert store_listing.price == before


def test_a_sold_lot_s_paused_store_listings_end_rather_than_resume(db, closed_auction, admin_user) -> None:
    """The coin is gone; resuming would offer something that no longer exists."""


def test_shares_of_a_lot_sum_to_its_hammer_price(db, closed_auction, admin_user) -> None:
    """Three coins at $100.00, weighted by cost: the cents still add up."""


def test_fees_are_divided_the_same_way_as_the_price(db, closed_auction, admin_user) -> None:


def test_settlement_is_refused_while_any_lot_lacks_a_result(db, closed_auction, admin_user) -> None:
    with pytest.raises(AuctionRefused, match="result"):
        settle(db, closed_auction, lines=[incomplete], fees={}, settled_by=admin_user)


def test_settlement_is_refused_when_a_sold_lot_has_no_price_or_buyer(db, closed_auction, admin_user) -> None:


def test_a_negative_fee_refuses_the_whole_settlement(db, closed_auction, admin_user) -> None:


def test_a_failure_part_way_through_leaves_nothing_written(db, closed_auction, admin_user, monkeypatch) -> None:
    """The guarantee that matters most: no half-settled auction.

    Force `record_sale` to raise on the second buyer and assert that the
    first buyer's order, the first lot's claims and the first lot's status
    are all unchanged afterwards.
    """
    ...
    assert db.scalar(select(func.count()).select_from(SalesOrder)) == orders_before
    assert closed_auction.status is AuctionStatus.closed


def test_returned_items_move_to_the_chosen_location(db, closed_house_auction, admin_user, drawer) -> None:
    """For an auction house, the owner picks where unsold coins come back to."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_auction_settlement.py -v`
Expected: FAIL — `settle` does not exist.

- [ ] **Step 3: Write `settle`**

Order of operations, and each one matters:

1. **Validate everything first.** Every lot has a result; every `sold` lot has
   a hammer price and a buyer; no fee is negative; the auction is `closed`.
   Refuse before writing anything.
2. **Lock** every affected item `FOR UPDATE` in **item id order** across the
   whole auction — not per lot. Two lots sharing no items still share the
   auction row, and locking per lot in lot order is how two concurrent settles
   deadlock.
3. **Sold lots, grouped by buyer:** one `sales_writes.record_sale` per buyer.
   The spec's status rule applies — an auction house sale is `delivered`, a
   marketplace or live auction `paid`.
4. **Unsold and withdrawn lots:** `offering_writes.end_offer` (not `sold`), so
   the lot dissolves, paused store listings resume at their old price, and
   members with no remaining claim go to `held`. For an auction house, move
   them to `returned_to_location_id` through `lifecycle_writes.set_location`.
5. **Auction status → `settled`.**

`record_sale` takes one listing; a buyer taking two lots means two calls and
therefore two orders unless the orders are merged. Read the spec: it says one
order per buyer. **If `record_sale` cannot yet put two listings on one order,
widen it here rather than writing a second order creator** — add a
`record_sale_lines` entry point taking several listings, and have the
single-listing `record_sale` call it. Keep one implementation.

- [ ] **Step 4: Prove the all-or-nothing guarantee by mutation**

Remove the transaction boundary (commit after each buyer) and confirm
`test_a_failure_part_way_through_leaves_nothing_written` goes **red**. Restore
it and confirm green. A rollback guarantee that no test can break is not a
guarantee.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_auction_settlement.py -v`
Expected: PASS.

- [ ] **Step 6: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/auctions.py backend/tests/test_auction_settlement.py
git commit -m "Settle an auction in one transaction"
```

---

### Task 4: Two settles of one auction cannot both run

**Files:**
- Modify: `backend/tests/test_offer_races.py` or a new
  `backend/tests/test_settlement_race.py`

- [ ] **Step 1: Write the race**

Real threads behind a barrier, a session each — `TestClient` serialises
requests and cannot see this. Two settles of one closed auction: exactly one
succeeds, the other gets a 409, and **no coin is sold twice**.

- [ ] **Step 2: Mutation-prove it**

Remove the `FOR UPDATE` on the auction row (or on the items) and confirm the
test goes red; restore and confirm green. Record in the docstring which
mutation it survives.

- [ ] **Step 3: Run, gate, commit**

```
python -m pytest backend/tests/test_settlement_race.py -v
scripts\ccweb_check.cmd
git add backend/tests
git commit -m "Prove two settlements of one auction cannot both run"
```

---

### Task 5: The auctions API

**Files:**
- Create: `backend/app/routers/auctions.py`
- Modify: `backend/app/main.py`, `backend/app/schemas.py`
- Test: `backend/tests/test_auctions_api.py`

**Interfaces:**
- `GET/POST /api/auctions`, `PATCH /api/auctions/{id}`
- `POST /api/auctions/{id}/lots`, `DELETE /api/auctions/{id}/lots/{lot_id}`,
  `PATCH /api/auctions/{id}/lots/{lot_id}` (renumber, reserve)
- `POST /api/auctions/{id}/{schedule|consign|close|cancel|settle}`

- [ ] **Step 1: Write the failing tests**

Admin-only (401 anonymous); `AuctionRefused` → 409 naming the obstacle;
unknown codes → 422; `StaleDataError` → 409; settle refusals listing **every**
problem lot, not just the first — the console shows a grid and fixing one
problem at a time is miserable; money as strings.

- [ ] **Step 2: Run to verify they fail, then write the router**

Follow `routers/offers.py`. Capture plain ids before the `try`; keep implicit
autoflushes inside it.

- [ ] **Step 3: Run the tests, gate, commit**

```
python -m pytest backend/tests/test_auctions_api.py -v
scripts\ccweb_check.cmd
git add backend/app/routers/auctions.py backend/app/main.py backend/app/schemas.py backend/tests/test_auctions_api.py
git commit -m "Serve auctions and settlement over the API"
```

---

### Task 6: The Auctions page

**Files:**
- Modify: `frontend/src/owner/api.js`
- Create: `frontend/src/owner/pages/Auctions.jsx`, `Auctions.test.jsx`
- Create: `frontend/src/owner/pages/SettlementGrid.jsx`, `SettlementGrid.test.jsx`
- Modify: the console menu (Selling group)

- [ ] **Step 1: Write the failing tests**

List by platform, date, status and lot count. Detail: platform, sale number,
dates; Schedule, Mark consigned, Close, Cancel; a lot table with editable lot
numbers and **Add lot** (an assembling lot, or a single item as a lot of one).

The settlement grid: per lot result, hammer price and buyer; fees per buyer
order; gross, fees, net and cost-basis totals; for an auction house, the
location for returned items. **Settle** confirms and applies all of it.

Render with `strict: true`. Assert request bodies in `owner/api.test.js`, not
through a wholesale `api` mock. A load failure must show an error, not blank
the page.

- [ ] **Step 2: Run to verify they fail, then build the pages**

Totals are computed for display only; send the figures the owner typed, as
strings. Do not let JavaScript arithmetic produce a stored number.

- [ ] **Step 3: Run the tests, gate, commit**

```
npm --prefix frontend test
scripts\ccweb_check.cmd
git add frontend/src/owner
git commit -m "Run an auction and settle it from the console"
```

---

### Task 7: Documentation, and the limits worth naming

- [ ] **Step 1: Mark phase 4 built** in `docs/specs/selling-design.md`.
- [ ] **Step 2: Document the `listing_status_history` limit** at the point it
      now matters: settlement makes a listing's ending part of the financial
      record, and relisting clears `ended_at`, so the previous ending's
      timestamp is lost. Say so in `Listing`'s docstring and in the spec's
      out-of-scope note. Do **not** build the table — that is a separate
      decision.
- [ ] **Step 3: Write the live-apply procedure** in
      `docs/system-administration.md`: backup and **verify** it (`--list` is
      not evidence — a copy once listed at a plausible 13 MB and held zero
      items), `alembic upgrade head`, then the counts to check.
- [ ] **Step 4: Re-read every docstring in this branch** against the final code.
- [ ] **Step 5: Gate and commit.**

---

## Verification before handing back

- [ ] `scripts\ccweb_check.cmd` exits zero.
- [ ] `test_migrations_round_trip` and `test_migrations_match_models` pass.
- [ ] Every race and the all-or-nothing settlement guarantee is
      mutation-proven, with the mutation named in each docstring.
- [ ] **Live is still untouched:** `ccwebdb`'s `alembic_version` reads
      `e7c3a5b19d84`. The migration happens after this merges, on the owner's
      word, with a verified backup first.
- [ ] `git merge-base --is-ancestor main HEAD` succeeds.

---

## After all three phases merge: the live migration

Not a task in this plan — it is the owner's call and their command. The shape,
for when they ask:

1. **Back up and verify.** `pg_dump` to `C:\Users\wnmil\dev\ccwebdb-backups\`,
   then verify the copy actually holds the collection. `--list` is not
   evidence: a copy once listed at a plausible 13 MB and held zero inventory
   items.
2. **Rehearse on a scratch copy first**, which the spec requires
   (*Testing*, "Before live"). Restore the verified backup into a scratch
   database, run `alembic upgrade head` against **that**, and show the owner
   the counts before and after. Three migrations applied at once to a
   database holding the whole collection is exactly the case worth rehearsing,
   and a scratch run costs minutes. Only then touch `ccwebdb`.
3. **Apply:** `alembic upgrade head` — three revisions in one go.
3. **Check:** `alembic_version` reads the phase-4 head; 7,656 items and
   $536,118.82 cost basis unchanged; 0 lots, 0 auctions, 0 fees, 0 shares;
   the shop catalogue and the console both answer.
4. **Restart the servers** — `ccweb_startup.cmd` runs uvicorn without
   `--reload`, so merged backend code is not served until they restart.
