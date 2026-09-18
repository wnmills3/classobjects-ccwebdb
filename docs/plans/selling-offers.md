# Selling, phase 2: offering items for sale -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Written 2026-09-17, after phases 0-1 shipped (`docs/plans/selling-platforms.md`).

**Goal:** Offer an item the business already owns for sale on any platform, end
that offer, and see every offer in one place -- with the database itself
refusing to offer one item in two places at once.

**Architecture:** A new `offer_claim` row per item per listing carries the
offer's state (`active`/`paused`/`released`); a partial unique index over
active claims is the "never offered twice" guarantee. A new
`app/offering_writes.py` is the only writer of `listing.status`, `offer_claim`
and the item disposition changes they cause, and it becomes the single home of
the store/fixed-price predicate that phase 1 left expressed in three places.
The console gains a Listings page and an Offers panel; the old Manage page and
the catalogue write endpoints retire.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 18,
pytest; React + Vite, vitest, Testing Library.

**Spec:** `docs/specs/selling-design.md` (phase 2 of its Phases section)

**Not in this plan:** recording an outside sale with its fees and per-item
shares, and platform buyers as customers. That is the rest of the spec's
phase 2 and gets its own plan (`docs/plans/selling-sales.md`) once this lands:
it needs `sales_fee_kind`, `sales_order_fee`, `sales_order_item_share` and
`customer.sales_venue_id`, and it builds on `end_offer(..., sold=True)` from
Task 2 here. Sales lots (phase 3) and auctions (phase 4) follow those.

## Global Constraints

- Git: work on `feat/selling-offers` (already cut from `main` at `8461382`); never commit to `main`.
- Scripts and commands: cmd only, never PowerShell. Sleep with `ping -n 2 127.0.0.1 >nul`, never `timeout`.
- Files: create with the Write tool, change with Edit; **no shell heredocs**; never `sed` a Windows path. A script that rewrites a file uses `newline=""`.
- Gate: `scripts\ccweb_check.cmd` runs ruff, mypy, pytest, eslint, prettier, vitest and the bundle-isolation check; all are at zero. Run it as its own command, output redirected to a file, and **read its own exit code** -- `cmd ... ; echo $?` reports the echo, not the gate. Commit in a separate call. One pytest run at a time (`ccwebdb_test` is shared).
- pytest: `addopts` already has `-q`; do not add another.
- Every public class, method and function has a docstring; every function is annotated (ruff `D`, `ANN`). No new ignores. Escape regex metacharacters in `pytest.raises(match=...)` (ruff `RUF043`).
- Classifiers cross the API as codes, never ids; unknown code -> 422.
- Money is `Decimal`, never float.
- Console API calls go in `frontend/src/owner/api.js`, never `shared/api.js`.
- Console edit windows use `AccessLabel` + `accel()` + `useSaveShortcut` (`frontend/src/owner/shortcuts.js`); letters avoid D, E and F.
- Enum types created by a migration are dropped by its downgrade (`DROP TYPE IF EXISTS`); every constraint, index and foreign key is named.
- A migration that changes data is tested **with rows** (`test_the_sales_venue_migration_moves_real_rows` in `backend/tests/test_migrations.py` is the pattern); an empty-database round trip proves only that the DDL runs.
- Any `create_views(...)` call added to a migration gets a row in `HISTORICAL_VIEW_CALLS`.
- Live database writes only on the owner's word, backup (`pg_dump`) first, dry-run counts shown first. **Nothing in this plan touches live** -- the migration is applied in a later, owner-gated step.
- The owner enters purchases and items in the console while this work proceeds: never run a data pass against `ccwebdb`, and never assume the item count is unchanged from one day to the next.

Run backend tests from `backend/` with the env's python:
`/c/Users/wnmil/miniforge3/envs/ccwebdb/python.exe -m pytest tests/test_x.py`.
Frontend: from `frontend/`, `npx vitest run src/owner/...`.

---

## File map

| File | Responsibility |
|---|---|
| `backend/app/models/sales.py` | + `ClaimState`, `OfferClaim`; `Listing.paused_by_listing_id` and its relationship |
| `backend/app/models/__init__.py` | exports |
| `backend/alembic/versions/e7c3a5b19d84_offer_claims.py` (new) | `offer_claim`, the partial unique index, `paused_by_listing_id`, and a claim for every listing that already exists |
| `backend/app/offering_writes.py` (new) | the only writer of `listing.status`, `offer_claim` and the dispositions they cause; the single home of the store/fixed-price rule |
| `backend/app/order_writes.py`, `backend/app/routers/catalog.py` | use `offering_writes`' predicate instead of their own copies |
| `backend/app/sale_state.py` | "for sale" counts active **and paused** claims |
| `backend/app/splitting.py` | ending a split lot's listings goes through `offering_writes` |
| `backend/app/routers/offers.py` (new) | `POST /api/offers`, `GET /api/listings`, `PATCH /api/listings/{id}`, `POST /api/listings/{id}/end` |
| `backend/app/routers/catalog.py` | `POST`/`PATCH`/`DELETE` removed (retired with the Manage page) |
| `backend/app/schemas.py` | `OfferIn`, `OfferRefusal`, `OfferResultOut`, `ListingOut`, `ListingUpdate` |
| `backend/app/main.py`, `backend/app/routers/__init__.py` | register the new router |
| `backend/tests/test_offering_writes.py`, `test_offers_api.py`, `test_offer_races.py` (new) | unit, API and concurrency tests |
| `frontend/src/owner/pages/Listings.jsx` (+ test) (new) | the Listings page |
| `frontend/src/owner/pages/inventory/OfferDialog.jsx` (+ test) (new) | the offer form, used from inventory and the item editor |
| `frontend/src/owner/pages/inventory/OffersPanel.jsx` (+ test) (new) | current and past offers inside the item editor |
| `frontend/src/owner/pages/inventory/BulkEditBar.jsx` | + "Offer for sale..." |
| `frontend/src/owner/pages/inventory/ItemEditForm.jsx` | + the Offers panel |
| `frontend/src/owner/OwnerApp.jsx` | Listings route and nav; Manage route and nav removed |
| `frontend/src/owner/pages/AdminCoins.jsx` (+ test) | deleted |
| `frontend/src/owner/api.js` | offer calls added; catalogue write calls removed |
| `docs/database-design.md`, `docs/system-administration.md`, `docs/specs/selling-design.md` | documentation |

---

### Task 1: `offer_claim` and the pause link

**Files:**
- Modify: `backend/app/models/sales.py`, `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/e7c3a5b19d84_offer_claims.py`
- Test: `backend/tests/test_offering_writes.py` (new), `backend/tests/test_migrations.py`

**Interfaces:**
- Consumes: `Listing` (with `status`, `sales_venue_id`, `format`), `ListingStatus`, `InventoryItem`.
- Produces:
  - `ClaimState` (`enum.StrEnum`): `active`, `paused`, `released`; PG type `offer_claim_state`.
  - `OfferClaim`: `id`, `inventory_item_id`, `listing_id`, `state`, `created_at`/`updated_at`; relationships `listing`, `item`; index `uq_offer_claim_active` (unique, partial, `inventory_item_id` where `state = 'active'`).
  - `Listing.paused_by_listing_id: int | None` and `Listing.paused_by: Listing | None`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_offering_writes.py`:

```python
"""Offer claims and the offering writer (selling design, phase 2)."""

from __future__ import annotations

import pytest
from app.models import ClaimState, InventoryItem, Listing, ListingStatus, OfferClaim
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session


def _claim(db: Session, item: InventoryItem, listing: Listing, state: ClaimState) -> OfferClaim:
    claim = OfferClaim(inventory_item_id=item.id, listing_id=listing.id, state=state)
    db.add(claim)
    db.flush()
    return claim


def test_one_item_can_have_only_one_active_claim(
    db: Session, listing: Listing, make_listing: object
) -> None:
    """The database, not the application, is what refuses the second offer."""
    item_id = listing.inventory_item_id
    second = make_listing(inventory_item_id=item_id)
    db.add(OfferClaim(inventory_item_id=item_id, listing_id=listing.id, state=ClaimState.active))
    db.flush()

    db.add(OfferClaim(inventory_item_id=item_id, listing_id=second.id, state=ClaimState.active))
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_paused_claim_does_not_block_an_active_one(
    db: Session, listing: Listing, make_listing: object
) -> None:
    item_id = listing.inventory_item_id
    second = make_listing(inventory_item_id=item_id)
    db.add(OfferClaim(inventory_item_id=item_id, listing_id=listing.id, state=ClaimState.paused))
    db.add(OfferClaim(inventory_item_id=item_id, listing_id=second.id, state=ClaimState.active))

    db.flush()  # no IntegrityError

    assert db.query(OfferClaim).filter_by(inventory_item_id=item_id).count() == 2


def test_a_released_claim_does_not_block_an_active_one(
    db: Session, listing: Listing, make_listing: object
) -> None:
    item_id = listing.inventory_item_id
    second = make_listing(inventory_item_id=item_id)
    db.add(OfferClaim(inventory_item_id=item_id, listing_id=listing.id, state=ClaimState.released))
    db.add(OfferClaim(inventory_item_id=item_id, listing_id=second.id, state=ClaimState.active))

    db.flush()

    assert db.query(OfferClaim).filter_by(inventory_item_id=item_id).count() == 2


def test_a_listing_records_what_paused_it(
    db: Session, listing: Listing, make_listing: object
) -> None:
    """Settlement needs to know which offer to resume a store listing for."""
    elsewhere = make_listing(inventory_item_id=listing.inventory_item_id)
    listing.status = ListingStatus.paused
    listing.paused_by_listing_id = elsewhere.id
    db.commit()
    db.refresh(listing)

    assert listing.paused_by.id == elsewhere.id
```

`make_listing(inventory_item_id=...)` already passes overrides through to
`Listing` (`backend/tests/conftest.py`); read `build_listing` before relying
on it and use the same style.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_offering_writes.py`
Expected: FAIL at collection -- `ImportError: cannot import name 'ClaimState' from 'app.models'`.

- [ ] **Step 3: The models**

In `backend/app/models/sales.py`, after `ListingStatus`:

```python
class ClaimState(enum.StrEnum):
    """Whether a claim currently holds its item off every other platform.

    `paused` is a store listing set aside while the item is offered
    elsewhere: it keeps the listing and its price, and resumes when that
    offer ends unsold. Only an `active` claim reserves the item, which is
    what the partial unique index below enforces.
    """

    active = "active"
    paused = "paused"
    released = "released"
```

Add to `Listing`, beside `external_id`:

```python
    #: The offer this store listing was set aside for, so settling that offer
    #: knows which listings to resume. Null unless `status` is `paused`.
    paused_by_listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=True
    )
```

and beside its relationships:

```python
    paused_by: Mapped[Listing | None] = relationship(
        remote_side=lambda: [Listing.id], foreign_keys=lambda: [Listing.paused_by_listing_id]
    )
```

(A self-referential many-to-one needs `remote_side`; read SQLAlchemy's error
message if it complains and adjust rather than guessing.)

Then, after `Listing`:

```python
class OfferClaim(TimestampMixin, Base):
    """One item's hold on one listing, and the "offered once" guarantee.

    An item listing has one claim; a lot listing (phase 3) will have one per
    member, which is why the rule lives here rather than on `listing`.
    Written only by `app.offering_writes`, in the same transaction as the
    listing it mirrors: a claim's state always follows its listing's status.
    """

    __tablename__ = "offer_claim"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    listing_id: Mapped[int] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), index=True, nullable=False
    )
    state: Mapped[ClaimState] = mapped_column(
        enum_column(ClaimState, "offer_claim_state"),
        default=ClaimState.active,
        nullable=False,
    )

    listing: Mapped[Listing] = relationship()
    item: Mapped[InventoryItem] = relationship()

    __table_args__ = (
        UniqueConstraint("listing_id", "inventory_item_id", name="uq_offer_claim_pair"),
        # The guarantee: at most one active claim per item. A paused or
        # released claim does not reserve anything, so it is excluded.
        Index(
            "uq_offer_claim_active",
            "inventory_item_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )
```

Export `ClaimState` and `OfferClaim` from `backend/app/models/__init__.py`
(import list and `__all__`, alphabetical).

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_offering_writes.py`
Expected: PASS, 4 tests. (`test_migrations_match_models` fails until step 5.)

- [ ] **Step 5: The migration**

Create `backend/alembic/versions/e7c3a5b19d84_offer_claims.py` with
`down_revision = "d6a1f3b8c402"`. Read
`backend/alembic/versions/d6a1f3b8c402_sales_venues.py` first and follow its
shape exactly (named constraints, enum created and dropped, `TimestampMixin`
columns copied as that migration copies them).

Upgrade:
1. `postgresql.ENUM("active", "paused", "released", name="offer_claim_state").create(bind)`.
2. `op.create_table("offer_claim", ...)` with the columns above, named FKs
   (`fk_offer_claim_inventory_item_id`, `fk_offer_claim_listing_id`), the
   `uq_offer_claim_pair` unique constraint and the two plain indexes.
3. `op.create_index("uq_offer_claim_active", "offer_claim", ["inventory_item_id"], unique=True, postgresql_where=sa.text("state = 'active'"))`.
4. `op.add_column("listing", sa.Column("paused_by_listing_id", sa.Integer(), nullable=True))`
   plus `op.create_foreign_key("fk_listing_paused_by_listing_id", "listing", "listing", ["paused_by_listing_id"], ["id"], ondelete="RESTRICT")`.
5. **Backfill a claim for every listing that already exists**, so the
   invariant holds from the first moment:

```python
    bind.execute(
        sa.text(
            "INSERT INTO offer_claim "
            "(inventory_item_id, listing_id, state, created_at, updated_at) "
            "SELECT inventory_item_id, id, "
            "CASE status "
            "  WHEN 'active' THEN 'active'::offer_claim_state "
            "  WHEN 'paused' THEN 'paused'::offer_claim_state "
            "  ELSE 'released'::offer_claim_state END, "
            "now(), now() FROM listing WHERE inventory_item_id IS NOT NULL"
        )
    )
```

Downgrade: drop `paused_by_listing_id` (constraint first), drop the table, then
`DROP TYPE IF EXISTS offer_claim_state`. No view is touched, so no
`create_views` call and no `HISTORICAL_VIEW_CALLS` row.

- [ ] **Step 6: Test the migration with rows**

In `backend/tests/test_migrations.py`, beside
`test_the_sales_venue_migration_moves_real_rows`, add
`test_the_offer_claim_migration_claims_existing_listings`, built the same way
(read that test and copy its fixture and helper usage):

- upgrade a fresh database to `d6a1f3b8c402`;
- insert the rows a listing needs, then three listings for three different
  items with `status` `active`, `paused` and `ended`;
- upgrade to `head`;
- assert one claim per listing, with states `active`, `paused`, `released`
  respectively, and that `uq_offer_claim_active` exists:

```python
        state_by_status = dict(
            conn.execute(
                sa.text(
                    "SELECT l.status::text, c.state::text FROM offer_claim c "
                    "JOIN listing l ON l.id = c.listing_id"
                )
            ).all()
        )
        assert state_by_status == {
            "active": "active",
            "paused": "paused",
            "ended": "released",
        }
```

- then downgrade to `d6a1f3b8c402` and assert the listings survive with their
  statuses and that `to_regclass('offer_claim')` is null.

- [ ] **Step 7: Run the migration tests**

Run: `pytest tests/test_migrations.py`
Expected: PASS. If `test_migrations_match_models` reports the partial index
predicate as different, PostgreSQL normalised it -- copy the reflected text
into both the model and the migration.

- [ ] **Step 8: Mutation check**

Remove `unique=True` from `uq_offer_claim_active` in the model, run
`pytest tests/test_offering_writes.py -k only_one_active`, confirm it FAILS,
restore it, confirm it passes. Note both outputs in your report.

- [ ] **Step 9: Gate and commit**

```cmd
.\scripts\ccweb_check.cmd > %TEMP%\gate.txt 2>&1
```
Read the file and the script's own exit code; commit only on 0, with
`git commit -F <message file>` and the Co-Authored-By trailer. The message
should say why the rule lives on a claim row rather than on `listing`.

---

### Task 2: `offering_writes`, and one home for the shop rule

**Files:**
- Create: `backend/app/offering_writes.py`
- Modify: `backend/app/order_writes.py` (`_sellable_here`), `backend/app/routers/catalog.py` (`_sold_in_shop`, `list_catalog`'s filter), `backend/app/sale_state.py`, `backend/app/splitting.py`
- Test: `backend/tests/test_offering_writes.py`, `backend/tests/test_sale_state.py` (if it exists; otherwise the sale-state tests inside `backend/tests/test_inventory_edit.py` -- grep for `acknowledge_for_sale` and put the new test beside the others)

**Interfaces:**
- Consumes: Task 1's `OfferClaim`, `ClaimState`; `Listing`, `ListingStatus`, `ListingFormat`, `SalesVenue`; `app.sales_venues.store_venue_id`; `app.references.require_code`.
- Produces:
  - `sellable_in_shop(listing: Listing) -> bool` -- the Python form of the rule.
  - `shop_listing_filters() -> list[ColumnElement[bool]]` -- the SQL form, for `list_catalog`.
  - `OfferRefused(Exception)` with `.reason: str` and `.item_code: str`.
  - `offer(db, *, item, venue, listing_format, price, title, description, external_id, quantity) -> Listing`.
  - `end_offer(db, listing, *, sold: bool = False) -> None`.
  - `claims_for(db, item_ids: Collection[int]) -> dict[int, list[OfferClaim]]` -- active and paused claims, for `sale_state`.

Behaviour (spec, "How things move"):

| Case | Result |
|---|---|
| item has an active claim on a **non-store** listing | `OfferRefused`: "CC-001234 is active on eBay, listing #12: end it first" |
| item has an active claim on a **store** listing, and the new offer is elsewhere | that listing is `paused`, its claim `paused`, `paused_by_listing_id` set to the new listing |
| item has an active claim on a store listing, and the new offer is also the store | `OfferRefused` ("already offered in the shop, listing #12") |
| item not `received`, or `split_at` set, or `deleted_at` set | `OfferRefused` naming which |
| venue retired (`is_active` false) | `OfferRefused` |
| success | new listing `active` with one `active` claim; item disposition `listed` |
| `end_offer(sold=False)` | listing `ended` + `ended_at`; claims `released`; every listing paused **by** it resumes (`active`, claim `active`, `paused_by_listing_id` cleared); item `held` if it now has no active or paused claim |
| `end_offer(sold=True)` | as above, except listings paused by it are **ended** instead of resumed, and the item is left alone (the sale path sets `sold`) |

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_offering_writes.py` (imports at the top of the
file, ruff `I` order):

```python
def _venue(db: Session, code: str, kind: str = "marketplace") -> SalesVenue:
    kind_id = db.scalar(select(SalesVenueKind.id).where(SalesVenueKind.code == kind))
    venue = SalesVenue(code=code, name=code.title(), sales_venue_kind_id=kind_id)
    db.add(venue)
    db.flush()
    return venue


def test_offering_an_item_creates_an_active_listing_and_claim(
    db: Session, make_item: object
) -> None:
    item = make_item()
    ebay = _venue(db, "ebay-offer")

    listing = offering_writes.offer(
        db,
        item=item,
        venue=ebay,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("120.00"),
        title="1881-S Morgan",
        description="",
        external_id="12345",
        quantity=1,
    )
    db.commit()

    assert listing.status is ListingStatus.active
    assert listing.sales_venue_id == ebay.id
    claims = db.scalars(select(OfferClaim).where(OfferClaim.listing_id == listing.id)).all()
    assert [c.state for c in claims] == [ClaimState.active]
    assert item.disposition.code == "listed"


def test_offering_elsewhere_pauses_the_store_listing(db: Session, listing: Listing) -> None:
    item = listing.inventory_item
    ebay = _venue(db, "ebay-pause")

    elsewhere = offering_writes.offer(
        db,
        item=item,
        venue=ebay,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("99.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()
    db.refresh(listing)

    assert listing.status is ListingStatus.paused
    assert listing.paused_by_listing_id == elsewhere.id
    assert elsewhere.status is ListingStatus.active


def test_a_second_offer_elsewhere_is_refused(db: Session, make_item: object) -> None:
    item = make_item()
    first = _venue(db, "ebay-first")
    second = _venue(db, "whatnot-second")
    offering_writes.offer(
        db, item=item, venue=first, listing_format=ListingFormat.fixed_price,
        price=Decimal("10.00"), title="", description="", external_id=None, quantity=1,
    )
    db.commit()

    with pytest.raises(offering_writes.OfferRefused, match="end it first"):
        offering_writes.offer(
            db, item=item, venue=second, listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"), title="", description="", external_id=None, quantity=1,
        )


def test_an_unreceived_item_cannot_be_offered(db: Session, make_item: object) -> None:
    item = make_item(status="ordered")
    venue = _venue(db, "ebay-unreceived")

    with pytest.raises(offering_writes.OfferRefused, match="not received"):
        offering_writes.offer(
            db, item=item, venue=venue, listing_format=ListingFormat.fixed_price,
            price=Decimal("10.00"), title="", description="", external_id=None, quantity=1,
        )


def test_ending_an_offer_resumes_the_paused_store_listing(
    db: Session, listing: Listing
) -> None:
    item = listing.inventory_item
    ebay = _venue(db, "ebay-resume")
    elsewhere = offering_writes.offer(
        db, item=item, venue=ebay, listing_format=ListingFormat.fixed_price,
        price=Decimal("99.00"), title="", description="", external_id=None, quantity=1,
    )
    db.commit()

    offering_writes.end_offer(db, elsewhere)
    db.commit()
    db.refresh(listing)

    assert elsewhere.status is ListingStatus.ended
    assert elsewhere.ended_at is not None
    assert listing.status is ListingStatus.active
    assert listing.paused_by_listing_id is None
    assert item.disposition.code == "listed"


def test_ending_a_sold_offer_ends_the_paused_store_listing(
    db: Session, listing: Listing
) -> None:
    """A sold item must not come back into the shop."""
    item = listing.inventory_item
    ebay = _venue(db, "ebay-sold")
    elsewhere = offering_writes.offer(
        db, item=item, venue=ebay, listing_format=ListingFormat.fixed_price,
        price=Decimal("99.00"), title="", description="", external_id=None, quantity=1,
    )
    db.commit()

    offering_writes.end_offer(db, elsewhere, sold=True)
    db.commit()
    db.refresh(listing)

    assert listing.status is ListingStatus.ended


def test_ending_the_only_offer_puts_the_item_back_to_held(
    db: Session, make_item: object
) -> None:
    item = make_item()
    venue = _venue(db, "ebay-held")
    made = offering_writes.offer(
        db, item=item, venue=venue, listing_format=ListingFormat.fixed_price,
        price=Decimal("10.00"), title="", description="", external_id=None, quantity=1,
    )
    db.commit()

    offering_writes.end_offer(db, made)
    db.commit()

    assert item.disposition.code == "held"
    claims = db.scalars(select(OfferClaim).where(OfferClaim.listing_id == made.id)).all()
    assert [c.state for c in claims] == [ClaimState.released]


def test_an_item_offered_elsewhere_counts_as_for_sale(
    db: Session, listing: Listing
) -> None:
    """The edit warning must fire for a paused store listing too."""
    item = listing.inventory_item
    ebay = _venue(db, "ebay-warning")
    offering_writes.offer(
        db, item=item, venue=ebay, listing_format=ListingFormat.fixed_price,
        price=Decimal("99.00"), title="", description="", external_id=None, quantity=1,
    )
    db.commit()

    uses = sale_state.for_sale(db, [item.id])

    assert len(uses[item.id]) == 2  # the active offer and the paused store listing
```

Check `make_item`'s signature in `conftest.py` before using `status="ordered"`;
if it does not take a status, build the item with `build_item(db, status_id=...)`
the way the other tests do.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_offering_writes.py`
Expected: FAIL -- `AttributeError: module 'app.offering_writes' has no attribute 'offer'` (or an import error).

- [ ] **Step 3: Write `offering_writes`**

Create `backend/app/offering_writes.py`. Its docstring explains the module's
two jobs: the sole writer, and the single home of the shop rule. Sketch:

```python
def sellable_in_shop(listing: Listing) -> bool:
    """Whether the shop may sell this listing: our store, fixed price, active."""
    return (
        listing.sales_venue.is_own_store
        and listing.format is ListingFormat.fixed_price
        and listing.status is ListingStatus.active
    )


def shop_listing_filters() -> list[ColumnElement[bool]]:
    """The same rule as SQL, for the catalogue query."""
    return [
        Listing.sales_venue.has(SalesVenue.is_own_store.is_(True)),
        Listing.format == ListingFormat.fixed_price,
        Listing.is_active.is_(True),
    ]
```

`offer(...)` refuses per the table above, pauses a store listing when the new
offer is elsewhere, creates the listing and its claim, and sets the item's
disposition through `require_code(db, Disposition, "listed", "disposition")`.
`end_offer(...)` ends the listing, releases its claims, and resumes or ends the
listings it paused. Both take rows under `FOR UPDATE` before reading claims:

```python
    db.execute(
        select(InventoryItem.id).where(InventoryItem.id == item.id).with_for_update()
    )
```

- [ ] **Step 4: Point the three copies of the rule at it**

- `backend/app/order_writes.py`: delete `_sellable_here` and call
  `offering_writes.sellable_in_shop(listing)` in both `place_order` and
  `revise_order`. Keep the refusal wording ("not sold in this shop") so the
  existing tests still pass.
- `backend/app/routers/catalog.py`: delete `_sold_in_shop`, use
  `offering_writes.sellable_in_shop` in `get_catalog_item`, and replace
  `list_catalog`'s two inline predicates with `*offering_writes.shop_listing_filters()`
  (drop the now-duplicated `Listing.is_active` filter it already had).
- `backend/app/sale_state.py`: read claims rather than listings --
  `offering_writes.claims_for(db, ids)` -- so a paused store listing and an
  offer elsewhere both count. Keep the `SaleUse.text` wording style
  ("listing #3 at 120.00"), adding the platform name for a non-store listing.
- `backend/app/splitting.py`: replace the inline `status`/`ended_at` write with
  `offering_writes.end_offer(db, listing)` for each of the lot's listings.

Watch for an import cycle: `offering_writes` must not import `order_writes` or
the routers. If `sale_state` importing `offering_writes` creates a cycle, move
`claims_for` into `sale_state` and have `offering_writes` import *it*.

- [ ] **Step 5: Run the affected suites**

Run: `pytest tests/test_offering_writes.py tests/test_order_writes.py tests/test_catalog.py tests/test_split.py tests/test_sale_snapshots.py`
Expected: PASS.

- [ ] **Step 6: Mutation check the pause/resume pair**

Comment out the resume loop in `end_offer`, run
`pytest tests/test_offering_writes.py -k resumes`, confirm FAIL; restore and
confirm PASS.

- [ ] **Step 7: Gate and commit** (as Task 1, step 9).

---

### Task 3: Race tests for the claim rule

**Files:**
- Create: `backend/tests/test_offer_races.py`
- Test only; no source changes expected. If a race exposes a defect, fix it in
  `offering_writes` and say so in your report.

**Interfaces:** consumes Task 2's `offer`, `end_offer`.

Read `backend/tests/test_order_revision_race.py` first: it is this project's
pattern for real-thread races (each thread its own `Session` and connection, a
`threading.Barrier`, `TestClient` deliberately avoided because Starlette
serialises through one portal).

- [ ] **Step 1: Write the tests**

Two threads offering the same item on two platforms at once: exactly one
succeeds; the other raises (`OfferRefused` or `IntegrityError` from
`uq_offer_claim_active`), and afterwards the item has exactly one active claim.

Second test: one thread offers the item elsewhere while another ends the
store listing. Whatever the order, the end state is consistent -- no listing
is left `paused` with its pauser `ended`, and the item has at most one active
claim. Assert that directly:

```python
    orphaned = session.scalars(
        select(Listing).where(
            Listing.status == ListingStatus.paused,
            Listing.paused_by_listing_id.in_(
                select(Listing.id).where(Listing.status == ListingStatus.ended)
            ),
        )
    ).all()
    assert orphaned == []
```

- [ ] **Step 2: Run them**

Run: `pytest tests/test_offer_races.py`
Expected: PASS. A flake here is a real defect -- investigate, do not re-run
until green.

- [ ] **Step 3: Mutation check**

Drop the `FOR UPDATE` from `offer`, run the race tests, confirm at least one
fails (or that the failure mode changes from a clean refusal to an
`IntegrityError` reaching the caller), restore, confirm they pass. Record
both outputs.

- [ ] **Step 4: Gate and commit.**

---

### Task 4: The offers API

**Files:**
- Create: `backend/app/routers/offers.py`, `backend/tests/test_offers_api.py`
- Modify: `backend/app/schemas.py`, `backend/app/routers/__init__.py`, `backend/app/main.py`

**Interfaces:**
- Consumes: Task 2's `offer`, `end_offer`, `OfferRefused`.
- Produces:
  - `POST /api/offers` -- body `{venue, format, quantity?, items: [{item_id, price, title?, description?, external_id?}]}`; 201 with `{listings: [ListingOut]}`; **all or nothing**: a refusal returns 409 with `{"detail": ..., "refused": [{"item_code": ..., "reason": ...}]}` and writes nothing.
  - `GET /api/listings?venue=&format=&status=&item_id=` -> `list[ListingOut]` (active and paused by default; `status=all` includes ended).
  - `PATCH /api/listings/{id}` -- `price`, `title`, `description`, `external_id`, `version`; 409 on a stale version.
  - `POST /api/listings/{id}/end` -> `ListingOut`.
  - `ListingOut`: `id, item_id, item_code, venue (code), venue_name, format, status, price, currency, quantity_available, title, description, external_id, external_url, listed_at, ended_at, paused_by_listing_id, version, item_title, cost_basis`.

`external_url` is computed for the response from the platform's
`listing_url_template` when the listing has an `external_id` and no explicit
URL -- **do not store it**; phase 1's docs say the column is what a person
typed. Put that in the schema docstring.

`cost_basis` is `inventory_item.total_cost`, staff-only -- this router is
admin-only, so it may carry it. Never add it to `CatalogItemOut`.

- [ ] **Step 1: Write the failing tests** (`backend/tests/test_offers_api.py`)

Cover: admin-only (401/403 as `tests/test_user_admin.py:27-28` shows); offering
two items at once; a batch where one item is already offered elsewhere is
refused **whole** (assert the other item has no listing afterwards); ending an
offer; the listing list filtered by platform and by status; a stale `version`
on PATCH giving 409; offering an unknown item id giving 422; and that
`external_url` comes back built from the platform's template.

- [ ] **Step 2: Run to verify they fail** (404 -- no router yet).

- [ ] **Step 3: Write the schemas and the router**, following
  `backend/app/routers/sales_venues.py` for structure, error shapes and the
  version-conflict handling (`StaleDataError` -> 409).

- [ ] **Step 4: Run to verify they pass.**

- [ ] **Step 5: Mutation check:** make the batch loop commit per item instead
  of once at the end; confirm the all-or-nothing test fails; restore.

- [ ] **Step 6: Gate and commit.**

---

### Task 5: Retire the catalogue write endpoints

**The page is already gone.** The owner met three kind-blindness bugs in it
while entering banknotes on 2026-09-17, so `AdminCoins.jsx`, its route, its
nav link and the console's `createCatalogItem`/`updateCatalogItem`/
`deleteCatalogItem` were removed that evening (commit `33a8f85`). The
endpoints were deliberately left behind so nothing lost the ability to end a
listing before this plan's offers API existed. **This task removes them**, now
that `POST /api/offers` and `POST /api/listings/{id}/end` replace them.

**Files:**
- Modify: `backend/app/routers/catalog.py` (remove `create_catalog_item`, `update_catalog_item`, `delete_catalog_item`, `_set_listing_active`, and any helper left unused -- `_resolve_classifiers`, `CatalogItemCreate`/`CatalogItemUpdate` in `schemas.py` if nothing else uses them; grep before deleting)
- Modify: `backend/tests/test_catalog.py`, `backend/tests/test_split.py`, `backend/tests/test_orders.py`, `backend/tests/test_order_writes.py` -- any test that creates or deletes through `/api/catalog` moves to `make_listing`/`offering_writes` or is deleted if it only tested the retired endpoint.

The spec's reason, worth keeping in the commit message: Manage created an item
*and* a listing together, which contradicts "no item is ever entered outside a
purchase", and it could not offer an item the business already owns -- which is
all of them.

**Before deleting `delete_catalog_item`, check what replaces it.** It is the
only path that removes a listing *and* its item when nothing else refers to
them; `end_offer` ends an offer but keeps both. The demo-listing cleanup of
2026-09-17 used it. If nothing needs the delete, say so in your report rather
than assuming.

- [ ] **Step 1:** grep for every caller first:

```cmd
cd backend && findstr /s /n /c:"/api/catalog" app\*.py tests\*.py
cd frontend && npx eslint src --max-warnings 0
```

- [ ] **Step 2:** remove the endpoints and the page, and update the tests.
- [ ] **Step 3:** run `pytest tests/test_catalog.py tests/test_split.py tests/test_orders.py tests/test_order_writes.py` and `npx vitest run src/owner`: expect PASS with the retired tests gone.
- [ ] **Step 4:** confirm the shop still works: `pytest tests/test_catalog.py -k "public or hides or shop"`.
- [ ] **Step 5: Gate and commit.**

---

### Task 6: The Listings page

**Files:**
- Create: `frontend/src/owner/pages/Listings.jsx`, `frontend/src/owner/pages/Listings.test.jsx`
- Modify: `frontend/src/owner/api.js`, `frontend/src/owner/OwnerApp.jsx`

**Interfaces:** consumes Task 4's endpoints. Produces
`api.listListings(params)`, `api.updateListing(id, payload)`,
`api.endListing(id)`, `api.createOffers(payload)`; route `/listings`, nav
entry **Listings** (in the Selling group with Platforms).

The page: a table of active and paused listings (platform, item code, title,
price, cost basis, margin, status, listed date, external link), filters for
platform/format/status, and row actions **Edit** (a modal using
`AccessLabel` + `accel()` + `useSaveShortcut`, sending `version`) and **End**.
A paused row says what paused it ("paused for listing #14 on eBay") and offers
no Edit. Show a refusal in place, keeping the form open.

- [ ] **Step 1: Write the failing tests** -- mock `../api` as
  `Platforms.test.jsx` does. **Assert exact payloads** with
  `toHaveBeenCalledWith`, never `objectContaining`: the mocked API is why a
  wrong payload shipped in phase 1.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Write the page.**
- [ ] **Step 4: Run to verify they pass** (`npx vitest run src/owner`).
- [ ] **Step 5: Gate and commit.**

---

### Task 7: Offering from inventory, and the item editor's Offers panel

**Files:**
- Create: `frontend/src/owner/pages/inventory/OfferDialog.jsx` (+ test), `frontend/src/owner/pages/inventory/OffersPanel.jsx` (+ test)
- Modify: `frontend/src/owner/pages/inventory/BulkEditBar.jsx` (+ test), `frontend/src/owner/pages/inventory/ItemEditForm.jsx` (+ test)

**Interfaces:** consumes Task 4's `POST /api/offers` and
`GET /api/listings?item_id=`.

- **OfferDialog**: platform and format pickers, then one row per selected item
  with price, title, description and the platform's listing number, each row
  showing cost basis and (when the platform has default fees) estimated fees,
  net and margin -- compute with the helpers in
  `frontend/src/owner/pages/platform-rates.js`; add one there if a needed
  calculation is missing rather than inlining arithmetic in the component.
  Items the API refuses are listed with their reason and the dialog stays open.
- **BulkEditBar**: an **Offer for sale...** button beside the bulk field
  editor, enabled when at least one row is selected.
- **OffersPanel** in `ItemEditForm`, beside `SaleHistory`: current and past
  offers with their state, an **Offer** button when the item has none active,
  and **End** on the active one.

- [ ] **Step 1: Write the failing tests** (exact payload assertions, as Task 6).
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Build the components.**
- [ ] **Step 4: Run to verify they pass.**
- [ ] **Step 5: Gate and commit.**

---

### Task 8: Documentation

**Files:** `docs/database-design.md`, `docs/system-administration.md`, `docs/specs/selling-design.md`

- [ ] **Step 1:** `database-design.md`: `offer_claim` (with the partial unique
  index as the "offered once" guarantee) and `listing.paused_by_listing_id`.
- [ ] **Step 2:** `system-administration.md`: how to offer an item, what a
  paused listing means, how to end an offer, and that the Manage page is gone.
- [ ] **Step 3:** `selling-design.md`: status line to
  `Design. Status: **agreed with the owner 2026-09-17**; phases 0-2 (offering) built.`
- [ ] **Step 4:** Describe only what exists. Recording an outside sale, fees,
  shares and platform buyers are the **next** plan -- do not document them as
  built. Verify every claim against the source, as the phase-1 doc task did.
- [ ] **Step 5: Commit.**

---

### Task 9: Apply to live (owner-gated)

Not dispatched to a subagent. The controller stops here and asks the owner.

- [ ] **Step 1:** capture the baseline on live **before** anything:

```cmd
scripts\ccweb_psql.cmd -c "select count(*) listings, count(*) filter (where is_active) active from listing" -c "select count(*) from inventory_item where deleted_at is null"
```
  saved to a file. The owner enters items daily, so re-read it on the day.
- [ ] **Step 2:** `pg_dump -Fc` to `C:\Users\wnmil\dev\ccwebdb-backups\`
  (an `app.backup` copy is built from the current models and carries no
  `alembic_version`, so it is not a faithful pre-migration snapshot).
- [ ] **Step 3:** restore that dump into `ccwebdb_offercheck`, `alembic upgrade
  head` against it, and check: one claim per listing, states matching each
  listing's status, `select count(*) from listing l left join offer_claim c on
  c.listing_id = l.id where c.id is null` = 0. Then `alembic downgrade -1`,
  confirm the listings survive, `alembic upgrade head` again, drop the copy.
- [ ] **Step 4:** stop the servers (`scripts\ccweb_shutdown.cmd --keepdb`),
  `alembic upgrade head`, `python -m app.seeding load`,
  `scripts\ccweb_startup.cmd` (output redirected, never piped -- the servers
  inherit a pipe and it hangs). The backend runs without `--reload`, so the
  restart is what serves the new code.
- [ ] **Step 5:** verify in the console: Listings page loads, an item can be
  offered and the offer ended, and the shop catalogue still shows only what it
  should.

---

## Appendix A: exact signatures

Tasks 2, 4, 6 and 7 must use these names and shapes; they are how the tasks
fit together.

```python
# backend/app/offering_writes.py
class OfferRefused(Exception):
    """One item cannot be offered; the batch it belongs to writes nothing."""

    def __init__(self, item_code: str, reason: str) -> None:
        self.item_code = item_code
        self.reason = reason
        super().__init__(f"{item_code}: {reason}")


def sellable_in_shop(listing: Listing) -> bool: ...
def shop_listing_filters() -> list[ColumnElement[bool]]: ...
def claims_for(db: Session, item_ids: Collection[int]) -> dict[int, list[OfferClaim]]: ...


def offer(
    db: Session,
    *,
    item: InventoryItem,
    venue: SalesVenue,
    listing_format: ListingFormat,
    price: Decimal,
    title: str,
    description: str,
    external_id: str | None,
    quantity: int = 1,
) -> Listing:
    """Offer one item on one platform. Raises OfferRefused; caller commits."""


def end_offer(db: Session, listing: Listing, *, sold: bool = False) -> None:
    """End an offer. `sold=True` ends the listings it paused instead of resuming them."""
```

```python
# backend/app/schemas.py
class OfferItemIn(BaseModel):
    """One item in an offer batch."""

    model_config = ConfigDict(extra="forbid")

    item_id: int
    price: Decimal = Field(ge=0)
    title: str = Field(default="", max_length=500)
    description: str = ""
    external_id: str | None = Field(default=None, max_length=128)


class OfferIn(BaseModel):
    """Offer one or more items on one platform, all or nothing."""

    model_config = ConfigDict(extra="forbid")

    #: A `sales_venue` code.
    venue: str = Field(min_length=1, max_length=64)
    #: `fixed_price` or `auction`.
    format: str = "fixed_price"
    quantity: int = Field(default=1, ge=1)
    items: list[OfferItemIn] = Field(min_length=1)


class OfferRefusalOut(BaseModel):
    """Why one item of a batch could not be offered."""

    item_code: str
    reason: str


class ListingOut(BaseModel):
    """One offer, for the console. Admin-only: it carries cost basis."""

    id: int
    item_id: int
    item_code: str
    item_title: str
    venue: str
    venue_name: str
    format: str
    status: str
    price: Decimal
    currency: str
    quantity_available: int
    title: str
    description: str
    external_id: str | None = None
    #: Built from the platform's `listing_url_template` when the listing has
    #: an external id and no stored URL. Never written back to the row.
    external_url: str | None = None
    listed_at: datetime
    ended_at: datetime | None = None
    paused_by_listing_id: int | None = None
    cost_basis: Decimal | None = None
    version: str


class ListingUpdate(BaseModel):
    """A change to an offer. Omitted fields are left alone."""

    model_config = ConfigDict(extra="forbid")

    price: Decimal | None = Field(default=None, ge=0)
    title: str | None = Field(default=None, max_length=500)
    description: str | None = None
    external_id: str | None = Field(default=None, max_length=128)
    version: str | None = None
```

```python
# backend/app/routers/offers.py
router = APIRouter(tags=["selling"])   # paths carry their own prefixes

@router.post("/offers", status_code=status.HTTP_201_CREATED)
def create_offers(payload: OfferIn, db: DbSession, _admin: AdminUser) -> list[ListingOut]: ...

@router.get("/listings")
def list_listings(
    db: DbSession,
    _admin: AdminUser,
    venue: str | None = None,
    format: str | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    item_id: int | None = None,
) -> list[ListingOut]: ...

@router.patch("/listings/{listing_id}")
def update_listing(
    listing_id: int, payload: ListingUpdate, db: DbSession, _admin: AdminUser
) -> ListingOut: ...

@router.post("/listings/{listing_id}/end")
def end_listing(listing_id: int, db: DbSession, _admin: AdminUser) -> ListingOut: ...
```

A batch refusal is a 409 whose body is
`{"detail": "<n> item(s) cannot be offered", "refused": [OfferRefusalOut, ...]}`.
Raise it with `HTTPException(status_code=409, detail=...)` carrying that dict
as `detail`, matching how the project already returns structured refusals --
grep `detail={` in `backend/app/routers/` and follow the closest example; if
there is none, put the list in `detail` and say so in your report.

```js
// frontend/src/owner/api.js -- add beside the platform calls
  createOffers: (payload) => send('/api/offers', { method: 'POST', body: payload }),
  listListings: (params = {}) => send(`/api/listings?${query(params)}`),
  updateListing: (id, payload) =>
    send(`/api/listings/${id}`, { method: 'PATCH', body: payload }),
  endListing: (id) => send(`/api/listings/${id}/end`, { method: 'POST' }),
```

The offer dialog sends exactly:

```js
{
  venue: 'ebay',
  format: 'fixed_price',
  quantity: 1,
  items: [{ item_id: 12, price: '120.00', title: '1881-S Morgan', description: '', external_id: '1234' }],
}
```

Prices are strings, never numbers: the API takes `Decimal`, and a JavaScript
number cannot carry cents exactly.
