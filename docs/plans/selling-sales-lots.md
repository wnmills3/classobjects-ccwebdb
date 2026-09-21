# Selling, phase 3: sales lots — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Group several items into a temporary **sales lot**, offer it as one
thing on any platform, and have it dissolve back into its items if it does not
sell.

**Architecture:** A `listing` currently names exactly one item. It learns to
name a lot instead — exactly one of the two, enforced by a check constraint —
and everything that reads a listing learns to ask "which items?" rather than
"which item?". That question gets **one** answer in the codebase,
`offering_writes.offered_items`, which `_affected_items`,
`order_writes._after_stock_change`, `sales_writes._shared_items` and
`sale_snapshot.take` all call rather than each deciding for themselves.
`offering_writes` stays the only writer of listing status, claims and lot
state; a lot listing gets one `offer_claim` per member, which is why the
one-active-offer guarantee already lives on the claim rather than the listing.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL 16,
pytest; React + Vite for the console.

**Spec:** `docs/specs/selling-design.md` (revised 2026-09-20). Read *Three kinds
of lot*, *`sales_lot` and `sales_lot_item`*, *How things move*, *Errors* and
*Console*. Where this plan and the spec disagree, **the spec wins**; where this
plan and the code disagree, **the code wins**.

**Depends on:** `docs/plans/selling-record-a-sale.md` (phase 2R), **merged**
2026-09-21 at `6b080a0`. This plan divides a lot's money among its members
using `sales_writes.record_sale` and `sales_order_item_share`, which that plan
built.

**Rewritten 2026-09-21** against `.superpowers/sdd/selling-sales-lots/plan-audit.md`,
a read-only audit of the previous draft against the merged 2R code. The draft
was written before 2R existed and named sixteen things that had since moved,
including eleven fixtures that do not exist and two tests that would have
passed while testing nothing. Every interface named below was re-read in the
tree at `6b080a0`.

## Global Constraints

Every task's requirements implicitly include this section.

- **Branch:** `feat/sales-lots`, already cut from `main`. Never commit to
  `main`; the owner says when to merge.
- **The gate:** `scripts\ccweb_check.cmd` exits zero before every commit.
  `ccweb_check.cmd fix` auto-fixes formatting first. **Never pipe it** and
  never add `-qq` (pytest's `addopts` already carries `-q`); gate on the exit
  code. There is **no** `scripts\ccweb_test.cmd` — run pytest as
  `python -m pytest ...` from the `ccwebdb` conda environment.
- **The mutation-scaffolding stage.** `ccweb_check.cmd` greps `backend\app`
  for `if False:`, `if True:` and the literal `MUTATION` (case-sensitive) and
  fails if any is left behind. A mutation that leaves none of the three — a
  deleted `with_for_update()`, a dropped index — is invisible to it, so the
  convention is to leave a `MUTATION` comment at the site of **any**
  deliberate mutation while it is in place. Every mutation this plan asks for
  is temporary; the gate is what stops one shipping.
- **One pytest session at a time**, against `ccwebdb_test`. Two concurrent
  runs produce failures that mimic real bugs.
- **Money is `Decimal`**, never float, tests included. Shares must sum to
  their line exactly. A `Decimal` inside a **plain dict** returned from an
  endpoint becomes a float; a `Decimal` field on a Pydantic response model
  serializes as a **string** (`test_offers_api.py:147`), which is what the
  console expects.
- **`total_cost` is a generated column** (`item_cost + shipping_cost + tax`),
  so a fixture cannot assign it. Set `item_cost=` and, where exact arithmetic
  matters, `tax_rate=Decimal("0")` so `total_cost == item_cost`. Nothing in
  the suite passes `total_cost=`; several tests pass `item_cost=`
  (`test_sales_tax.py:49`).
- **Docstrings and annotations enforced** (ruff `D`, `ANN`) on every public
  class, method and function, tests included. `backend/tests` is in
  `[tool.mypy] files`, so an annotation that lies fails the gate.
- **`__mapper_args__` is a `@declared_attr.directive`**, never a dict
  literal — a literal trips RUF012, and the `ClassVar`/`Final` spellings that
  would silence it fail mypy instead. Copy `Listing`'s form
  (`models/sales.py:236-241`).
- **Enum columns use `models.base.enum_column(PyEnum, "pg_type_name")`**,
  never a raw `Enum(..., native_enum=True)`: `enum_column` passes
  `values_callable`, without which SQLAlchemy persists the member *name*
  rather than its value.
- **Do not migrate the live database.** Live is migrated only after phase 4
  merges too. `ccwebdb`'s `alembic_version` must still read `e7c3a5b19d84` at
  the end of this branch; `main`'s head is `c6908f789bf8`.
- **Writers stay single:** `offering_writes` alone writes `listing.status`,
  `offer_claim`, `sales_lot.status` and `sales_lot_item.released_at`;
  `order_writes` alone writes orders and `sales_order_item_share` **rows**;
  `sales_writes` fills in only `fee_amount` on rows that already exist and
  **never constructs a share** (its module docstring says so — keep it true).
  `routers/offers.py:428` computes `net_amount` "here and only here"; do not
  add a second.
- **`_sync_shares`'s `new_line` parameter.** `_sync_shares(db, line, listing,
  amount, *, new_line: bool = False)`. `new_line=True` means the caller
  created this line in this call, so it provably has no shares and
  `line.shares` **must not be read**: that read emits a SELECT inside the
  `FOR UPDATE` window *and* leaves the relationship cached empty for the rest
  of the session, because `db.add(share)` does not invalidate a collection an
  earlier read populated. `place_order` (`order_writes.py:334`) always passes
  `True`; `revise_order` (`:569`) passes the 4th element of `to_sync:
  list[tuple[SalesOrderItem, Listing, Decimal, bool]]`.
- **The `SaleRefused` / `SaleInputInvalid` split.** `SaleInputInvalid`
  subclasses `SaleRefused` (`sales_writes.py:68,72`). HTTP mapping is by
  `except`-clause **order** in `routers/offers.py:511-518`; mypy cannot check
  that order, and only
  `test_sale_input_invalid_from_record_sale_is_a_422_not_a_409` protects it.
  **409** = conflict or stale (not on offer, unmapped venue kind).
  **422** = bad input (negative or sub-cent money, an empty lot, a lot listing
  with quantity other than 1 — spec *Errors*, `:343-351`).
- **The autouse claim invariant.** `tests/conftest.py:292`'s `_claim_invariant`
  runs `check_claim_invariant(db)` after every test that has `db` in its
  closure. Its join is **`listing_id`-only, deliberately**, and its docstring
  (`:230-275`) names this plan and argues the case: an item-id join would drop
  every member claim, because `NULL = <member id>` is UNKNOWN in SQL. **It
  already grades lot claims correctly and must not be touched.**
- **The `claim_invariant_waiver` marker** (registered in `pyproject.toml`)
  makes the fixture run the check *and require it to raise*, so a waiver that
  stops biting fails loudly. Its `reason=` is mandatory. **Known limit
  (`conftest.py:379-386`): a waiver absorbs *any* `ClaimInvariantViolation`**
  — which is why the lot invariant added in Task 8 gets its own exception
  type.
- **Four race files are skipped by the autouse fixture entirely** —
  `test_offer_races.py`, `test_concurrency.py`, `test_concurrent_writes.py`,
  `test_order_revision_race.py` take `committed`, not `db`. Anything they
  commit must call the invariants explicitly, as `_cleanup_race_rows`
  (`test_offer_races.py:52-108`) already does.
- **Write tool for new files, Edit for surgical changes.** No shell heredocs.
- **cmd/batch only** for any script or documented command. No PowerShell.
  From bash, `cmd /c file.cmd` silently no-ops; `cmd //c file.cmd` works.
- Console API calls go in `frontend/src/owner/api.js`, never
  `shared/api.js`; a build check enforces it.
- **Every test must be able to fail for the reason its name claims.** Eleven
  tests were caught passing for the wrong reason during phase 2R, and the
  audit caught a twelfth in this plan's previous draft before a line of it was
  written. Where a test's value depends on it, this plan says which mutation
  proves it.

---

## Carried from phase 2R

Ten findings from the record-a-sale branch, recorded in that branch's scratch
directory, which is deleted at the end of its work — this plan is their only
durable home. Each was re-checked against `6b080a0` on 2026-09-21; the
corrections are marked. Each now names the task that closes it, so none is
carried without an owner.

a. **`order_writes._sync_shares` returns early when
   `listing.inventory_item_id is None`** (`order_writes.py:188`, marked
   `# pragma: no cover - phase 3 lots`). That early return sits *before*
   either branch, so a **revised** lot line's shares would silently stop
   matching the line's money. Remove the guard and widen both branches.
   **Corrected:** the previous draft said "the function's own docstring
   currently understates this". It no longer does — commit 8abd2f4 rewrote it
   (`:171-182`) to cover both branches in the same words. What the docstring
   does *not* yet cover is the interaction with `new_line`: for
   `new_line=True` the member list must come **from the lot**, never from
   `line.shares`; for `new_line=False` the amount is redistributed across the
   N existing rows keyed by `inventory_item_id`. A lot is frozen once offered,
   so the member set cannot have changed under a revision — if it ever has,
   refuse loudly rather than leaving an orphan share. **Closed by Task 4.**

b. **No multi-item division can be driven through `record_sale` on the 2R
   branch**, because a listing there maps to exactly one item. That branch's
   division test therefore points at `allocation.allocate` directly and says
   so in its docstring. **Closed by Task 4's
   `test_selling_a_lot_divides_the_price_among_its_members`**, which drives
   the division through `record_sale` for real. Nothing further to add.

c. **`sales_writes.record_sale`'s `shares_by_item[item.id]` is a bare
   `KeyError`** (`sales_writes.py:277`) if a share is ever absent for one of
   the listing's items. **Ruling, 2026-09-21: this is not a `SaleRefused`.**
   `SaleRefused` maps to 409, which tells a caller "retry, something is in the
   way" — false for an internal invariant violation that no retry can fix.
   Raise a dedicated exception, **unmapped in `routers/offers.py`** so it
   surfaces as a 500, carrying a message that names the item and the listing.
   **Closed by Task 4, Step 6.**

d. **`test_concurrency.py:66` and `test_order_revision_race.py:103` both do an
   unfiltered `cleanup.query(Listing).delete()`**, with no handling of
   `offer_claim.listing_id`'s `ondelete="RESTRICT"`. Neither file creates
   claims, so it has never fired. **Incomplete as recorded:**
   `test_offer_races.py` — the file Task 9 actually edits — has the *opposite*
   problem, a cleanup that is too **narrow**: `_cleanup_race_rows`
   (`:96-99`) deletes listings matching `Listing.inventory_item_id.in_(item_ids)`,
   and a lot listing's is NULL, so Task 9's lot listings survive and the
   `InventoryItem` delete right after fails on `sales_lot_item`'s RESTRICT
   **inside the `finally`**, masking whatever the `try` raised. **Closed by
   Task 9, Step 1**, which widens that filter and deletes `sales_lot_item` and
   `sales_lot` ahead of the items. The two unfiltered deletes in the other
   files are left as recorded: neither file writes a claim or a lot, so
   nothing this branch adds reaches them.

e. **The suite-wide invariant "an item's disposition agrees with its claims"
   is unscheduled anywhere.** The spec's *Testing* section lists three checks
   after every write; claim-state-vs-listing-status is built (2R), lot
   membership is Task 8, and this one was never picked up.
   **Ruling, 2026-09-21: the rule is one-directional.** "A held claim implies
   the item is `listed`", **not** the biconditional. The biconditional fails
   immediately: `conftest.build_listing` (`:703-757`) creates a **`listed`**
   item with **no claim**, and `listing` / `make_listing` are used by dozens
   of tests across `test_catalog.py`, `test_orders.py`,
   `test_for_sale_guards.py` and `test_sale_snapshots.py`. Do not rediscover
   this by running it.
   One further exception belongs in the rule as built, found while rewriting
   this plan and not in the audit: a **shop checkout that takes the last unit**
   sets the item to `sold` (`order_writes._after_stock_change`) while its
   store listing stays `active` with an `active` claim — `end_offer`'s own
   comment (`offering_writes.py:596-600`) names that shape. So the rule the
   suite can actually hold is "a claim in `HELD_BY` implies its item's
   disposition is `listed`, **or** in `SOLD_AWAY`" — same direction, with the
   sale's own writes allowed for. **Closed by Task 8, Step 4.**

f. **`sales_fee_kind` is missing from `_SEQUENCED_TABLES`**
   (`routers/reference.py:101-112`), so `GET /api/reference/sales_fee_kind`
   sorts by label instead of the migration's curated `sort_order`
   (commission 10, processing 20, listing 30, shipping_label 40,
   promotion 50, other 60). **Contested by the code, and overridden by the
   owner 2026-09-21:** commit 4fde7ee rewrote `test_reference.py:192-195`'s
   docstring to assert the opposite as deliberate ("fees are a descriptive
   list, so they are not in `_SEQUENCED_TABLES`"). The override stands on two
   facts. Alphabetical puts the catch-all **"Other" third** — the one position
   a catch-all should never occupy — and `docs/system-administration.md:884`
   already prints the curated order in writing, so the product disagrees with
   its own manual. **Closed by Task 10**, which changes `_SEQUENCED_TABLES`,
   its explanatory comment, the expected list in
   `test_the_fee_vocabulary_reaches_a_picker`, **and** that test's docstring.

g. **`frontend/src/owner/pages/RecordSaleDialog.test.jsx` fabricates a stale
   refusal message.** It mocks a 409 carrying `'Listing 14 is not on offer
   (ended)'` (`:153-165`), a sentence `record_sale` no longer sends — it names
   the platform too: `f"Listing {listing.id} on {listing.sales_venue.name} is
   not on offer ({listing.status.value})"` (`sales_writes.py:197-200`). The
   test still proves what its name claims, so this is fidelity drift rather
   than a wrong-reason pass. **Unassigned in the previous draft; closed by
   Task 11, Step 3.**

h. **`disposition` is the one lifecycle-ish fact on an item with no single
   writer.** Production sets `disposition_id` at four sanctioned transition
   sites (`offering_writes` ×2, `order_writes` ×2) and four row constructors
   (`importers/loader.py`, `seed.py`, `splitting.py`, `routers/inventory.py`'s
   split write) — and also as a freely settable classifier on
   `PATCH /api/inventory/items/{id}` and the bulk edit, because `disposition`
   sits in `ITEM_CLASSIFIERS` with no special case, while `status` is pulled
   out of that same loop one line above to route through
   `lifecycle_writes.set_status`. So an administrator can set an item to
   `sold` with no order, after which `offering_writes._refuse_sold`'s
   `SOLD_AWAY` check refuses to offer it ever again — a dead end with no
   remedy. Building (e)'s invariant first is the cheap way to learn whether
   anything already disagrees. **Whether `disposition` should leave
   `ITEM_CLASSIFIERS` is a separate decision for the owner and is not in this
   plan.** Task 8, Step 5 reports what the invariant found; it does not decide
   this.

i. **`OrderOut` (`schemas.py:292-313`) exposes no platform field**, so the
   Orders page cannot tell a shop sale from an outside sale, and its status
   dropdown still offers `cancelled` for an outside order behind a
   confirmation promising "Unshipped stock goes back on sale"
   (`Orders.jsx:55-61`) — no longer true for those orders, whose listing
   `record_sale` already ended. `routers/orders.py:281-292` refuses the
   transition safely with a 409 naming the platform, so this is a confusing
   prompt rather than a broken action. **Unassigned in the previous draft;
   closed by Task 11, Steps 1-2.**

j. **Nothing tests that a migration carries a `CHECK` constraint.**
   `test_migrations_match_models` (`test_migrations.py:795`) diffs with
   `compare_metadata`, which does not compare `CHECK` constraints, and
   `test_both_money_tables_refuse_a_negative_amount`
   (`test_sales_fees_schema.py`) runs against the `db` fixture, which
   `conftest.py` builds with `Base.metadata.create_all` from the **models**
   rather than by migrating. So `ck_sales_order_item_share_non_negative`'s
   copy in migration `c6908f789bf8` is unverified — and **Task 1 adds two
   more** (`ck_listing_item_xor_lot`, `ck_listing_lot_quantity_one`), doubling
   the exposure. **Closed by Task 1, Step 6.**

---

## What the audit found already correct, and what it got wrong

Recorded so the implementer does not re-derive either.

**Already lot-correct; change nothing:**

- `check_claim_invariant`'s `listing_id`-only join (`conftest.py:276-280`).
- `offering_writes._still_offered` (`:491`), `_locked_offers` (`:277`) and
  `offers_holding` (`:245`) — all three ask the claim table, and their
  docstrings say they were written for a lot.
- `sale_state.for_sale`'s order half, which joins through
  `SalesOrderItemShare.inventory_item_id` (`sale_state.py:139-156`).
- `uq_share_line_item` on `(sales_order_item_id, inventory_item_id)`
  (`models/sales.py:661`) permits N shares on one line.

**Two audit claims that did not hold up, verified against the tree:**

1. **"`sale_state._offering` filters only on `Listing.inventory_item_id`, so
   an offered lot's members would not raise the for-sale edit warning."** Not
   so. `_offering` (`sale_state.py:69-90`) has **two** halves, and the first
   is `offering_writes.claims_for`, which reads `offer_claim` — where a lot
   listing's members each have a row. The direct half is the fallback for
   listings written before claims existed. An offered lot's member is
   therefore already found, and `for_sale`'s describing query accepts the lot
   listing (status `active`, `quantity_available` 1 > 0). **No production
   change is needed; the guarantee is untested, which is what Task 7 fixes.**
2. **"`make_item(total_cost=Decimal("500.00"))` works."** It does not:
   `total_cost` is `Computed(..., persisted=True)` (`models/core.py:432-436`).
   Set `item_cost=` instead — see *Global Constraints*.

**Three things the audit did not name, found while rewriting:**

- `routers/orders.py:58-63`'s `_sold_as` falls back to
  `line.listing.inventory_item.source_title` when the snapshot carries no
  item title — an `AttributeError` for a lot order. **Task 4, Step 3.**
- `routers/offers.py:144-148`'s `_out` reads `listing.inventory_item.id` and
  `.item_code` to build `ListingOut`, whose `item_id: int` and
  `item_code: str` are required — so `GET /api/listings` 500s for every
  administrator the moment a lot listing exists. **Task 5, Step 5.**
- Widening `offer` does **not** change its callers. The audit expected it to;
  it does not, because `item` keeps its name and merely gains a default, and
  every existing call already passes it by keyword.

---

### Task 1: Schema — lots, membership, and a listing that names one or the other

**Files:**
- Modify: `backend/app/models/sales.py` (add two classes and one enum; widen
  `Listing`)
- Modify: `backend/app/models/__init__.py` (export `SalesLot`, `SalesLotItem`,
  `SalesLotStatus`)
- Create: `backend/alembic/versions/<generated>_sales_lots.py`
  (`down_revision = "c6908f789bf8"`)
- Test: `backend/tests/test_sales_lot_schema.py`
- Test: `backend/tests/test_migrations.py` (one new test, finding (j))

**Interfaces:**
- Consumes: `Base`, `TimestampMixin`, `enum_column` from `app/models/base.py`;
  `require_code` from `app/references.py`.
- Produces: `SalesLot`, `SalesLotItem`, `SalesLotStatus`;
  `Listing.sales_lot_id: Mapped[int | None]`;
  `Listing.inventory_item_id` becomes `Mapped[int | None]`;
  `Listing.inventory_item` becomes `Mapped[InventoryItem | None]`.

- [ ] **Step 1: Write the failing test**

Two things about this file decide whether it tests anything at all.
`Listing.currency_id` is `nullable=False` with **no default**
(`models/sales.py:250`), so a `Listing(...)` that omits it raises
`IntegrityError` on NOT NULL **before PostgreSQL evaluates either check
constraint** — both constraint tests would go green testing nothing. Every
`Listing` below therefore passes `currency_id`, and every one asserts on the
**constraint name** in the exception text rather than merely that an
`IntegrityError` was raised. (psycopg's message is
`violates check constraint "ck_listing_item_xor_lot"`, so the name is in
`str(excinfo.value)`.)

These tests end with the session's transaction deactivated, which is the
documented case `_claim_invariant` skips (`conftest.py:351-363`) — expected,
not a gap this file introduces.

```python
# backend/tests/test_sales_lot_schema.py
"""The database guarantees behind a sales lot.

Each of these is a constraint rather than a rule in Python, because each one
is a rule two concurrent requests could otherwise both believe they satisfy.

Every `Listing(...)` here passes `currency_id`. It is `nullable=False` with no
default, so omitting it fires `IntegrityError` on NOT NULL *before* PostgreSQL
evaluates any CHECK -- which would leave both constraint tests green while
testing nothing at all. Each raises-test asserts on the constraint's *name*
for the same reason.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from app.models import (
    Currency,
    InventoryItem,
    Listing,
    SalesLot,
    SalesLotItem,
    SalesVenue,
    utcnow,
)
from app.references import require_code
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _usd(db: Session) -> int:
    """The currency id every listing below needs. See the module docstring."""
    return require_code(db, Currency, "USD", "currency")


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
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "uq_sales_lot_item_open" in str(excinfo.value)


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
    db.flush()  # must not raise: the partial index counts only open rows


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
            currency_id=_usd(db),
            price=Decimal("10.00"),
            quantity_available=1,
        )
    )
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "ck_listing_item_xor_lot" in str(excinfo.value)


def test_a_listing_names_at_least_one_of_them(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """Neither would be an offer of nothing."""
    db.add(
        Listing(
            sales_venue_id=ebay_venue.id,
            currency_id=_usd(db),
            price=Decimal("10.00"),
            quantity_available=1,
        )
    )
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "ck_listing_item_xor_lot" in str(excinfo.value)


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
            currency_id=_usd(db),
            price=Decimal("10.00"),
            quantity_available=2,
        )
    )
    with pytest.raises(IntegrityError) as excinfo:
        db.flush()
    assert "ck_listing_lot_quantity_one" in str(excinfo.value)
```

**Mutation that proves the two constraint tests:** drop the `currency_id`
argument from `_usd(db)`'s call sites. Before this rewrite both tests passed
that way; after it they must fail on the *missing constraint name*, not on
NOT NULL. Confirm once, restore, and do not leave the mutation in.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest backend/tests/test_sales_lot_schema.py -v`
Expected: FAIL — `ImportError: cannot import name 'SalesLot' from 'app.models'`.

- [ ] **Step 3: Add the models**

In `backend/app/models/sales.py`, after `OfferClaim`:

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
    withdrawn it ends, releasing its members. It never comes back:
    re-offering a dissolved lot starts a new one, so each lot is a faithful
    record of one group that was offered once.
    """

    __tablename__ = "sales_lot"

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Public: what a buyer sees.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    status: Mapped[SalesLotStatus] = mapped_column(
        enum_column(SalesLotStatus, "sales_lot_status"),
        nullable=False,
        default=SalesLotStatus.assembling,
        server_default=SalesLotStatus.assembling.value,
    )
    #: Optimistic concurrency, as on Listing and InventoryItem.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    members: Mapped[list[SalesLotItem]] = relationship(
        back_populates="lot", cascade=_CASCADE_ALL_DELETE_ORPHAN
    )

    # A directive rather than a dict literal, for the reason `Listing` gives
    # above: a literal trips RUF012 and every annotation that would silence
    # it fails mypy instead.
    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        """Optimistic concurrency: every UPDATE checks the version it read."""
        return {"version_id_col": cls.version}


class SalesLotItem(Base):
    """One item's membership of one lot.

    `released_at` rather than deletion: which coins were in a lot that sold is
    part of the sale's record, and a dissolved lot is evidence of what was
    tried. The partial unique index is what stops an item being in two open
    lots at once -- the same shape, and the same reason, as
    `uq_offer_claim_active`.
    """

    __tablename__ = "sales_lot_item"

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_lot_id: Mapped[int] = mapped_column(
        ForeignKey("sales_lot.id", ondelete="CASCADE"), index=True, nullable=False
    )
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    released_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    lot: Mapped[SalesLot] = relationship(back_populates="members")
    #: The item itself. Named here because every reader of a lot needs it --
    #: shares, snapshots and the catalogue all ask "which coins" -- and a
    #: membership row with no way to reach its item makes each of them write
    #: its own join.
    item: Mapped[InventoryItem] = relationship()

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

Then widen `Listing` (`models/sales.py:243-247` and its `__table_args__`):

```python
    inventory_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="RESTRICT"),
        index=True,
        nullable=True,
    )
    #: Exactly one of this and `inventory_item_id` is set -- see
    #: `ck_listing_item_xor_lot`. A lot listing offers the group, and its
    #: members are reached through `sales_lot_item`.
    sales_lot_id: Mapped[int | None] = mapped_column(
        ForeignKey("sales_lot.id", ondelete="RESTRICT"), index=True, nullable=True
    )
```

and the relationship, which mypy will otherwise let lie:

```python
    inventory_item: Mapped[InventoryItem | None] = relationship(
        back_populates="listings"
    )
    sales_lot: Mapped[SalesLot | None] = relationship()
```

and two constraints in `__table_args__`:

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

Making `inventory_item` optional is what turns every existing
`listing.inventory_item.<x>` into a mypy error. **That error list is this
task's map of the rest of the branch** — do not silence any of it with a
cast; each site is fixed by the task that owns it (Tasks 4, 5, 6). Until
those land, mypy will fail, so **run the gate's earlier stages while
working and expect `python types` red until Task 6 commits.** Record the
site list in the branch's notes at the end of this step.

- [ ] **Step 4: Write the migration**

Generate, then correct by hand:

```
python -m alembic revision --autogenerate -m "sales lots"
```

- `down_revision = "c6908f789bf8"` — `main`'s head.
- **Name every foreign key, index and constraint.** An unnamed one cannot be
  dropped.
- **`downgrade()` must `DROP TYPE IF EXISTS sales_lot_status`.** Autogenerate
  never drops the enum types it creates, so the next `upgrade` fails with
  *"type already exists"*. This repo has hit it.
- **Check the drop order by hand:** the listing's two CHECK constraints and
  its `sales_lot_id` column before `sales_lot`; `sales_lot_item` before
  `sales_lot`.
- **Making `inventory_item_id` nullable is not autogenerated reliably** —
  confirm `alter_column("listing", "inventory_item_id", nullable=True)` is
  present, and that `downgrade()` restores `nullable=False` only **after**
  deleting any lot listings (there will be none on live; the round-trip test
  runs it anyway).
- `ix_listing_active` (`models/sales.py:315-319`) indexes
  `inventory_item_id` where `is_active`; a nullable column is fine in a
  partial index and the index needs no change. Confirm autogenerate left it
  alone rather than proposing a drop-and-recreate.

- [ ] **Step 5: Run the schema and migration tests**

Run: `python -m pytest backend/tests/test_sales_lot_schema.py backend/tests/test_migrations.py -v`
Expected: PASS, `test_migrations_round_trip` and `test_migrations_match_models`
included.

- [ ] **Step 6: Close finding (j) — prove the migration carries its CHECKs**

`test_migrations_match_models` diffs with `compare_metadata`, which ignores
`CHECK` constraints entirely, and the constraint tests above run against the
`create_all`-built `db`. So nothing yet proves the migration's own copies
exist. Add one test against `migrated_url` — a database built **only** by
running the migrations — modelled on
`test_the_fee_kind_migration_seeds_the_vocabulary` (`test_migrations.py:830`):

```python
#: Every CHECK constraint the models declare on a table the migrations build,
#: with the table it belongs to. `compare_metadata` (the diff
#: `test_migrations_match_models` runs) does not compare CHECK constraints at
#: all, so without this list a migration could omit one and both of this
#: file's other tests would stay green.
_EXPECTED_CHECKS = {
    ("listing", "ck_listing_item_xor_lot"),
    ("listing", "ck_listing_lot_quantity_one"),
    ("listing", "ck_listing_price_non_negative"),
    ("listing", "ck_listing_quantity_non_negative"),
    ("sales_order_fee", "ck_sales_order_fee_non_negative"),
    ("sales_order_item_share", "ck_sales_order_item_share_non_negative"),
}


def test_the_migration_carries_every_check_constraint(migrated_url: str) -> None:
    """A CHECK in the models but not in a migration is invisible to the diff.

    `compare_metadata` does not compare CHECK constraints, and the tests that
    do query them run against the `create_all`-built `db` fixture -- which is
    built from the models, so it would find a model's constraint whether the
    migration wrote one or not. Only a purely migrated database can tell.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", migrated_url)
    upgrade(config, "head")

    engine = create_engine(migrated_url)
    try:
        with engine.connect() as connection:
            found = set(
                connection.execute(
                    text(
                        "SELECT rel.relname, con.conname FROM pg_constraint con "
                        "JOIN pg_class rel ON rel.oid = con.conrelid "
                        "WHERE con.contype = 'c'"
                    )
                ).all()
            )
    finally:
        engine.dispose()

    missing = sorted(_EXPECTED_CHECKS - found)
    assert missing == [], (
        f"constraint(s) {missing} are declared on the models but no migration "
        "creates them, and compare_metadata cannot see the difference"
    )
```

**Mutation that proves it:** delete one `sa.CheckConstraint` from the new
migration's `create_table`/`create_check_constraint` call and confirm this
test — and only this test — fails. Restore.

- [ ] **Step 7: Run the gate and commit**

`python types` will still be red (Step 3); commit anyway, because a schema
commit that also fixes six readers is not reviewable. Run the rest:

```
scripts\ccweb_check.cmd
git add backend/app/models backend/alembic/versions backend/tests
git commit -m "Add sales lots, and let a listing name one instead of an item"
```

---

### Task 2: Assembling a lot

**Files:**
- Create: `backend/app/lot_writes.py`
- Modify: `backend/tests/conftest.py` (three new fixtures, Step 5)
- Test: `backend/tests/test_lot_writes.py`

**Interfaces:**
- Consumes: `SalesLot`, `SalesLotItem`, `SalesLotStatus`, `InventoryItem`,
  `Listing`, `SalesOrderItem`; `offering_writes.ON_OFFER`.
- Produces:
  `create_lot(db, *, title: str, description: str = "") -> SalesLot`;
  `add_member(db, lot: SalesLot, item: InventoryItem) -> SalesLotItem`;
  `remove_member(db, lot: SalesLot, item: InventoryItem) -> None`;
  `open_members(db, lot: SalesLot) -> list[SalesLotItem]` (in item id order);
  `LotRefused(Exception)`; `EmptyLot(LotRefused)`.

Membership changes only while `assembling`; everything about a lot's *offer*
belongs to `offering_writes`, which Task 3 widens. Keeping assembly separate
is what stops this module needing the claim rules — and it is why
`offering_writes` may import from here without a cycle.

**`EmptyLot` subclasses `LotRefused` deliberately**, mirroring
`SaleInputInvalid`/`SaleRefused`: the router maps `EmptyLot` to **422** ("an
empty lot", spec *Errors*) and the plain `LotRefused` to **409**, by
`except`-clause order, and every existing `except LotRefused` keeps catching
both.

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lot_writes.py
"""Assembling a sales lot, and what cannot be in one."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import pytest
from app import lot_writes
from app.lot_writes import LotRefused, add_member, create_lot, remove_member
from app.models import InventoryItem, Listing, SalesLot, SalesLotStatus, utcnow
from sqlalchemy import select
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]


def _lot(db: Session, title: str = "Three Morgan Dollars") -> SalesLot:
    """An empty assembling lot, through the writer rather than by hand."""
    return create_lot(db, title=title, description="")


def test_a_new_lot_is_assembling_and_empty(db: Session) -> None:
    """Nothing is grouped until someone adds it."""
    lot = _lot(db)
    assert lot.status is SalesLotStatus.assembling
    assert lot_writes.open_members(db, lot) == []


def test_membership_is_frozen_once_offered(
    db: Session, received_item: InventoryItem
) -> None:
    """The buyer is looking at that exact group."""
    lot = _lot(db)
    lot.status = SalesLotStatus.offered
    db.flush()
    with pytest.raises(LotRefused, match="offered"):
        add_member(db, lot, received_item)


def test_an_item_with_stock_above_one_cannot_join(
    db: Session, listing: Listing
) -> None:
    """A claim covers a whole item; a partly-sold item is not a whole item.

    The `listing` fixture's `quantity_available` is 5 (conftest's
    `build_listing` default), which is exactly the shape this refuses.
    """
    item = listing.inventory_item
    assert item is not None
    with pytest.raises(LotRefused, match="quantity"):
        add_member(db, _lot(db), item)


def test_a_split_item_cannot_join(
    db: Session, received_item: InventoryItem
) -> None:
    """Offer the pieces, the same refusal `offering_writes` gives."""
    received_item.split_at = utcnow()
    db.flush()
    with pytest.raises(LotRefused, match="split"):
        add_member(db, _lot(db), received_item)


def test_an_item_already_in_an_open_lot_cannot_join_another(
    db: Session, received_item: InventoryItem
) -> None:
    """Refused with a message, not an IntegrityError in the user's face."""
    first, second = _lot(db, "First"), _lot(db, "Second")
    add_member(db, first, received_item)
    with pytest.raises(LotRefused, match="already in lot"):
        add_member(db, second, received_item)


def test_removing_a_member_releases_it(
    db: Session, received_item: InventoryItem
) -> None:
    """And the item can then join another lot.

    Both lots are taken as locals here. The previous draft of this test named
    `other_lot` in its body without taking it as a parameter -- a `NameError`
    that would have been discovered only at run time.
    """
    first, second = _lot(db, "First"), _lot(db, "Second")
    add_member(db, first, received_item)
    remove_member(db, first, received_item)
    membership = db.scalars(
        select(lot_writes.SalesLotItem).where(
            lot_writes.SalesLotItem.sales_lot_id == first.id
        )
    ).one()
    assert membership.released_at is not None
    add_member(db, second, received_item)  # must not raise
    assert [row.inventory_item_id for row in lot_writes.open_members(db, second)] == [
        received_item.id
    ]


def test_a_refused_add_writes_nothing(
    db: Session, listing: Listing, make_item: ItemFactory
) -> None:
    """A refused change leaves the lot exactly as it was.

    The same discipline `offering_writes.offer` follows: every condition is
    checked before anything is written, so a batch can be all or nothing.
    """
    lot = _lot(db)
    good = make_item(title="Joins fine", item_cost=Decimal("10.00"))
    add_member(db, lot, good)
    busy = listing.inventory_item
    assert busy is not None
    with pytest.raises(LotRefused):
        add_member(db, lot, busy)
    assert [row.inventory_item_id for row in lot_writes.open_members(db, lot)] == [
        good.id
    ]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_lot_writes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.lot_writes'`.

- [ ] **Step 3: Write the module**

`backend/app/lot_writes.py`. Functions flush and never commit; the caller's
request owns the transaction, as everywhere else in this codebase.

`add_member` refuses, with a message naming the item's `item_code`, when:

- the lot is not `assembling` (`f"lot #{lot.id} is {lot.status.value}, so its
  membership is frozen"`);
- the item is deleted (`deleted_at`), split (`split_at`), or its status is not
  `received` — the same three `offering_writes._refuse_unofferable` asks, in
  the same order and the same words;
- the item has a listing in `offering_writes.ON_OFFER` with
  `quantity_available > 1`, or any `SalesOrderItem` row against a listing of
  that item ("sold units") — spec, *`sales_lot` and `sales_lot_item`*: a claim
  covers the whole item, and a partly sold broken-up lot is not a whole item;
- the item is already in an open lot
  (`f"{item.item_code} is already in lot #{other.id}"`).

**Check every condition before writing anything.** The partial unique index
`uq_sales_lot_item_open` is the backstop, not the check: catch `IntegrityError`
around the flush and re-raise as `LotRefused` with the same "already in lot"
message, so a race produces what a sequential attempt produces rather than a
500.

`remove_member` sets `released_at = utcnow()` on the open membership row and
refuses if the lot is not `assembling`. It does **not** delete the row: which
coins were in a lot is part of its record.

`open_members(db, lot)` returns the `SalesLotItem` rows with
`released_at IS NULL`, **ordered by `inventory_item_id`**. Every downstream
reader — locking order, share order, snapshot order — depends on that being
one deterministic sequence decided here.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_lot_writes.py -v`
Expected: PASS.

- [ ] **Step 5: Add the fixtures the rest of the plan uses**

The previous draft invented eleven lot fixtures and two token fixtures that do
not exist. These three are the only new ones; everything else in this plan
uses `db`, `client`, `admin_user`, `customer_user`, **`admin_headers`** and
**`customer_headers`** (there is no `admin_token` or `customer_token`),
`ebay_venue`, `whatnot_venue`, `heritage_venue`, `received_item`,
`ebay_listing`, `heritage_listing`, `stored_then_ebay`, `listing`,
`make_item` and `make_listing`, exactly as `conftest.py` defines them.

In `backend/tests/conftest.py`, after `make_listing`:

```python
def build_lot(
    db: Session,
    items: Sequence[InventoryItem],
    *,
    title: str = "Three Morgan Dollars",
    description: str = "",
) -> SalesLot:
    """An assembling lot holding these items, built through `lot_writes`.

    Through the writer rather than by hand, for the reason `ebay_listing`
    goes through `offering_writes.offer`: a lot assembled around the rules is
    a lot the rules have accepted, and a fixture that inserted the rows
    directly would state the membership rules a second time.
    """
    lot = lot_writes.create_lot(db, title=title, description=description)
    for item in items:
        lot_writes.add_member(db, lot, item)
    db.flush()
    return lot


@pytest.fixture
def make_lot(db: Session) -> Callable[..., SalesLot]:
    """Factory for sales lots within one test."""

    def _make(items: Sequence[InventoryItem], **overrides: str) -> SalesLot:
        return build_lot(db, items, **overrides)

    return _make


@pytest.fixture
def lot_of_three(db: Session, make_item: Callable[..., InventoryItem]) -> SalesLot:
    """An assembling lot of three items with deliberately uneven cost bases.

    `item_cost`, never `total_cost`: `total_cost` is a generated column
    (`item_cost + shipping_cost + sales_tax`) and cannot be assigned.
    `tax_rate=0` makes the two equal, which is what lets a share assertion be
    exact rather than approximate -- every other item in the suite carries
    the configured 0.0635.
    """
    costs = (Decimal("500.00"), Decimal("300.00"), Decimal("200.00"))
    items = [
        make_item(
            title=f"Lot member {index}",
            item_cost=cost,
            tax_rate=Decimal("0"),
        )
        for index, cost in enumerate(costs, start=1)
    ]
    return build_lot(db, items)
```

Add `from app import lot_writes, offering_writes` and the
`SalesLot` import to conftest's existing import block, and
`from collections.abc import Sequence` to its `collections.abc` line.

**The three fixture traps, written down so nobody meets them twice:**
`make_listing`'s `quantity_available` defaults to **5**, so any lot-listing
fixture must pass `quantity_available=1` or violate
`ck_listing_lot_quantity_one`; `build_listing` creates a **`listed`** item with
**no claim**; `received_item` is **`held`** while `listing`'s item is
`listed` — they are not interchangeable.

- [ ] **Step 6: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/lot_writes.py backend/tests/test_lot_writes.py backend/tests/conftest.py
git commit -m "Assemble a sales lot, and refuse what cannot be in one"
```

---

### Task 3: Offering a lot, and dissolving it

**Files:**
- Modify: `backend/app/offering_writes.py` (`offer`, `_move_claims`,
  `_affected_items`, `_end`; new `offered_items`)
- Modify: `backend/tests/test_offering_writes.py` (add cases; rewrite three
  waiver reasons)
- Modify: `backend/tests/test_offer_races.py` (rewrite two docstrings)
- Modify: `backend/tests/conftest.py` (one fixture, Step 6)

**Interfaces:**
- Consumes: `lot_writes.open_members`, `LotRefused`, `EmptyLot`.
- Produces: `offer(db, *, item: InventoryItem | None = None, lot: SalesLot |
  None = None, venue, listing_format, price, title, description, external_id,
  quantity=1) -> Listing`;
  `offered_items(db, listing: Listing) -> list[InventoryItem]`.

**Existing callers do not change.** `item` keeps its name and gains a
default, and `routers/offers.py:264`, `conftest.py:564/586/608`,
`test_offer_races.py` and `test_offering_writes.py::_offer_on` all already
pass it by keyword.

- [ ] **Step 1: Write the failing test**

Appended to `backend/tests/test_offering_writes.py`, reusing that file's own
`_venue`, `_offer_on`, `_claim_states` and `_set_disposition` helpers.

```python
def test_offering_a_lot_claims_every_member(
    db: Session, lot_of_three: SalesLot, ebay_venue: SalesVenue
) -> None:
    """One claim per member: the one-offer guarantee is per item, not per listing."""
    listing = offering_writes.offer(
        db,
        lot=lot_of_three,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("900.00"),
        title="Three Morgan Dollars",
        description="",
        external_id=None,
    )
    claims = db.scalars(
        select(OfferClaim).where(OfferClaim.listing_id == listing.id)
    ).all()
    member_ids = {
        row.inventory_item_id for row in lot_writes.open_members(db, lot_of_three)
    }
    assert {claim.inventory_item_id for claim in claims} == member_ids
    assert all(claim.state is ClaimState.active for claim in claims)
    assert listing.inventory_item_id is None
    assert listing.quantity_available == 1
    assert lot_of_three.status is SalesLotStatus.offered


def test_offering_a_lot_pauses_each_member_s_store_listing(
    db: Session, make_item: ItemFactory, ebay_venue: SalesVenue
) -> None:
    """Shop to eBay is one step, for every coin in the group."""
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    items = [make_item(title=f"Stored {n}") for n in range(2)]
    store_listings = [_offer_on(db, item, store) for item in items]
    lot = lot_writes.create_lot(db, title="Two stored coins", description="")
    for item in items:
        lot_writes.add_member(db, lot, item)

    offering_writes.offer(
        db,
        lot=lot,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("200.00"),
        title="Two stored coins",
        description="",
        external_id=None,
    )

    for store_listing in store_listings:
        db.refresh(store_listing)
        assert store_listing.status is ListingStatus.paused
        assert store_listing.paused_by_listing_id is not None
    for item in items:
        assert _claim_states(db, item)[store_listings[items.index(item)].id] is (
            ClaimState.paused
        )


def test_a_member_offered_elsewhere_refuses_the_whole_lot(
    db: Session,
    make_item: ItemFactory,
    ebay_venue: SalesVenue,
    whatnot_venue: SalesVenue,
) -> None:
    """Named refusal, and nothing written: all or nothing, as for a batch."""
    free, busy = make_item(title="Free"), make_item(title="Busy")
    elsewhere = _offer_on(db, busy, whatnot_venue)
    lot = lot_writes.create_lot(db, title="One busy member", description="")
    lot_writes.add_member(db, lot, free)
    lot_writes.add_member(db, lot, busy)

    with pytest.raises(OfferRefused) as excinfo:
        offering_writes.offer(
            db,
            lot=lot,
            venue=ebay_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("50.00"),
            title="One busy member",
            description="",
            external_id=None,
        )
    assert excinfo.value.item_code == busy.item_code
    assert f"listing #{elsewhere.id}" in excinfo.value.reason
    assert "Whatnot" in excinfo.value.reason
    # Nothing written for the member that could have been offered.
    assert _claim_states(db, free) == {}
    assert lot.status is SalesLotStatus.assembling


def test_offering_an_empty_lot_is_bad_input(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """The spec's *Errors* list says 422 for an empty lot, not 409."""
    empty = lot_writes.create_lot(db, title="Nothing in it", description="")
    with pytest.raises(lot_writes.EmptyLot):
        offering_writes.offer(
            db,
            lot=empty,
            venue=ebay_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="Nothing in it",
            description="",
            external_id=None,
        )


def test_offering_neither_an_item_nor_a_lot_is_a_programming_error(
    db: Session, ebay_venue: SalesVenue
) -> None:
    """`ValueError`, not `OfferRefused`: there is no item code to name.

    `OfferRefused.__init__` requires an `item_code` and `routers/offers.py`
    reads it to build `OfferRefusalOut`, so the shape is load-bearing at the
    HTTP boundary. A caller that passed neither has a bug; the request
    schema (`OfferIn`) makes it unreachable from outside.
    """
    with pytest.raises(ValueError, match="exactly one"):
        offering_writes.offer(
            db,
            venue=ebay_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="",
            description="",
            external_id=None,
        )


def test_offering_both_an_item_and_a_lot_is_a_programming_error(
    db: Session,
    received_item: InventoryItem,
    lot_of_three: SalesLot,
    ebay_venue: SalesVenue,
) -> None:
    """Same reason, the other way round."""
    with pytest.raises(ValueError, match="exactly one"):
        offering_writes.offer(
            db,
            item=received_item,
            lot=lot_of_three,
            venue=ebay_venue,
            listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"),
            title="",
            description="",
            external_id=None,
        )


def test_ending_a_lot_listing_dissolves_the_lot(
    db: Session, offered_lot_listing: Listing
) -> None:
    """Unsold means the group is not a thing any more; the coins are free."""
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    offering_writes.end_offer(db, offered_lot_listing)
    db.refresh(lot)
    assert lot.status is SalesLotStatus.dissolved
    assert all(member.released_at is not None for member in lot.members)


def test_ending_a_lot_listing_does_not_trip_over_its_null_item(
    db: Session, offered_lot_listing: Listing
) -> None:
    """`_affected_items` used to put NULL in a set it then sorted.

    A lot listing's `inventory_item_id` is NULL, so the first of
    `_affected_items`' two queries yields `None`, and `sorted({None, 12, 13})`
    raises `TypeError: '<' not supported between instances of 'int' and
    'NoneType'` -- a crash, not a silent skip. `_lock_items` has the same
    shape. This test is the regression: it fails with that `TypeError`, not
    with an assertion, if the NULL filter is removed.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    expected = sorted(member.inventory_item_id for member in lot.members)
    assert offering_writes._affected_items(db, offered_lot_listing) == expected


def test_a_sold_lot_is_sold_not_dissolved(
    db: Session, offered_lot_listing: Listing
) -> None:
    """`sold` and `dissolved` are different histories and must stay apart."""
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    offering_writes.end_offer(db, offered_lot_listing, sold=True)
    db.refresh(lot)
    assert lot.status is SalesLotStatus.sold
    assert all(member.released_at is not None for member in lot.members)


def test_members_with_no_remaining_claim_go_held(
    db: Session, offered_lot_listing: Listing
) -> None:
    """Back in the drawer, not still marked as listed.

    Only `listed` moves back to `held` -- `end_offer`'s own rule, unchanged
    here: a member a sale had already moved past `listed` is not this
    function's to undo.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member_ids = [member.inventory_item_id for member in lot.members]
    offering_writes.end_offer(db, offered_lot_listing)
    for item_id in member_ids:
        item = db.get(InventoryItem, item_id)
        assert item is not None
        assert item.disposition.code == "held"
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_offering_writes.py -k lot -v`
Expected: FAIL — `TypeError: offer() got an unexpected keyword argument 'lot'`.

- [ ] **Step 3: Widen `offer`**

```python
def offer(
    db: Session,
    *,
    item: InventoryItem | None = None,
    lot: SalesLot | None = None,
    venue: SalesVenue,
    listing_format: ListingFormat,
    price: Decimal,
    title: str,
    description: str,
    external_id: str | None,
    quantity: int = 1,
) -> Listing:
```

- **Exactly one of `item` and `lot`.** `if (item is None) == (lot is None):
  raise ValueError("offer() takes exactly one of item= and lot=")`. **Not
  `OfferRefused`:** its `__init__` requires an `item_code` and there is none
  to give, and `OfferIn` prevents a caller sending both, so this is a
  programming error rather than a refusal a person can act on.
- **Build the member list once**, then reuse the existing machinery unchanged:
  `members = [item] if item is not None else [row.item for row in
  lot_writes.open_members(db, lot)]`. An empty list raises `lot_writes.EmptyLot`.
  Refuse a lot whose status is not `assembling` with `LotRefused`.
- `_lock_items(db, [m.id for m in members])` — it already sorts, which is the
  deadlock rule; a lot makes multi-row locking the normal case rather than the
  exception.
- `_refuse_unofferable` per member, `_locked_offers` per member, the same
  `elsewhere` / `to_pause` decisions per member, **all before any write** —
  so a lot with one busy member writes nothing at all.
- One `Listing` carrying `sales_lot_id=lot.id` and `inventory_item_id=None`
  for a lot (and `quantity=1`, which the check constraint enforces anyway),
  then one `OfferClaim` per member, then `disposition = listed` per member,
  then `lot.status = SalesLotStatus.offered`.
- **The single-item path comes out of this as a lot of one internally**, so
  there is one implementation rather than two that can drift. The existing
  single-item tests in this file are the proof it did not move.

Also guard `_move_claims` (`:194-204`): its "a listing made before claims
existed has none" branch constructs
`OfferClaim(inventory_item_id=listing.inventory_item_id, ...)`, which is
`None` for a lot listing and would fail NOT NULL. A lot listing always has
claims, so the fix is a one-line `if listing.inventory_item_id is None:
return` inside that branch, with a comment saying why.

- [ ] **Step 4: Add `offered_items`, and correct `_affected_items`**

**The previous draft's premise here was factually wrong.** It said
`_affected_items` "currently returns `[listing.inventory_item_id]`". It does
not (`:446-471`): it unions the item ids of this listing **and every listing
it paused** with **every `OfferClaim.inventory_item_id`** whose listing is in
that set and whose state is in `HELD_BY`, and returns `sorted(ids)`. The claim
half **already finds a lot's members**, because Step 3 writes one claim per
member. So this step is small, and it is not the step the draft described.

Two real changes:

1. **Filter the NULL.** For a lot listing the first query yields `None`, and
   `sorted({None, 12, 13})` raises `TypeError` — `end_offer` on a lot listing
   *crashes* rather than silently skipping. Drop `None` from the set before
   sorting:

   ```python
       ids = {
           item_id
           for item_id in db.scalars(
               select(Listing.inventory_item_id).where(Listing.id.in_(touched))
           ).all()
           if item_id is not None
       }
   ```

2. **One answer to "which items does this listing offer".** Add:

   ```python
   def offered_items(db: Session, listing: Listing) -> list[InventoryItem]:
       """The items this listing itself offers, in item id order.

       One for an item listing; a lot listing's open members for a lot. The
       one answer in the codebase to "which items": `_affected_items` below,
       `order_writes._after_stock_change`, `sales_writes._shared_items` and
       `sale_snapshot.take` all ask here rather than each deciding for
       themselves, because four answers to one question is four places for
       a lot's members to be silently skipped.

       Deliberately *narrower* than `_affected_items`, which also carries the
       items of listings this one paused: moving a paused listing's item is
       an ending's job, never a sale's.
       """
   ```

   `_affected_items` keeps its own two queries (it must also reach paused
   listings and released-elsewhere claims) and its docstring gains a sentence
   pointing at `offered_items` for the narrower question.

`_still_offered`, `_locked_offers` and `offers_holding` need **no change** —
their docstrings already say they were written for a lot, and they ask the
claim table. Say so in the commit message so a reviewer does not go looking.

- [ ] **Step 5: Widen `_end`**

`_end(db, listing)` (`:515-520`) ends one listing and releases what it held.
Add: when `listing.sales_lot_id` is set, set the lot's status — `sold` when
`end_offer`'s `sold=True`, `dissolved` otherwise — and `released_at =
utcnow()` on every open member. `_end` is also called for the store listings a
sold offer ends, and those are item listings, so the branch simply does not
fire for them. `_end` needs the `sold` flag threaded through from `end_offer`;
pass it as a keyword-only argument with a `False` default so the resume path
reads unchanged.

- [ ] **Step 6: Add the `offered_lot_listing` fixture**

In `conftest.py`, after `lot_of_three`:

```python
@pytest.fixture
def offered_lot_listing(
    db: Session, lot_of_three: SalesLot, ebay_venue: SalesVenue
) -> Listing:
    """A lot of three offered on eBay, with the three claims `offer` creates.

    Built through `offering_writes.offer` for the reason `ebay_listing` is:
    it carries the real claims a real offer produces, not a listing that
    merely looks like one. Price 1,000.00 against member costs of 500, 300
    and 200 -- so a cost-weighted division is 500.00 / 300.00 / 200.00 and an
    equal one is not, which is what makes the weighting assertions in Task 4
    able to fail.
    """
    return offering_writes.offer(
        db,
        lot=lot_of_three,
        venue=ebay_venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("1000.00"),
        title="Three Morgan Dollars",
        description="",
        external_id="987654",
    )
```

- [ ] **Step 7: Rewrite the waiver reasons that Task 3 makes untrue**

Four tests carry `claim_invariant_waiver` — **four, not three**:
`test_offering_writes.py:644`, `:739`, `:844`, `:869`. `:644` is the retired
`PATCH .../is_active` shape, not a lot shape; leave it alone.

Do the three lot-shaped waivers go stale? **No.** Their violations are *state*
disagreements (a `released` claim on an `active` listing), and this phase does
not change `_EXPECTED_CLAIM_STATE`, so the check keeps raising and the waivers
keep biting. But their prose stops being true:

- **`:739`** says the lot-member shape is one "which `offering_writes` cannot
  write yet". After Step 3 it can. Delete that clause and replace it with
  "which `offering_writes.offer` now writes for a real lot, though not with
  this test's deliberately mismatched *state*".
- **`:844` and `:869`** say "the lot-member shape phase 3 introduces". Change
  "introduces" to "phase 3 introduced", so a reader is not left looking for
  unwritten work.

The same prose appears twice in `test_offer_races.py` — `_cleanup_race_rows`'s
docstring (`:52-108`, "Latent today ... and phase 3 will write for real") and
`test_a_lot_shaped_claim_does_not_survive_cleanup_or_block_it`'s (`:247-300`,
"bypassing `offering_writes` on purpose (no write path creates it yet)").
Correct both to past tense and keep their substance: the *mismatched state* is
still something no write path produces, which is the part those tests actually
rest on.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_offering_writes.py backend/tests/test_offers_api.py -v`
Expected: PASS, with **every existing single-item case green** — they are the
proof the single-item path did not move.

- [ ] **Step 9: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/offering_writes.py backend/tests
git commit -m "Offer a sales lot, and dissolve it when the offer ends"
```

---

### Task 4: A lot listing can be snapshotted and sold

Four functions crash or silently do nothing on a lot listing today. All four
are found by reading, not by any existing test, because no lot listing has ever
existed.

**Files:**
- Modify: `backend/app/sale_snapshot.py` (`take`; the file is **67 lines**,
  and `take` is `:36-67`)
- Modify: `backend/app/routers/orders.py` (`_sold_as`)
- Modify: `backend/app/order_writes.py` (`_after_stock_change`, `_sync_shares`)
- Modify: `backend/app/sales_writes.py` (`_shared_items`, `record_sale`)
- Test: `backend/tests/test_sale_snapshots.py`,
  `backend/tests/test_sales_writes.py`,
  `backend/tests/test_order_revision.py`

**Interfaces:**
- Consumes: `offering_writes.offered_items` (Task 3);
  `allocation.allocate(total: Decimal, weights: list[Decimal]) -> list[Decimal]`
  — the annotation is `list`, **not `Sequence`**, so mypy rejects a tuple or a
  generator.
- Consumes: `record_sale(db, listing, *, price: Decimal, buyer_username: str |
  None, external_order_id: str | None, fees: Sequence[FeeLine], recorded_by:
  User, equal_shares: bool = False, status_code: str | None = None) ->
  SalesOrder`, with `FeeLine(kind_code: str, amount: Decimal, note: str | None
  = None)` (`sales_writes.py:96`). **All four of `price`, `buyer_username`,
  `external_order_id` and `fees` are required keywords** — the previous draft
  hid them behind `...`.
- Produces: `SNAPSHOT_VERSION = 2`; `ShareMissing(Exception)` in
  `sales_writes`.

- [ ] **Step 1: Write the failing test**

In `backend/tests/test_sale_snapshots.py`:

```python
def test_a_lot_listing_snapshots_every_member(
    db: Session, offered_lot_listing: Listing
) -> None:
    """`take` reads `listing.inventory_item` and would crash on None.

    The snapshot is what the order keeps forever, so a lot's must name every
    coin that was in it -- the membership rows are released at sale, and the
    group can be reconstructed from nothing but this.
    """
    snapshot = sale_snapshot.take(db, offered_lot_listing)
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    assert snapshot["snapshot_version"] == 2
    assert "item" not in snapshot
    assert snapshot["lot"]["title"] == lot.title
    assert len(snapshot["items"]) == 3
    assert [entry["item_code"] for entry in snapshot["items"]] == sorted(
        entry["item_code"] for entry in snapshot["items"]
    )


def test_an_item_listing_s_snapshot_keeps_its_shape(
    db: Session, ebay_listing: Listing
) -> None:
    """Widening must not move the single-item keys every reader already uses.

    `snapshot_version` rises to 2 because the shape *set* changed -- a
    snapshot may now lack `item` entirely -- and a reader has to be able to
    tell which shapes it may meet. The keys themselves do not move.
    """
    snapshot = sale_snapshot.take(db, ebay_listing)
    assert snapshot["snapshot_version"] == 2
    assert "lot" not in snapshot
    assert snapshot["item"]["item_code"]
    assert snapshot["listing"]["price"] == "120.00"


def test_an_order_line_for_a_lot_is_titled_by_the_lot(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
    admin_user: User,
) -> None:
    """`_sold_as` falls back to `listing.inventory_item.source_title`.

    That is `None` for a lot listing, so the Orders page would raise
    `AttributeError` -- a 500 on every page that includes the order -- rather
    than showing the lot's title. Reached through the API, not by calling
    `_sold_as`, because the 500 is what an operator actually meets.
    """
    order = record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    db.commit()
    body = client.get(f"/api/orders/{order.id}", headers=admin_headers).json()
    assert body["items"][0]["title"] == "Three Morgan Dollars"
```

In `backend/tests/test_sales_writes.py`, reusing that file's `_status_code`
helper (there is **no** `order.status` attribute):

```python
def test_selling_a_lot_divides_the_price_among_its_members(
    db: Session, offered_lot_listing: Listing, admin_user: User
) -> None:
    """One line, one share per member, summing to the line exactly."""
    order = record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == order.items[0].id
        )
    ).all()
    assert len(shares) == 3
    assert sum(share.amount for share in shares) == Decimal("1000.00")


def test_shares_are_weighted_by_cost_basis_by_default(
    db: Session, offered_lot_listing: Listing, admin_user: User
) -> None:
    """A $500 coin and a $200 coin do not each take a third of the price.

    The fixture's members cost 500, 300 and 200 with `tax_rate=0`, so
    `total_cost` equals `item_cost` exactly and 1,000.00 divides as
    500 / 300 / 200. An equal split would be 333.34 / 333.33 / 333.33, so
    this assertion fails if the weighting is dropped -- which is the mutation
    that proves it: pass `equal_shares=True` and confirm it goes red.
    """
    order = record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    by_cost = {
        share.inventory_item_id: share.amount
        for share in db.scalars(
            select(SalesOrderItemShare).where(
                SalesOrderItemShare.sales_order_item_id == order.items[0].id
            )
        )
    }
    costs = {
        item.id: item.total_cost
        for item in db.scalars(
            select(InventoryItem).where(InventoryItem.id.in_(by_cost))
        )
    }
    assert {costs[item_id]: amount for item_id, amount in by_cost.items()} == {
        Decimal("500.00"): Decimal("500.00"),
        Decimal("300.00"): Decimal("300.00"),
        Decimal("200.00"): Decimal("200.00"),
    }


def test_a_lot_s_members_all_become_sold(
    db: Session, offered_lot_listing: Listing, admin_user: User
) -> None:
    """`_after_stock_change` returns early on a null item and would skip them.

    Verified rather than assumed: `db.get(InventoryItem, None)` does *not*
    raise in SQLAlchemy 2.0.52 -- it runs `SELECT ... WHERE id = NULL`,
    returns `None`, and the function returns silently. So a lot's members
    would stay `listed` forever with no error anywhere.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member_ids = [member.inventory_item_id for member in lot.members]
    record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    for item_id in member_ids:
        item = db.get(InventoryItem, item_id)
        assert item is not None
        assert item.disposition.code == "sold"


def test_a_missing_share_is_an_internal_error_not_a_refusal(
    db: Session, offered_lot_listing: Listing, admin_user: User
) -> None:
    """Carried finding (c): a bare `KeyError` was an unhandled 500 with no name.

    A share missing for one of a listing's items is an invariant violation
    inside this codebase, not a conflict a caller can retry past -- so it is
    `ShareMissing`, unmapped in `routers/offers.py` and therefore still a 500,
    but one whose message names the item and the listing. Deliberately *not*
    `SaleRefused`, which maps to 409 and would tell the caller to try again.

    The scaffolding removes one share between `place_order` and the lookup,
    which is the only way to reach the branch: nothing in production writes
    a line with a member missing.
    """
    original = order_writes._sync_shares

    def _drop_one(db_, line, listing, amount, *, new_line=False):  # noqa: ANN001, ANN202
        original(db_, line, listing, amount, new_line=new_line)
        db_.flush()
        victim = db_.scalars(
            select(SalesOrderItemShare)
            .where(SalesOrderItemShare.sales_order_item_id == line.id)
            .order_by(SalesOrderItemShare.inventory_item_id)
            .limit(1)
        ).one()
        db_.delete(victim)
        db_.flush()

    order_writes._sync_shares = _drop_one
    try:
        with pytest.raises(ShareMissing) as excinfo:
            record_sale(
                db,
                offered_lot_listing,
                price=Decimal("1000.00"),
                buyer_username="coinfan88",
                external_order_id="EB-1",
                fees=[FeeLine("commission", Decimal("10.00"))],
                recorded_by=admin_user,
            )
    finally:
        order_writes._sync_shares = original
    assert f"listing {offered_lot_listing.id}" in str(excinfo.value)
    assert "CC-" in str(excinfo.value)
```

In `backend/tests/test_order_revision.py` (or the file that already drives
`revise_order` — check `git grep -l revise_order backend/tests`), for the
update branch of finding (a):

```python
def test_revising_a_lot_line_redistributes_every_share(
    db: Session, offered_lot_listing: Listing, admin_user: User
) -> None:
    """The update branch had no lot case at all, because the guard returned first.

    `_sync_shares` returned immediately when `listing.inventory_item_id is
    None`, so a revised lot line's money would move while its shares stayed
    where they were -- silently, since nothing sums them back. The mutation
    that proves this test: restore the early return and confirm it goes red.
    """
    order = record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    line = order.items[0]
    order_writes.revise_order(
        db,
        order,
        [order_writes.Line(listing_id=line.listing_id, quantity=1,
                           unit_price=Decimal("700.00"))],
        customer=order.customer,
        notes=None,
        revised_by=admin_user,
    )
    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == line.id
        )
    ).all()
    assert len(shares) == 3
    assert sum(share.amount for share in shares) == Decimal("700.00")
```

Check `revise_order`'s real signature before writing this last one — it is the
one interface here this plan did not re-read line by line.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_sale_snapshots.py -k lot -v`
Expected: FAIL — `AttributeError: 'NoneType' object has no attribute 'id'`
inside `item_detail`, which is the crash this task exists to fix.

- [ ] **Step 3: Widen `sale_snapshot.take`, and `_sold_as` with it**

`take` keeps the same keys for an item listing (`item`, `listing`) so existing
snapshots and their readers are unchanged, and for a lot listing returns
`items` (a list of the same per-item details, in item id order, from
`offering_writes.offered_items`) and `lot` (`id`, `title`, `description`)
**instead of** `item`.

Raise `SNAPSHOT_VERSION` to **2**, because a reader must be able to tell the
two shapes apart. It has exactly **one reader in the whole tree**,
`test_sale_snapshots.py:87` (`assert snapshot["snapshot_version"] == 1`) —
checked before changing it, as the previous draft asked; update that line
to `== 2`.

Then `routers/orders.py:58-63`'s `_sold_as`, which the audit did not name:

```python
def _sold_as(line: SalesOrderItem) -> str:
    """What the line sold, as it was called then; today's name for older lines."""
    snapshot = line.item_snapshot or {}
    if not isinstance(snapshot, dict):
        snapshot = {}
    item = snapshot.get("item")
    title = item.get("source_title") if isinstance(item, dict) else None
    if title:
        return str(title)
    lot = snapshot.get("lot")
    lot_title = lot.get("title") if isinstance(lot, dict) else None
    if lot_title:
        return str(lot_title)
    # A line whose snapshot predates lots and carries no title: fall back to
    # the item behind the listing, which for such a line always exists.
    item_row = line.listing.inventory_item
    return item_row.source_title if item_row is not None else f"Listing #{line.listing_id}"
```

Without this, the Orders page raises `AttributeError` on a lot order and every
page that lists it 500s.

- [ ] **Step 4: Widen `_after_stock_change`**

It reads `db.get(InventoryItem, listing.inventory_item_id)` and returns when
that is `None` — silently skipping every member of a lot. Make it move the
disposition of **every** item the listing offered, through
`offering_writes.offered_items`, so there is one answer to "which items"
rather than three. The `before`/`after` stock logic is unchanged; only the
item set it applies to widens.

`order_writes` already imports from `offering_writes`
(`from .offering_writes import sellable_in_shop`), so this adds no new
dependency and no cycle.

- [ ] **Step 5: Remove `_sync_shares`'s early return, and widen both branches**

Carried finding (a). Remove
`if listing.inventory_item_id is None:  # pragma: no cover - phase 3 lots`
(`order_writes.py:188-189`) and widen both branches for a lot listing's
several members rather than its one item:

- **Insert branch** (`new_line=True`; `place_order`, no share yet for this
  line): one row per open lot member, dividing `amount` with
  `allocation.allocate` weighted by `total_cost`, exactly as
  `sales_writes._weights`/`allocate` do. **Take the member list from
  `offering_writes.offered_items`, never from `line.shares`** — see the
  `new_line` note in *Global Constraints*.
- **Update branch** (`new_line=False`; `revise_order`, shares already exist):
  read the existing rows keyed by `inventory_item_id` and redistribute the new
  `amount` across **all** of them, not just move one row's. A lot is frozen
  once offered, so the member set cannot have changed under a revision — if
  the existing keys and the current member ids disagree, **raise** rather than
  leaving an orphan share, with a message naming the line and the listing.
- `allocate`'s second argument is annotated `list[Decimal]`; build a list, not
  a tuple or a generator, or mypy fails the gate.

Update the docstring to describe both branches *and* the `new_line`
interaction — the current one (`:171-182`) already covers both branches in the
abstract but says nothing about where the member list comes from.

- [ ] **Step 6: Widen `sales_writes._shared_items`, and name the missing share**

Replace the `# pragma: no cover - phase 3 widens this` branch
(`sales_writes.py:112-113`) with the real one: `offering_writes.offered_items`,
which returns the lot's open members in item id order so the shares are
deterministic. `_shared_items` gains a `db` parameter; it has one caller
(`record_sale`, `:212`).

Keep the `SaleInputInvalid` for a listing that offers nothing at all — that is
the empty-lot case the spec maps to 422, and it stays reachable through
`record_sale` even though `offer` now refuses to create such a listing.

Then carried finding (c), at `:275-277`:

```python
class ShareMissing(Exception):
    """A line is missing a share for one of the items its listing offered.

    Deliberately **not** a `SaleRefused`. `SaleRefused` maps to 409 in
    `routers/offers.py`, which tells a caller "something is in the way, try
    again" -- false here: `order_writes._sync_shares` writes one share per
    offered item in the same transaction, so a gap is an invariant violation
    inside this codebase that no retry can fix. Unmapped in the router on
    purpose, so it surfaces as a 500 with a message naming the item and the
    listing rather than as a bare `KeyError` naming an integer.
    """
```

and the lookup becomes:

```python
    for item, fee_amount in zip(items, fee_amounts, strict=True):
        share = shares_by_item.get(item.id)
        if share is None:
            raise ShareMissing(
                f"{item.item_code} has no share on the line for listing "
                f"{listing.id}; order_writes wrote one per offered item"
            )
        share.fee_amount = fee_amount
```

`sales_writes` still never constructs a share — keep its module docstring's
claim true.

- [ ] **Step 7: Run the whole suite**

Run: `python -m pytest backend/tests -v`
Expected: PASS. Run the whole suite here, not just the new files: this task
changes four functions that checkout, snapshots, revisions and receiving all
use.

- [ ] **Step 8: Run the gate and commit**

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
- Modify: `backend/app/schemas.py` (`SalesLotIn`, `SalesLotUpdate`,
  `SalesLotMemberOut`, `SalesLotOut`; widen `ListingOut`)
- Modify: `backend/app/routers/offers.py` (`_out`)
- Test: `backend/tests/test_lots_api.py`,
  `backend/tests/test_offers_api.py` (one case)

**Interfaces:**
- `GET /api/sales-lots` (filter by status), `POST /api/sales-lots`,
  `PATCH /api/sales-lots/{id}` (title, description, membership while
  assembling), `DELETE /api/sales-lots/{id}` (an assembling lot only).
- Schemas: `SalesLotIn`, `SalesLotUpdate`, `SalesLotOut` (members, running cost
  basis and value — staff-only figures).

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_lots_api.py
"""The sales-lot endpoints: admin-only, optimistic, and money as strings."""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

from app.models import InventoryItem, SalesLot, SalesLotStatus
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

ItemFactory = Callable[..., InventoryItem]


def test_a_lot_can_be_created_and_read_back(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """The round trip, so every test below starts from something real."""
    created = client.post(
        "/api/sales-lots",
        headers=admin_headers,
        json={"title": "Three Morgans", "description": "Lightly toned"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "assembling"
    assert body["members"] == []

    listed = client.get("/api/sales-lots", headers=admin_headers).json()
    assert [row["id"] for row in listed["lots"]] == [body["id"]]


def test_membership_changes_need_the_current_version(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    lot_of_three: SalesLot,
    make_item: ItemFactory,
) -> None:
    """Optimistic locking, as everywhere else in the console: 409 on a stale token."""
    db.commit()
    stale = lot_of_three.version
    joiner = make_item(title="A fourth coin")
    db.commit()

    first = client.patch(
        f"/api/sales-lots/{lot_of_three.id}",
        headers=admin_headers,
        json={"version": stale, "add_item_ids": [joiner.id]},
    )
    assert first.status_code == 200, first.text

    second = client.patch(
        f"/api/sales-lots/{lot_of_three.id}",
        headers=admin_headers,
        json={"version": stale, "remove_item_ids": [joiner.id]},
    )
    assert second.status_code == 409
    assert "changed" in second.json()["detail"].lower()


def test_editing_an_offered_lot_is_refused(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """409, naming the listing that froze it."""
    db.commit()
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    response = client.patch(
        f"/api/sales-lots/{lot.id}",
        headers=admin_headers,
        json={"version": lot.version, "title": "Renamed after the fact"},
    )
    assert response.status_code == 409
    assert "offered" in response.json()["detail"]


def test_an_empty_lot_cannot_be_offered(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """422, not 409: the spec's *Errors* list calls an empty lot bad input."""
    lot_id = client.post(
        "/api/sales-lots",
        headers=admin_headers,
        json={"title": "Nothing in it", "description": ""},
    ).json()["id"]
    response = client.post(
        "/api/offers",
        headers=admin_headers,
        json={"venue": "ebay", "format": "fixed_price", "lot_id": lot_id},
    )
    assert response.status_code == 422


def test_the_lot_list_is_admin_only(
    client: TestClient, db: Session, lot_of_three: SalesLot
) -> None:
    """Cost basis is on the row; an anonymous caller gets 401."""
    db.commit()
    assert client.get("/api/sales-lots").status_code == 401


def test_a_customer_cannot_read_a_lot(
    client: TestClient, customer_headers: dict[str, str], db: Session,
    lot_of_three: SalesLot,
) -> None:
    """403 for a signed-in non-administrator: 401 alone would not prove the role.

    The fixture is `customer_headers`, not an invented `customer_token` --
    conftest has no such fixture.
    """
    db.commit()
    assert client.get("/api/sales-lots", headers=customer_headers).status_code == 403


def test_money_fields_are_strings(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    lot_of_three: SalesLot,
) -> None:
    """A Decimal in a plain dict becomes a float; these must not.

    The fixture's three members cost 500, 300 and 200 with no tax, so the
    running cost basis is exactly 1000.00 -- a figure a float would render as
    1000.0 and this assertion would catch.
    """
    db.commit()
    row = client.get("/api/sales-lots", headers=admin_headers).json()["lots"][0]
    assert row["cost_basis"] == "1000.00"
    assert isinstance(row["cost_basis"], str)


def test_an_assembling_lot_can_be_deleted_and_an_offered_one_cannot(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """A lot that was offered is a record of what was tried; it stays."""
    db.commit()
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    assert (
        client.delete(f"/api/sales-lots/{lot.id}", headers=admin_headers).status_code
        == 409
    )
    spare = client.post(
        "/api/sales-lots",
        headers=admin_headers,
        json={"title": "Never offered", "description": ""},
    ).json()
    assert (
        client.delete(
            f"/api/sales-lots/{spare['id']}", headers=admin_headers
        ).status_code
        == 204
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_lots_api.py -v`
Expected: FAIL — 404, no route.

- [ ] **Step 3: Write the router**

Follow `routers/offers.py`: admin-only via `AdminUser`, resolve codes, own the
transaction, shape the response, decide nothing. Map `lot_writes.EmptyLot` to
**422** and the plain `LotRefused` to **409** with the message — in that
`except`-clause order, and with a comment saying that mypy cannot check the
order, the same trap `SaleInputInvalid` carries. `StaleDataError` maps to 409
with `_STALE`.

Capture plain ids into locals **before** the `try`, and keep implicit
autoflushes inside it: after a failed flush, reading any ORM attribute raises
`PendingRollbackError` instead of the 409 you meant to send.

`SalesLotOut` carries `id`, `title`, `description`, `status`, `version`,
`members` (each `inventory_item_id`, `item_code`, `title`, `cost_basis`,
`value`) and the running `cost_basis` / `value` totals. Declare the money
fields as `Decimal` on the Pydantic model — a `Decimal` field serializes as a
string (`test_offers_api.py:147`); only a plain `dict` turns it into a float.

- [ ] **Step 4: Let `POST /api/offers` offer a lot**

`OfferIn` (`schemas.py:1213-1233`) requires `items: list[OfferItemIn]` with
`min_length=1`. Add an optional `lot_id: int | None = None` and a model
validator refusing a body that sets both or neither — which is what keeps
`offering_writes.offer`'s `ValueError` unreachable from outside, as Task 3's
test asserts. A lot body carries one price, title, description and external
id rather than a row per item.

- [ ] **Step 5: Widen `ListingOut` and `_out`**

Not in the audit, and load-bearing: `routers/offers.py:144-148`'s `_out` reads
`listing.inventory_item.id` and `.item_code`, and `ListingOut.item_id: int` /
`item_code: str` are required (`schemas.py:1253-1255`). The moment Task 3
creates a lot listing, `GET /api/listings` — the Listings page — raises
`AttributeError` for every administrator.

Make `item_id: int | None` and `item_code: str | None`, add
`sales_lot_id: int | None` and `member_count: int | None`, and have `_out`
fill the lot fields for a lot listing and leave the item fields `None`.
`item_title` becomes the lot's title for a lot listing. `cost_basis` sums the
members' `total_cost`.

Add one case to `backend/tests/test_offers_api.py`:

```python
def test_the_listings_page_shows_a_lot_listing(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """`_out` reads `listing.inventory_item.item_code`, which a lot has not.

    Without the widening this is an `AttributeError` inside the endpoint --
    a 500 on the page that lists every offer, not a missing row.
    """
    db.commit()
    rows = client.get("/api/listings", headers=admin_headers).json()["listings"]
    row = next(entry for entry in rows if entry["id"] == offered_lot_listing.id)
    assert row["item_id"] is None
    assert row["sales_lot_id"] == offered_lot_listing.sales_lot_id
    assert row["member_count"] == 3
    assert row["item_title"] == "Three Morgan Dollars"
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_lots_api.py backend/tests/test_offers_api.py -v`
Expected: PASS.

- [ ] **Step 7: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/routers backend/app/main.py backend/app/schemas.py backend/tests
git commit -m "Serve sales lots over the API, and show one on the Listings page"
```

---

### Task 6: Lots in the shop

**Files:**
- Modify: `backend/app/routers/catalog.py` (`to_catalog_item`, `_eager`, the
  list query and the count query)
- Modify: `backend/app/schemas.py` (`CatalogItemOut`, new `CatalogMemberOut`)
- Test: `backend/tests/test_catalog.py`, `backend/tests/test_shop_boundary.py`

**Interfaces:**
- Consumes: `offering_writes.shop_listing_filters`, `offered_items`.
- Produces: `CatalogItemOut.inventory_item_id: int | None`,
  `CatalogItemOut.item_code: str | None`,
  `CatalogItemOut.members: list[CatalogMemberOut]`.

**`CatalogItemOut.inventory_item_id: int` is required and non-nullable today**
(`schemas.py:139`), and `version_token(listing, item)` (`catalog.py:70-77`)
takes a non-optional item. Both must widen, and the list query's **inner** join
to `InventoryItem` (`catalog.py:196-198` and the count at `:202-208`) excludes
lot listings outright — which is why Step 2's test fails before any code
changes.

- [ ] **Step 1: Write the failing test**

```python
def test_a_store_lot_appears_as_one_catalogue_entry(
    client: TestClient, db: Session, store_lot_listing: Listing
) -> None:
    """One thing for sale, with its members' public descriptions."""
    db.commit()
    body = client.get("/api/catalog").json()
    entry = next(
        row for row in body["items"] if row["id"] == store_lot_listing.id
    )
    lot = store_lot_listing.sales_lot
    assert lot is not None
    assert entry["title"] == lot.title
    assert entry["inventory_item_id"] is None
    assert len(entry["members"]) == 3


def test_a_lot_entry_never_carries_cost_or_location(
    client: TestClient, db: Session, store_lot_listing: Listing
) -> None:
    """The authorisation boundary is `to_catalog_item` building fields by name.

    A lot widens what that function must build; this asserts the widening did
    not reach for the whole row. `json.dumps` rather than a key check for
    storage location, because it could arrive nested inside a member.
    """
    db.commit()
    body = client.get("/api/catalog").json()
    entry = next(row for row in body["items"] if row["id"] == store_lot_listing.id)
    assert "total_cost" not in entry
    assert "storage_location" not in json.dumps(entry)
    assert "item_cost" not in json.dumps(entry)
    for member in entry["members"]:
        assert "total_cost" not in member


def test_a_non_store_lot_listing_stays_out_of_the_catalogue(
    client: TestClient, db: Session, offered_lot_listing: Listing
) -> None:
    """`shop_listing_filters` is the rule, and a lot does not get an exemption.

    The mutation that proves it: drop `shop_listing_filters` from the widened
    query and confirm this goes red while the test above stays green.
    """
    db.commit()
    body = client.get("/api/catalog").json()
    assert all(row["id"] != offered_lot_listing.id for row in body["items"])


def test_buying_a_lot_sells_every_member(
    client: TestClient,
    db: Session,
    customer_headers: dict[str, str],
    store_lot_listing: Listing,
) -> None:
    """Checkout of a lot is one line, with a share per member."""
    db.commit()
    lot = store_lot_listing.sales_lot
    assert lot is not None
    member_ids = [member.inventory_item_id for member in lot.members]

    client.post(
        "/api/cart/items",
        headers=customer_headers,
        json={"listing_id": store_lot_listing.id, "quantity": 1},
    )
    placed = client.post("/api/orders", headers=customer_headers, json={})
    assert placed.status_code == 201, placed.text

    line_id = placed.json()["items"][0]["id"]
    shares = db.scalars(
        select(SalesOrderItemShare).where(
            SalesOrderItemShare.sales_order_item_id == line_id
        )
    ).all()
    assert sorted(share.inventory_item_id for share in shares) == sorted(member_ids)
    for item_id in member_ids:
        item = db.get(InventoryItem, item_id)
        assert item is not None
        assert item.disposition.code == "sold"
```

Check the cart and checkout endpoint shapes in `test_catalog.py` /
`test_orders.py` before writing the last one — the bodies above are this
plan's best reading, not a re-verified contract.

Add the fixture this task needs, beside `offered_lot_listing` in
`conftest.py`:

```python
@pytest.fixture
def store_lot_listing(db: Session, lot_of_three: SalesLot) -> Listing:
    """A lot of three offered in the web store, so the catalogue can serve it.

    `quantity=1` is the default and is also what `ck_listing_lot_quantity_one`
    requires -- note that `make_listing` would default it to 5, which is why
    this fixture goes through `offering_writes.offer` instead.
    """
    store = db.get(SalesVenue, store_venue_id(db))
    assert store is not None
    return offering_writes.offer(
        db,
        lot=lot_of_three,
        venue=store,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("1200.00"),
        title="Three Morgan Dollars",
        description="Three coins, one price.",
        external_id=None,
    )
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_catalog.py -k lot -v`
Expected: FAIL — the entry is absent, because the catalogue's inner join to
`InventoryItem` drops every listing whose `inventory_item_id` is NULL.

- [ ] **Step 3: Serve lots from the catalogue**

- The list query and the count query both become `outerjoin(InventoryItem,
  Listing.inventory_item_id == InventoryItem.id)`. Every item-column filter
  (`q`, `kind`, `country`, `metal`, `year_min`, `year_max`) then matches no
  lot listing, which is the right answer: a filter on grade cannot describe a
  group. Say that in a comment rather than leaving the next reader to wonder.
- `_eager` gains `selectinload(Listing.sales_lot).selectinload(SalesLot.members).selectinload(SalesLotItem.item)`
  with the same classifier chain, or a page of lots costs a query per member.
- `version_token(listing, item)` takes `InventoryItem | None` and returns
  `f"{listing.version}.0"` for a lot — the lot's own `version` is not in it,
  because the catalogue entry a buyer sees is the listing plus its members,
  and the members cannot change while the lot is offered.
- `to_catalog_item` gains a lot branch. **Build every public field by name**,
  as it already does for items — it, not the `public_catalog` view, is what
  keeps cost basis and storage location from a buyer. The view is maintained
  and unread (audited 2026-09-20); do not start trusting it here.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_catalog.py backend/tests/test_shop_boundary.py -v`
Expected: PASS, with the bundle-isolation check still green.

At the end of this step, `python -m mypy` should be clean for the first time
since Task 1 — every `listing.inventory_item.<x>` site is now handled. If any
remain, they are readers no task claimed; fix them here rather than carrying
them.

- [ ] **Step 5: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/routers/catalog.py backend/app/schemas.py backend/tests
git commit -m "Show a store lot in the shop as one thing for sale"
```

---

### Task 7: The for-sale warning reaches a lot's members

**Files:**
- Test: `backend/tests/test_for_sale_guards.py` (two cases)
- Modify: `backend/app/sale_state.py` (docstring only, if the tests pass)

The spec requires this at `:279-281`: "The for-sale edit warning
(`app.sale_state`) counts active *and* paused claims, so an item in an
unsettled auction or an offered lot warns like a listed one."

**The audit said no task covered this and that `_offering` "filters only on
`Listing.inventory_item_id`, so an offered lot's members would NOT raise the
warning". That is wrong, and re-checking it is this task's first act.**
`_offering` (`sale_state.py:69-90`) has two halves, and the first is
`offering_writes.claims_for`, which reads `offer_claim` — where Task 3 writes
one row per member. The direct half is the fallback for listings written
before claims existed, and a lot listing simply never matches it.

So the expected outcome of this task is **two new tests and no production
change**. If a test fails, the fix belongs in `sale_state` and this task grows;
do not weaken the test to match the code.

- [ ] **Step 1: Write the tests**

```python
def test_an_offered_lot_s_member_warns_like_a_listed_item(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
) -> None:
    """Spec: an item in an offered lot warns like a listed one.

    Reached through the API, because the warning is a 409 an operator meets
    on save, not a function's return value. The mutation that proves it:
    delete the `claims_for` half of `sale_state._offering` and confirm this
    goes red -- the direct half never matches a lot listing, whose
    `inventory_item_id` is NULL.
    """
    db.commit()
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member_id = lot.members[0].inventory_item_id

    response = client.patch(
        f"/api/inventory/items/{member_id}",
        headers=admin_headers,
        json={"source_title": "Renamed while offered"},
    )
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.startswith("For sale")
    assert f"listing #{offered_lot_listing.id}" in detail


def test_a_sold_lot_s_member_still_warns_while_the_order_is_open(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    offered_lot_listing: Listing,
    admin_user: User,
) -> None:
    """After the sale the claim is released, so only the share can find it.

    This is the rule 2R decided and `sale_state`'s order half implements
    (`SalesOrderItemShare.inventory_item_id`). A lot is the case that half
    exists for, and nothing tested it with a real lot until now. The mutation
    that proves it: join the order half through `listing.inventory_item_id`
    instead of through the share, and confirm this goes red.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    member_id = lot.members[0].inventory_item_id
    record_sale(
        db,
        offered_lot_listing,
        price=Decimal("1000.00"),
        buyer_username="coinfan88",
        external_order_id="EB-1",
        fees=[],
        recorded_by=admin_user,
    )
    db.commit()

    response = client.patch(
        f"/api/inventory/items/{member_id}",
        headers=admin_headers,
        json={"source_title": "Renamed after the sale"},
    )
    assert response.status_code == 409
    assert "order #" in response.json()["detail"]
```

The second test's status matters: `record_sale` on a `marketplace` venue
creates the order `paid`, which is in `OPEN_ORDER_STATUSES`
(`sale_state.py:55`). A `heritage_venue` sale would be `delivered` and would
**not** warn — deliberately, per that constant's own note. Do not swap the
fixture.

- [ ] **Step 2: Run them, and only then decide whether code changes**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -k lot -v`
Expected: **PASS with no production change.** If either fails, fix
`sale_state`, not the test, and record what was wrong here.

- [ ] **Step 3: Correct the module docstring**

`sale_state.py:14-17` says a lot listing is "(phase 3)". Drop the tense
marker and state the finished rule. `_offering`'s docstring already says the
claim half "is the only one that will work for a lot" — make it "is the only
one that works for a lot", and name these two tests as its proof.

- [ ] **Step 4: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/app/sale_state.py backend/tests/test_for_sale_guards.py
git commit -m "Prove the for-sale warning reaches an offered lot's members"
```

---

### Task 8: The lot-membership invariant, and the disposition one

**Files:**
- Modify: `backend/tests/conftest.py` (the autouse fixture from phase 2R)
- Modify: `backend/tests/test_claim_invariant.py` (**omitted from the previous
  draft's file list**, and the file where the proof pattern already lives)
- Modify: `pyproject.toml` (if a second marker turns out to be needed — see
  Step 3)

**Narrowed from the previous draft's "extend the claim invariant to lots".**
The claim invariant is not extended and its query is not touched: its
`listing_id`-only join is deliberate and already grades a lot's claims
correctly (`conftest.py:230-275`). What this task adds is a **separate
query** for a different rule, and a second one for finding (e).

- [ ] **Step 1: Add the lot-membership check, with its own exception type**

In `conftest.py`, beside `check_claim_invariant`:

```python
class LotInvariantViolation(AssertionError):
    """An open lot membership disagrees with its lot's status.

    A distinct type from `ClaimInvariantViolation`, for two reasons. The
    `xfail(raises=...)` proof in `test_claim_invariant.py` narrows on the
    exact type, so sharing one would let either proof pass on the other's
    failure. And `claim_invariant_waiver` absorbs *any*
    `ClaimInvariantViolation` (the documented limit at this fixture's
    docstring): reusing that type would silently exempt the four waived
    tests from this rule as well, which is exactly the accident this check
    exists to prevent.
    """


def check_lot_invariant(db: Session) -> None:
    """Assert every open lot membership agrees with its lot's status, right now.

    One direction only: an open membership (`released_at IS NULL`) implies
    its lot is `assembling` or `offered`. The converse -- that an
    `assembling` lot has members -- is false by design: a lot is created
    empty and is assembled a coin at a time.

    One query, no per-row loads: the autouse fixture below calls this
    roughly 1,300 times.
    """
    open_in_closed = db.execute(
        select(SalesLotItem.sales_lot_id, SalesLot.status)
        .join(SalesLot, SalesLot.id == SalesLotItem.sales_lot_id)
        .where(
            SalesLotItem.released_at.is_(None),
            SalesLot.status.in_((SalesLotStatus.sold, SalesLotStatus.dissolved)),
        )
    ).all()
    if open_in_closed:
        raise LotInvariantViolation(
            f"lot membership is still open on a finished lot: {open_in_closed}"
        )
```

- [ ] **Step 2: Call it from the autouse fixture, ahead of the waiver path**

In `_claim_invariant` (`conftest.py:389-434`), between the `not db.is_active`
early return and the waiver handling:

```python
    if not db.is_active:
        return
    # Before the waiver branch below, and never waived: the
    # `claim_invariant_waiver` marker absorbs any `ClaimInvariantViolation`,
    # and a lot violation inside an already-waived test must still fail.
    # That is why this raises its own type.
    check_lot_invariant(db)
    if waiver is None:
        check_claim_invariant(db)
        return
```

Update the fixture's docstring and the marker's `pyproject.toml` help text to
say the waiver covers the **claim** half only.

- [ ] **Step 3: Mutation-prove it, in the file where that pattern lives**

`test_claim_invariant.py:66-96` already carries the working shape —
`@pytest.mark.xfail(strict=True, raises=...)` **plus a direct call to the
check function in the test body**, because an `xfail` on a test whose call
phase raises nothing is an unexpected pass that `strict` promotes to a hard
failure. That file's module docstring explains the whole trap. **Reuse it;
a second hand-rolled copy is how the two drift.** Add:

```python
@pytest.mark.xfail(
    strict=True,
    raises=LotInvariantViolation,
    reason=(
        "proof that the lot invariant can fail: an open membership on a "
        "dissolved lot must raise both in this call and in the autouse "
        "fixture's teardown"
    ),
)
def test_the_lot_invariant_catches_an_open_member_of_a_finished_lot(
    db: Session, offered_lot_listing: Listing
) -> None:
    """Dissolving a lot without releasing its members is what this must catch.

    Deliberately left broken; the per-test transaction rolls it back.
    Mutation-tested by hand: commenting out the `status = ...` line below
    makes this XPASS(strict) and fails the run, confirming the proof is not
    vacuous.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    lot.status = SalesLotStatus.dissolved  # members left open on purpose
    db.flush()
    check_lot_invariant(db)


def test_the_lot_invariant_is_wired_into_the_autouse_fixture(
    request: pytest.FixtureRequest, db: Session, offered_lot_listing: Listing
) -> None:
    """The fixture, not just the function, must run the lot check.

    `test_the_invariant_fixture_is_wired_up`'s lesson, applied to the second
    rule: the xfail test above calls `check_lot_invariant` itself, so it
    would stay green even if the fixture never called it. Asserting on
    `fixturenames` proves the fixture is active; reading its source for the
    call is what proves the call. Here the cheaper equivalent: the fixture is
    the only thing that would fail a test with a broken lot *and no direct
    call*, so this test simply leaves the lot correct and asserts the check
    is satisfied -- and the xfail above, whose teardown also raises, is the
    other half.
    """
    assert "_claim_invariant" in request.fixturenames  # the fixture that calls it
    check_lot_invariant(db)


@pytest.mark.claim_invariant_waiver(
    reason=(
        "breaks the claim invariant deliberately (a released claim on an "
        "active listing) to prove that a claim waiver does not also absorb a "
        "lot violation -- the two checks raise different types on purpose"
    )
)
@pytest.mark.xfail(
    strict=True,
    raises=LotInvariantViolation,
    reason="the lot violation must surface through the claim waiver",
)
def test_a_claim_waiver_does_not_absorb_a_lot_violation(
    db: Session, offered_lot_listing: Listing
) -> None:
    """The documented waiver limit must stop at the claim half.

    A waiver absorbs any `ClaimInvariantViolation` (conftest.py's fixture
    docstring). If the lot rule reused that type, this test would pass
    silently with *both* rules broken -- which is the accident the separate
    exception type exists to prevent.
    """
    lot = offered_lot_listing.sales_lot
    assert lot is not None
    claim = db.scalars(
        select(OfferClaim).where(OfferClaim.listing_id == offered_lot_listing.id)
    ).first()
    assert claim is not None
    claim.state = ClaimState.released  # claim half: waived
    lot.status = SalesLotStatus.dissolved  # lot half: must not be waived
    db.flush()
    check_lot_invariant(db)
```

Take `request: pytest.FixtureRequest` as a parameter on the second test.

- [ ] **Step 4: Add the disposition invariant (carried finding (e))**

Same file, same shape. The rule, **one-directional**, and the two allowances
that make it true of the code as it actually is:

> A claim in `HELD_BY` (`active` or `paused`) implies its item's disposition
> is `listed` — **or** in `offering_writes.SOLD_AWAY`, which a sale wrote.

- **Not the biconditional.** `conftest.build_listing` (`:703-757`) creates a
  **`listed`** item with **no claim**, and `listing` / `make_listing` are used
  by dozens of tests. "A `listed` item has a held claim" fails all of them on
  the first run.
- **The `SOLD_AWAY` allowance is not slack.** A shop checkout that takes the
  last unit sets the item to `sold` (`order_writes._after_stock_change`) while
  its store listing stays `active` with an `active` claim — `end_offer`'s own
  comment (`offering_writes.py:596-600`) names that shape as intended. Without
  the allowance the rule is false the first time a test buys out a listing.

What is left after both allowances is worth having: it catches a held claim on
an item filed as `held`, which is the shape "a lot's members were never moved
to `listed`" and "an ending moved an item back while something still holds it"
both produce.

`check_disposition_invariant(db)` is one query joining `offer_claim` to
`inventory_item` to `disposition`, raising `DispositionInvariantViolation`
(its own type, for the reasons above), called from the same fixture beside
`check_lot_invariant`. Prove it with the same `xfail(strict=True, raises=...)`
pattern: set a held claim's item to `held` and confirm both phases raise.

- [ ] **Step 5: Run the whole suite and report what the new rules found**

Run: `python -m pytest backend/tests -v`
Expected: PASS. **If other tests now fail, the invariants have found real
disagreements.** Fix them in `offering_writes` / `order_writes`, not by
weakening a check or by adding a waiver, and write down what was found — that
list is the evidence finding (h) asks for before anyone decides whether
`disposition` should leave `ITEM_CLASSIFIERS`. **That decision is the owner's
and is not part of this task.**

- [ ] **Step 6: Run the gate and commit**

```
scripts\ccweb_check.cmd
git add backend/tests/conftest.py backend/tests/test_claim_invariant.py pyproject.toml
git commit -m "Check after every test that lot membership and disposition agree with their claims"
```

---

### Task 9: Race tests, mutation-proven

**Files:**
- Modify: `backend/tests/test_offer_races.py`

`TestClient` serialises requests and cannot see a race. Use real threads
behind a `threading.Barrier` with a session each, as this file and
`test_order_revision_race.py` already do. Reuse the file's own `_seed_item`,
`_venue`, `_active_claims`, `RACE_TITLE`, `_cleanup_race_rows` and `committed`.

**This file's tests take `committed`, not `db`, so the autouse invariants do
not run for any of them** (`conftest.py:324-350`). The lot invariant must be
called **explicitly**, exactly as `_cleanup_race_rows` already calls
`check_claim_invariant`, inside the same `try`/`finally`.

- [ ] **Step 1: Fix this file's own cleanup first — carried finding (d)**

`_cleanup_race_rows` (`:88-108`) deletes listings matching
`Listing.inventory_item_id.in_(item_ids)`. **A lot listing's is NULL**, so
every lot listing this task creates survives the delete; the `InventoryItem`
delete on the next line then fails on `sales_lot_item.inventory_item_id`'s
`ondelete="RESTRICT"` — **inside the `finally`**, replacing whatever the `try`
raised with a foreign-key error. Do this before writing a single race, or
every failure in this file becomes unreadable.

- Widen `race_listing_ids` to `or_(Listing.inventory_item_id.in_(item_ids),
  Listing.sales_lot_id.in_(race_lot_ids))`, where `race_lot_ids` selects
  `SalesLotItem.sales_lot_id` for this file's items.
- Widen the `Listing` delete with the same `or_`.
- Delete `sales_lot_item` for these items, then `sales_lot` for those lot ids,
  **before** the `InventoryItem` delete and **after** the `Listing` delete
  (a lot listing's `sales_lot_id` is RESTRICT).
- Call `check_lot_invariant(cleanup)` beside `check_claim_invariant(cleanup)`
  inside the `try`, and extend the docstring to say why both are here rather
  than left to the autouse fixture.

- [ ] **Step 2: Write the races**

```python
def test_two_lots_cannot_both_claim_one_item(
    committed: sessionmaker[Session],
) -> None:
    """Two offers of lots sharing a coin: exactly one wins.

    Survives: dropping `uq_sales_lot_item_open` makes this fail, because both
    memberships are then accepted and both lots are offered. The item-level
    `uq_offer_claim_active` is the second backstop and is the subject of the
    next test.
    """
    shared = _seed_item(committed)
    first_only = _seed_item(committed)
    second_only = _seed_item(committed)
    venue_id = _venue(committed, "race-lot-a")
    barrier = threading.Barrier(2)

    def offer_lot(extra_id: int, title: str) -> Outcome:
        with committed() as session:
            try:
                lot = lot_writes.create_lot(session, title=title, description="")
                lot_writes.add_member(session, lot, session.get_one(InventoryItem, shared))
                lot_writes.add_member(session, lot, session.get_one(InventoryItem, extra_id))
                session.flush()
                barrier.wait(timeout=10)
                offering_writes.offer(
                    session,
                    lot=lot,
                    venue=session.get_one(SalesVenue, venue_id),
                    listing_format=ListingFormat.fixed_price,
                    price=Decimal("10.00"),
                    title=title,
                    description="",
                    external_id=None,
                )
                session.commit()
                return "won"
            except (IntegrityError, LotRefused, OfferRefused):
                session.rollback()
                return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            future.result()
            for future in [
                pool.submit(offer_lot, first_only, "RACE lot A"),
                pool.submit(offer_lot, second_only, "RACE lot B"),
            ]
        )
    assert outcomes == ["refused", "won"]
    assert len(_active_claims(committed, shared)) == 1


def test_offering_a_lot_races_offering_one_of_its_members(
    committed: sessionmaker[Session],
) -> None:
    """The item-level claim decides, whichever shape got there first.

    Survives: dropping `uq_offer_claim_active` makes this fail -- both the
    lot's member claim and the single item's claim are then accepted, and the
    coin is offered twice, which is the one thing the selling design says can
    never happen.
    """
    shared = _seed_item(committed)
    partner = _seed_item(committed)
    lot_venue = _venue(committed, "race-lot-c")
    item_venue = _venue(committed, "race-item-c")
    barrier = threading.Barrier(2)

    def offer_the_lot() -> Outcome:
        with committed() as session:
            try:
                lot = lot_writes.create_lot(session, title="RACE lot C", description="")
                for item_id in (shared, partner):
                    lot_writes.add_member(
                        session, lot, session.get_one(InventoryItem, item_id)
                    )
                session.flush()
                barrier.wait(timeout=10)
                offering_writes.offer(
                    session,
                    lot=lot,
                    venue=session.get_one(SalesVenue, lot_venue),
                    listing_format=ListingFormat.fixed_price,
                    price=Decimal("10.00"),
                    title="RACE lot C",
                    description="",
                    external_id=None,
                )
                session.commit()
                return "won"
            except (IntegrityError, OfferRefused):
                session.rollback()
                return "refused"

    def offer_the_member() -> Outcome:
        with committed() as session:
            try:
                barrier.wait(timeout=10)
                offering_writes.offer(
                    session,
                    item=session.get_one(InventoryItem, shared),
                    venue=session.get_one(SalesVenue, item_venue),
                    listing_format=ListingFormat.fixed_price,
                    price=Decimal("10.00"),
                    title="",
                    description="",
                    external_id=None,
                )
                session.commit()
                return "won"
            except (IntegrityError, OfferRefused):
                session.rollback()
                return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(
            future.result()
            for future in [pool.submit(offer_the_lot), pool.submit(offer_the_member)]
        )
    assert outcomes == ["refused", "won"]
    assert len(_active_claims(committed, shared)) == 1


def test_checkout_races_a_pause_of_the_same_lot(
    committed: sessionmaker[Session],
) -> None:
    """A lot paused while in a cart cannot be bought.

    Survives: removing the `with_for_update()` from
    `order_writes._lock_listings` makes this fail -- checkout then decides on
    a status it read before the pause committed and sells a lot that is no
    longer on offer. Leave a `MUTATION` comment at the site while the guard
    is out, or `ccweb_check.cmd`'s mutation-scaffolding stage cannot see it.

    Checkout, not `record_sale`: `place_order` with `venue=None` is the path
    a shopper takes, and the one that asks `sellable_in_shop` under the
    listing lock. The pause comes from a second offer of one member
    elsewhere -- the same sequence `stored_then_ebay` builds single-threaded.
    """
    members = [_seed_item(committed) for _ in range(2)]
    store_id = _store_venue_id(committed)
    elsewhere_id = _venue(committed, "race-pause")
    listing_id, customer_id, admin_id = _seed_lot_listing_and_buyer(
        committed, members, store_id
    )
    barrier = threading.Barrier(2)

    def buy_it() -> Outcome:
        with committed() as session:
            try:
                barrier.wait(timeout=10)
                order_writes.place_order(
                    session,
                    session.get_one(Customer, customer_id),
                    [order_writes.Line(listing_id=listing_id, quantity=1)],
                    session.get_one(User, admin_id),
                )
                session.commit()
                return "won"
            except (HTTPException, IntegrityError, StaleDataError):
                session.rollback()
                return "refused"

    def pause_it() -> Outcome:
        with committed() as session:
            try:
                barrier.wait(timeout=10)
                offering_writes.offer(
                    session,
                    item=session.get_one(InventoryItem, members[0]),
                    venue=session.get_one(SalesVenue, elsewhere_id),
                    listing_format=ListingFormat.fixed_price,
                    price=Decimal("10.00"),
                    title="",
                    description="",
                    external_id=None,
                )
                session.commit()
                return "won"
            except (IntegrityError, OfferRefused):
                session.rollback()
                return "refused"

    with ThreadPoolExecutor(max_workers=2) as pool:
        bought, paused = (
            pool.submit(buy_it).result(),
            pool.submit(pause_it).result(),
        )

    # Either order is a legitimate outcome of a race; both winning is not.
    # A lot paused while it sat in a cart cannot also have been bought.
    assert [bought, paused].count("won") == 1
    with committed() as verify:
        sold = verify.get_one(Listing, listing_id)
        assert (sold.quantity_available == 0) == (bought == "won")
```

Two helpers this race needs that the file does not have yet, both written
beside `_seed_item` in the same committed-session style:

- `_store_venue_id(factory) -> int`, one line over
  `app.sales_venues.store_venue_id` inside a committed session.
- `_seed_lot_listing_and_buyer(factory, member_ids, store_id) -> tuple[int,
  int, int]`, which assembles the lot through `lot_writes`, offers it in the
  store through `offering_writes.offer`, and commits a `RACE`-named customer
  and administrator, returning their ids. Returning **ids, not instances**, for
  the reason every other helper here does: each thread has its own session.

Extend `_cleanup_race_rows` to remove the order, its lines, its shares and the
`RACE` customer and user — in that order, **ahead of** the listings, because
`sales_order_item.listing_id` is RESTRICT.

- [ ] **Step 3: Mutation-prove each one**

For each race: remove the guarantee, confirm the **named** test goes red,
restore it, confirm green. A race test that passes with the lock removed is
testing nothing — this repo found 14 unfailable tests across two branches by
asking exactly this.

- Drop `uq_sales_lot_item_open` → the two-lots test must fail.
- Drop `uq_offer_claim_active` → the lot-vs-member test must fail.
- Remove the `FOR UPDATE` in `order_writes._lock_listings` → the checkout race
  must fail. **Leave a `MUTATION` comment while it is out**, and confirm
  `ccweb_check.cmd` fails on it before restoring — that is the stage's own
  proof.

Record in each test's docstring which mutation it survives, in the words used
above.

- [ ] **Step 4: Run and commit**

```
python -m pytest backend/tests/test_offer_races.py -v
scripts\ccweb_check.cmd
git add backend/tests/test_offer_races.py
git commit -m "Prove the lot guarantees hold under concurrency"
```

---

### Task 10: The fee vocabulary comes back in its curated order

Carried finding (f), with the owner's ruling of 2026-09-21.

**Files:**
- Modify: `backend/app/routers/reference.py` (`_SEQUENCED_TABLES` and its
  comment)
- Modify: `backend/tests/test_reference.py`
  (`test_the_fee_vocabulary_reaches_a_picker`, docstring included)

- [ ] **Step 1: Add the table, and say why it belongs**

`_SEQUENCED_TABLES` (`:101-112`) is preceded by a comment naming three reasons
an entry belongs: a scale, a lifecycle, or a curated sequence. Add
`"sales_fee_kind"` to the frozenset and a clause to the comment's curated-
sequence sentence:

> `sales_fee_kind` is curated too: the migration orders it commission,
> processing, listing, shipping label, promotion, **other**, which is the
> order a person reads a platform's statement in and puts the catch-all last.
> Alphabetical by label puts "Other" **third**, and
> `docs/system-administration.md` already prints the curated order in writing.

- [ ] **Step 2: Rewrite the test, and its docstring, to pin the new order**

`test_the_fee_vocabulary_reaches_a_picker` (`test_reference.py:185-207`)
currently asserts alphabetical and its docstring calls that deliberate. Both
change. Note what the previous draft spotted and which still holds: sorted by
**code** and sorted by **label** produce the *same* list here
(commission, listing, other, processing, promotion, shipping_label), so the
old assertion never actually pinned "alphabetical by label". The curated order
differs from both, so the new assertion pins something real:

```python
def test_the_fee_vocabulary_reaches_a_picker(client: TestClient) -> None:
    """The six seeded fee kinds come back in the migration's curated order.

    `RecordSaleDialog` builds one fee row per value this returns. A 404 here
    is a dialog whose fees are all zero and whose net always equals its
    gross, with nothing on screen saying so.

    The order asserted is the migration's `sort_order`, not alphabetical:
    `sales_fee_kind` is in `_SEQUENCED_TABLES` because the sequence is the
    order a platform's statement reads in and it puts the catch-all "other"
    last -- alphabetical puts "Other" third, and
    `docs/system-administration.md` prints the curated order in writing.
    This assertion would have passed under either sort before that change,
    because by code and by label the six happen to agree; the curated order
    differs from both, so it now pins something.
    """
    response = client.get("/api/reference/sales_fee_kind")
    assert response.status_code == 200
    body = response.json()
    assert body["table"] == "sales_fee_kind"
    assert [value["code"] for value in body["values"]] == [
        "commission",
        "processing",
        "listing",
        "shipping_label",
        "promotion",
        "other",
    ]


def test_the_catch_all_fee_kind_comes_last(client: TestClient) -> None:
    """"Other" last is the point of the curated order, so assert it alone.

    The list above would also pass if the whole sequence were reversed by
    accident; this one names the property the ruling actually rests on, and
    fails on its own if `sales_fee_kind` ever drops out of
    `_SEQUENCED_TABLES` (alphabetical puts "Other" third).
    """
    values = client.get("/api/reference/sales_fee_kind").json()["values"]
    assert values[-1]["code"] == "other"
```

- [ ] **Step 3: Check the manual still matches**

`docs/system-administration.md:884` prints "commission, processing, listing,
shipping label, promotion, other". It is now what the endpoint returns; leave
it as it is and confirm, rather than editing it.

- [ ] **Step 4: Run the tests, gate and commit**

```
python -m pytest backend/tests/test_reference.py -v
scripts\ccweb_check.cmd
git add backend/app/routers/reference.py backend/tests/test_reference.py
git commit -m "Return fee kinds in their curated order, with Other last"
```

---

### Task 11: An order says which platform it sold on

Carried findings (i) and (g), neither of which had a task.

**Files:**
- Modify: `backend/app/schemas.py` (`OrderOut`)
- Modify: `backend/app/routers/orders.py` (`_order_out`, `_ORDER_DETAIL`)
- Modify: `backend/tests/test_orders.py` (one case)
- Modify: `frontend/src/owner/pages/Orders.jsx` (grey out `cancelled`)
- Modify: `frontend/src/owner/pages/Orders.test.jsx` (one case)
- Modify: `frontend/src/owner/pages/RecordSaleDialog.test.jsx` (finding (g))

- [ ] **Step 1: Expose the platform on `OrderOut`**

Add `sales_venue_code: str` and `sales_venue_name: str`. `SalesOrder` has
`sales_venue_id` but **no `sales_venue` relationship** (`models/sales.py:478`,
`:515-531`), so either add one — with a matching entry in `_ORDER_DETAIL`
(`routers/orders.py:102-111`), or a page of orders costs a query each — or
pass the venue in from `_load`. Adding the relationship is the smaller change
and matches `Listing.sales_venue`.

`_order_out(order, status_code, *, for_admin)` fills both from it. Both fields
are safe for a shopper: the platform a sale happened on is not staff-only, and
a store order simply reads `store`.

- [ ] **Step 2: Grey out the cancel option instead of offering and refusing it**

`routers/orders.py:281-292` already refuses the transition with a 409 naming
the platform. `Orders.jsx:55-61` still offers `cancelled` behind a
confirmation promising "Unshipped stock goes back on sale" — untrue for an
outside order, whose listing `record_sale` ended. With the platform on the
row, disable the option for any order whose `sales_venue_code` is not the
store's, and say why in the disabled row's title.

```python
def test_an_order_says_which_platform_it_was_sold_on(
    client: TestClient,
    admin_headers: dict[str, str],
    db: Session,
    ebay_listing: Listing,
    admin_user: User,
) -> None:
    """The Orders page cannot tell a shop sale from an outside one without it.

    Its status dropdown offers `cancelled` behind a confirmation promising
    the stock goes back on sale -- which `routers/orders.py` then refuses
    with a 409. A confusing prompt, not a broken action, and the platform is
    what lets the page grey the option out instead.
    """
    order = record_sale(
        db,
        ebay_listing,
        price=Decimal("120.00"),
        buyer_username="coinfan88",
        external_order_id="EB-2",
        fees=[],
        recorded_by=admin_user,
    )
    db.commit()
    body = client.get(f"/api/orders/{order.id}", headers=admin_headers).json()
    assert body["sales_venue_code"] == "ebay"
    assert body["sales_venue_name"] == "eBay"
```

```jsx
// frontend/src/owner/pages/Orders.test.jsx
it('does not offer cancel for a sale made on another platform', async () => {
  // The server refuses the transition with a 409 (routers/orders.py); the
  // page offering it and then reporting a refusal is the confusing half.
  renderWithProviders(<Orders />, { strict: true })
  api.listOrders.mockResolvedValue({
    orders: [{ ...outsideOrder, sales_venue_code: 'ebay', sales_venue_name: 'eBay' }],
  })
  const select = await screen.findByLabelText(/status/i)
  const cancelled = within(select).getByRole('option', { name: /cancelled/i })
  expect(cancelled).toBeDisabled()
})
```

- [ ] **Step 3: Correct the mocked refusal message — finding (g)**

`RecordSaleDialog.test.jsx:153-165` mocks
`'Listing 14 is not on offer (ended)'`. `record_sale` now sends
`f"Listing {listing.id} on {listing.sales_venue.name} is not on offer
({listing.status.value})"` (`sales_writes.py:197-200`). The test still proves
what its name claims — that the dialog renders a 409 `detail` as plain text —
so this is fidelity drift, not a wrong-reason pass. Update **both** the mocked
string and the text the test looks for:

```jsx
    api.recordSale.mockRejectedValue(
      new ApiError(409, 'Listing 14 on eBay is not on offer (ended)', {
        detail: 'Listing 14 on eBay is not on offer (ended)',
      }),
    )
    // the existing renderDialog / type / click lines are unchanged
    expect(
      await screen.findByText('Listing 14 on eBay is not on offer (ended)'),
    ).toBeVisible()
```

- [ ] **Step 4: Run the tests, gate and commit**

```
python -m pytest backend/tests/test_orders.py -v
npm --prefix frontend test -- Orders RecordSaleDialog
scripts\ccweb_check.cmd
git add backend/app backend/tests/test_orders.py frontend/src/owner
git commit -m "Say which platform an order sold on, and stop offering a cancel that is refused"
```

---

### Task 12: The Lots page, and grouping from inventory

**Files:**
- Modify: `frontend/src/owner/api.js` (five methods)
- Modify: `frontend/src/owner/api.test.js` (request-body assertions)
- Create: `frontend/src/owner/pages/Lots.jsx`, `Lots.test.jsx`
- Modify: `frontend/src/owner/pages/inventory/BulkEditBar.jsx` (**not
  `Inventory.jsx`** — the bulk bar is its own component) and
  `BulkEditBar.test.jsx`
- Modify: `frontend/src/owner/pages/inventory/OfferDialog.jsx` (accept a lot)
- Modify: `frontend/src/owner/OwnerApp.jsx` and `OwnerApp.test.jsx`

**There is no "Selling group" in the console menu.** `OwnerApp.jsx:44-55` is a
flat list of ten `NavLink`s inside one `<nav className="nav">`, and
`OwnerApp.test.jsx:38-51` asserts on individual links rather than on a list, so
adding one breaks nothing. A Lots page needs **a `NavLink` and a `<Route>`** —
and `OwnerApp.test.jsx` proves both halves separately, because the link and the
route can each be deleted with the other still green (see its
`routes /photos to the unattached-photographs page`).

- [ ] **Step 1: Write the failing tests**

```jsx
// frontend/src/owner/pages/Lots.test.jsx
it('lists assembling lots with their running cost basis', async () => {
  api.listLots.mockResolvedValue({
    lots: [
      { id: 1, title: 'Three Morgans', status: 'assembling', version: 1,
        cost_basis: '1000.00', value: '1400.00', members: [] },
    ],
  })
  renderWithProviders(<Lots />, { strict: true })
  expect(await screen.findByText('Three Morgans')).toBeVisible()
  expect(screen.getByText('1000.00')).toBeVisible()
})

it('sends the version token when membership changes', async () => {
  const user = userEvent.setup()
  api.listLots.mockResolvedValue({ lots: [assembling] })
  api.updateLot.mockResolvedValue({ ...assembling, version: 2 })
  renderWithProviders(<Lots />, { strict: true })
  await user.click(await screen.findByRole('button', { name: /remove/i }))
  expect(api.updateLot).toHaveBeenCalledWith(
    assembling.id,
    expect.objectContaining({ version: assembling.version }),
  )
})

it('offers a lot through the same dialog an item uses', async () => {
  const user = userEvent.setup()
  api.listLots.mockResolvedValue({ lots: [assembling] })
  renderWithProviders(<Lots />, { strict: true })
  await user.click(await screen.findByRole('button', { name: /^offer/i }))
  expect(await screen.findByRole('dialog', { name: /offer/i })).toBeVisible()
  expect(within(screen.getByRole('dialog')).getAllByRole('row')).toHaveLength(2)
})

it('shows a load failure without blanking the page', async () => {
  // A venues- or lots-load failure taking the page down is the exact bug
  // 872e219 fixed on main on 2026-09-19 ("Stop a refused action taking the
  // console page down with it"). The page must stay usable.
  api.listLots.mockRejectedValue(new ApiError(500, 'boom', {}))
  renderWithProviders(<Lots />, { strict: true })
  expect(await screen.findByText(/boom/)).toBeVisible()
  expect(screen.getByRole('heading', { name: /sales lots/i })).toBeVisible()
})

it('re-offers a dissolved lot as a new assembling one', async () => {
  const user = userEvent.setup()
  api.listLots.mockResolvedValue({ lots: [dissolved] })
  api.createLot.mockResolvedValue({ ...assembling, id: 9 })
  renderWithProviders(<Lots />, { strict: true })
  await user.click(await screen.findByRole('button', { name: /re-offer as a lot/i }))
  expect(api.createLot).toHaveBeenCalledWith(
    expect.objectContaining({ title: dissolved.title }),
  )
})
```

```jsx
// frontend/src/owner/api.test.js
it('sends lot membership changes as the API expects', async () => {
  fetchMock.mockResolvedValue(jsonResponse({ id: 1 }))
  await api.updateLot(1, { version: 3, add_item_ids: [7] })
  const [url, init] = fetchMock.mock.calls[0]
  expect(url).toBe('/api/sales-lots/1')
  expect(init.method).toBe('PATCH')
  expect(JSON.parse(init.body)).toEqual({ version: 3, add_item_ids: [7] })
})
```

```jsx
// frontend/src/owner/pages/inventory/BulkEditBar.test.jsx
it('groups the selection into a lot', async () => {
  const user = userEvent.setup()
  api.createLot.mockResolvedValue({ id: 5, title: 'New lot', members: [] })
  renderWithProviders(
    <BulkEditBar ids={[1, 2]} rows={rows} view="coins" />, { strict: true },
  )
  await user.click(screen.getByRole('button', { name: /group into lot/i }))
  await user.click(await screen.findByRole('button', { name: /^create lot$/i }))
  expect(api.createLot).toHaveBeenCalledWith(
    expect.objectContaining({ add_item_ids: [1, 2] }),
  )
})
```

```jsx
// frontend/src/owner/OwnerApp.test.jsx -- two cases, one per half
it('links to the Lots page', () => {
  renderWithProviders(<OwnerApp />, { auth: adminAuth(), route: '/nowhere' })
  expect(screen.getByRole('link', { name: /^lots$/i })).toBeInTheDocument()
})

it('routes /lots to the sales lots page', async () => {
  // The link above proves only that the link renders. Deleting the
  // <Route path="/lots" ...> line leaves it in place and lands the operator
  // on "Page not found", which no assertion on the navigation can see.
  renderWithProviders(<OwnerApp />, { auth: adminAuth(), route: '/lots' })
  expect(
    await screen.findByRole('heading', { name: /sales lots/i }),
  ).toBeInTheDocument()
})
```

**Render with `strict: true`.** The console runs in `<StrictMode>` and the
harness does not by default; a `mounted` ref set only in a cleanup is
permanently false in the app while its test passes. That trap has silenced a
save-failure message and stopped a dialog closing, both on `main`.

**Do not mock `api` wholesale for the request-body assertions.** Page tests
that mock the whole module test nothing about what the client actually sends —
that is how `api.updateImageLink` shipped clearing a photograph's role. The
body assertion lives in `owner/api.test.js`, above.

- [ ] **Step 2: Run to verify they fail, then add the API methods**

Run: `npm --prefix frontend test -- Lots`
Expected: FAIL — the component does not exist.

In `frontend/src/owner/api.js` — **not** `shared/api.js`, which every
anonymous shop visitor downloads — beside `createOffers`:

```js
  listLots: (params) => {
    const qs = new URLSearchParams(params ?? {}).toString()
    return send(`/api/sales-lots${qs ? `?${qs}` : ''}`)
  },
  createLot: (body) => send('/api/sales-lots', { method: 'POST', body }),
  updateLot: (id, body) => send(`/api/sales-lots/${id}`, { method: 'PATCH', body }),
  deleteLot: (id) => send(`/api/sales-lots/${id}`, { method: 'DELETE' }),
```

- [ ] **Step 3: Build the page and the two entry points**

Follow `Listings.jsx` for the table and row actions, `Platforms.jsx` for the
form. The page shows assembling lots with add and remove, title and
description, running cost basis and value; **Offer** reusing
`inventory/OfferDialog.jsx` with one row; a history of offered, sold and
dissolved lots; and **Re-offer as a lot** on a dissolved one, which pre-fills a
new assembling lot.

`BulkEditBar.jsx` gains **Group into lot...** beside its existing
**Offer for sale...** button (`:109-111`), taking the same `chosen` rows.

`OwnerApp.jsx` gains `<NavLink to="/lots">Lots</NavLink>` in the flat nav list
and `<Route path="/lots" element={<Lots />} />` beside the others.

A venues- or lots-load failure must not blank the page — show the error and
keep the page usable, per 872e219 (2026-09-19).

- [ ] **Step 4: Run the tests, gate and commit**

```
npm --prefix frontend test
scripts\ccweb_check.cmd
git add frontend/src/owner
git commit -m "Assemble and offer sales lots from the console"
```

---

### Task 13: Documentation

**Files:**
- Modify: `docs/specs/selling-design.md` (phase list, *Testing*, the Revision
  note's item 3)
- Modify: `docs/system-administration.md` (a Lots section)
- Modify: `backend/app/sale_snapshot.py` (the module docstring's shape
  description)

- [ ] **Step 1: Mark phase 3 built**, with the date, in the *Phases* list and
      the status line at the top. Leave phase 4 as it is.
- [ ] **Step 2: Update *Testing*.** Its three invariants now read: claim state
      (2R), **lot membership (this phase)** and **disposition (this phase)**.
      The Revision note's item 3 says the claim invariant is "extended in phase
      3 to cover lot membership" — correct it to say what was actually built:
      a **separate** check with its own exception type, because the claim
      waiver absorbs any `ClaimInvariantViolation`.
- [ ] **Step 3: Note the snapshot version change** where snapshot readers are
      documented, so an older snapshot's shape stays readable: version 1 always
      has `item`; version 2 has `item` **or** `lot` + `items`.
- [ ] **Step 4: Document the Lots page** in `docs/system-administration.md`,
      beside *Recording a sale*: assembling, offering, what dissolves a lot and
      what a sold one leaves behind.
- [ ] **Step 5: Re-read every docstring written in this branch** against the
      final code. A docstring describing an earlier draft is a trap for the
      next reader; this repo has a commit named for exactly that cleanup.
- [ ] **Step 6: Gate and commit.**

```
scripts\ccweb_check.cmd
git add docs backend/app
git commit -m "Document sales lots"
```

---

## Verification before handing back

- [ ] `scripts\ccweb_check.cmd` exits zero — every stage, including
      `python types` and the mutation-scaffolding guard.
- [ ] `python -m pytest backend/tests/test_migrations.py -v` passes, including
      `test_migrations_round_trip`, `test_migrations_match_models` and the new
      `test_the_migration_carries_every_check_constraint`.
- [ ] Every race test in Task 9 is mutation-proven, and each docstring names
      the mutation it survives. No `MUTATION` comment, `if False:` or
      `if True:` is left in `backend/app`.
- [ ] The three new invariant proofs are `xfail(strict=True, raises=<its own
      type>)` and the suite reports them as `xfailed`, not `failed` or
      `xpassed`.
- [ ] **Live is untouched:** `ccwebdb`'s `alembic_version` still reads
      `e7c3a5b19d84`. Check it against the database, not against this branch's
      migration files.
- [ ] `git log --oneline main..HEAD` shows one commit per task, no merge
      commits, and `git merge-base --is-ancestor main HEAD` succeeds so the
      owner's merge can fast-forward.
- [ ] Report to the owner: branch name, commit count, what the two new
      invariants found when first run across the whole suite, and that phase 4
      follows before anything is migrated.
