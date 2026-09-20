# Selling, phase 3: sales lots — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group several items into a temporary **sales lot**, offer it as one
thing on any platform, and have it dissolve back into its items if it does not
sell.

**Architecture:** A `listing` currently names exactly one item. It learns to
name a lot instead — exactly one of the two, enforced by a check constraint —
and everything that reads a listing learns to ask "which items?" rather than
"which item?". `offering_writes` stays the only writer of listing status,
claims and lot state; a lot listing gets one `offer_claim` per member, which is
why the one-active-offer guarantee already lives on the claim rather than the
listing.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL 16,
pytest; React + Vite for the console.

**Spec:** `docs/specs/selling-design.md` (revised 2026-09-20). Read *Three kinds
of lot*, *`sales_lot` and `sales_lot_item`*, *How things move* and *Console*.

**Depends on:** `docs/plans/selling-record-a-sale.md` must be merged first.
This plan divides a lot's money among its members using
`sales_writes.record_sale` and `sales_order_item_share`, which that plan builds.

## Global Constraints

Every task's requirements implicitly include this section.

- **Branch:** `feat/sales-lots`, cut from `main` after phase 2R merges. Never
  commit to `main`; the owner says when to merge.
- **The gate:** `scripts\ccweb_check.cmd` exits zero before every commit. Never
  pipe it; gate on the exit code.
- **One pytest session at a time**, against `ccwebdb_test`.
- **Money is `Decimal`**, and shares must sum to their line exactly.
- **Docstrings and annotations enforced** (ruff `D`, `ANN`), tests included.
- **Do not migrate the live database.** Live is migrated only after phase 4
  merges too.
- **Writers stay single:** `offering_writes` alone writes `listing.status`,
  `offer_claim`, `sales_lot.status` and `sales_lot_item.released_at`.
- **Write tool for new files, Edit for changes.** No shell heredocs.
- **cmd/batch only.** No PowerShell.
- Console API calls in `frontend/src/owner/api.js` only.

---

### Task 1: Schema — lots, membership, and a listing that names one or the other

**Files:**
- Modify: `backend/app/models/sales.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/<generated>_sales_lots.py`
- Test: `backend/tests/test_sales_lot_schema.py`

**Interfaces:**
- Produces: `SalesLot`, `SalesLotItem`, `SalesLotStatus`;
  `Listing.sales_lot_id: Mapped[int | None]`;
  `Listing.inventory_item_id` becomes `Mapped[int | None]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_sales_lot_schema.py
"""The database guarantees behind a sales lot.

Each of these is a constraint rather than a rule in Python, because each one
is a rule two concurrent requests could otherwise both believe they satisfy.
"""

from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import InventoryItem, Listing, SalesLot, SalesLotItem, SalesVenue


def test_an_item_is_in_at_most_one_open_lot(
    db: Session, received_item: InventoryItem
) -> None:
    """Two open lots holding one coin would offer it twice."""
    first, second = SalesLot(title="A"), SalesLot(title="B")
    db.add_all([first, second])
    db.flush()
    db.add(SalesLotItem(sales_lot_id=first.id, inventory_item_id=received_item.id))
    db.flush()
    db.add(SalesLotItem(sales_lot_id=second.id, inventory_item_id=received_item.id))
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_released_membership_frees_the_item(
    db: Session, received_item: InventoryItem
) -> None:
    """A dissolved lot's members can be grouped again; history is kept."""
    first, second = SalesLot(title="A"), SalesLot(title="B")
    db.add_all([first, second])
    db.flush()
    db.add(
        SalesLotItem(
            sales_lot_id=first.id,
            inventory_item_id=received_item.id,
            released_at=utcnow(),
        )
    )
    db.flush()
    db.add(SalesLotItem(sales_lot_id=second.id, inventory_item_id=received_item.id))
    db.flush()  # must not raise


def test_a_listing_names_an_item_or_a_lot_but_not_both(
    db: Session, received_item: InventoryItem, ebay_venue: SalesVenue
) -> None:
    """Both would make "which items did this offer?" ambiguous."""
    lot = SalesLot(title="A")
    db.add(lot)
    db.flush()
    db.add(
        Listing(
            inventory_item_id=received_item.id,
            sales_lot_id=lot.id,
            sales_venue_id=ebay_venue.id,
            price=Decimal("10.00"),
            quantity_available=1,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_listing_names_at_least_one_of_them(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """Neither would be an offer of nothing."""
    db.add(
        Listing(
            sales_venue_id=ebay_venue.id,
            price=Decimal("10.00"),
            quantity_available=1,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_lot_listing_offers_exactly_one(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """A lot is a specific group of specific coins; there is only one of it."""
    lot = SalesLot(title="A")
    db.add(lot)
    db.flush()
    db.add(
        Listing(
            sales_lot_id=lot.id,
            sales_venue_id=ebay_venue.id,
            price=Decimal("10.00"),
            quantity_available=2,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest backend/tests/test_sales_lot_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'SalesLot'`.

- [ ] **Step 3: Add the models**

```python
class SalesLotStatus(enum.StrEnum):
    """Where a sales lot is in its short life."""

    assembling = "assembling"
    offered = "offered"
    sold = "sold"
    dissolved = "dissolved"


class SalesLot(TimestampMixin, Base):
    """A temporary grouping of items, offered and sold as one thing.

    Not an inventory item, deliberately: a lot that were an item would be
    counted in inventory and cost basis beside its own members, which is the
    problem split purchase lots already had to be excluded from every view to
    avoid.

    A lot is editable only while `assembling`. Once offered it is frozen --
    the buyer is looking at that exact group -- and when it sells or is
    withdrawn it ends, releasing its members. It never comes back: re-offering
    a dissolved lot starts a new one, so each lot is a faithful record of one
    group that was offered once.
    """

    __tablename__ = "sales_lot"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Public: what a buyer sees.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    status: Mapped[SalesLotStatus] = mapped_column(
        Enum(SalesLotStatus, name="sales_lot_status", native_enum=True),
        nullable=False,
        server_default=SalesLotStatus.assembling.value,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    members: Mapped[list[SalesLotItem]] = relationship(
        back_populates="lot", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )

    __mapper_args__ = {"version_id_col": version}


class SalesLotItem(Base):
    """One item's membership of one lot.

    `released_at` rather than deletion: which coins were in a lot that sold is
    part of the sale's record, and a dissolved lot is evidence of what was
    tried. The partial unique index is what stops an item being in two open
    lots at once.
    """

    __tablename__ = "sales_lot_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_lot_id: Mapped[int] = mapped_column(
        ForeignKey("sales_lot.id", ondelete="CASCADE"), index=True, nullable=False
    )
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    lot: Mapped[SalesLot] = relationship(back_populates="members")

    __table_args__ = (
        UniqueConstraint(
            "sales_lot_id", "inventory_item_id", name="uq_sales_lot_item_pair"
        ),
        Index(
            "uq_sales_lot_item_open",
            "inventory_item_id",
            unique=True,
            postgresql_where=text("released_at IS NULL"),
        ),
    )
```

Widen `Listing`: make `inventory_item_id` nullable, add `sales_lot_id`, and add
the two check constraints to `__table_args__`:

```python
        CheckConstraint(
            "(inventory_item_id IS NULL) <> (sales_lot_id IS NULL)",
            name="ck_listing_item_xor_lot",
        ),
        CheckConstraint(
            "sales_lot_id IS NULL OR quantity_available <= 1",
            name="ck_listing_lot_quantity_one",
        ),
```

- [ ] **Step 4: Write the migration**

Generate, then correct by hand:

```
python -m alembic revision --autogenerate -m "sales lots"
```

- **Name every foreign key, index and constraint.** Unnamed ones cannot be
  dropped.
- **`downgrade()` must `DROP TYPE IF EXISTS sales_lot_status`.** Autogenerate
  never drops the enum types it creates, so the next `upgrade` fails with
  *"type already exists"*. This repo has hit it.
- **Check the drop order by hand:** the listing constraints and column before
  `sales_lot`, `sales_lot_item` before `sales_lot`.
- **Making `inventory_item_id` nullable is not autogenerated reliably** —
  confirm the `alter_column(..., nullable=True)` is present, and that
  `downgrade()` restores `nullable=False` only after any lot listings are
  gone (there will be none on live, but the round-trip test runs it).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_sales_lot_schema.py backend/tests/test_migrations.py -v`
Expected: PASS, round trip included.

- [ ] **Step 6: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/models backend/alembic/versions backend/tests
git commit -m "Add sales lots, and let a listing name one instead of an item"
```

---

### Task 2: Assembling a lot

**Files:**
- Create: `backend/app/lot_writes.py`
- Test: `backend/tests/test_lot_writes.py`

**Interfaces:**
- Produces:
  `create_lot(db, *, title, description) -> SalesLot`;
  `add_member(db, lot, item) -> SalesLotItem`;
  `remove_member(db, lot, item) -> None`;
  `LotRefused(Exception)`.

Membership changes only while `assembling`; everything about a lot's *offer*
belongs to `offering_writes`, which Task 3 widens. Keeping assembly separate is
what stops this module needing the claim rules.

- [ ] **Step 1: Write the failing test**

```python
def test_membership_is_frozen_once_offered(db: Session, offered_lot: SalesLot, received_item) -> None:
    """The buyer is looking at that exact group."""
    with pytest.raises(LotRefused, match="offered"):
        add_member(db, offered_lot, received_item)


def test_an_item_with_stock_above_one_cannot_join(db: Session, lot: SalesLot, multi_item) -> None:
    """A claim covers a whole item; a partly-sold item is not a whole item."""
    with pytest.raises(LotRefused, match="quantity"):
        add_member(db, lot, multi_item)


def test_an_item_already_in_an_open_lot_cannot_join_another(db, lot, other_lot, received_item) -> None:
    """Refused with a message, not an IntegrityError in the user's face."""
    add_member(db, lot, received_item)
    with pytest.raises(LotRefused, match="already in lot"):
        add_member(db, other_lot, received_item)


def test_removing_a_member_releases_it(db, lot, received_item) -> None:
    """And the item can then join another lot."""
    add_member(db, lot, received_item)
    remove_member(db, lot, received_item)
    add_member(db, other_lot, received_item)  # must not raise
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_lot_writes.py -v`
Expected: FAIL — module does not exist.

- [ ] **Step 3: Write the module**

Refuse, with a message naming the item's code, when: the lot is not
`assembling`; the item is not `received`, is split, or is deleted; the item has
an item listing with `quantity_available > 1` or any sold units; or the item is
already in an open lot. Check every condition **before writing anything**, so a
refused change leaves the lot as it was — the same discipline as
`offering_writes.offer`.

The partial unique index is the backstop, not the check: catch `IntegrityError`
and re-raise as `LotRefused` so a race produces the same message as a
sequential attempt rather than a 500.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_lot_writes.py -v`
Expected: PASS.

- [ ] **Step 5: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/lot_writes.py backend/tests/test_lot_writes.py
git commit -m "Assemble a sales lot, and refuse what cannot be in one"
```

---

### Task 3: Offering a lot, and dissolving it

**Files:**
- Modify: `backend/app/offering_writes.py` (`offer`, `_affected_items`, `_end`)
- Test: `backend/tests/test_offering_writes.py` (add cases)

**Interfaces:**
- Produces: `offer(db, *, lot=..., venue=..., ...)` accepting a `SalesLot`
  in place of `item`; `_affected_items` returns a lot's members.

- [ ] **Step 1: Write the failing test**

```python
def test_offering_a_lot_claims_every_member(db, lot_of_three, ebay_venue, admin_user) -> None:
    """One claim per member: the one-offer guarantee is per item, not per listing."""
    listing = offering_writes.offer(db, lot=lot_of_three, venue=ebay_venue, ...)
    claims = db.scalars(select(OfferClaim).where(OfferClaim.listing_id == listing.id)).all()
    assert {claim.inventory_item_id for claim in claims} == set(member_ids(lot_of_three))
    assert all(claim.state is ClaimState.active for claim in claims)


def test_offering_a_lot_pauses_each_member_s_store_listing(db, lot_with_stored_members, ebay_venue) -> None:
    """Shop to eBay is one step, for every coin in the group."""


def test_a_member_offered_elsewhere_refuses_the_whole_lot(db, lot_with_busy_member, ebay_venue) -> None:
    """Named refusal: "CC-001234 is active on Whatnot, listing #9: end it first"."""


def test_ending_a_lot_listing_dissolves_the_lot(db, offered_lot_listing) -> None:
    """Unsold means the group is not a thing any more; the coins are free."""
    offering_writes.end_offer(db, offered_lot_listing)
    assert offered_lot_listing.sales_lot.status is SalesLotStatus.dissolved
    assert all(member.released_at is not None for member in offered_lot_listing.sales_lot.members)


def test_a_sold_lot_is_sold_not_dissolved(db, offered_lot_listing) -> None:
    """`sold` and `dissolved` are different histories and must stay distinguishable."""
    offering_writes.end_offer(db, offered_lot_listing, sold=True)
    assert offered_lot_listing.sales_lot.status is SalesLotStatus.sold


def test_members_with_no_remaining_claim_go_held(db, offered_lot_listing) -> None:
    """Back in the drawer, not still marked as listed."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_offering_writes.py -k lot -v`
Expected: FAIL — `offer() got an unexpected keyword argument 'lot'`.

- [ ] **Step 3: Widen `offer`**

Take `item` **or** `lot`, exactly one, and raise `OfferRefused` if both or
neither. Build the member list once, then reuse the existing machinery
unchanged: `_lock_items` over every member **in item id order** (the deadlock
rule — a lot makes multi-row locking the normal case, not the exception),
`_refuse_unofferable` per member, `_locked_offers` per member, one claim per
member.

The existing single-item path must come out of this as a lot of one internally
so that there is one implementation, not two that can drift.

- [ ] **Step 4: Widen `_affected_items` and `_end`**

`_affected_items` currently returns `[listing.inventory_item_id]`. For a lot
listing it must return the lot's open member ids. `_end` must set the lot's
status — `sold` when `sold=True`, `dissolved` otherwise — and set
`released_at` on every member.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_offering_writes.py backend/tests/test_offers_api.py -v`
Expected: PASS, with every existing single-item case green — they are the proof
the single-item path did not move.

- [ ] **Step 6: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/offering_writes.py backend/tests/test_offering_writes.py
git commit -m "Offer a sales lot, and dissolve it when the offer ends"
```

---

### Task 4: A lot listing can be snapshotted and sold

Two functions crash or silently do nothing on a lot listing today. Both are
found by reading, not by any existing test, because no lot listing has ever
existed.

**Files:**
- Modify: `backend/app/sale_snapshot.py:36-70` (`take`)
- Modify: `backend/app/order_writes.py` (`_after_stock_change`, `_add_shares`)
- Modify: `backend/app/sales_writes.py` (`_shared_items`)
- Test: `backend/tests/test_sale_snapshots.py`, `backend/tests/test_sales_writes.py`

- [ ] **Step 1: Write the failing test**

```python
def test_a_lot_listing_snapshots_every_member(db, offered_lot_listing) -> None:
    """`take` reads `listing.inventory_item` and would crash on None.

    The snapshot is what the order keeps forever, so a lot's must name every
    coin that was in it -- the membership rows are released at sale and the
    lot can be re-assembled from nothing but this.
    """
    snapshot = sale_snapshot.take(db, offered_lot_listing)
    assert len(snapshot["items"]) == 3
    assert snapshot["lot"]["title"] == offered_lot_listing.sales_lot.title


def test_selling_a_lot_divides_the_price_among_its_members(db, offered_lot_listing, admin_user) -> None:
    """Three coins at $100.00: 33.33, 33.33, 33.34 -- summing to the line."""
    order = record_sale(db, offered_lot_listing, price=Decimal("100.00"), ...)
    shares = order.items[0].shares
    assert len(shares) == 3
    assert sum(share.amount for share in shares) == Decimal("100.00")


def test_a_lot_s_members_all_become_sold(db, offered_lot_listing, admin_user) -> None:
    """`_after_stock_change` returns early on a null item and would skip them."""
    record_sale(db, offered_lot_listing, price=Decimal("100.00"), ...)
    for member in offered_lot_listing.sales_lot.members:
        assert member.item.disposition.code == "sold"


def test_shares_are_weighted_by_cost_basis_by_default(db, lot_of_uneven_costs, admin_user) -> None:
    """A $500 coin and a $5 coin do not each take half the price."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_sale_snapshots.py -k lot -v`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'id'`
inside `item_detail`, which is the crash this task exists to fix.

- [ ] **Step 3: Widen `sale_snapshot.take`**

Return the same shape for an item listing (so existing snapshots and their
readers are unchanged), and for a lot listing add `items` (a list of the
per-item details) and `lot` (id, title, description). Keep `snapshot_version`
and **raise it**, since readers must be able to tell the shapes apart; check
where `SNAPSHOT_VERSION` is read before changing it.

- [ ] **Step 4: Widen `_after_stock_change`**

It reads `db.get(InventoryItem, listing.inventory_item_id)` and returns when
that is None — silently skipping every member of a lot. Make it move the
disposition of **every** item the listing offered, through the same
`_affected_items` helper `offering_writes` uses, so there is one answer to
"which items" in the codebase rather than three.

- [ ] **Step 5: Widen `sales_writes._shared_items`**

Replace the `pragma: no cover` branch written in phase 2R with the real one:
the lot's open members, in item id order so shares are deterministic.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest backend/tests -v`
Expected: PASS — run the whole suite here, not just the new files: this task
changes three functions that checkout, snapshots and receiving all use.

- [ ] **Step 7: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app backend/tests
git commit -m "Snapshot, sell and settle a lot listing as a group of items"
```

---

### Task 5: The lots API

**Files:**
- Create: `backend/app/routers/lots.py`
- Modify: `backend/app/main.py` (register the router)
- Modify: `backend/app/schemas.py`
- Test: `backend/tests/test_lots_api.py`

**Interfaces:**
- `GET /api/sales-lots` (filter by status), `POST /api/sales-lots`,
  `PATCH /api/sales-lots/{id}` (title, description, membership while
  assembling), `DELETE /api/sales-lots/{id}` (an assembling lot only).
- Schemas: `SalesLotIn`, `SalesLotUpdate`, `SalesLotOut` (with members, running
  cost basis and value — staff-only figures).

- [ ] **Step 1: Write the failing test**

```python
def test_membership_changes_need_the_current_version(client, db, lot, admin_token) -> None:
    """Optimistic locking, as everywhere else in the console: 409 on a stale token."""


def test_editing_an_offered_lot_is_refused(client, offered_lot, admin_token) -> None:
    """409, naming the listing that froze it."""


def test_the_lot_list_is_admin_only(client, lot) -> None:
    """Cost basis is on the row; anonymous gets 401."""


def test_money_fields_are_strings(client, lot, admin_token) -> None:
    """A Decimal in a plain dict becomes a float; these must not."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_lots_api.py -v`
Expected: FAIL — 404, no route.

- [ ] **Step 3: Write the router**

Follow `routers/offers.py`: admin-only via `AdminUser`, resolve codes, own the
transaction, shape the response, decide nothing. Map `LotRefused` to 409 with
the message, unknown codes to 422, `StaleDataError` to 409 with `_STALE`.

Capture plain ids into locals before the `try`, and keep implicit autoflushes
inside it: after a failed flush, reading any ORM attribute raises
`PendingRollbackError` instead of the 409 you meant to send.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_lots_api.py -v`
Expected: PASS.

- [ ] **Step 5: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/routers/lots.py backend/app/main.py backend/app/schemas.py backend/tests/test_lots_api.py
git commit -m "Serve sales lots over the API"
```

---

### Task 6: Lots in the shop

**Files:**
- Modify: `backend/app/routers/catalog.py` (`to_catalog_item` and the query)
- Modify: `backend/app/offering_writes.py` (`shop_listing_filters` if needed)
- Test: `backend/tests/test_catalog.py`, `backend/tests/test_shop_boundary.py`

- [ ] **Step 1: Write the failing test**

```python
def test_a_store_lot_appears_as_one_catalogue_entry(client, store_lot_listing) -> None:
    """One thing for sale, with its members' public descriptions."""
    body = client.get("/api/catalog").json()
    entry = next(row for row in body["items"] if row["listing_id"] == store_lot_listing.id)
    assert entry["title"] == store_lot_listing.sales_lot.title
    assert len(entry["members"]) == 3


def test_a_lot_entry_never_carries_cost_or_location(client, store_lot_listing) -> None:
    """The authorisation boundary is `to_catalog_item` building fields by name.

    A lot widens what that function must build; this asserts the widening did
    not reach for the whole row.
    """
    entry = ...
    assert "total_cost" not in entry
    assert "storage_location" not in json.dumps(entry)
    for member in entry["members"]:
        assert "total_cost" not in member


def test_buying_a_lot_sells_every_member(client, db, store_lot_listing, customer_token) -> None:
    """Checkout of a lot is one line, with a share per member."""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_catalog.py -k lot -v`
Expected: FAIL — the catalogue query filters on `inventory_item_id`, so lot
listings do not appear.

- [ ] **Step 3: Serve lots from the catalogue**

Widen the query and `to_catalog_item`. **Build every public field by name**, as
that function already does for items — it, not the `public_catalog` view, is
what keeps cost basis and storage location from a buyer. The view is maintained
and unread (audited 2026-09-20); do not start trusting it here.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_catalog.py backend/tests/test_shop_boundary.py -v`
Expected: PASS, with the bundle-isolation check still green.

- [ ] **Step 5: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/routers/catalog.py backend/tests
git commit -m "Show a store lot in the shop as one thing for sale"
```

---

### Task 7: Race tests, mutation-proven

**Files:**
- Modify: `backend/tests/test_offer_races.py`

`TestClient` serialises requests and cannot see a race. Use real threads behind
a barrier with a session each, as `test_order_revision_race.py` does.

- [ ] **Step 1: Write the races**

```python
def test_two_lots_cannot_both_claim_one_item() -> None:
    """Two offers of lots sharing a coin: exactly one wins."""


def test_offering_a_lot_races_offering_one_of_its_members() -> None:
    """The item-level claim is what decides, whichever shape got there first."""


def test_checkout_races_a_pause_of_the_same_lot() -> None:
    """A lot paused while in a cart cannot be bought."""
```

- [ ] **Step 2: Mutation-prove each one**

For each race: remove the guarantee, confirm the named test goes **red**,
restore it, confirm green. A race test that passes with the lock removed is
testing nothing — this repo found 14 unfailable tests by asking exactly this.

- Drop `uq_sales_lot_item_open` → the two-lots test must fail.
- Drop `uq_offer_claim_active` → the lot-vs-member test must fail.
- Remove the `FOR UPDATE` in `_lock_items` → the checkout race must fail.

Record in the test docstrings which mutation each one survives.

- [ ] **Step 3: Run and commit**

```
python -m pytest backend/tests/test_offer_races.py -v
scripts\ccweb_check.cmd
git add backend/tests/test_offer_races.py
git commit -m "Prove the lot guarantees hold under concurrency"
```

---

### Task 8: Extend the claim invariant to lots

**Files:**
- Modify: `backend/tests/conftest.py` (the autouse fixture from phase 2R)

- [ ] **Step 1: Add the lot half**

After every test: an open membership (`released_at IS NULL`) implies its lot is
`assembling` or `offered`; a `sold` or `dissolved` lot has no open members.

- [ ] **Step 2: Mutation-prove it**

Break it on purpose in one test and confirm the fixture fails that test, then
make the proof permanent with `xfail(strict=True)`.

- [ ] **Step 3: Run the whole suite, gate, commit**

```
python -m pytest backend/tests -v
scripts\ccweb_check.cmd
git add backend/tests/conftest.py
git commit -m "Check that lot membership agrees with lot status"
```

---

### Task 9: The Lots page, and grouping from inventory

**Files:**
- Modify: `frontend/src/owner/api.js`
- Create: `frontend/src/owner/pages/Lots.jsx`, `Lots.test.jsx`
- Modify: `frontend/src/owner/pages/Inventory.jsx` (bulk bar: **Group into lot…**)
- Modify: the console menu (Selling group)

- [ ] **Step 1: Write the failing tests**

Assembling lots with add and remove, title and description, running cost basis
and value; **Offer** reusing the existing offer dialog with one row; history of
offered, sold and dissolved lots; **Re-offer as a lot** on a dissolved one,
pre-filling a new assembling lot.

Render with `strict: true` — the console runs in `<StrictMode>` and the harness
does not by default. Assert the request body in `owner/api.test.js`, not
through a wholesale `api` mock.

- [ ] **Step 2: Run to verify they fail, then build the page**

Follow `Listings.jsx` for table and row actions, `Platforms.jsx` for the form.
A venues- or lots-load failure must not blank the page — that exact bug was
fixed on `main` on 2026-09-20; show the error and keep the page usable.

- [ ] **Step 3: Run the tests, gate, commit**

```
npm --prefix frontend test
scripts\ccweb_check.cmd
git add frontend/src/owner
git commit -m "Assemble and offer sales lots from the console"
```

---

### Task 10: Documentation

- [ ] **Step 1: Mark phase 3 built** in `docs/specs/selling-design.md`.
- [ ] **Step 2: Note the snapshot version change** where snapshot readers are
      documented, so an older snapshot's shape stays readable.
- [ ] **Step 3: Re-read every docstring written in this branch** against the
      final code.
- [ ] **Step 4: Gate and commit.**

---

## Verification before handing back

- [ ] `scripts\ccweb_check.cmd` exits zero.
- [ ] `test_migrations_round_trip` and `test_migrations_match_models` pass.
- [ ] Every race test is mutation-proven, and the docstrings say which
      mutation each survives.
- [ ] **Live is untouched:** `ccwebdb`'s `alembic_version` still reads
      `e7c3a5b19d84`.
- [ ] `git merge-base --is-ancestor main HEAD` succeeds.
