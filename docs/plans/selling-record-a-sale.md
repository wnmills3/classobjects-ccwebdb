# Selling, phase 2R: recording an outside sale — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Record a sale that happened on an outside platform — buyer, price, the
platform's actual fees, and a per-item share of both — so that every sold item
can answer what it sold for and what it cost to sell.

**Architecture:** One new module, `app/sales_writes.py`, orchestrates a sale. It
owns the two facts nothing else writes (`sales_order_fee`,
`sales_order_item_share`) and delegates: the order and its snapshot to
`order_writes.place_order`, the listing's ending to
`offering_writes.end_offer(sold=True)`. It has one entry point, `record_sale`,
because phase 4's settlement will be its third caller and must not grow a second
copy of fee and share logic. Shares are computed by the existing
`app/allocation.py`, weighted by `inventory_item.total_cost`.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 (declarative, `Mapped[]`),
Alembic, PostgreSQL 16, pytest; React + Vite for the console.

**Spec:** `docs/specs/selling-design.md` (revised 2026-09-20). Read *Sales*,
*Buyers*, *How things move* and *Revision, 2026-09-20* before starting.

## Global Constraints

Every task's requirements implicitly include this section.

- **Branch:** all work on `feat/record-a-sale`, cut from `main`. Never commit to
  `main`. Do not merge; the owner says when.
- **The gate:** `scripts\ccweb_check.cmd` must exit zero before every commit.
  `ccweb_check.cmd fix` auto-fixes formatting first. It runs ruff, eslint,
  prettier, pytest and mypy, all currently at zero findings — any finding is
  yours. **Never pipe it** and never add `-qq`; gate on the exit code.
- **One pytest session at a time.** The suite uses `ccwebdb_test`, never
  `ccwebdb`. Two concurrent runs produce failures that mimic real bugs.
- **Money is `Decimal`,** never float, everywhere including tests. FastAPI's
  encoder turns a `Decimal` inside a plain `dict` into a float — any endpoint
  returning rows as dicts must stringify money itself.
- **Docstrings and annotations are enforced** (ruff `D` and `ANN`) on every
  public class, method and function, tests included. `backend/tests` is in
  `[tool.mypy] files`: an annotation that lies fails the gate.
- **Do not migrate the live database.** This branch's migration is applied to
  live only after phases 3 and 4 are also merged. `ccwebdb` is untouched here.
- **Writers stay single.** `order_writes` is the only writer of orders,
  `offering_writes` the only writer of `listing.status` and `offer_claim`,
  `lifecycle_writes` the only writer of status and location. `sales_writes`
  adds no second path to any of them.
- **Use the Write tool for new files, Edit for surgical changes.** No shell
  heredocs — they have repeatedly failed on this repo's quoting.
- **cmd/batch only** for any script or documented command. No PowerShell.
- Console API calls go in `frontend/src/owner/api.js`, never `shared/api.js`;
  a build check enforces it.

---

### Task 1: Schema — fee kinds, fees, shares and platform buyers

One migration for the whole branch. `down_revision` is `e7c3a5b19d84`.

**Files:**
- Modify: `backend/app/models/sales.py` (add three classes; widen `Customer`)
- Modify: `backend/app/models/__init__.py` (export the three new models)
- Create: `backend/alembic/versions/<generated>_sales_fees_and_shares.py`
- Test: `backend/tests/test_sales_fees_schema.py`

**Interfaces:**
- Consumes: `ReferenceMixin` from `app/models/base.py`; `SalesOrder`,
  `SalesOrderItem`, `Customer`, `SalesVenue` from `app/models/sales.py`.
- Produces: `SalesFeeKind`, `SalesOrderFee`, `SalesOrderItemShare`;
  `Customer.sales_venue_id: Mapped[int | None]`,
  `Customer.venue_username: Mapped[str | None]`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_sales_fees_schema.py
"""The fee, share and platform-buyer schema.

These assert the *database* guarantees rather than the Python models: a
uniqueness rule that lives only in application code is one concurrent
request away from being untrue.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Customer, SalesFeeKind, SalesVenue


def test_fee_kinds_are_seeded(db: Session) -> None:
    """The six fee kinds the spec names are present and active."""
    codes = set(
        db.scalars(select(SalesFeeKind.code).where(SalesFeeKind.is_active)).all()
    )
    assert codes == {
        "commission",
        "processing",
        "listing",
        "shipping_label",
        "promotion",
        "other",
    }


def test_one_buyer_per_platform_username(db: Session, ebay_venue: SalesVenue) -> None:
    """The same username on the same platform cannot be stored twice."""
    db.add(
        Customer(
            display_name="coinfan88",
            sales_venue_id=ebay_venue.id,
            venue_username="coinfan88",
        )
    )
    db.flush()
    db.add(
        Customer(
            display_name="coinfan88 again",
            sales_venue_id=ebay_venue.id,
            venue_username="coinfan88",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_one_undisclosed_buyer_per_platform(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """A platform gets exactly one buyer with no username."""
    db.add(Customer(display_name="Undisclosed buyer (eBay)", sales_venue_id=ebay_venue.id))
    db.flush()
    db.add(Customer(display_name="Undisclosed buyer (eBay) 2", sales_venue_id=ebay_venue.id))
    with pytest.raises(IntegrityError):
        db.flush()


def test_store_customers_are_unaffected(db: Session) -> None:
    """Any number of store customers have neither column set."""
    db.add_all([Customer(display_name="A"), Customer(display_name="B")])
    db.flush()  # must not raise


def test_fee_and_share_amounts_are_exact(db: Session) -> None:
    """Money columns are numeric(12,2), so a cent is a cent."""
    scale = db.scalar(
        text(
            "SELECT numeric_scale FROM information_schema.columns "
            "WHERE table_name = 'sales_order_fee' AND column_name = 'amount'"
        )
    )
    assert scale == 2
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `scripts\ccweb_test.cmd backend/tests/test_sales_fees_schema.py`
(or `python -m pytest backend/tests/test_sales_fees_schema.py -v` from the
conda env). Expected: FAIL — `ImportError: cannot import name 'SalesFeeKind'`.

- [ ] **Step 3: Add the models**

In `backend/app/models/sales.py`, after `SalesOrderItem`:

```python
class SalesFeeKind(ReferenceMixin, Base):
    """A kind of fee a platform charges on a sale.

    Seeded with the schema rather than from `backend/data/reference/`: this is
    a closed vocabulary the product defines, not numismatic reference data
    with an outside source to cite.
    """

    __tablename__ = "sales_fee_kind"


class SalesOrderFee(Base):
    """One fee line on an order, as the platform's statement shows it.

    Actual money, not an estimate: the platform's own figures are what a tax
    return needs. The estimates shown while offering come from
    `sales_venue`'s default rates and are never stored.

    Net payout is `sales_order.total_amount - sum(amount)`, computed when
    asked. Storing it would give two places to disagree.
    """

    __tablename__ = "sales_order_fee"

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_order_id: Mapped[int] = mapped_column(
        ForeignKey("sales_order.id", ondelete="CASCADE"), index=True, nullable=False
    )
    sales_fee_kind_id: Mapped[int] = mapped_column(
        ForeignKey("sales_fee_kind.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)

    order: Mapped[SalesOrder] = relationship(back_populates="fees")
    fee_kind: Mapped[SalesFeeKind] = relationship()

    __table_args__ = (
        CheckConstraint("amount >= 0", name="ck_sales_order_fee_non_negative"),
    )


class SalesOrderItemShare(Base):
    """One item's share of an order line's money.

    Every sold line has shares: one row for a single-item listing, one per
    member for a lot. A share of one looks redundant and is deliberate --
    it makes this table the single permanent answer to "which items did this
    order carry", with one query shape instead of two. `app.sale_state`
    depends on that, and so will realised-gain reporting.

    Shares sum to their line exactly (`app.allocation`), so a cent is never
    lost between the order total and the items that made it up.
    """

    __tablename__ = "sales_order_item_share"

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_order_item_id: Mapped[int] = mapped_column(
        ForeignKey("sales_order_item.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    #: This item's share of `sales_order_item.unit_price * quantity`.
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    #: This item's share of the order's fees.
    fee_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), nullable=False, server_default=text("0")
    )

    line: Mapped[SalesOrderItem] = relationship(back_populates="shares")

    __table_args__ = (
        UniqueConstraint(
            "sales_order_item_id",
            "inventory_item_id",
            name="uq_share_line_item",
        ),
    )
```

Add the back-references on the existing classes:

```python
# in SalesOrder
fees: Mapped[list[SalesOrderFee]] = relationship(
    back_populates="order", cascade=_CASCADE_ALL_DELETE_ORPHAN
)

# in SalesOrderItem
shares: Mapped[list[SalesOrderItemShare]] = relationship(
    back_populates="line", cascade=_CASCADE_ALL_DELETE_ORPHAN
)
```

Widen `Customer` — add the two columns and replace its `__table_args__`:

```python
    #: The platform this buyer is known on; null for a store customer.
    sales_venue_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_venue.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    #: Their username there ("coinfan88"). Null on the platform's single
    #: undisclosed buyer, used by auction houses that do not name buyers.
    venue_username: Mapped[str | None] = mapped_column(String(128), nullable=True)

    __table_args__ = (
        UniqueConstraint("user_id", name="uq_customer_user"),
        Index(
            "uq_customer_venue_username",
            "sales_venue_id",
            "venue_username",
            unique=True,
            postgresql_where=text("venue_username IS NOT NULL"),
        ),
        Index(
            "uq_customer_venue_undisclosed",
            "sales_venue_id",
            unique=True,
            postgresql_where=text(
                "venue_username IS NULL AND sales_venue_id IS NOT NULL"
            ),
        ),
    )
```

Export the three new names from `backend/app/models/__init__.py`.

- [ ] **Step 4: Write the migration**

Generate it, then edit — autogenerate output is a draft here, not an answer:

```
python -m alembic revision --autogenerate -m "sales fees and shares"
```

The generated file must be corrected for the traps this repo has already hit
(`docs/code-quality.md`, and the Alembic section of the project's notes):

- **Name every foreign key and index.** Autogenerate emits
  `op.create_foreign_key(None, ...)`, which pairs with a `drop_constraint(None,
  ...)` that fails with *"cannot emit DROP CONSTRAINT ... it has no name"*.
- **Check the drop order in `downgrade()`** by hand: shares before lines,
  fees before orders, and the two customer indexes before the columns.
- **Seed the fee kinds in `upgrade()`**, as `d6a1f3b8c402` does for
  `sales_venue_kind`:

```python
    op.execute(
        sa.text(
            "INSERT INTO sales_fee_kind (code, label, sort_order, is_active) VALUES "
            "('commission', 'Commission', 10, true), "
            "('processing', 'Payment processing', 20, true), "
            "('listing', 'Listing fee', 30, true), "
            "('shipping_label', 'Shipping label', 40, true), "
            "('promotion', 'Promotion', 50, true), "
            "('other', 'Other', 60, true)"
        )
    )
```

- The two customer indexes are partial, so they must be written as
  `op.create_index(..., postgresql_where=sa.text(...))` with the predicate
  spelled **exactly** as PostgreSQL stores it, or the drift test reports the
  index as changed on every run. Confirm with:
  `select indexdef from pg_indexes where indexname = 'uq_customer_venue_username';`
  and copy the `WHERE` clause back into the model and the migration verbatim.

- [ ] **Step 5: Add the `ebay_venue` fixture the test needs**

In `backend/tests/conftest.py`, beside the existing venue fixtures:

```python
@pytest.fixture
def ebay_venue(db: Session) -> SalesVenue:
    """A marketplace platform to offer and sell on, separate from the store."""
    venue = SalesVenue(
        code="ebay",
        name="eBay",
        sales_venue_kind_id=require_code(db, SalesVenueKind, "marketplace", "kind"),
        commission_rate=Decimal("0.1325"),
    )
    db.add(venue)
    db.flush()
    return venue
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_sales_fees_schema.py backend/tests/test_migrations.py -v`
Expected: PASS, including `test_migrations_match_models` and
`test_migrations_round_trip` — the round trip proves `downgrade()` actually
runs, which is how four ordering bugs of this exact shape were caught before.

- [ ] **Step 7: Run the full gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/models backend/alembic/versions backend/tests
git commit -m "Add fee, share and platform-buyer schema"
```

---

### Task 2: Resolving a buyer on a platform

**Files:**
- Create: `backend/app/buyers.py`
- Test: `backend/tests/test_buyers.py`

**Interfaces:**
- Consumes: `Customer`, `SalesVenue` (Task 1).
- Produces: `venue_buyer(db: Session, venue: SalesVenue, username: str | None) -> Customer`.
  A `None` username returns the platform's single undisclosed buyer, creating
  it on first use.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_buyers.py
"""Finding or creating the buyer behind an outside sale."""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.buyers import venue_buyer
from app.models import SalesVenue


def test_same_username_returns_the_same_customer(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """A repeat buyer is one customer record, not one per sale."""
    first = venue_buyer(db, ebay_venue, "coinfan88")
    second = venue_buyer(db, ebay_venue, "coinfan88")
    assert first.id == second.id


def test_username_is_matched_case_insensitively(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """Platforms display usernames inconsistently; the person is the same."""
    first = venue_buyer(db, ebay_venue, "CoinFan88")
    second = venue_buyer(db, ebay_venue, "coinfan88")
    assert first.id == second.id


def test_the_same_name_on_two_platforms_is_two_people(
    db: Session, ebay_venue: SalesVenue, whatnot_venue: SalesVenue
) -> None:
    """`coinfan88` on eBay and on Whatnot are not known to be the same buyer."""
    assert venue_buyer(db, ebay_venue, "coinfan88").id != venue_buyer(
        db, whatnot_venue, "coinfan88"
    ).id


def test_undisclosed_buyer_is_one_per_platform(
    db: Session, heritage_venue: SalesVenue
) -> None:
    """An auction house that names no buyer gets one standing record."""
    first = venue_buyer(db, heritage_venue, None)
    second = venue_buyer(db, heritage_venue, None)
    assert first.id == second.id
    assert first.display_name == "Undisclosed buyer (Heritage)"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest backend/tests/test_buyers.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.buyers'`.

- [ ] **Step 3: Write the module**

```python
# backend/app/buyers.py
"""The buyer behind a sale on an outside platform.

A store customer is a login. A buyer on eBay or Whatnot is a username on that
platform and nothing more, and an auction house may not name the buyer at all
-- so each platform has one standing "undisclosed buyer" to hang those sales
on. Both are `customer` rows, so orders, snapshots and history work the same
way whatever the sale was.

Uniqueness is enforced by two partial indexes, not by the lookup here: two
concurrent sales to the same new buyer would otherwise both find nothing and
both insert.
"""

from __future__ import annotations

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import Customer, SalesVenue

__all__ = ["venue_buyer"]


def venue_buyer(db: Session, venue: SalesVenue, username: str | None) -> Customer:
    """The customer for `username` on `venue`, created if new.

    `username` of None means the platform's single undisclosed buyer, which
    auction houses that do not name buyers sell to.

    Matching ignores case: platforms display the same account as `CoinFan88`
    and `coinfan88`, and two records would split one person's history.
    """
    stored = username.strip() if username else None
    if stored == "":
        stored = None
    query = select(Customer).where(Customer.sales_venue_id == venue.id)
    if stored is None:
        query = query.where(Customer.venue_username.is_(None))
    else:
        query = query.where(func.lower(Customer.venue_username) == stored.lower())
    found = db.scalar(query)
    if found is not None:
        return found
    customer = Customer(
        display_name=stored or f"Undisclosed buyer ({venue.name})",
        sales_venue_id=venue.id,
        venue_username=stored,
    )
    db.add(customer)
    db.flush()
    return customer
```

- [ ] **Step 4: Make case-insensitive matching a database guarantee too**

The partial unique index from Task 1 is case-*sensitive*, so it would allow
`CoinFan88` beside `coinfan88` — the exact split this function avoids,
reintroduced by a race. Change the index in the model and the migration to
index the lowered value:

```python
        Index(
            "uq_customer_venue_username",
            "sales_venue_id",
            text("lower(venue_username)"),
            unique=True,
            postgresql_where=text("venue_username IS NOT NULL"),
        ),
```

Re-run `test_migrations_match_models`. If it reports the index as changed on
every run, PostgreSQL has normalised the expression — read the stored form
with `select indexdef from pg_indexes where indexname =
'uq_customer_venue_username';` and copy it back verbatim.

- [ ] **Step 5: Add a test that proves the index, not just the function**

```python
def test_case_variant_username_is_refused_by_the_database(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """Two sessions racing cannot create `CoinFan88` beside `coinfan88`."""
    venue_buyer(db, ebay_venue, "coinfan88")
    db.add(
        Customer(
            display_name="CoinFan88",
            sales_venue_id=ebay_venue.id,
            venue_username="CoinFan88",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
```

Add the `whatnot_venue` and `heritage_venue` fixtures to `conftest.py`
alongside `ebay_venue`, with kinds `marketplace` and `auction_house` and names
`Whatnot` and `Heritage`.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_buyers.py backend/tests/test_migrations.py -v`
Expected: PASS.

- [ ] **Step 7: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/buyers.py backend/tests/test_buyers.py backend/tests/conftest.py backend/app/models/sales.py backend/alembic/versions
git commit -m "Find or create the buyer behind an outside sale"
```

---

### Task 3: Let an order be placed on a platform other than the store

`place_order` hard-codes `sales_venue_id=store_venue_id(db)`, creates every
order `pending`, and refuses any listing that is not `sellable_in_shop`. All
three are right for checkout and wrong for an outside sale. Widen it rather
than writing a second order creator.

**Files:**
- Modify: `backend/app/order_writes.py:139-215` (`place_order`)
- Test: `backend/tests/test_order_writes.py` (add cases)

**Interfaces:**
- Consumes: `Line`, `place_order` as they are today.
- Produces: `place_order(db, customer, lines, placed_by, notes=None, *,
  venue: SalesVenue | None = None, status_code: str = "pending") -> SalesOrder`.
  `venue=None` keeps today's behaviour exactly: the store, with the shop
  guards applied. A non-None venue means an outside sale — the shop guards do
  not apply, because the listing is not in the shop.

- [ ] **Step 1: Write the failing test**

```python
def test_an_outside_sale_records_its_platform_and_status(
    db: Session, ebay_listing: Listing, admin_user: User, ebay_venue: SalesVenue
) -> None:
    """A sale on eBay is stored against eBay, already paid."""
    buyer = venue_buyer(db, ebay_venue, "coinfan88")
    order = place_order(
        db,
        buyer,
        [Line(listing_id=ebay_listing.id, quantity=1, unit_price=Decimal("120.00"))],
        admin_user,
        venue=ebay_venue,
        status_code="paid",
    )
    assert order.sales_venue_id == ebay_venue.id
    assert db.get(SalesOrderStatus, order.sales_order_status_id).code == "paid"


def test_an_outside_listing_is_not_refused_for_being_outside_the_shop(
    db: Session, ebay_listing: Listing, admin_user: User, ebay_venue: SalesVenue
) -> None:
    """The shop guards are checkout's rules, not every order's."""
    buyer = venue_buyer(db, ebay_venue, "coinfan88")
    order = place_order(
        db,
        buyer,
        [Line(listing_id=ebay_listing.id, quantity=1, unit_price=Decimal("120.00"))],
        admin_user,
        venue=ebay_venue,
        status_code="paid",
    )
    assert order.id is not None


def test_checkout_still_refuses_a_listing_from_another_platform(
    db: Session, ebay_listing: Listing, admin_user: User, customer: Customer
) -> None:
    """Without a venue this is checkout, and checkout is the shop only.

    This is the guard that stops a shop request buying an eBay listing; the
    new keyword must not have opened it.
    """
    with pytest.raises(HTTPException) as caught:
        place_order(
            db,
            customer,
            [Line(listing_id=ebay_listing.id, quantity=1)],
            admin_user,
        )
    assert caught.value.status_code == 409
    assert "not sold in this shop" in caught.value.detail
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_order_writes.py -k outside -v`
Expected: FAIL — `TypeError: place_order() got an unexpected keyword argument 'venue'`.

- [ ] **Step 3: Widen `place_order`**

Change the signature and the two places that assume the store:

```python
def place_order(
    db: Session,
    customer: Customer,
    lines: Sequence[Line],
    placed_by: User,
    notes: str | None = None,
    *,
    venue: SalesVenue | None = None,
    status_code: str = "pending",
) -> SalesOrder:
    """Create an order, taking its stock under row locks.

    `venue` is None for a shop checkout: the order is the store's and the
    shop's rules apply -- the listing must be this shop's, active, and hold
    the stock asked for. Passing a venue means the sale happened somewhere
    else and is being recorded after the fact, so those three checks do not
    apply: an eBay listing is not meant to be sellable in the shop, and by the
    time a sale is recorded it is over. What still applies to both is the row
    lock and the stock arithmetic.

    `status_code` is `pending` for checkout, which the buyer has not paid yet.
    An outside platform has already collected the money (`paid`), and an
    auction house has already shipped (`delivered`).

    Raises `HTTPException`: 404 for a listing that does not exist, and 409
    for one no longer sellable in the shop, one without the stock asked for,
    and a version that has moved. Every one goes through `_refuse`, which
    rolls the transaction back before raising -- so a caller that catches
    one holds a rolled-back session.
    """
    listings = _lock_listings(db, {line.listing_id for line in lines})
    for line in sorted(lines, key=lambda line: line.listing_id):
        listing = listings[line.listing_id]
        if venue is None:
            # `active_only=False`: whether the listing is this shop's at all is
            # one question, and whether it is on offer this minute is another
            # with its own message below. `offering_writes` owns both halves.
            if not sellable_in_shop(listing, active_only=False):
                _refuse(
                    db,
                    status.HTTP_409_CONFLICT,
                    f"Listing {listing.id} is not sold in this shop",
                )
            if not listing.is_active:
                _refuse(
                    db,
                    status.HTTP_409_CONFLICT,
                    f"Listing {listing.id} is not currently for sale",
                )
        if listing.quantity_available < line.quantity:
            _refuse(
                db,
                status.HTTP_409_CONFLICT,
                f"Only {listing.quantity_available} of listing {listing.id} remain "
                f"(requested {line.quantity})",
            )

    order = SalesOrder(
        customer_id=customer.id,
        sales_venue_id=store_venue_id(db) if venue is None else venue.id,
        sales_order_status_id=require_code(
            db, SalesOrderStatus, status_code, "status"
        ),
        placed_by_id=placed_by.id,
        notes=notes,
    )
```

The rest of the function is unchanged.

- [ ] **Step 4: Add the `ebay_listing` fixture**

In `conftest.py`, an active eBay listing over a received item, made through
`offering_writes.offer` so it has its claim:

```python
@pytest.fixture
def ebay_listing(db: Session, received_item: InventoryItem, ebay_venue: SalesVenue) -> Listing:
    """An item offered on eBay, with the claim that offer creates."""
    return offering_writes.offer(
        db,
        item=received_item,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("120.00"),
        title="1881-S Morgan Dollar",
        description="",
        external_id="123456",
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_order_writes.py backend/tests/test_orders.py -v`
Expected: PASS. `test_orders.py` covers checkout and must be untouched by
this — it is the proof the default path did not move.

- [ ] **Step 6: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/order_writes.py backend/tests
git commit -m "Let an order record a sale that happened on another platform"
```

---

### Task 4: `sales_writes.record_sale`

**Files:**
- Create: `backend/app/sales_writes.py`
- Test: `backend/tests/test_sales_writes.py`

**Interfaces:**
- Consumes: `place_order`, `Line` (Task 3); `venue_buyer` (Task 2);
  `allocation.allocate`; `offering_writes.end_offer`.
- Produces:

```python
@dataclass(frozen=True)
class FeeLine:
    """One actual fee from the platform's statement."""
    kind_code: str
    amount: Decimal
    note: str | None = None


def record_sale(
    db: Session,
    listing: Listing,
    *,
    price: Decimal,
    buyer_username: str | None,
    external_order_id: str | None,
    fees: Sequence[FeeLine],
    recorded_by: User,
    equal_shares: bool = False,
    status_code: str | None = None,
) -> SalesOrder:
```

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_sales_writes.py
"""Recording a sale that happened on an outside platform."""

from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Listing,
    ListingStatus,
    SalesOrderItemShare,
    SalesVenue,
    User,
)
from app.sales_writes import FeeLine, record_sale


def test_the_sale_ends_the_listing_and_releases_its_claim(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """A sold listing is over: ended, claims released, item sold."""
    record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id="04-12345-67890",
        fees=[FeeLine("commission", Decimal("15.90"))],
        recorded_by=admin_user,
    )
    assert ebay_listing.status is ListingStatus.ended
    assert ebay_listing.inventory_item.disposition.code == "sold"


def test_fees_are_stored_as_given(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """The platform's own figures, not an estimate from the venue's rates."""
    order = record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[
            FeeLine("commission", Decimal("15.90")),
            FeeLine("shipping_label", Decimal("5.35"), note="USPS Ground"),
        ],
        recorded_by=admin_user,
    )
    assert sorted(fee.amount for fee in order.fees) == [
        Decimal("5.35"),
        Decimal("15.90"),
    ]


def test_a_single_item_sale_still_gets_a_share(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """One row carrying the whole line: the permanent item-to-order link."""
    order = record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[FeeLine("commission", Decimal("15.90"))],
        recorded_by=admin_user,
    )
    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == order.items[0].id
        )
    ).all()
    assert len(shares) == 1
    assert shares[0].inventory_item_id == ebay_listing.inventory_item_id
    assert shares[0].amount == Decimal("120.00")
    assert shares[0].fee_amount == Decimal("15.90")


def test_the_buyer_is_recorded_on_the_platform(
    db: Session, ebay_listing: Listing, admin_user: User, ebay_venue: SalesVenue
) -> None:
    """The order's customer is the eBay username, not a store login."""
    order = record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )
    assert order.customer.venue_username == "coinfan88"
    assert order.customer.sales_venue_id == ebay_venue.id


def test_an_auction_house_sale_is_delivered_not_paid(
    db: Session, heritage_listing: Listing, admin_user: User
) -> None:
    """The house held and shipped the coin; there is nothing left to do."""
    order = record_sale(
        db,
        heritage_listing,
        price=Decimal("500.00"),
        buyer_username=None,
        external_order_id=None,
        fees=[FeeLine("commission", Decimal("75.00"))],
        recorded_by=admin_user,
    )
    assert order.status.code == "delivered"


def test_a_negative_fee_is_refused(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """A refund is not a negative fee; it is a separate thing not built yet."""
    with pytest.raises(SaleRefused, match="negative"):
        record_sale(
            db,
            ebay_listing,
            price=Decimal("120.00"),
            buyer_username="coinfan88",
            external_order_id=None,
            fees=[FeeLine("commission", Decimal("-1.00"))],
            recorded_by=admin_user,
        )


def test_an_already_ended_listing_cannot_be_sold(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """Recording the same sale twice would sell one coin twice."""
    offering_writes.end_offer(db, ebay_listing)
    with pytest.raises(SaleRefused, match="not on offer"):
        record_sale(
            db,
            ebay_listing,
            price=Decimal("120.00"),
            buyer_username="coinfan88",
            external_order_id=None,
            fees=[],
            recorded_by=admin_user,
        )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_sales_writes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.sales_writes'`.

- [ ] **Step 3: Write the module**

```python
# backend/app/sales_writes.py
"""Recording a sale: the money, the buyer, and which items it covered.

This module owns the two facts nothing else writes, `sales_order_fee` and
`sales_order_item_share`, and orchestrates the rest rather than duplicating
it: the order and its snapshot come from `order_writes.place_order`, and the
listing is ended by `offering_writes.end_offer(sold=True)`. Neither of those
grows a second path here.

**One entry point on purpose.** `record_sale` has three callers -- the
Listings page's Record sale, shop checkout, and auction settlement -- and an
auction settling four lots to two buyers is two calls, not a second
implementation of fees and shares that can drift from this one.

Shares are what make a sale answerable per item. Every sold line gets them,
including a single-item store sale, so that "which items did this order
carry" has one query shape and one answer forever -- a claim is `released`
the moment its listing sells, so the claim cannot answer it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import offering_writes, order_writes
from .allocation import allocate
from .buyers import venue_buyer
from .models import (
    InventoryItem,
    Listing,
    ListingStatus,
    SalesFeeKind,
    SalesOrder,
    SalesOrderFee,
    SalesOrderItem,
    SalesOrderItemShare,
    User,
)
from .references import require_code

__all__ = ["FeeLine", "SaleRefused", "record_sale"]

#: What a sale's order status is, by the kind of platform it happened on.
#: A marketplace or live auction has collected the money and the owner still
#: has to ship; an auction house has already shipped for us.
_STATUS_BY_VENUE_KIND = {
    "own_store": "paid",
    "marketplace": "paid",
    "live_auction": "paid",
    "auction_house": "delivered",
}


class SaleRefused(Exception):
    """The sale cannot be recorded as asked, with the reason for a person."""


@dataclass(frozen=True)
class FeeLine:
    """One actual fee from the platform's statement."""

    kind_code: str
    amount: Decimal
    note: str | None = None


def _shared_items(db: Session, listing: Listing) -> list[InventoryItem]:
    """The items a listing's money must be divided among.

    One for an item listing. For a lot listing (phase 3) this becomes the
    lot's members; until then a lot listing cannot exist.
    """
    item = listing.inventory_item
    if item is None:  # pragma: no cover - phase 3 widens this
        raise SaleRefused(f"Listing {listing.id} offers no item")
    return [item]


def _weights(items: Sequence[InventoryItem], *, equal: bool) -> list[Decimal]:
    """How to divide money between items: by cost, or equally.

    Cost basis is the default because it is the closest thing to what each
    piece was worth when the group was priced. Equal is chosen explicitly,
    and is also the fallback when every cost is zero -- dividing by a total
    of nothing has no proportional answer.
    """
    if equal:
        return [Decimal(1)] * len(items)
    costs = [item.total_cost or Decimal(0) for item in items]
    return [Decimal(1)] * len(items) if sum(costs) == 0 else costs


def record_sale(
    db: Session,
    listing: Listing,
    *,
    price: Decimal,
    buyer_username: str | None,
    external_order_id: str | None,
    fees: Sequence[FeeLine],
    recorded_by: User,
    equal_shares: bool = False,
    status_code: str | None = None,
) -> SalesOrder:
    """Record that `listing` sold, and end it. Caller commits.

    Raises `SaleRefused` before writing anything if the listing is not on
    offer or a fee is negative, so a refused sale leaves everything as it was.
    """
    if listing.status is not ListingStatus.active:
        raise SaleRefused(
            f"Listing {listing.id} is not on offer ({listing.status.value})"
        )
    if any(fee.amount < 0 for fee in fees):
        raise SaleRefused("A fee cannot be negative")

    venue = listing.sales_venue
    items = _shared_items(db, listing)
    buyer = venue_buyer(db, venue, buyer_username)
    status = status_code or _STATUS_BY_VENUE_KIND.get(venue.kind.code, "paid")

    order = order_writes.place_order(
        db,
        buyer,
        [order_writes.Line(listing_id=listing.id, quantity=1, unit_price=price)],
        recorded_by,
        venue=venue,
        status_code=status,
    )
    order.external_order_id = external_order_id

    total_fees = Decimal(0)
    for fee in fees:
        db.add(
            SalesOrderFee(
                sales_order_id=order.id,
                sales_fee_kind_id=require_code(db, SalesFeeKind, fee.kind_code, "fee"),
                amount=fee.amount,
                note=fee.note,
            )
        )
        total_fees += fee.amount

    line = order.items[0]
    weights = _weights(items, equal=equal_shares)
    amounts = allocate(price, weights)
    fee_amounts = allocate(total_fees, weights)
    for item, amount, fee_amount in zip(items, amounts, fee_amounts, strict=True):
        db.add(
            SalesOrderItemShare(
                sales_order_item_id=line.id,
                inventory_item_id=item.id,
                amount=amount,
                fee_amount=fee_amount,
            )
        )

    offering_writes.end_offer(db, listing, sold=True)
    db.flush()
    return order
```

- [ ] **Step 4: Check `end_offer(sold=True)` does what this needs**

Read `backend/app/offering_writes.py:493-560`. `_still_offered` and `_end`
decide where an item lands when its listing ends. Confirm by test that
`sold=True` leaves the item `sold` and **not** `held`, and that a paused store
listing of the same item is **ended** rather than resumed — the spec's rule,
and the opposite of an ordinary End. If `end_offer` does not already do the
second part, fix it there, not here: it is the only writer of listing status.

Add the test either way, because it is the guarantee that matters:

```python
def test_a_sold_item_s_paused_store_listing_ends_rather_than_resuming(
    db: Session, stored_then_ebay: tuple[Listing, Listing], admin_user: User
) -> None:
    """The coin is gone; putting it back in the shop would sell it twice."""
    store_listing, ebay_listing = stored_then_ebay
    assert store_listing.status is ListingStatus.paused
    record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )
    assert store_listing.status is ListingStatus.ended
```

- [ ] **Step 5: Prove the shares add up, with a case that cannot divide evenly**

```python
def test_shares_of_an_indivisible_price_still_sum_to_it(
    db: Session, admin_user: User, three_item_costs: list[Decimal]
) -> None:
    """$100.00 three ways is 33.33, 33.33, 33.34 -- never 99.99."""
    amounts = allocate(Decimal("100.00"), three_item_costs)
    assert sum(amounts) == Decimal("100.00")
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_sales_writes.py -v`
Expected: PASS.

- [ ] **Step 7: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/sales_writes.py backend/tests/test_sales_writes.py
git commit -m "Record an outside sale with its fees and per-item shares"
```

---

### Task 5: Shares on a shop checkout

**Files:**
- Modify: `backend/app/order_writes.py` (`place_order`, after the lines are built)
- Test: `backend/tests/test_orders.py` (add a case)

**Interfaces:**
- Consumes: `SalesOrderItemShare` (Task 1).
- Produces: nothing new; every order line now has at least one share.

Checkout does not go through `sales_writes` — it is a customer buying from the
shop, not the owner recording a sale — so the share must be written where the
line is made. Putting it in `place_order` also means **every** line ever made
has shares, which is what `sale_state` (Task 6) relies on.

- [ ] **Step 1: Write the failing test**

```python
def test_a_shop_checkout_line_carries_a_share(
    client: TestClient, db: Session, store_listing: Listing, customer_token: str
) -> None:
    """One share for one item: the permanent order-to-item link.

    Without this, `sale_state` would find store orders through shares and
    outside orders not at all -- two query shapes and one of them wrong.
    """
    response = client.post(
        "/api/checkout",
        json={"items": [{"listing_id": store_listing.id, "quantity": 1}]},
        headers={"Authorization": f"Bearer {customer_token}"},
    )
    assert response.status_code == 201
    order = db.get(SalesOrder, response.json()["id"])
    assert [share.inventory_item_id for share in order.items[0].shares] == [
        store_listing.inventory_item_id
    ]
    assert order.items[0].shares[0].amount == store_listing.price
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest backend/tests/test_orders.py -k share -v`
Expected: FAIL — `AttributeError: 'SalesOrderItem' object has no attribute 'shares'`
if Task 1's relationship is missing, otherwise `assert [] == [3]`.

- [ ] **Step 3: Write the share in `place_order`**

In `place_order`, immediately after `order.items.append(...)` and the
`_after_stock_change` call, inside the same loop:

```python
        # Every line gets shares, a single item included: `sale_state` and
        # realised gain both ask this table "which items did this order
        # carry", and a line with no shares would silently answer "none".
        # A lot listing's line is divided among its members (phase 3); an
        # item listing's line is one share carrying the whole amount.
        db.flush()
        _add_shares(db, order.items[-1], listing, price * line.quantity)
```

and the helper, beside `_line`:

```python
def _add_shares(
    db: Session, line: SalesOrderItem, listing: Listing, amount: Decimal
) -> None:
    """Divide a line's money among the items the listing offered.

    Fees are not known at checkout -- the shop charges none -- so
    `fee_amount` keeps its zero default. An outside sale sets it through
    `sales_writes`.
    """
    if listing.inventory_item_id is None:  # pragma: no cover - phase 3
        return
    db.add(
        SalesOrderItemShare(
            sales_order_item_id=line.id,
            inventory_item_id=listing.inventory_item_id,
            amount=amount,
            fee_amount=Decimal("0.00"),
        )
    )
```

**Watch the flush.** `SalesOrderItemShare` needs `line.id`, which does not
exist until the order and its lines are flushed. The `db.flush()` above is
why; without it `sales_order_item_id` is None and the insert fails on a
not-null violation. Do not move the share creation before it.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_orders.py backend/tests/test_sales_writes.py -v`
Expected: **`test_sales_writes.py` now FAILS** with an `IntegrityError` on
`uq_share_line_item` — and that is the constraint from Task 1 doing its job.
`record_sale` calls `place_order`, so a single-item sale now gets a share from
here **and** inserts its own.

Resolve it in one place: `place_order` creates the shares, `record_sale` fills
in the fee half. Replace the share loop written in Task 4, Step 3 with:

```python
    # `place_order` has already created one share per item the listing
    # offered, with `fee_amount` zero -- checkout knows no fees. Here the
    # fees are known, so this fills them in rather than inserting a second
    # set. Two writers of one row is how the amounts drift apart.
    line = order.items[0]
    shares = sorted(line.shares, key=lambda share: share.inventory_item_id)
    items_by_id = {item.id: item for item in items}
    weights = _weights([items_by_id[share.inventory_item_id] for share in shares], equal=equal_shares)
    amounts = allocate(price, weights)
    fee_amounts = allocate(total_fees, weights)
    for share, amount, fee_amount in zip(shares, amounts, fee_amounts, strict=True):
        share.amount = amount
        share.fee_amount = fee_amount
```

Note `strict=True` on both `zip`s: a silent length mismatch would assign one
item's money to another. Re-run and expect PASS.

- [ ] **Step 5: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/order_writes.py backend/tests/test_orders.py
git commit -m "Give every order line a share, so an order names its items"
```

---

### Task 6: The for-sale warning reaches an item through its shares

Closes the gap `app/sale_state.py:114-127` defers to phase 3.

**Files:**
- Modify: `backend/app/sale_state.py:114-140` (the order half)
- Test: `backend/tests/test_for_sale_guards.py` (add cases)

**Interfaces:**
- Consumes: `SalesOrderItemShare` (Task 1), shares on every line (Tasks 4, 5).
- Produces: unchanged signature — `for_sale(db, item_ids) -> dict[int, list[SaleUse]]`.

- [ ] **Step 1: Write the failing test**

```python
def test_an_item_sold_through_a_finished_sale_still_warns(
    db: Session, ebay_listing: Listing, admin_user: User
) -> None:
    """A sold coin's record is what a buyer was shown; editing it needs care.

    The claim is `released` at sale and the listing is `ended`, so before
    shares existed neither half of `for_sale` could find this item.
    """
    item_id = ebay_listing.inventory_item_id
    record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id=None,
        fees=[],
        recorded_by=admin_user,
    )
    uses = sale_state.for_sale(db, [item_id])
    assert any(use.kind == "order" for use in uses[item_id])
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -k finished_sale -v`
Expected: FAIL — `KeyError` or an empty list: the order half finds nothing.

- [ ] **Step 3: Replace the order half**

Replace the comment block at `sale_state.py:114-127` and the query beneath it:

```python
    # Reached through `sales_order_item_share`, which names every item on
    # every line -- one share for an item listing, one per member for a lot.
    # The direct link (`listing.inventory_item_id`) cannot do this: a lot
    # listing names no item, and after a sale the claim is `released`, so
    # neither the claim nor the link finds the pieces that were sold. A share
    # is permanent, which is why every line has them, a single-item store
    # sale included (`order_writes._add_shares`). Decided 2026-09-20; this
    # is the rule the module previously deferred to phase 3.
    orders = db.execute(
        select(SalesOrderItemShare.inventory_item_id, SalesOrder.id, SalesOrderStatus.code)
        .join(
            SalesOrderItem,
            SalesOrderItem.id == SalesOrderItemShare.sales_order_item_id,
        )
        .join(SalesOrder, SalesOrder.id == SalesOrderItem.sales_order_id)
        .join(SalesOrderStatus, SalesOrderStatus.id == SalesOrder.sales_order_status_id)
        .where(
            SalesOrderItemShare.inventory_item_id.in_(ids),
            SalesOrderStatus.code.in_(OPEN_ORDER_STATUSES),
        )
        .distinct()
    ).tuples()
```

- [ ] **Step 4: Decide and state whether a delivered sale still warns**

`OPEN_ORDER_STATUSES` decides this. A `delivered` auction-house sale is not an
open order, so it would not warn — but the item is gone and its record is what
the buyer was shown. Read the constant, and **if `delivered` is excluded, add
a test asserting that and a comment saying it is deliberate.** Do not widen
`OPEN_ORDER_STATUSES` silently: it is read by the order screens too.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -v`
Expected: PASS, with every existing case still green — the guard's other
callers (receiving, split, item errors, images, vocabulary merge) go through
the same function.

- [ ] **Step 6: Update the module docstring and commit**

`sale_state.py`'s docstring describes two ways an item is on a listing. Add
that an order reaches an item through its shares, and remove the sentence
deferring the rule to phase 3.

```
scripts\ccweb_check.cmd
git add backend/app/sale_state.py backend/tests/test_for_sale_guards.py
git commit -m "Warn about an item a finished sale carried, through its shares"
```

---

### Task 7: `POST /api/listings/{id}/sale`

**Files:**
- Modify: `backend/app/routers/offers.py` (add the endpoint)
- Modify: `backend/app/schemas.py` (three schemas)
- Test: `backend/tests/test_record_sale_api.py`

**Interfaces:**
- Consumes: `record_sale`, `FeeLine`, `SaleRefused` (Task 4).
- Produces: `RecordSaleIn`, `FeeLineIn`, `SaleRecordedOut`.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_record_sale_api.py
"""The HTTP face of recording an outside sale."""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.models import Listing, ListingStatus


def test_recording_a_sale_returns_the_order_and_net(
    client: TestClient, db: Session, ebay_listing: Listing, admin_token: str
) -> None:
    """Gross, fees and net, so the console shows what was actually made."""
    response = client.post(
        f"/api/listings/{ebay_listing.id}/sale",
        json={
            "price": "120.00",
            "buyer_username": "coinfan88",
            "external_order_id": "04-12345-67890",
            "fees": [{"kind": "commission", "amount": "15.90"}],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["total_amount"] == "120.00"
    assert body["fee_total"] == "15.90"
    assert body["net_amount"] == "104.10"


def test_an_anonymous_request_is_refused(
    client: TestClient, ebay_listing: Listing
) -> None:
    """Fees and cost basis are staff-only; this endpoint is admin-only."""
    response = client.post(
        f"/api/listings/{ebay_listing.id}/sale",
        json={"price": "120.00", "buyer_username": "x", "fees": []},
    )
    assert response.status_code == 401


def test_selling_an_ended_listing_is_a_409(
    client: TestClient, db: Session, ebay_listing: Listing, admin_token: str
) -> None:
    """The refusal names what is in the way, per the spec's error rules."""
    offering_writes.end_offer(db, ebay_listing)
    db.commit()
    response = client.post(
        f"/api/listings/{ebay_listing.id}/sale",
        json={"price": "120.00", "buyer_username": "x", "fees": []},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409
    assert "not on offer" in response.json()["detail"]


def test_an_unknown_fee_kind_is_a_422(
    client: TestClient, ebay_listing: Listing, admin_token: str
) -> None:
    """A code in the wrong vocabulary must not vanish into a default."""
    response = client.post(
        f"/api/listings/{ebay_listing.id}/sale",
        json={
            "price": "120.00",
            "buyer_username": "x",
            "fees": [{"kind": "gratuity", "amount": "1.00"}],
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 422


def test_money_is_returned_as_a_string(
    client: TestClient, ebay_listing: Listing, admin_token: str
) -> None:
    """FastAPI turns a Decimal in a plain dict into a float; this must not."""
    response = client.post(
        f"/api/listings/{ebay_listing.id}/sale",
        json={"price": "120.00", "buyer_username": "x", "fees": []},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert isinstance(response.json()["net_amount"], str)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_record_sale_api.py -v`
Expected: FAIL — 404, the route does not exist.

- [ ] **Step 3: Add the schemas**

In `backend/app/schemas.py`, following the file's existing conventions for
money (`Decimal` fields serialised as strings):

```python
class FeeLineIn(BaseModel):
    """One actual fee from the platform's statement."""

    kind: str
    amount: Decimal
    note: str | None = None


class RecordSaleIn(BaseModel):
    """A sale that happened on an outside platform, entered after the fact."""

    price: Decimal
    buyer_username: str | None = None
    external_order_id: str | None = None
    fees: list[FeeLineIn] = Field(default_factory=list)
    equal_shares: bool = False


class SaleRecordedOut(BaseModel):
    """What was recorded, with the arithmetic the console shows back."""

    id: int
    external_order_id: str | None
    total_amount: Decimal
    fee_total: Decimal
    net_amount: Decimal
    buyer: str
    item_codes: list[str]
```

- [ ] **Step 4: Add the endpoint**

In `backend/app/routers/offers.py`, following the module's existing pattern —
it resolves codes, owns the transaction and shapes the response, and decides
nothing:

```python
@router.post(
    "/listings/{listing_id}/sale",
    response_model=SaleRecordedOut,
    status_code=status.HTTP_201_CREATED,
)
def record_listing_sale(
    listing_id: int, body: RecordSaleIn, db: DbSession, user: AdminUser
) -> SaleRecordedOut:
    """Record that a listing sold on its platform, with the platform's fees.

    The sale is over by the time it is entered, so this both creates the
    order and ends the listing, in one transaction: a sale recorded with the
    listing left on offer would be an item for sale that is already gone.
    """
    listing = db.get(Listing, listing_id)
    if listing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No listing {listing_id}")
    try:
        order = sales_writes.record_sale(
            db,
            listing,
            price=body.price,
            buyer_username=body.buyer_username,
            external_order_id=body.external_order_id,
            fees=[
                sales_writes.FeeLine(fee.kind, fee.amount, fee.note)
                for fee in body.fees
            ],
            recorded_by=user,
            equal_shares=body.equal_shares,
        )
    except sales_writes.SaleRefused as refused:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, str(refused)) from refused
    except StaleDataError as stale:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, _STALE) from stale
    db.commit()
    return _sale_recorded(db, order)
```

**Capture ids before any flush can fail.** After a failed flush, reading any
ORM attribute is itself a 500: a `StaleDataError` expires every instance and
leaves the transaction awaiting rollback, so `f"order {order.id}"` in an
exception handler issues a SELECT and raises `PendingRollbackError` instead of
the 409. This repo has been bitten twice. Read plain ids into locals before
the `try`, and keep implicit autoflushes (`db.get`, lazy loads, `require_code`)
inside it.

An unknown fee code must be a **422**, not a 500 or a silent default —
`require_code` raises for an unknown code; confirm which exception and map it.
A word in the wrong vocabulary vanishing into a default is a failure mode this
project has hit before.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_record_sale_api.py -v`
Expected: PASS.

- [ ] **Step 6: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/routers/offers.py backend/app/schemas.py backend/tests/test_record_sale_api.py
git commit -m "Serve recording an outside sale over the API"
```

---

### Task 8: `seed.py` offers through `offering_writes`

**Files:**
- Modify: `backend/app/seed.py` (the listing creation)
- Test: `backend/tests/test_seed.py` (add a case)

`seed.py` creates an active `Listing` directly, with no `offer_claim` — the one
sanctioned exception to "every listing has a claim". It is dev-only, but
`sale_state._offering` compensates for it by asking both the claim and the
direct link, and phase 3 makes that compensation load-bearing. Remove the
exception instead.

- [ ] **Step 1: Write the failing test**

```python
def test_every_seeded_listing_has_a_claim(db: Session) -> None:
    """The invariant holds with no exceptions, dev data included."""
    seed.run(db)
    listings = db.scalars(select(Listing)).all()
    assert listings
    for listing in listings:
        claims = db.scalars(
            select(OfferClaim).where(OfferClaim.listing_id == listing.id)
        ).all()
        assert claims, f"listing {listing.id} has no claim"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest backend/tests/test_seed.py -k claim -v`
Expected: FAIL — `AssertionError: listing 1 has no claim`.

- [ ] **Step 3: Replace the direct construction**

Find where `seed.py` builds `Listing(...)` and call `offering_writes.offer`
with the same values instead. The item must be `received` first — `offer`
refuses anything else — so the seed's items may need their disposition set
through `lifecycle_writes` before being offered.

- [ ] **Step 4: Remove the compensation that is no longer needed**

In `app/sale_state.py`, `_offering` asks both the claims and
`listing.inventory_item_id` and its docstring names `app.seed` as the reason
for the second. Leave the second source in place — a listing made before
claims existed still needs it — but **correct the docstring**: `seed.py` is no
longer an example of it. A comment that names a reason which is no longer true
is worse than no comment.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_seed.py backend/tests/test_for_sale_guards.py -v`
Expected: PASS.

- [ ] **Step 6: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/seed.py backend/app/sale_state.py backend/tests/test_seed.py
git commit -m "Make the demo data obey the claim rule like everything else"
```

---

### Task 9: The suite-wide claim invariant

`docs/specs/selling-design.md` (*Testing*) requires this and it was never
built; it would have caught Task 8 by itself.

**Files:**
- Modify: `backend/tests/conftest.py` (an autouse fixture)
- Test: `backend/tests/test_claim_invariant.py` (proves the check can fail)

- [ ] **Step 1: Write the check as an autouse fixture**

```python
@pytest.fixture(autouse=True)
def _claim_invariant(db: Session) -> Iterator[None]:
    """After every test, a claim's state must equal its listing's status.

    `offering_writes` writes the two together in one transaction, so any
    disagreement means something wrote around it. Checking after every test
    in the suite is deliberate: the bug this catches is a *missing* call, and
    a missing call has no test of its own to fail.
    """
    yield
    if not db.is_active:
        return
    rows = db.execute(
        select(Listing.id, Listing.status, OfferClaim.state)
        .join(OfferClaim, OfferClaim.listing_id == Listing.id)
    ).tuples()
    expected = {
        ListingStatus.active: ClaimState.active,
        ListingStatus.paused: ClaimState.paused,
        ListingStatus.ended: ClaimState.released,
    }
    wrong = [
        (listing_id, status, state)
        for listing_id, status, state in rows
        if state is not expected[status]
    ]
    assert not wrong, f"claim state disagrees with listing status: {wrong}"
```

- [ ] **Step 2: Mutation-prove it**

A check that cannot fail is worse than none — this repo found 14 unfailable
tests across two branches by asking this question. Write a test that breaks
the invariant on purpose and confirm the fixture catches it:

```python
# backend/tests/test_claim_invariant.py
"""Proof that the suite-wide claim check can actually fail.

Without this, a fixture that silently passed on every input would look
exactly like a fixture that works.
"""


def test_the_invariant_fixture_catches_a_disagreement(
    db: Session, ebay_listing: Listing
) -> None:
    """Writing a claim around `offering_writes` is what this must catch."""
    claim = db.scalar(select(OfferClaim).where(OfferClaim.listing_id == ebay_listing.id))
    claim.state = ClaimState.released  # the listing is still active
    db.flush()
    # The autouse fixture runs after this test and must fail it. Assert the
    # condition here directly as well, so the intent is readable:
    assert claim.state is not ClaimState.active
```

Run the suite and confirm this test **fails** in teardown with the invariant
message. Then invert it: mark it `xfail(strict=True)` so it stays a permanent,
green proof that the check bites.

- [ ] **Step 3: Run the whole suite**

Run: `python -m pytest backend/tests -v`
Expected: PASS. If other tests now fail, the invariant has found real
disagreements — fix them in `offering_writes`, not by weakening the check.

- [ ] **Step 4: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/tests/conftest.py backend/tests/test_claim_invariant.py
git commit -m "Check after every test that a claim agrees with its listing"
```

---

### Task 10: Record sale… in the console

**Files:**
- Modify: `frontend/src/owner/api.js` (one method)
- Create: `frontend/src/owner/pages/RecordSaleDialog.jsx`
- Create: `frontend/src/owner/pages/RecordSaleDialog.test.jsx`
- Modify: `frontend/src/owner/pages/Listings.jsx` (row action)
- Modify: `frontend/src/owner/pages/Listings.test.jsx`

**Interfaces:**
- Consumes: `POST /api/listings/{id}/sale` (Task 7).
- Produces: `api.recordSale(listingId, body)`.

- [ ] **Step 1: Write the failing test**

```jsx
// frontend/src/owner/pages/RecordSaleDialog.test.jsx
it('sends the fees the user entered, as strings', async () => {
  const recordSale = vi.fn().mockResolvedValue({ id: 1, net_amount: '104.10' })
  renderWithProviders(<RecordSaleDialog listing={listing} />, { strict: true })
  await userEvent.type(screen.getByLabelText('Sale price'), '120.00')
  await userEvent.type(screen.getByLabelText('Commission'), '15.90')
  await userEvent.click(screen.getByRole('button', { name: 'Record sale' }))
  expect(recordSale).toHaveBeenCalledWith(listing.id, expect.objectContaining({
    price: '120.00',
    fees: [{ kind: 'commission', amount: '15.90' }],
  }))
})

it('shows net before the user confirms', async () => {
  // gross minus fees, computed in the dialog so the owner sees what they made
  // before committing to it
})

it('lists the refusal instead of closing when the server refuses', async () => {
  // a 409 carries `detail`; ApiError.body carries structured refusals
})
```

**Render with `strict: true`.** The console runs in `<StrictMode>` and the test
harness does not by default; a `mounted` ref set only in a cleanup is
permanently false in the app while its test passes. That trap has silenced a
save-failure message and stopped a dialog closing, both on `main`.

**Do not mock `api` wholesale for the request-body test.** Page tests that mock
the whole module test nothing about what the client actually sends — that is
how `api.updateImageLink` shipped clearing a photograph's role. Assert the body
in `owner/api.test.js`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `npm --prefix frontend test -- RecordSaleDialog`
Expected: FAIL — the component does not exist.

- [ ] **Step 3: Add the API method**

In `frontend/src/owner/api.js` — **not** `shared/api.js`, which every anonymous
shop visitor downloads:

```js
  recordSale: (listingId, body) =>
    post(`/api/listings/${listingId}/sale`, body),
```

- [ ] **Step 4: Build the dialog**

Following `EndOfferConfirm.jsx` for shape and `Platforms.jsx` for form
handling: sale price, buyer username (blank means the platform's undisclosed
buyer, and the dialog should say so for an auction house), external order id, a
fee row per kind fetched from the fee-kind vocabulary, and a live gross / fees /
net / margin summary. Send money as strings; do not let JavaScript arithmetic
near a stored figure — `Number()` on a money string is how a cent goes missing.

- [ ] **Step 5: Wire it into the Listings row actions**

Beside Edit and End, per the spec's console section.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `npm --prefix frontend test`
Expected: PASS.

- [ ] **Step 7: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add frontend/src/owner
git commit -m "Record a sale from the Listings page"
```

---

### Task 11: Documentation

**Files:**
- Modify: `docs/specs/selling-design.md` (mark 2R built)
- Modify: `docs/system-administration.md` (recording a sale, if it documents
  console workflows)
- Modify: `backend/app/sale_state.py` docstring (done in Task 6 — verify)

- [ ] **Step 1: Update the spec's phase list**

Mark phase 2R built, with the date. Leave phases 3 and 4 as they are.

- [ ] **Step 2: Check the docstrings say what the code does**

Re-read the docstrings written in Tasks 4-7 against the final code. A
docstring describing an earlier draft is a trap for the next reader; this repo
has a commit named for exactly that cleanup.

- [ ] **Step 3: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add docs backend/app
git commit -m "Document recording an outside sale"
```

---

## Verification before handing back

- [ ] `scripts\ccweb_check.cmd` exits zero.
- [ ] `python -m pytest backend/tests/test_migrations.py -v` passes, including
      `test_migrations_round_trip` and `test_migrations_match_models`.
- [ ] `git log --oneline main..HEAD` shows one commit per task, no merge
      commits, and `git merge-base --is-ancestor main HEAD` succeeds so the
      owner's merge can fast-forward.
- [ ] **The live database is untouched:** `psql -Atc "select version_num from
      alembic_version"` against `ccwebdb` still answers `e7c3a5b19d84`.
- [ ] Report to the owner: branch name, commit count, what was verified, and
      that phases 3 and 4 follow before anything is migrated.
