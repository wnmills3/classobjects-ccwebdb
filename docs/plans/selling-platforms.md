# Selling, phases 0 and 1: purchase-source clean-up and platforms -- Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Written 2026-09-17. Phases 2-4 of the spec get their own plans once this has
landed.

**Goal:** Record the platforms the business sells through, tie every listing
and sales order to one, and clean the purchase-source list those platforms
link to.

**Architecture:** A `vendor_cleanup` data pass (dry run by default) merges
duplicate purchase sources and sets their kinds. A new `sales_venue_kind`
vocabulary and `sales_venue` table describe platforms; the migration creates
the one web-store platform and points every existing listing and order at it.
`listing` gains `sales_venue_id`, `format`, `status` and external ids, and
`is_active` becomes a column generated from `status`, so existing readers are
untouched while writers move to `status`. A Platforms page in the console
manages the rest.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 18,
pytest; React + Vite, vitest, Testing Library.

**Spec:** `docs/specs/selling-design.md`

## Global Constraints

- Git: work on a topic branch (`feat/selling-platforms`, cut from `main` after
  `docs/selling-design` is merged); never commit to `main`.
- Scripts and commands: cmd only, never PowerShell. Invoke scripts as
  `.\scripts\x.cmd`. Sleep with `ping -n 2 127.0.0.1 >nul`, never `timeout`.
- Files: create with the Write tool, change with Edit; no shell heredocs; never
  `sed` a Windows path. A script that rewrites a file uses `newline=""`.
- Gate: `.\scripts\ccweb_check.cmd` runs ruff, mypy, pytest, eslint, prettier,
  vitest and the bundle-isolation check; every one is at zero. Run it as its
  own command, output redirected to a file; never pipe it; commit in a
  separate call. Only one pytest run at a time (`ccwebdb_test` is shared).
- pytest: `addopts` already has `-q`; do not add another. Judge by exit code.
- Every public class, method and function has a docstring; every function is
  annotated (ruff `D`, `ANN`).
- Classifiers cross the API as codes, never ids; unknown code -> 422.
- Money and rates are `Decimal`, never float.
- Console API calls go in `frontend/src/owner/api.js`, never
  `frontend/src/shared/api.js`.
- Enum types created by a migration are dropped by its downgrade
  (`DROP TYPE IF EXISTS`); every constraint and foreign key is named.
- Any `create_views(...)` call added to a migration gets a row in
  `HISTORICAL_VIEW_CALLS` (`backend/tests/test_migrations.py`).
- Live database writes only on the owner's word: backup first, dry-run counts
  shown first, a scratch copy before live.
- **No fee figures are seeded** for any platform (spec, "sales_venue").

Run backend tests from the repository root with the ccwebdb environment:

```cmd
.\scripts\ccweb_env.cmd && cd backend && python -m pytest tests\test_x.py
```

(Below, `pytest tests\...` means that, from `backend`.) Frontend tests:
`cd frontend && npx vitest run src\owner\pages\Platforms.test.jsx`.

---

## File map

| File | Responsibility |
|---|---|
| `backend/app/vendor_cleanup.py` (new) | Phase 0 pass: merge purchase sources, set kinds; dry run by default |
| `backend/tests/test_vendor_cleanup.py` (new) | its tests |
| `backend/data/reference/operations.json` | + `sales_venue_kind` vocabulary |
| `backend/app/models/reference.py` | + `SalesVenueKind` |
| `backend/app/models/sales.py` | + `SalesVenue`, `ListingFormat`, `ListingStatus`; `Listing` and `SalesOrder` columns |
| `backend/app/models/__init__.py` | exports; `SalesVenueKind` in `REFERENCE_MODELS` |
| `backend/app/models/views.py` | `public_catalog` shows store fixed-price listings only; `selling` flag for historical variants |
| `backend/app/sales_venues.py` (new) | `store_venue_id`, `ensure_store_venue` |
| `backend/alembic/versions/d6a1f3b8c402_sales_venues.py` (new) | the migration |
| `backend/alembic/versions/{b78d71343405,c847d0c63f84,e4b8c1d27f63,fdf22151d022,ffe36996607c}_*.py` | pass `selling=False` to their `create_views` calls |
| `backend/app/routers/catalog.py`, `backend/app/splitting.py`, `backend/app/seed.py`, `backend/app/order_writes.py` | write `status` instead of `is_active`; set `sales_venue_id` |
| `backend/app/routers/reference.py` | `sales_venue_kind` is code-keyed |
| `backend/app/schemas.py` | `SalesVenueOut`, `SalesVenueCreate`, `SalesVenueUpdate` |
| `backend/app/routers/sales_venues.py` (new), `backend/app/main.py` | the API |
| `backend/tests/conftest.py` and listing-building tests | store platform in the test database; `status` instead of `is_active` |
| `backend/tests/test_sales_venues.py` (new) | model, migration-shape and API tests |
| `frontend/src/owner/api.js` | platform calls |
| `frontend/src/owner/pages/Platforms.jsx` (+ `.test.jsx`) (new) | the Platforms page |
| `frontend/src/owner/OwnerApp.jsx` | route and nav link |
| `docs/database-design.md`, `docs/system-administration.md` | documentation |

---

### Task 1: Purchase-source clean-up pass

**Files:**
- Create: `backend/app/vendor_cleanup.py`
- Test: `backend/tests/test_vendor_cleanup.py`

**Interfaces:**
- Produces: `run(db: Session, merges: list[tuple[int, int]], kinds: list[tuple[int, str]], deletes: list[int], commit: bool) -> Report`;
  CLI `python -m app.vendor_cleanup --merge FROM:INTO ... --kind ID:CODE ... --delete ID ... [--commit]`.

Rules the pass enforces (spec, phase 0):
- A merge moves every `purchase_order` of FROM to INTO, then deletes FROM.
  Refused if FROM and INTO both hold an order with the same non-null
  `order_number` (`uq_purchase_order_vendor_number` would fail), naming them.
- `--delete` removes only a vendor with **no** purchase orders.
- `--kind` sets `vendor_kind` by code; an unknown code is an error.
- Nothing is written without `--commit`; the report is printed either way.
- Vendor rows carry no history of their own (the orders do), which is why a
  merged vendor may be deleted.

- [ ] **Step 1: Write the failing tests**

```python
"""The purchase-source clean-up pass (selling design, phase 0)."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PurchaseOrder, Vendor, VendorKind
from app.vendor_cleanup import CleanupError, run

# Setup is committed, not flushed. Under the `db` fixture a commit releases a
# savepoint, so the rollback `run` does on a dry run or a refusal discards
# only what `run` did -- not the vendors the test created.


def _vendor(db: Session, name: str) -> Vendor:
    vendor = Vendor(name=name)
    db.add(vendor)
    db.commit()
    return vendor


def _order(db: Session, vendor: Vendor, number: str | None) -> PurchaseOrder:
    order = PurchaseOrder(vendor_id=vendor.id, order_number=number)
    db.add(order)
    db.commit()
    return order


def test_a_dry_run_reports_and_writes_nothing(db: Session) -> None:
    typo = _vendor(db, "builionsharks.com")
    real = _vendor(db, "bullionshark.com")
    order = _order(db, typo, "A1")

    report = run(db, merges=[(typo.id, real.id)], kinds=[], deletes=[], commit=False)

    assert report.merged == [("builionsharks.com", "bullionshark.com", 1)]
    db.expire_all()
    assert db.get(PurchaseOrder, order.id).vendor_id == typo.id
    assert db.get(Vendor, typo.id) is not None


def test_a_merge_moves_orders_and_removes_the_duplicate(db: Session) -> None:
    typo = _vendor(db, "hibid.co")
    real = _vendor(db, "hibid.com")
    moved = _order(db, typo, None)
    kept = _order(db, real, "X9")

    run(db, merges=[(typo.id, real.id)], kinds=[], deletes=[], commit=True)

    db.expire_all()
    assert db.get(PurchaseOrder, moved.id).vendor_id == real.id
    assert db.get(PurchaseOrder, kept.id).vendor_id == real.id
    assert db.get(Vendor, typo.id) is None


def test_a_merge_that_would_duplicate_an_order_number_is_refused(db: Session) -> None:
    left = _vendor(db, "a.example")
    right = _vendor(db, "b.example")
    _order(db, left, "SAME")
    _order(db, right, "SAME")

    with pytest.raises(CleanupError, match="SAME"):
        run(db, merges=[(left.id, right.id)], kinds=[], deletes=[], commit=True)


def test_delete_refuses_a_vendor_with_orders(db: Session) -> None:
    used = _vendor(db, "used.example")
    _order(db, used, "1")

    with pytest.raises(CleanupError, match="used.example"):
        run(db, merges=[], kinds=[], deletes=[used.id], commit=True)


def test_delete_removes_an_unused_vendor(db: Session) -> None:
    typo = _vendor(db, "usming.gov")

    report = run(db, merges=[], kinds=[], deletes=[typo.id], commit=True)

    assert report.deleted == ["usming.gov"]
    db.expire_all()
    assert db.get(Vendor, typo.id) is None


def test_kind_is_set_by_code(db: Session) -> None:
    vendor = _vendor(db, "whatnot.example")

    run(db, merges=[], kinds=[(vendor.id, "marketplace")], deletes=[], commit=True)

    db.expire_all()
    marketplace = db.scalar(select(VendorKind).where(VendorKind.code == "marketplace"))
    assert db.get(Vendor, vendor.id).vendor_kind_id == marketplace.id


def test_an_unknown_kind_is_an_error(db: Session) -> None:
    vendor = _vendor(db, "x.example")

    with pytest.raises(CleanupError, match="nonsense"):
        run(db, merges=[], kinds=[(vendor.id, "nonsense")], deletes=[], commit=True)


def test_an_unknown_vendor_id_is_an_error(db: Session) -> None:
    with pytest.raises(CleanupError, match="999999"):
        run(db, merges=[], kinds=[(999999, "dealer")], deletes=[], commit=False)


def test_refusal_writes_nothing(db: Session) -> None:
    left = _vendor(db, "c.example")
    right = _vendor(db, "d.example")
    _order(db, left, "SAME")
    _order(db, right, "SAME")
    other = _vendor(db, "e.example")

    with pytest.raises(CleanupError):
        run(
            db,
            merges=[(left.id, right.id)],
            kinds=[(other.id, "dealer")],
            deletes=[],
            commit=True,
        )

    db.expire_all()
    assert db.get(Vendor, other.id).vendor_kind_id is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `pytest tests\test_vendor_cleanup.py`
Expected: FAIL at import -- `No module named 'app.vendor_cleanup'`.

- [ ] **Step 3: Write the pass**

```python
"""Tidy the purchase-source list before sales platforms link to it.

The importer made one vendor per spelling it met, so the list holds typos
(`builionsharks.com`, `usming.gov`), two HiBid hosts, a vendor named `.`, and
no kind on anything. Sales platforms link to a vendor (selling design), so the
list is cleaned first.

Every change is named on the command line -- which vendors are the same
business is the owner's call, not something to guess -- and nothing is
written without --commit:

    python -m app.vendor_cleanup --merge 15:12 --merge 21:9 --delete 6
        --kind 1:marketplace --kind 19:marketplace [--commit]

A merge moves the purchase orders and removes the duplicate. Vendor rows hold
no history of their own; the orders carry it, and they are kept.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import PurchaseOrder, Vendor, VendorKind


class CleanupError(Exception):
    """A requested change that cannot be made; nothing was written."""


@dataclass
class Report:
    """What the pass did, or would do."""

    #: (from name, into name, orders moved)
    merged: list[tuple[str, str, int]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    #: (vendor name, kind code)
    kinds: list[tuple[str, str]] = field(default_factory=list)


def _vendor(db: Session, vendor_id: int) -> Vendor:
    vendor = db.get(Vendor, vendor_id)
    if vendor is None:
        raise CleanupError(f"No vendor with id {vendor_id}")
    return vendor


def _order_count(db: Session, vendor_id: int) -> int:
    return db.scalar(
        select(func.count()).where(PurchaseOrder.vendor_id == vendor_id)
    ) or 0


def _clashing_numbers(db: Session, left: int, right: int) -> list[str]:
    """Order numbers both vendors use, which a merge would duplicate."""
    left_numbers = select(PurchaseOrder.order_number).where(
        PurchaseOrder.vendor_id == left, PurchaseOrder.order_number.is_not(None)
    )
    return sorted(
        db.scalars(
            select(PurchaseOrder.order_number).where(
                PurchaseOrder.vendor_id == right,
                PurchaseOrder.order_number.in_(left_numbers),
            )
        ).all()
    )


def run(
    db: Session,
    merges: Sequence[tuple[int, int]],
    kinds: Sequence[tuple[int, str]],
    deletes: Sequence[int],
    commit: bool,
) -> Report:
    """Apply the named merges, deletions and kinds; roll back unless `commit`."""
    report = Report()
    try:
        for source_id, target_id in merges:
            source, target = _vendor(db, source_id), _vendor(db, target_id)
            if source_id == target_id:
                raise CleanupError(f"Cannot merge {source.name} into itself")
            clashes = _clashing_numbers(db, source_id, target_id)
            if clashes:
                raise CleanupError(
                    f"{source.name} and {target.name} both have order number(s) "
                    f"{', '.join(clashes)}; resolve those first"
                )
            moved = _order_count(db, source_id)
            db.execute(
                update(PurchaseOrder)
                .where(PurchaseOrder.vendor_id == source_id)
                .values(vendor_id=target_id)
            )
            report.merged.append((source.name, target.name, moved))
            db.delete(source)

        for vendor_id in deletes:
            vendor = _vendor(db, vendor_id)
            if _order_count(db, vendor_id):
                raise CleanupError(
                    f"{vendor.name} still has purchase orders; merge it instead"
                )
            report.deleted.append(vendor.name)
            db.delete(vendor)

        for vendor_id, code in kinds:
            vendor = _vendor(db, vendor_id)
            kind_id = db.scalar(select(VendorKind.id).where(VendorKind.code == code))
            if kind_id is None:
                raise CleanupError(f"Unknown vendor_kind {code!r}")
            vendor.vendor_kind_id = kind_id
            report.kinds.append((vendor.name, code))

        db.flush()
    except CleanupError:
        db.rollback()
        raise
    if commit:
        db.commit()
    else:
        db.rollback()
    return report


def _pair(text: str) -> tuple[int, int]:
    left, _, right = text.partition(":")
    return int(left), int(right)


def _kind(text: str) -> tuple[int, str]:
    left, _, right = text.partition(":")
    return int(left), right


def main(argv: list[str] | None = None) -> int:
    """Report or apply the clean-up."""
    parser = argparse.ArgumentParser(prog="vendor_cleanup", description=__doc__)
    parser.add_argument("--merge", type=_pair, action="append", default=[])
    parser.add_argument("--kind", type=_kind, action="append", default=[])
    parser.add_argument("--delete", type=int, action="append", default=[])
    parser.add_argument("--commit", action="store_true", help="write the changes")
    args = parser.parse_args(argv)
    with SessionLocal() as db:
        try:
            report = run(db, args.merge, args.kind, args.delete, commit=args.commit)
        except CleanupError as exc:
            print(f"REFUSED: {exc}")
            return 1
    for source, target, moved in report.merged:
        print(f"merge  {source} -> {target}  ({moved} orders)")
    for name in report.deleted:
        print(f"delete {name}")
    for name, code in report.kinds:
        print(f"kind   {name} = {code}")
    if not args.commit:
        print("\n(dry run -- nothing written; pass --commit)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

If a dry-run or refusal test finds its own vendors gone, the rollback reached
past the test's setup: confirm `_vendor`/`_order` commit (see the comment in
the test file) rather than changing `run`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests\test_vendor_cleanup.py`
Expected: PASS, 9 tests.

- [ ] **Step 5: Mutation check**

Comment out the `if clashes:` block, run
`pytest tests\test_vendor_cleanup.py -k duplicate`, confirm it FAILS (an
`IntegrityError` rather than `CleanupError`), restore the block, confirm it
passes.

- [ ] **Step 6: Gate and commit**

```cmd
.\scripts\ccweb_check.cmd > %TEMP%\ccweb_gate.txt 2>&1
```
Read the file; exit code must be 0. Then:
```cmd
git add backend/app/vendor_cleanup.py backend/tests/test_vendor_cleanup.py
git commit -m "Add a purchase-source clean-up pass: merge, delete unused, set kinds"
```

---

### Task 2: Apply the clean-up to live (owner-gated, no code)

**Files:** none.

- [ ] **Step 1: Show the owner the proposed changes and get decisions**

Live ids (read 2026-09-17; re-read first with
`.\scripts\ccweb_psql.cmd -c "select v.id, v.name, count(p.id) from vendor v left join purchase_order p on p.vendor_id = v.id group by 1, 2 order by 2"`):

| Proposal | Needs the owner to confirm |
|---|---|
| merge 15 `builionsharks.com` -> 12 `bullionshark.com` | same business? |
| delete 6 `usming.gov` (0 orders) | typo of usmint.gov |
| merge 21 `hibid.co` -> 9 `hibid.com` | same platform? |
| vendor 20 `.` (1 order) | which vendor is it really? |
| kinds: ebay, whatnot, etsy -> `marketplace`; hibid hosts, liveauctioneers, proxibid, auctionzip -> `auction`; usmint, govmint -> `mint`; sdbullion, bullionshark, pinehurstcoins, coinadvisor, ampex -> `dealer` | each one |

Do not proceed without the owner's answers.

- [ ] **Step 2: Dry run on live**

```cmd
.\scripts\ccweb_env.cmd && cd backend && python -m app.vendor_cleanup <the agreed --merge/--delete/--kind flags>
```
Show the output to the owner.

- [ ] **Step 3: Backup, then commit on the owner's word**

```cmd
.\scripts\ccweb_env.cmd && cd backend && python -m app.backup --help
```
Take the backup the way `docs/system-administration.md` describes (a
`ccwebdb_bak_<yyyymmdd_hhmm>` copy), then re-run step 2's command with
`--commit`. Verify with the psql query from step 1 that the counts moved as
reported.

---

### Task 3: The `sales_venue_kind` vocabulary

**Files:**
- Modify: `backend/data/reference/operations.json` (after `vendor_kind`)
- Modify: `backend/app/models/reference.py` (after `VendorKind`)
- Modify: `backend/app/models/__init__.py` (import, `REFERENCE_MODELS` after `VendorKind`, `__all__`)
- Modify: `backend/app/routers/reference.py` (`_CODE_KEYED_TABLES`)
- Test: `backend/tests/test_sales_venues.py` (new)

**Interfaces:**
- Produces: `SalesVenueKind` (ReferenceMixin table `sales_venue_kind`), codes
  `own_store`, `marketplace`, `live_auction`, `auction_house`.

- [ ] **Step 1: Write the failing test**

```python
"""Sales platforms (selling design, phase 1)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SalesVenueKind
from app.routers.reference import retirable


def test_the_platform_kinds_are_seeded(db: Session) -> None:
    codes = set(db.scalars(select(SalesVenueKind.code)))
    assert codes == {"own_store", "marketplace", "live_auction", "auction_house"}


def test_platform_kinds_cannot_be_retired() -> None:
    # The store is found by kind, and the API branches on it.
    assert not retirable("sales_venue_kind", "marketplace")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests\test_sales_venues.py`
Expected: FAIL -- `ImportError: cannot import name 'SalesVenueKind'`.

- [ ] **Step 3: Add the model**

In `backend/app/models/reference.py`, after `class VendorKind`:

```python
class SalesVenueKind(ReferenceMixin, Base):
    """How a sales platform sells: own store, marketplace, live show, auction house."""

    __tablename__ = "sales_venue_kind"
```

In `backend/app/models/__init__.py`: add `SalesVenueKind` to the import from
`.reference`, to `REFERENCE_MODELS` directly after `VendorKind`, and to
`__all__` in alphabetical position.

- [ ] **Step 4: Seed it**

In `backend/data/reference/operations.json`, after the `vendor_kind` array:

```json
  "sales_venue_kind": [
    {"code": "own_store", "label": "Our web store", "sort_order": 10,
     "_note": "Exactly one platform has this kind; the migration creates it."},
    {"code": "marketplace", "label": "Marketplace", "sort_order": 20,
     "_note": "Fixed-price listings on a platform: eBay, Whatnot."},
    {"code": "live_auction", "label": "Live auction show", "sort_order": 30,
     "_note": "Run directly by the owner: eBay Live, Whatnot shows."},
    {"code": "auction_house", "label": "Auction house (agent)", "sort_order": 40,
     "_note": "Sells on the owner's behalf as consignee: Heritage, HiBid auctioneers."}
  ],
```

Check the `_comment` at the top of the file still describes the file; add
`"Sales platform kinds describe how a platform sells, not which one it is."`
as a further line.

- [ ] **Step 5: Mark it code-keyed**

In `backend/app/routers/reference.py`, add `"sales_venue_kind",` to
`_CODE_KEYED_TABLES`.

- [ ] **Step 6: Run to verify it passes**

Run: `pytest tests\test_sales_venues.py`
Expected: PASS, 2 tests. (`test_migrations_match_models` will fail until
Task 4's migration exists, so this task has no commit of its own: its changes
are committed with Task 4.)

---

### Task 4: Platforms, listing status and the migration

This task is one commit: the models, every caller, the view and the migration
must change together or the suite cannot pass.

**Files:**
- Modify: `backend/app/models/sales.py`
- Modify: `backend/app/models/__init__.py`
- Create: `backend/app/sales_venues.py`
- Modify: `backend/app/models/views.py`
- Create: `backend/alembic/versions/d6a1f3b8c402_sales_venues.py`
- Modify: the five older migrations listed in the file map
- Modify: `backend/app/routers/catalog.py`, `backend/app/splitting.py`, `backend/app/seed.py`, `backend/app/order_writes.py`
- Modify: `backend/tests/conftest.py`, `backend/tests/test_schema.py`, `backend/tests/test_concurrency.py`, `backend/tests/test_order_revision_race.py`, `backend/tests/test_order_writes.py`, `backend/tests/test_orders.py`, `backend/tests/test_migrations.py`
- Test: `backend/tests/test_sales_venues.py`

**Interfaces:**
- Consumes: `SalesVenueKind` (Task 3).
- Produces:
  - `SalesVenue` model (fields in step 3), `ListingFormat` (`fixed_price`, `auction`), `ListingStatus` (`active`, `paused`, `ended`), all exported from `app.models`.
  - `Listing.sales_venue_id: int`, `Listing.format: ListingFormat`, `Listing.status: ListingStatus`, `Listing.is_active: bool` (generated, read-only), `Listing.external_id: str | None`, `Listing.external_url: str | None`, `Listing.sales_venue: SalesVenue`.
  - `SalesOrder.sales_venue_id: int`, `SalesOrder.external_order_id: str | None`.
  - `app.sales_venues.STORE_CODE = "store"`, `store_venue_id(db: Session) -> int`, `ensure_store_venue(db: Session) -> int`.
  - `create_views(..., selling: bool = True)`.

- [ ] **Step 1: Write the failing tests**

Add these imports to the import block at the top of
`backend/tests/test_sales_venues.py` (ruff `I` sorts them; never leave imports
mid-file):

```python
from collections.abc import Callable

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.models import Listing, ListingFormat, ListingStatus, SalesVenue, Vendor
from app.sales_venues import STORE_CODE, ensure_store_venue, store_venue_id
```

and append these tests:

```python

def _kind_id(db: Session, code: str) -> int:
    return db.scalar(select(SalesVenueKind.id).where(SalesVenueKind.code == code))


def test_the_test_database_has_exactly_one_store(db: Session) -> None:
    stores = db.scalars(select(SalesVenue).where(SalesVenue.is_own_store)).all()
    assert [s.code for s in stores] == [STORE_CODE]
    assert store_venue_id(db) == stores[0].id


def test_ensure_store_venue_is_idempotent(db: Session) -> None:
    assert ensure_store_venue(db) == store_venue_id(db)


def test_a_second_store_is_refused_by_the_database(db: Session) -> None:
    db.add(
        SalesVenue(
            code="store2",
            name="Another store",
            sales_venue_kind_id=_kind_id(db, "own_store"),
            is_own_store=True,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()


def test_a_purchase_source_links_to_at_most_one_platform(db: Session) -> None:
    vendor = Vendor(name="linked.example")
    db.add(vendor)
    db.flush()
    kind = _kind_id(db, "marketplace")
    db.add(SalesVenue(code="a", name="A", sales_venue_kind_id=kind, vendor_id=vendor.id))
    db.add(SalesVenue(code="b", name="B", sales_venue_kind_id=kind, vendor_id=vendor.id))
    with pytest.raises(IntegrityError):
        db.flush()


def test_is_active_follows_status(db: Session, listing: Listing) -> None:
    assert listing.status is ListingStatus.active
    assert listing.is_active is True

    listing.status = ListingStatus.paused
    db.commit()
    db.refresh(listing)
    assert listing.is_active is False


def test_a_new_listing_is_active_fixed_price(db: Session, listing: Listing) -> None:
    assert listing.format is ListingFormat.fixed_price
    assert listing.sales_venue_id == store_venue_id(db)


def test_public_catalog_shows_only_store_fixed_price_listings(
    db: Session, make_listing: Callable[..., Listing]
) -> None:
    store_listing = make_listing()
    ebay = SalesVenue(
        code="ebay", name="eBay", sales_venue_kind_id=_kind_id(db, "marketplace")
    )
    db.add(ebay)
    db.flush()
    elsewhere = make_listing(sales_venue_id=ebay.id)
    auction = make_listing(format=ListingFormat.auction)

    shown = set(db.scalars(text("select listing_id from public_catalog")))
    assert store_listing.id in shown
    assert elsewhere.id not in shown
    assert auction.id not in shown
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests\test_sales_venues.py`
Expected: FAIL at collection -- `ImportError: cannot import name 'ListingFormat'
from 'app.models'`.

- [ ] **Step 3: Models**

In `backend/app/models/sales.py`:

Add `Computed` to the `sqlalchemy` import list. In the `TYPE_CHECKING` block
add `from .core import Vendor` (next to `InventoryItem`) and
`from .reference import SalesVenueKind` (next to `Currency`). Add to
`__all__`: `"ListingFormat"`, `"ListingStatus"`, `"SalesVenue"`.

After `SalesOrderChangeKind`, add:

```python
class ListingFormat(enum.StrEnum):
    """How a listing sells: at a fixed price, or to the highest bidder."""

    fixed_price = "fixed_price"
    auction = "auction"


class ListingStatus(enum.StrEnum):
    """Whether a listing is on offer now.

    `paused` is a store listing set aside while its item is offered
    elsewhere; it resumes when that offer ends unsold (selling design).
    """

    active = "active"
    paused = "paused"
    ended = "ended"


class SalesVenue(TimestampMixin, Base):
    """A platform the business sells through: the web store, eBay, an auction house.

    The owner's own accounts, not shipped reference data -- another
    installation sells elsewhere. `vendor_id` links the platform to the
    purchase source of the same name, so eBay is one partner whether buying
    or selling. The default fees are for estimates only; a sale records what
    was actually charged.
    """

    __tablename__ = "sales_venue"

    #: Optimistic concurrency -- see the note on InventoryItem.version.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        """Optimistic concurrency: every UPDATE checks the version it read."""
        return {"version_id_col": cls.version}

    id: Mapped[int] = mapped_column(primary_key=True)
    #: Machine-facing and immutable, like a reference code.
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    sales_venue_kind_id: Mapped[int] = mapped_column(
        ForeignKey("sales_venue_kind.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    #: True on exactly one row, the web store; a partial unique index says so.
    is_own_store: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    vendor_id: Mapped[int | None] = mapped_column(
        ForeignKey("vendor.id", ondelete="RESTRICT"), nullable=True
    )
    #: The owner's username or seller id on the platform.
    account_handle: Mapped[str | None] = mapped_column(String(255), nullable=True)
    #: e.g. https://www.ebay.com/itm/{external_id}
    listing_url_template: Mapped[str | None] = mapped_column(
        String(500), nullable=True
    )
    #: Fractions: 0.1325 is 13.25%.
    commission_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )
    processing_rate: Mapped[Decimal | None] = mapped_column(
        Numeric(6, 4), nullable=True
    )
    processing_fixed: Mapped[Decimal | None] = mapped_column(
        Numeric(12, 2), nullable=True
    )
    listing_fee: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    #: When the default fees were read from the platform.
    terms_as_of: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    kind: Mapped[SalesVenueKind] = relationship()
    vendor: Mapped[Vendor | None] = relationship()

    __table_args__ = (
        UniqueConstraint("code", name="uq_sales_venue_code"),
        UniqueConstraint("vendor_id", name="uq_sales_venue_vendor"),
        Index(
            "uq_sales_venue_own_store",
            "is_own_store",
            unique=True,
            postgresql_where=text("is_own_store"),
        ),
        CheckConstraint(
            "commission_rate >= 0 AND commission_rate <= 1",
            name="ck_sales_venue_commission_rate",
        ),
        CheckConstraint(
            "processing_rate >= 0 AND processing_rate <= 1",
            name="ck_sales_venue_processing_rate",
        ),
        CheckConstraint(
            "processing_fixed >= 0", name="ck_sales_venue_processing_fixed"
        ),
        CheckConstraint("listing_fee >= 0", name="ck_sales_venue_listing_fee"),
    )
```

In `class Listing`, replace the `is_active` column with:

```python
    sales_venue_id: Mapped[int] = mapped_column(
        ForeignKey("sales_venue.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    format: Mapped[ListingFormat] = mapped_column(
        enum_column(ListingFormat, "listing_format"),
        default=ListingFormat.fixed_price,
        nullable=False,
    )
    #: Written only through `status`; see ListingStatus.
    status: Mapped[ListingStatus] = mapped_column(
        enum_column(ListingStatus, "listing_status"),
        default=ListingStatus.active,
        nullable=False,
    )
    #: Generated from `status`, so every reader written before statuses
    #: existed -- checkout, the public catalogue, the for-sale warning -- keeps
    #: its meaning. It cannot be written; set `status`.
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        Computed("status = 'active'::listing_status", persisted=True),
        nullable=False,
    )
    #: The platform's own listing number, and its page.
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    external_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
```

and add, beside the other relationships:

```python
    sales_venue: Mapped[SalesVenue] = relationship()
```

In `class SalesOrder`, after `customer_id`, add:

```python
    sales_venue_id: Mapped[int] = mapped_column(
        ForeignKey("sales_venue.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    #: The platform's order number, for a sale made elsewhere.
    external_order_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
```

Import `Vendor` at runtime is not needed: the relationships use the
`TYPE_CHECKING` names only in annotations (the module has
`from __future__ import annotations`).

In `backend/app/models/__init__.py`, import and export `ListingFormat`,
`ListingStatus` and `SalesVenue` from `.sales` (both the import list and
`__all__`, alphabetical).

- [ ] **Step 4: The store helper**

Create `backend/app/sales_venues.py`:

```python
"""The platforms items are sold through, and the web store among them.

The web store is a platform like any other so a listing always names where it
is offered. Exactly one exists; the migration creates it on a real database,
and `ensure_store_venue` creates it where the schema is built from the models
instead (the test database).
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import SalesVenue, SalesVenueKind

__all__ = ["STORE_CODE", "ensure_store_venue", "store_venue_id"]

#: The web store platform's code.
STORE_CODE = "store"


def store_venue_id(db: Session) -> int:
    """The web store platform's id; it must exist."""
    found = db.scalar(select(SalesVenue.id).where(SalesVenue.is_own_store.is_(True)))
    if found is None:
        raise RuntimeError(
            "No web store platform: run `alembic upgrade head` on this database"
        )
    return found


def ensure_store_venue(db: Session) -> int:
    """Create the web store platform if it is missing, and return its id."""
    found = db.scalar(select(SalesVenue.id).where(SalesVenue.is_own_store.is_(True)))
    if found is not None:
        return found
    kind_id = db.scalar(
        select(SalesVenueKind.id).where(SalesVenueKind.code == "own_store")
    )
    if kind_id is None:
        raise RuntimeError(
            "sales_venue_kind 'own_store' is not seeded: run `python -m app.seeding load`"
        )
    venue = SalesVenue(
        code=STORE_CODE, name="Web store", sales_venue_kind_id=kind_id, is_own_store=True
    )
    db.add(venue)
    db.flush()
    return venue.id
```

- [ ] **Step 5: The public catalogue view**

In `backend/app/models/views.py`, in `_PUBLIC_CATALOG`, add the join directly
after `JOIN currency cur         ON cur.id = l.currency_id`:

```
JOIN sales_venue sv       ON sv.id = l.sales_venue_id
```

and change the start of its `WHERE` to:

```
WHERE l.is_active
  AND sv.is_own_store
  AND l.format = 'fixed_price'
  AND l.quantity_available > 0
```

After `_STRIKE_TYPE_FRAGMENTS`, add:

```python
#: Sales platforms and listing formats came later (selling design): a view
#: created by an earlier revision shows every active listing.
_SELLING_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("JOIN sales_venue sv       ON sv.id = l.sales_venue_id\n", ""),
    ("\n  AND sv.is_own_store\n  AND l.format = 'fixed_price'", ""),
)
```

Add a `selling: bool = True` keyword to `_removal_fragments`,
`_assert_stripped` and `create_views` (keyword-only, after `strike_type`), and
pass it through from `create_views` to both. In `_removal_fragments`, first
line of the body after `removals = []`:

```python
    if not selling:
        removals.extend(_SELLING_FRAGMENTS)
```

In `_assert_stripped`:

```python
    if not selling:
        assert "sales_venue" not in statement, (
            "the selling-stripping fragments no longer match the view SQL"
        )
        assert "l.format" not in statement
```

Extend `create_views`' docstring: "``selling`` covers the sales platform join
and the store/fixed-price filter on `public_catalog`."

Pass `selling=False` in `CREATE_VIEWS_WITHOUT_LINEAGE` and
`CREATE_VIEWS_ORIGINAL`.

- [ ] **Step 6: Older migrations keep their views as they were**

Add `selling=False` to every `create_views(...)` call in these files (read each
file first; each call is shown with its line as of 2026-09-17):

- `b78d71343405_...py:95` `create_views(soft_delete=False, strike_type=False)`
- `b78d71343405_...py:129` `create_views(renamed_costs=False, soft_delete=False, strike_type=False)`
- `c847d0c63f84_...py:204` `create_views(strike_type=False)[2:]`
- `c847d0c63f84_...py:235` `create_views(strike_type=False)`
- `e4b8c1d27f63_...py:275` `create_views()` -> `create_views(selling=False)`
- `e4b8c1d27f63_...py:345` `create_views(strike_type=False)`
- `fdf22151d022_...py:50` `create_views(renamed_costs=False, soft_delete=False, strike_type=False)`
- `ffe36996607c_...py:36` `create_views(strike_type=False)`
- `ffe36996607c_...py:49` `create_views(soft_delete=False, strike_type=False)`

Keep line length within ruff's limit; wrap as `ruff format` does.

In `backend/tests/test_migrations.py`, `HISTORICAL_VIEW_CALLS`: append
`"sales_venue"` to every existing row's forbidden tuple, and add:

```python
    (
        "e4b8c1d27f63_strike_type_and_number_grades",
        "upgrade",
        ("sales_venue",),
    ),
    (
        "d6a1f3b8c402_sales_venues",
        "downgrade",
        ("sales_venue", "l.format"),
    ),
```

- [ ] **Step 7: The migration**

Create `backend/alembic/versions/d6a1f3b8c402_sales_venues.py`:

```python
"""Sales platforms; every listing and order names one.

Adds the sales_venue_kind vocabulary and the sales_venue table, creates the
web store platform, and points every existing listing and sales order at it.
Listings gain a format, a status and the platform's own ids; `is_active`
becomes a column generated from `status`, so its readers are unchanged. The
public catalogue shows only the store's fixed-price listings.

Revision ID: d6a1f3b8c402
Revises: c2d7a9e5f614
Create Date: 2026-09-17 13:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models.views import DROP_VIEWS, create_views

revision: str = "d6a1f3b8c402"
down_revision: str | None = "c2d7a9e5f614"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE = postgresql.ENUM(
    "seeded", "derived", "manual", name="provenance_source", create_type=False
)
_FORMAT = postgresql.ENUM("fixed_price", "auction", name="listing_format")
_STATUS = postgresql.ENUM("active", "paused", "ended", name="listing_status")

#: As in operations.json; the seed loader later upserts the same codes.
_KINDS = (
    ("own_store", "Our web store", 10),
    ("marketplace", "Marketplace", 20),
    ("live_auction", "Live auction show", 30),
    ("auction_house", "Auction house (agent)", 40),
)


def upgrade() -> None:
    """Create platforms, the store, and the listing and order columns."""
    bind = op.get_bind()
    _FORMAT.create(bind)
    _STATUS.create(bind)

    op.create_table(
        "sales_venue_kind",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("source", _SOURCE, nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_sales_venue_kind_code"),
    )
    for code, label, order in _KINDS:
        bind.execute(
            sa.text(
                "INSERT INTO sales_venue_kind "
                "(code, label, sort_order, is_active, source) "
                "VALUES (:c, :l, :o, true, 'seeded')"
            ),
            {"c": code, "l": label, "o": order},
        )

    op.create_table(
        "sales_venue",
        sa.Column("version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("sales_venue_kind_id", sa.Integer(), nullable=False),
        sa.Column(
            "is_own_store", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("vendor_id", sa.Integer(), nullable=True),
        sa.Column("account_handle", sa.String(length=255), nullable=True),
        sa.Column("listing_url_template", sa.String(length=500), nullable=True),
        sa.Column("commission_rate", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("processing_rate", sa.Numeric(precision=6, scale=4), nullable=True),
        sa.Column("processing_fixed", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("listing_fee", sa.Numeric(precision=12, scale=2), nullable=True),
        sa.Column("terms_as_of", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "commission_rate >= 0 AND commission_rate <= 1",
            name="ck_sales_venue_commission_rate",
        ),
        sa.CheckConstraint(
            "processing_rate >= 0 AND processing_rate <= 1",
            name="ck_sales_venue_processing_rate",
        ),
        sa.CheckConstraint(
            "processing_fixed >= 0", name="ck_sales_venue_processing_fixed"
        ),
        sa.CheckConstraint("listing_fee >= 0", name="ck_sales_venue_listing_fee"),
        sa.ForeignKeyConstraint(
            ["sales_venue_kind_id"],
            ["sales_venue_kind.id"],
            name="fk_sales_venue_sales_venue_kind_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["vendor_id"],
            ["vendor.id"],
            name="fk_sales_venue_vendor_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_sales_venue_code"),
        sa.UniqueConstraint("vendor_id", name="uq_sales_venue_vendor"),
    )
    op.create_index(
        "ix_sales_venue_sales_venue_kind_id", "sales_venue", ["sales_venue_kind_id"]
    )
    op.create_index(
        "uq_sales_venue_own_store",
        "sales_venue",
        ["is_own_store"],
        unique=True,
        postgresql_where=sa.text("is_own_store"),
    )
    store_id = bind.execute(
        sa.text(
            "INSERT INTO sales_venue "
            "(code, name, sales_venue_kind_id, is_own_store, created_at, updated_at) "
            "SELECT 'store', 'Web store', id, true, now(), now() "
            "FROM sales_venue_kind WHERE code = 'own_store' RETURNING id"
        )
    ).scalar_one()

    for statement in DROP_VIEWS:
        op.execute(statement)

    # listing: platform, format, status, external ids
    op.add_column("listing", sa.Column("sales_venue_id", sa.Integer(), nullable=True))
    bind.execute(sa.text("UPDATE listing SET sales_venue_id = :s"), {"s": store_id})
    op.alter_column("listing", "sales_venue_id", nullable=False)
    op.create_foreign_key(
        "fk_listing_sales_venue_id",
        "listing",
        "sales_venue",
        ["sales_venue_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_listing_sales_venue_id", "listing", ["sales_venue_id"])

    op.add_column(
        "listing",
        sa.Column(
            "format",
            postgresql.ENUM(name="listing_format", create_type=False),
            server_default="fixed_price",
            nullable=False,
        ),
    )
    op.add_column(
        "listing",
        sa.Column(
            "status",
            postgresql.ENUM(name="listing_status", create_type=False),
            server_default="active",
            nullable=False,
        ),
    )
    op.execute("UPDATE listing SET status = 'ended' WHERE NOT is_active")
    # The defaults served only the backfill; the application always sets both.
    op.alter_column("listing", "format", server_default=None)
    op.alter_column("listing", "status", server_default=None)

    op.drop_index("ix_listing_active", table_name="listing")
    op.drop_column("listing", "is_active")
    op.add_column(
        "listing",
        sa.Column(
            "is_active",
            sa.Boolean(),
            sa.Computed("status = 'active'::listing_status", persisted=True),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_listing_active",
        "listing",
        ["inventory_item_id"],
        postgresql_where=sa.text("is_active"),
    )
    op.add_column("listing", sa.Column("external_id", sa.String(length=128), nullable=True))
    op.add_column(
        "listing", sa.Column("external_url", sa.String(length=1000), nullable=True)
    )

    # sales_order: platform and the platform's order number
    op.add_column(
        "sales_order", sa.Column("sales_venue_id", sa.Integer(), nullable=True)
    )
    bind.execute(sa.text("UPDATE sales_order SET sales_venue_id = :s"), {"s": store_id})
    op.alter_column("sales_order", "sales_venue_id", nullable=False)
    op.create_foreign_key(
        "fk_sales_order_sales_venue_id",
        "sales_order",
        "sales_venue",
        ["sales_venue_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_index("ix_sales_order_sales_venue_id", "sales_order", ["sales_venue_id"])
    op.add_column(
        "sales_order",
        sa.Column("external_order_id", sa.String(length=128), nullable=True),
    )

    for statement in create_views():
        op.execute(statement)


def downgrade() -> None:
    """Drop platforms; listings keep only whether they were active."""
    for statement in DROP_VIEWS:
        op.execute(statement)

    op.drop_column("sales_order", "external_order_id")
    op.drop_index("ix_sales_order_sales_venue_id", table_name="sales_order")
    op.drop_constraint(
        "fk_sales_order_sales_venue_id", "sales_order", type_="foreignkey"
    )
    op.drop_column("sales_order", "sales_venue_id")

    op.drop_column("listing", "external_url")
    op.drop_column("listing", "external_id")
    op.drop_index("ix_listing_active", table_name="listing")
    op.add_column(
        "listing",
        sa.Column(
            "is_active_plain",
            sa.Boolean(),
            server_default=sa.text("true"),
            nullable=False,
        ),
    )
    op.execute("UPDATE listing SET is_active_plain = is_active")
    op.drop_column("listing", "is_active")
    op.alter_column("listing", "is_active_plain", new_column_name="is_active")
    op.create_index(
        "ix_listing_active",
        "listing",
        ["inventory_item_id"],
        postgresql_where=sa.text("is_active"),
    )
    op.drop_column("listing", "status")
    op.drop_column("listing", "format")
    op.drop_index("ix_listing_sales_venue_id", table_name="listing")
    op.drop_constraint("fk_listing_sales_venue_id", "listing", type_="foreignkey")
    op.drop_column("listing", "sales_venue_id")

    op.drop_index("uq_sales_venue_own_store", table_name="sales_venue")
    op.drop_index("ix_sales_venue_sales_venue_kind_id", table_name="sales_venue")
    op.drop_table("sales_venue")
    op.drop_table("sales_venue_kind")
    op.execute("DROP TYPE IF EXISTS listing_status")
    op.execute("DROP TYPE IF EXISTS listing_format")

    for statement in create_views(selling=False):
        op.execute(statement)
```

Before writing it, compare with the original `is_active` definition in the
migration that created `listing` (`7c3922fb3a9e_target_schema.py`) and copy
its server default exactly into the downgrade's `is_active_plain` column, so a
round trip leaves the column as it was. Check `TimestampMixin`
(`backend/app/models/base.py`) for the exact `created_at`/`updated_at`
definitions (server defaults, if any) and copy them into `sales_venue` the
same way.

- [ ] **Step 8: Test database gets the store**

In `backend/tests/conftest.py`, in the `engine` fixture, change the seeding
block to:

```python
    with Session(test_engine) as session:
        seed_all(session)
        # The migration creates the web store platform on a real database;
        # this one is built from the models, so it is created here.
        ensure_store_venue(session)
        session.commit()
```

(If `seed_all` already commits, the extra commit is harmless.) Import
`ensure_store_venue` from `app.sales_venues` and `store_venue_id` too.

In `build_listing`, replace `is_active=overrides.pop("is_active", True),` with:

```python
        status=(
            ListingStatus.active
            if overrides.pop("is_active", True)
            else ListingStatus.ended
        ),
        sales_venue_id=overrides.pop("sales_venue_id", store_venue_id(db)),
```

and import `ListingStatus` from `app.models`. Existing callers that pass
`is_active=False` keep working.

- [ ] **Step 9: Every other writer**

Replace each `is_active=` or `.is_active =` write with `status`, and give each
new `Listing` a platform:

- `backend/app/routers/catalog.py` `create_catalog_item`: in `Listing(...)`,
  replace `is_active=data["is_active"],` with
  `status=ListingStatus.active if data["is_active"] else ListingStatus.ended,`
  and add `sales_venue_id=store_venue_id(db),`.
- `backend/app/routers/catalog.py` `update_catalog_item`: change the loop to
  `for field in ("price", "quantity_available"):` and add after it:

  ```python
      if "is_active" in data:
          listing.status = (
              ListingStatus.active if data["is_active"] else ListingStatus.ended
          )
  ```
- `backend/app/splitting.py`: `listing.is_active = False` ->
  `listing.status = ListingStatus.ended`.
- `backend/app/seed.py`: in `Listing(...)`, `is_active=True,` ->
  `status=ListingStatus.active, sales_venue_id=store_venue_id(db),`.
- `backend/app/order_writes.py` `place_order`: in `SalesOrder(...)`, add
  `sales_venue_id=store_venue_id(db),`.
- `backend/tests/test_schema.py` lines ~311-325: `is_active=True` ->
  `status=ListingStatus.active`, `is_active=False` ->
  `status=ListingStatus.ended`, and add `sales_venue_id=store_venue_id(db)` to
  all three `Listing(...)` calls (~311, ~318, ~345).
- `backend/tests/test_concurrency.py` ~93 and
  `backend/tests/test_order_revision_race.py` ~67: add
  `sales_venue_id=store_venue_id(session),`.
- `backend/tests/test_order_writes.py` ~39 and `backend/tests/test_orders.py`
  ~42: add `sales_venue_id=store_venue_id(db),` to `SalesOrder(...)`.
- `backend/tests/test_order_writes.py` ~552: `listing.is_active = False` ->
  `listing.status = ListingStatus.ended`.

Import `ListingStatus` from `app.models` and `store_venue_id` from
`app.sales_venues` wherever used. Then check nothing was missed:

```cmd
cd backend && findstr /s /n /c:"is_active=" /c:".is_active =" app\*.py tests\*.py
```

Every remaining hit must be a reference table's `is_active`, a user's, or a
read -- not a listing write.

- [ ] **Step 10: Run the focused tests**

Run: `pytest tests\test_sales_venues.py tests\test_schema.py tests\test_catalog.py tests\test_split.py tests\test_order_writes.py tests\test_orders.py`
Expected: PASS.

- [ ] **Step 11: Run the migration tests**

Run: `pytest tests\test_migrations.py`
Expected: PASS. If `test_migrations_match_models` reports the computed
expression or the partial index as different, PostgreSQL normalised it:
copy the reflected text from the failure message into **both** the model's
`Computed(...)`/`postgresql_where` and the migration, so they match what the
database stores (`docs/code-quality.md` and `ccwebdb-alembic-orm-gotchas`
describe this). Re-run until green.

- [ ] **Step 12: Mutation check on the store rule**

Temporarily remove `unique=True` from `uq_sales_venue_own_store` in the model
(the test database is built from the models), run
`pytest tests\test_sales_venues.py -k second_store`, confirm it FAILS, restore,
confirm it passes.

- [ ] **Step 13: Gate and commit**

```cmd
.\scripts\ccweb_check.cmd > %TEMP%\ccweb_gate.txt 2>&1
```
Exit code 0, then:
```cmd
git add backend
git commit -m "Name the platform on every listing and order; listing status replaces is_active"
```

The message body should explain: the store platform, `is_active` generated
from `status`, the public catalogue now store fixed-price only, and the
`selling=False` historical views.

---

### Task 5: Checkout sells only store fixed-price listings

**Files:**
- Modify: `backend/app/order_writes.py` (`place_order` ~134, `revise_order` ~257)
- Test: `backend/tests/test_order_writes.py`

**Interfaces:**
- Consumes: `Listing.sales_venue`, `SalesVenue.is_own_store`, `ListingFormat` (Task 4).
- Produces: `order_writes._sellable_here(listing: Listing) -> bool`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_order_writes.py` (add `SalesVenue`,
`SalesVenueKind` and `ListingFormat` to its `app.models` import at the top):

```python
def _ebay(db: Session) -> int:
    kind = db.scalar(select(SalesVenueKind.id).where(SalesVenueKind.code == "marketplace"))
    venue = SalesVenue(code="ebay-test", name="eBay", sales_venue_kind_id=kind)
    db.add(venue)
    db.commit()
    return venue.id


def test_checkout_refuses_a_listing_on_another_platform(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
    db: Session,
) -> None:
    listing = make_listing(sales_venue_id=_ebay(db))

    response = _place_response(client, customer_headers, listing.id, 1)

    assert response.status_code == 409
    assert "not sold in this shop" in response.json()["detail"]


def test_checkout_refuses_an_auction_listing(
    client: TestClient,
    make_listing: Callable[..., Listing],
    customer_headers: dict[str, str],
) -> None:
    listing = make_listing(format=ListingFormat.auction)

    response = _place_response(client, customer_headers, listing.id, 1)

    assert response.status_code == 409
    assert "not sold in this shop" in response.json()["detail"]
```

`test_order_writes.py` has `_place(...)` (~line 202), which returns the
response's JSON. Add a sibling beside it that returns the response itself,
with the same payload:

```python
def _place_response(
    client: TestClient, headers: dict[str, str], listing_id: int, qty: int
) -> Response:
    return client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing_id, "quantity": qty}]},
        headers=headers,
    )
```

`Response` is `httpx.Response`. Add `select` (sqlalchemy), `Callable`
(collections.abc) and `from httpx import Response` to the imports if absent.

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests\test_order_writes.py -k "another_platform or auction_listing"`
Expected: FAIL -- 201 instead of 409.

- [ ] **Step 3: Implement**

In `backend/app/order_writes.py`, add:

```python
def _sellable_here(listing: Listing) -> bool:
    """Whether the shop's checkout may sell this listing at all."""
    return (
        listing.sales_venue.is_own_store
        and listing.format is ListingFormat.fixed_price
    )
```

In `place_order`, before `if not listing.is_active:` add:

```python
        if not _sellable_here(listing):
            _refuse(
                db,
                status.HTTP_409_CONFLICT,
                f"Listing {listing.id} is not sold in this shop",
            )
```

and the same check (with `listing_id`) in `revise_order` before its
`if not listing.is_active:`. Import `ListingFormat` from `.models`. If
`_lock_listings` does not load `sales_venue`, the relationship lazy-loads
inside the transaction, which is fine.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests\test_order_writes.py`
Expected: PASS.

- [ ] **Step 5: Gate and commit**

```cmd
.\scripts\ccweb_check.cmd > %TEMP%\ccweb_gate.txt 2>&1
git add backend/app/order_writes.py backend/tests/test_order_writes.py
git commit -m "Checkout sells only the web store's fixed-price listings"
```
(Two separate commands after reading the gate result.)

---

### Task 6: Platforms API

**Files:**
- Modify: `backend/app/schemas.py` (after `VendorCreate`)
- Create: `backend/app/routers/sales_venues.py`
- Modify: `backend/app/routers/__init__.py`, `backend/app/main.py`
- Test: `backend/tests/test_sales_venues.py`

**Interfaces:**
- Consumes: `SalesVenue`, `SalesVenueKind`, `Vendor` models.
- Produces (HTTP, admin only):
  - `GET /api/sales-venues` -> `list[SalesVenueOut]`, store first, then by name, retired included.
  - `POST /api/sales-venues` (`SalesVenueCreate`) -> 201 `SalesVenueOut`.
  - `PATCH /api/sales-venues/{code}` (`SalesVenueUpdate`) -> `SalesVenueOut`.
  - `SalesVenueOut` fields: `code, name, kind, is_own_store, vendor_id, vendor_name, account_handle, listing_url_template, commission_rate, processing_rate, processing_fixed, listing_fee, terms_as_of, notes, is_active, version`.

Rules:
- `kind` is a `sales_venue_kind` code; unknown -> 422; `own_store` on create -> 422 ("There is only one web store").
- `code` unique -> 409; must match `^[a-z0-9][a-z0-9_-]*$` -> 422.
- `vendor_id` must exist -> 422; already linked to another platform -> 409 naming it.
- `listing_url_template`, when given, must contain `{external_id}` -> 422.
- Rates 0..1, fixed amounts >= 0 -> 422 (Pydantic).
- The store's `kind` cannot change and it cannot be retired -> 422.
- `version` in PATCH: mismatch -> 409; `StaleDataError` on commit -> 409.
- `code` never changes.

- [ ] **Step 1: Write the failing tests**

Add `from fastapi.testclient import TestClient` to the file's imports, and
append:

```python
URL = "/api/sales-venues"


def _create(client: TestClient, headers: dict[str, str], **fields: object) -> dict:
    body = {"code": "ebay", "name": "eBay", "kind": "marketplace", **fields}
    response = client.post(URL, json=body, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()


def test_platforms_are_admin_only(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    assert client.get(URL).status_code == 401
    assert client.get(URL, headers=customer_headers).status_code == 403


def test_the_store_is_listed_first(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    _create(client, admin_headers, code="aaa", name="AAA")
    rows = client.get(URL, headers=admin_headers).json()
    assert rows[0]["code"] == "store"
    assert rows[0]["is_own_store"] is True
    assert rows[0]["kind"] == "own_store"


def test_create_a_platform_with_fees(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    created = _create(
        client,
        admin_headers,
        account_handle="nh_lakes_coins",
        listing_url_template="https://www.ebay.com/itm/{external_id}",
        commission_rate="0.1325",
        processing_fixed="0.40",
        terms_as_of="2026-09-17",
    )
    assert created["kind"] == "marketplace"
    assert created["commission_rate"] == "0.1325"
    assert created["processing_fixed"] == "0.40"
    assert created["processing_rate"] is None
    assert created["is_own_store"] is False
    assert created["version"] == 1


def test_a_second_store_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL,
        json={"code": "shop2", "name": "Shop 2", "kind": "own_store"},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "only one web store" in response.json()["detail"]


def test_an_unknown_kind_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL, json={"code": "x", "name": "X", "kind": "bazaar"}, headers=admin_headers
    )
    assert response.status_code == 422
    assert "bazaar" in response.json()["detail"]


def test_a_duplicate_code_is_a_conflict(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    _create(client, admin_headers)
    response = client.post(
        URL,
        json={"code": "ebay", "name": "eBay again", "kind": "marketplace"},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_a_bad_code_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL,
        json={"code": "Has Spaces", "name": "X", "kind": "marketplace"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_a_template_without_the_placeholder_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL,
        json={
            "code": "x",
            "name": "X",
            "kind": "marketplace",
            "listing_url_template": "https://example.com/item",
        },
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_a_rate_above_one_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL,
        json={"code": "x", "name": "X", "kind": "marketplace", "commission_rate": "13.25"},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_a_vendor_links_to_one_platform_only(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = Vendor(name="ebay.example")
    db.add(vendor)
    db.commit()
    first = _create(client, admin_headers, vendor_id=vendor.id)
    assert first["vendor_name"] == "ebay.example"

    response = client.post(
        URL,
        json={"code": "ebay2", "name": "eBay 2", "kind": "marketplace", "vendor_id": vendor.id},
        headers=admin_headers,
    )
    assert response.status_code == 409
    assert "eBay" in response.json()["detail"]


def test_an_unknown_vendor_is_refused(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        URL,
        json={"code": "x", "name": "X", "kind": "marketplace", "vendor_id": 999999},
        headers=admin_headers,
    )
    assert response.status_code == 422


def test_edit_and_retire_a_platform(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    created = _create(client, admin_headers)
    response = client.patch(
        f"{URL}/ebay",
        json={"name": "eBay US", "is_active": False, "version": created["version"]},
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["name"] == "eBay US"
    assert body["is_active"] is False
    assert body["version"] == created["version"] + 1


def test_unlink_a_vendor_with_null(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    vendor = Vendor(name="whatnot.example")
    db.add(vendor)
    db.commit()
    _create(client, admin_headers, code="whatnot", vendor_id=vendor.id)

    response = client.patch(
        f"{URL}/whatnot", json={"vendor_id": None}, headers=admin_headers
    )
    assert response.status_code == 200
    assert response.json()["vendor_id"] is None


def test_a_stale_version_is_a_conflict(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    created = _create(client, admin_headers)
    client.patch(f"{URL}/ebay", json={"name": "one"}, headers=admin_headers)
    response = client.patch(
        f"{URL}/ebay",
        json={"name": "two", "version": created["version"]},
        headers=admin_headers,
    )
    assert response.status_code == 409


def test_the_store_keeps_its_kind_and_stays_active(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    kind = client.patch(
        f"{URL}/store", json={"kind": "marketplace"}, headers=admin_headers
    )
    retire = client.patch(f"{URL}/store", json={"is_active": False}, headers=admin_headers)
    rename = client.patch(
        f"{URL}/store", json={"name": "Our shop"}, headers=admin_headers
    )
    assert kind.status_code == 422
    assert retire.status_code == 422
    assert rename.status_code == 200


def test_patching_an_unknown_platform_is_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    response = client.patch(f"{URL}/nope", json={"name": "x"}, headers=admin_headers)
    assert response.status_code == 404
```

(401 without a token and 403 for a customer is what the other admin routers
return -- `tests/test_user_admin.py:27-28`.)

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests\test_sales_venues.py`
Expected: the new tests FAIL with 404 (route not found).

- [ ] **Step 3: Schemas**

In `backend/app/schemas.py`, after `VendorCreate`:

```python
_VENUE_CODE = r"^[a-z0-9][a-z0-9_-]*$"
_URL_PLACEHOLDER = "{external_id}"


def _template_has_placeholder(value: str | None) -> str | None:
    """A listing URL template must say where the listing number goes."""
    if value is not None and _URL_PLACEHOLDER not in value:
        raise ValueError(f"must contain {_URL_PLACEHOLDER}")
    return value


class SalesVenueOut(BaseModel):
    """A sales platform, for the Platforms page."""

    code: str
    name: str
    #: A `sales_venue_kind` code.
    kind: str
    is_own_store: bool
    vendor_id: int | None = None
    vendor_name: str | None = None
    account_handle: str | None = None
    listing_url_template: str | None = None
    commission_rate: Decimal | None = None
    processing_rate: Decimal | None = None
    processing_fixed: Decimal | None = None
    listing_fee: Decimal | None = None
    terms_as_of: date | None = None
    notes: str | None = None
    is_active: bool
    version: int


class _SalesVenueFields(BaseModel):
    """The editable fields a platform shares between create and update."""

    model_config = ConfigDict(extra="forbid")

    account_handle: str | None = Field(default=None, max_length=255)
    listing_url_template: str | None = Field(default=None, max_length=500)
    commission_rate: Decimal | None = Field(default=None, ge=0, le=1)
    processing_rate: Decimal | None = Field(default=None, ge=0, le=1)
    processing_fixed: Decimal | None = Field(default=None, ge=0)
    listing_fee: Decimal | None = Field(default=None, ge=0)
    terms_as_of: date | None = None
    notes: str | None = None
    vendor_id: int | None = None

    @field_validator("listing_url_template")
    @classmethod
    def _placeholder(cls, value: str | None) -> str | None:
        """The template names where the listing number goes."""
        return _template_has_placeholder(value)


class SalesVenueCreate(_SalesVenueFields):
    """A new platform. The web store already exists and cannot be added."""

    code: str = Field(min_length=1, max_length=64, pattern=_VENUE_CODE)
    name: str = Field(min_length=1, max_length=255)
    kind: str = Field(min_length=1, max_length=64)


class SalesVenueUpdate(_SalesVenueFields):
    """A change to a platform. Omitted fields are left alone; `code` is fixed."""

    name: str | None = Field(default=None, min_length=1, max_length=255)
    kind: str | None = Field(default=None, min_length=1, max_length=64)
    is_active: bool | None = None
    #: The version the form loaded; a mismatch is a 409.
    version: int | None = None
```

Check the top of `schemas.py` imports `date`, `Decimal`, `ConfigDict`,
`Field`, `field_validator`; add any that are missing.

- [ ] **Step 4: Router**

Create `backend/app/routers/sales_venues.py`:

```python
"""Sales platforms: the web store, marketplaces, live shows, auction houses.

Admin only. The web store exists from the start and is the one platform that
cannot change kind or be retired -- checkout and the public catalogue are
defined by it. Default fees are for estimating a sale's net; a sale records
what was actually charged (selling design).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from ..deps import AdminUser, DbSession
from ..models import SalesVenue, SalesVenueKind, Vendor
from ..references import code_to_id
from ..schemas import SalesVenueCreate, SalesVenueOut, SalesVenueUpdate

router = APIRouter(prefix="/sales-venues", tags=["selling"])

_STALE = "This platform was changed by someone else. Reload and reapply your changes."


def _out(db: Session, venue: SalesVenue) -> SalesVenueOut:
    vendor = db.get(Vendor, venue.vendor_id) if venue.vendor_id else None
    kind = db.get(SalesVenueKind, venue.sales_venue_kind_id)
    return SalesVenueOut(
        code=venue.code,
        name=venue.name,
        kind=kind.code if kind is not None else "",
        is_own_store=venue.is_own_store,
        vendor_id=venue.vendor_id,
        vendor_name=vendor.name if vendor is not None else None,
        account_handle=venue.account_handle,
        listing_url_template=venue.listing_url_template,
        commission_rate=venue.commission_rate,
        processing_rate=venue.processing_rate,
        processing_fixed=venue.processing_fixed,
        listing_fee=venue.listing_fee,
        terms_as_of=venue.terms_as_of,
        notes=venue.notes,
        is_active=venue.is_active,
        version=venue.version,
    )


def _kind_id(db: Session, code: str) -> int:
    if code == "own_store":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="There is only one web store; choose another kind",
        )
    found = code_to_id(db, SalesVenueKind, code, "kind")
    if found is None:  # code_to_id returns None only for an empty code
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="kind is required"
        )
    return found


def _check_vendor(db: Session, vendor_id: int | None, venue_id: int | None) -> None:
    """The purchase source exists and no other platform is linked to it."""
    if vendor_id is None:
        return
    if db.get(Vendor, vendor_id) is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown vendor_id: {vendor_id}",
        )
    other = db.scalar(
        select(SalesVenue).where(
            SalesVenue.vendor_id == vendor_id, SalesVenue.id != (venue_id or 0)
        )
    )
    if other is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"That purchase source is already linked to {other.name}",
        )


@router.get("")
def list_sales_venues(db: DbSession, _admin: AdminUser) -> list[SalesVenueOut]:
    """Every platform, the web store first, retired ones included."""
    venues = db.scalars(
        select(SalesVenue).order_by(SalesVenue.is_own_store.desc(), SalesVenue.name)
    ).all()
    return [_out(db, venue) for venue in venues]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_sales_venue(
    payload: SalesVenueCreate, db: DbSession, _admin: AdminUser
) -> SalesVenueOut:
    """Add a platform the business sells through."""
    data = payload.model_dump()
    kind_id = _kind_id(db, data.pop("kind"))
    _check_vendor(db, data["vendor_id"], None)
    if db.scalar(select(SalesVenue.id).where(SalesVenue.code == data["code"])):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A platform with code {data['code']} already exists",
        )
    venue = SalesVenue(**data, sales_venue_kind_id=kind_id)
    db.add(venue)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two submissions racing past the checks above.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That platform code or purchase source is already in use",
        ) from exc
    db.refresh(venue)
    return _out(db, venue)


@router.patch("/{code}")
def update_sales_venue(
    code: str, payload: SalesVenueUpdate, db: DbSession, _admin: AdminUser
) -> SalesVenueOut:
    """Change a platform. Send `version` to be told about conflicts."""
    venue = db.scalar(select(SalesVenue).where(SalesVenue.code == code))
    if venue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such platform")

    # exclude_unset: an omitted field is left alone, an explicit null clears it.
    data: dict[str, Any] = payload.model_dump(exclude_unset=True)
    expected = data.pop("version", None)
    if expected is not None and expected != venue.version:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_STALE)

    if venue.is_own_store:
        if "kind" in data:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The web store's kind cannot change",
            )
        if data.get("is_active") is False:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="The web store cannot be retired",
            )
    if "kind" in data:
        venue.sales_venue_kind_id = _kind_id(db, data.pop("kind"))
    if "vendor_id" in data:
        _check_vendor(db, data["vendor_id"], venue.id)
    for field, value in data.items():
        if field == "name" and value is None:
            continue
        if field == "is_active" and value is None:
            continue
        setattr(venue, field, value)

    try:
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_STALE) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That purchase source is already linked to another platform",
        ) from exc
    db.refresh(venue)
    return _out(db, venue)
```

Register it: add `sales_venues` to the imports in
`backend/app/routers/__init__.py` (follow the file's existing pattern) and in
`backend/app/main.py`, after the `acquisitions` routers:

```python
app.include_router(sales_venues.router, prefix=settings.api_prefix)
```

- [ ] **Step 5: Run to verify they pass**

Run: `pytest tests\test_sales_venues.py`
Expected: PASS, all tests.

- [ ] **Step 6: Mutation check on the vendor rule**

Comment out the `other = ...` / `if other is not None:` block in
`_check_vendor`. Run `pytest tests\test_sales_venues.py -k one_platform_only`:
it should still return 409 via the database's `uq_sales_venue_vendor`, but the
message check for "eBay" FAILS -- confirming the friendly message comes from
the check. Restore, confirm it passes.

- [ ] **Step 7: Gate and commit**

```cmd
.\scripts\ccweb_check.cmd > %TEMP%\ccweb_gate.txt 2>&1
git add backend
git commit -m "Add the sales platforms API"
```
(Commit only after reading a zero exit code.)

---

### Task 7: The Platforms page

**Files:**
- Modify: `frontend/src/owner/api.js`
- Create: `frontend/src/owner/pages/Platforms.jsx`
- Test: `frontend/src/owner/pages/Platforms.test.jsx`
- Modify: `frontend/src/owner/OwnerApp.jsx`

**Interfaces:**
- Consumes: the Task 6 endpoints; `api.listVendors()` (exists); `useReference('sales_venue_kind')`.
- Produces: `api.listSalesVenues()`, `api.createSalesVenue(payload)`, `api.updateSalesVenue(code, payload)`; route `/platforms`.

- [ ] **Step 1: API calls**

In `frontend/src/owner/api.js`, after `createVendor`:

```js
  // sales platforms
  listSalesVenues: () => send('/api/sales-venues'),
  createSalesVenue: (payload) =>
    send('/api/sales-venues', { method: 'POST', body: payload }),
  updateSalesVenue: (code, payload) =>
    send(`/api/sales-venues/${encodeURIComponent(code)}`, {
      method: 'PATCH',
      body: payload,
    }),
```

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/owner/pages/Platforms.test.jsx`:

```jsx
import userEvent from '@testing-library/user-event'
import { screen, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listSalesVenues: vi.fn(),
    createSalesVenue: vi.fn(),
    updateSalesVenue: vi.fn(),
    listVendors: vi.fn(),
  },
}))

import { api } from '../api'
import Platforms from './Platforms'
import { adminAuth, emptyReference, renderWithProviders } from '../../test/helpers'

const STORE = {
  code: 'store',
  name: 'Web store',
  kind: 'own_store',
  is_own_store: true,
  vendor_id: null,
  vendor_name: null,
  account_handle: null,
  listing_url_template: null,
  commission_rate: null,
  processing_rate: null,
  processing_fixed: null,
  listing_fee: null,
  terms_as_of: null,
  notes: null,
  is_active: true,
  version: 1,
}

const EBAY = {
  ...STORE,
  code: 'ebay',
  name: 'eBay',
  kind: 'marketplace',
  is_own_store: false,
  vendor_id: 1,
  vendor_name: 'ebay.com',
  account_handle: 'you518',
  listing_url_template: 'https://www.ebay.com/itm/{external_id}',
  commission_rate: '0.1325',
  processing_fixed: '0.40',
  terms_as_of: '2026-09-17',
}

const kinds = emptyReference({
  tables: {
    sales_venue_kind: [
      { code: 'own_store', label: 'Our web store', source: 'seeded' },
      { code: 'marketplace', label: 'Marketplace', source: 'seeded' },
      { code: 'auction_house', label: 'Auction house (agent)', source: 'seeded' },
    ],
  },
})

function renderPage() {
  return renderWithProviders(<Platforms />, { auth: adminAuth(), reference: kinds })
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listSalesVenues.mockResolvedValue([STORE, EBAY])
  api.listVendors.mockResolvedValue([
    { id: 1, name: 'ebay.com', vendor_kind: 'marketplace' },
    { id: 2, name: 'whatnot.com', vendor_kind: 'marketplace' },
  ])
})

describe('Platforms', () => {
  it('lists platforms with fees shown as percentages', async () => {
    renderPage()
    const row = await screen.findByRole('row', { name: /eBay/ })
    expect(within(row).getByText('Marketplace')).toBeInTheDocument()
    expect(within(row).getByText('13.25% + $0.40')).toBeInTheDocument()
    expect(within(row).getByText('ebay.com')).toBeInTheDocument()
  })

  it('adds a platform, sending the rate as a fraction', async () => {
    const user = userEvent.setup()
    api.createSalesVenue.mockResolvedValue({ ...EBAY, code: 'whatnot', name: 'Whatnot' })
    renderPage()
    await screen.findByRole('row', { name: /eBay/ })

    await user.click(screen.getByRole('button', { name: 'Add platform' }))
    const dialog = screen.getByRole('dialog', { name: 'Add platform' })
    await user.type(within(dialog).getByLabelText('Name'), 'Whatnot')
    await user.type(within(dialog).getByLabelText('Code'), 'whatnot')
    await user.selectOptions(within(dialog).getByLabelText('Kind'), 'marketplace')
    await user.selectOptions(within(dialog).getByLabelText('Purchase source'), '2')
    await user.type(within(dialog).getByLabelText('Commission %'), '8')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(api.createSalesVenue).toHaveBeenCalledWith(
      expect.objectContaining({
        code: 'whatnot',
        name: 'Whatnot',
        kind: 'marketplace',
        vendor_id: 2,
        commission_rate: '0.08',
      }),
    )
  })

  it('does not offer the web store kind for a new platform', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('row', { name: /eBay/ })
    await user.click(screen.getByRole('button', { name: 'Add platform' }))
    const kind = within(screen.getByRole('dialog')).getByLabelText('Kind')
    expect(within(kind).queryByRole('option', { name: 'Our web store' })).toBeNull()
  })

  it('offers only unlinked purchase sources', async () => {
    const user = userEvent.setup()
    renderPage()
    await screen.findByRole('row', { name: /eBay/ })
    await user.click(screen.getByRole('button', { name: 'Add platform' }))
    const source = within(screen.getByRole('dialog')).getByLabelText('Purchase source')
    expect(within(source).queryByRole('option', { name: 'ebay.com' })).toBeNull()
    expect(within(source).getByRole('option', { name: 'whatnot.com' })).toBeInTheDocument()
  })

  it('edits a platform, sending its version', async () => {
    const user = userEvent.setup()
    api.updateSalesVenue.mockResolvedValue({ ...EBAY, name: 'eBay US', version: 2 })
    renderPage()
    const row = await screen.findByRole('row', { name: /eBay/ })

    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit eBay' })
    const name = within(dialog).getByLabelText('Name')
    await user.clear(name)
    await user.type(name, 'eBay US')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(api.updateSalesVenue).toHaveBeenCalledWith(
      'ebay',
      expect.objectContaining({ name: 'eBay US', version: 1, commission_rate: '0.1325' }),
    )
    expect(await screen.findByRole('row', { name: /eBay US/ })).toBeInTheDocument()
  })

  it('does not let the web store change kind or be retired', async () => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /Web store/ })
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog', { name: 'Edit Web store' })
    expect(within(dialog).queryByLabelText('Kind')).toBeNull()
    expect(within(dialog).queryByLabelText('Retired')).toBeNull()
  })

  it('shows a sample listing link from the template', async () => {
    const user = userEvent.setup()
    renderPage()
    const row = await screen.findByRole('row', { name: /eBay/ })
    await user.click(within(row).getByRole('button', { name: 'Edit' }))
    const dialog = screen.getByRole('dialog')
    expect(
      within(dialog).getByText('https://www.ebay.com/itm/123456789'),
    ).toBeInTheDocument()
  })

  it('shows a refusal and keeps the form open', async () => {
    const user = userEvent.setup()
    api.createSalesVenue.mockRejectedValue(new Error('A platform with code ebay already exists'))
    renderPage()
    await screen.findByRole('row', { name: /eBay/ })
    await user.click(screen.getByRole('button', { name: 'Add platform' }))
    const dialog = screen.getByRole('dialog')
    await user.type(within(dialog).getByLabelText('Name'), 'eBay')
    await user.type(within(dialog).getByLabelText('Code'), 'ebay')
    await user.selectOptions(within(dialog).getByLabelText('Kind'), 'marketplace')
    await user.click(within(dialog).getByRole('button', { name: 'Save' }))

    expect(await within(dialog).findByText(/already exists/)).toBeInTheDocument()
    expect(screen.getByRole('dialog')).toBeInTheDocument()
  })
})
```

`frontend/src/test/setup.js` already polyfills `HTMLDialogElement.showModal`
for jsdom; nothing to add.

- [ ] **Step 3: Run to verify they fail**

Run: `cd frontend && npx vitest run src\owner\pages\Platforms.test.jsx`
Expected: FAIL -- cannot resolve `./Platforms`.

- [ ] **Step 4: The page**

Create `frontend/src/owner/pages/Platforms.jsx`:

```jsx
import { useEffect, useState } from 'react'

import { api } from '../api'
import ModalDialog from '../ModalDialog'
import { useReference } from '../../shared/reference-context'

/**
 * The platforms the business sells through.
 *
 * The web store is created with the database and is the one platform whose
 * kind is fixed and which cannot be retired: the shop's checkout is defined
 * by it. Every other platform -- eBay, Whatnot, an auction house -- is added
 * here and may be linked to the purchase source of the same name, so a
 * partner exists once whether items are bought or sold there.
 *
 * Default fees only estimate a sale's net when pricing; a sale records what
 * was actually charged. Rates are typed as percentages and sent as fractions
 * (13.25 -> "0.1325"), because that is how the API and the database hold them.
 */

const SAMPLE_ID = '123456789'

/** "13.25" (percent, as typed) -> "0.1325"; blank -> null. */
export function percentToFraction(text) {
  const trimmed = String(text ?? '').trim()
  if (trimmed === '') return null
  const [whole, frac = ''] = trimmed.split('.')
  // Shift the decimal point two places left without floating-point error.
  const digits = (whole.padStart(3, '0') + frac).replace(/^0+(?=\d{3})/, '')
  const cut = digits.length - frac.length - 2
  const result = `${digits.slice(0, cut) || '0'}.${digits.slice(cut)}`
  return result.replace(/(\.\d*?)0+$/, '$1').replace(/\.$/, '') || '0'
}

/** "0.1325" -> "13.25"; null -> "". */
export function fractionToPercent(value) {
  if (value === null || value === undefined || value === '') return ''
  const [whole, frac = ''] = String(value).split('.')
  const padded = frac.padEnd(2, '0')
  const shifted = `${whole}${padded.slice(0, 2)}`.replace(/^0+(?=\d)/, '')
  const rest = padded.slice(2).replace(/0+$/, '')
  return rest ? `${shifted}.${rest}` : shifted
}

function feeSummary(v) {
  const parts = []
  if (v.commission_rate) parts.push(`${fractionToPercent(v.commission_rate)}%`)
  if (v.processing_rate) parts.push(`${fractionToPercent(v.processing_rate)}%`)
  if (v.processing_fixed) parts.push(`$${v.processing_fixed}`)
  if (v.listing_fee) parts.push(`$${v.listing_fee} per listing`)
  return parts.join(' + ')
}

const BLANK = {
  code: '',
  name: '',
  kind: '',
  vendor_id: '',
  account_handle: '',
  listing_url_template: '',
  commission_pct: '',
  processing_pct: '',
  processing_fixed: '',
  listing_fee: '',
  terms_as_of: '',
  notes: '',
  is_active: true,
}

function toForm(v) {
  return {
    code: v.code,
    name: v.name,
    kind: v.kind,
    vendor_id: v.vendor_id ?? '',
    account_handle: v.account_handle ?? '',
    listing_url_template: v.listing_url_template ?? '',
    commission_pct: fractionToPercent(v.commission_rate),
    processing_pct: fractionToPercent(v.processing_rate),
    processing_fixed: v.processing_fixed ?? '',
    listing_fee: v.listing_fee ?? '',
    terms_as_of: v.terms_as_of ?? '',
    notes: v.notes ?? '',
    is_active: v.is_active,
  }
}

const orNull = (text) => (String(text).trim() === '' ? null : String(text).trim())

function toPayload(form) {
  return {
    name: form.name.trim(),
    kind: form.kind,
    vendor_id: form.vendor_id === '' ? null : Number(form.vendor_id),
    account_handle: orNull(form.account_handle),
    listing_url_template: orNull(form.listing_url_template),
    commission_rate: percentToFraction(form.commission_pct),
    processing_rate: percentToFraction(form.processing_pct),
    processing_fixed: orNull(form.processing_fixed),
    listing_fee: orNull(form.listing_fee),
    terms_as_of: orNull(form.terms_as_of),
    notes: orNull(form.notes),
    is_active: form.is_active,
  }
}

function PlatformForm({ venue, venues, vendors, onSaved, onClose }) {
  const adding = venue === null
  const [form, setForm] = useState(adding ? BLANK : toForm(venue))
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)
  const kinds = useReference('sales_venue_kind') ?? []
  const isStore = !adding && venue.is_own_store
  const set = (k) => (e) =>
    setForm({ ...form, [k]: e.target.type === 'checkbox' ? !e.target.checked : e.target.value })

  // A purchase source may be linked to one platform: offer the free ones
  // and this platform's own.
  const taken = new Set(
    venues.filter((v) => v.vendor_id && v.code !== venue?.code).map((v) => v.vendor_id),
  )
  const sources = vendors.filter((v) => !taken.has(v.id))

  async function save() {
    setSaving(true)
    setError('')
    try {
      const payload = toPayload(form)
      let saved
      if (adding) {
        saved = await api.createSalesVenue({ ...payload, code: form.code.trim() })
      } else {
        if (isStore) {
          delete payload.kind
          delete payload.is_active
        }
        saved = await api.updateSalesVenue(venue.code, {
          ...payload,
          version: venue.version,
        })
      }
      onSaved(saved)
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  const label = adding ? 'Add platform' : `Edit ${venue.name}`
  const sample = form.listing_url_template.includes('{external_id}')
    ? form.listing_url_template.replace('{external_id}', SAMPLE_ID)
    : ''

  return (
    <ModalDialog label={label} onClose={onClose}>
      <h2>{label}</h2>
      {error && <p className="error">{error}</p>}
      <div className="filter-grid">
        <label>
          Name{/* */}
          <input value={form.name} onChange={set('name')} />
        </label>
        {adding && (
          <label>
            Code{/* */}
            <input
              value={form.code}
              onChange={set('code')}
              placeholder="lower-case, e.g. ebay"
            />
          </label>
        )}
        {!isStore && (
          <label>
            Kind{/* */}
            <select value={form.kind} onChange={set('kind')}>
              <option value="">(choose)</option>
              {kinds
                .filter((k) => k.code !== 'own_store')
                .map((k) => (
                  <option key={k.code} value={k.code}>
                    {k.label}
                  </option>
                ))}
            </select>
          </label>
        )}
        <label>
          Purchase source{/* */}
          <select value={form.vendor_id} onChange={set('vendor_id')}>
            <option value="">(none)</option>
            {sources.map((v) => (
              <option key={v.id} value={v.id}>
                {v.name}
              </option>
            ))}
          </select>
        </label>
        <label>
          Account{/* */}
          <input value={form.account_handle} onChange={set('account_handle')} />
        </label>
        <label>
          Listing link{/* */}
          <input
            value={form.listing_url_template}
            onChange={set('listing_url_template')}
            placeholder="https://.../{external_id}"
          />
        </label>
        <label>
          Commission %{/* */}
          <input
            inputMode="decimal"
            value={form.commission_pct}
            onChange={set('commission_pct')}
          />
        </label>
        <label>
          Processing %{/* */}
          <input
            inputMode="decimal"
            value={form.processing_pct}
            onChange={set('processing_pct')}
          />
        </label>
        <label>
          Processing $ per sale{/* */}
          <input
            inputMode="decimal"
            value={form.processing_fixed}
            onChange={set('processing_fixed')}
          />
        </label>
        <label>
          Fee $ per listing{/* */}
          <input
            inputMode="decimal"
            value={form.listing_fee}
            onChange={set('listing_fee')}
          />
        </label>
        <label>
          Fees as of{/* */}
          <input type="date" value={form.terms_as_of} onChange={set('terms_as_of')} />
        </label>
        <label>
          Notes{/* */}
          <input value={form.notes} onChange={set('notes')} />
        </label>
        {!isStore && !adding && (
          <label>
            <input type="checkbox" checked={!form.is_active} onChange={set('is_active')} />
            Retired
          </label>
        )}
      </div>
      {sample && (
        <p className="muted">
          Example link: <span>{sample}</span>
        </p>
      )}
      <div className="row">
        <button disabled={saving} onClick={save}>
          {saving ? 'Saving...' : 'Save'}
        </button>
        <button className="link" onClick={onClose}>
          Cancel
        </button>
      </div>
    </ModalDialog>
  )
}

export default function Platforms() {
  const [venues, setVenues] = useState(null)
  const [vendors, setVendors] = useState([])
  const [error, setError] = useState('')
  // null: nothing open; 'new': adding; otherwise the platform being edited.
  const [open, setOpen] = useState(null)
  const kinds = useReference('sales_venue_kind', { includeRetired: true }) ?? []
  const kindLabel = (code) => kinds.find((k) => k.code === code)?.label ?? code

  useEffect(() => {
    let cancelled = false
    Promise.all([api.listSalesVenues(), api.listVendors()])
      .then(([v, s]) => {
        if (cancelled) return
        setVenues(v)
        setVendors(s)
      })
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [])

  function saved(venue) {
    setVenues((current) => {
      const others = current.filter((v) => v.code !== venue.code)
      return [...others, venue].sort(
        (a, b) => Number(b.is_own_store) - Number(a.is_own_store) || a.name.localeCompare(b.name),
      )
    })
    setOpen(null)
  }

  if (error) return <p className="error">{error}</p>
  if (venues === null) return <p className="muted">Loading...</p>

  return (
    <section>
      <h1>Platforms</h1>
      <p className="muted">Where items are sold. Fees here are defaults for estimates.</p>
      <button onClick={() => setOpen('new')}>Add platform</button>
      <table>
        <thead>
          <tr>
            <th>Name</th>
            <th>Kind</th>
            <th>Purchase source</th>
            <th>Account</th>
            <th>Default fees</th>
            <th>Fees as of</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {venues.map((v) => (
            <tr key={v.code} className={v.is_active ? '' : 'muted'}>
              <td>
                {v.name}
                {!v.is_active && ' (retired)'}
              </td>
              <td>{kindLabel(v.kind)}</td>
              <td>{v.vendor_name ?? ''}</td>
              <td>{v.account_handle ?? ''}</td>
              <td>{feeSummary(v)}</td>
              <td>{v.terms_as_of ?? ''}</td>
              <td>
                <button className="link" onClick={() => setOpen(v)}>
                  Edit
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {open !== null && (
        <PlatformForm
          venue={open === 'new' ? null : open}
          venues={venues}
          vendors={vendors}
          onSaved={saved}
          onClose={() => setOpen(null)}
        />
      )}
    </section>
  )
}
```

The `set` helper inverts the checkbox because the box reads "Retired" while
the field is `is_active`. Write unit tests for the two conversion helpers
alongside: change the test file's `import Platforms from './Platforms'` to
`import Platforms, { fractionToPercent, percentToFraction } from './Platforms'`
and append:

```jsx
describe('rate conversion', () => {
  it.each([
    ['13.25', '0.1325'],
    ['8', '0.08'],
    ['0.5', '0.005'],
    ['100', '1'],
    ['2.9', '0.029'],
    ['', null],
  ])('percent %s is fraction %s', (percent, fraction) => {
    expect(percentToFraction(percent)).toBe(fraction)
  })

  it.each([
    ['0.1325', '13.25'],
    ['0.0800', '8'],
    ['0.0050', '0.5'],
    ['1.0000', '100'],
    [null, ''],
  ])('fraction %s is percent %s', (fraction, percent) => {
    expect(fractionToPercent(fraction)).toBe(percent)
  })
})
```

If a conversion case fails, fix the helper -- do not change the expectation;
these are the values the owner will type and see. (Exporting helpers from a
component module breaks Fast Refresh -- `docs/code-quality.md`. If eslint's
`react-refresh/only-export-components` flags it, move the two helpers to
`frontend/src/owner/pages/platform-rates.js` and import them from there in
both files.)

- [ ] **Step 5: Route and nav**

In `frontend/src/owner/OwnerApp.jsx`: import `Platforms from './pages/Platforms'`;
add `<NavLink to="/platforms">Platforms</NavLink>` directly before the
Vocabularies link; add `<Route path="/platforms" element={<Platforms />} />`
before the Vocabularies route. Read `OwnerApp.test.jsx` and add a nav-link
assertion for Platforms if it asserts the existing links.

- [ ] **Step 6: Run to verify they pass**

Run: `cd frontend && npx vitest run src\owner`
Expected: PASS.

- [ ] **Step 7: Gate and commit**

```cmd
.\scripts\ccweb_check.cmd > %TEMP%\ccweb_gate.txt 2>&1
git add frontend
git commit -m "Add the console's Platforms page"
```
(Commit after reading a zero exit code; the bundle-isolation check must stay
green -- the new calls are in owner/api.js only.)

---

### Task 8: Documentation

**Files:**
- Modify: `docs/database-design.md`
- Modify: `docs/system-administration.md`
- Modify: `docs/specs/selling-design.md` (status line only)

- [ ] **Step 1: Database design**

Read `docs/database-design.md` sections 12 and 13. Add to the section that
lists the sales tables a `sales_venue` entry (columns as in the spec), the
`listing` changes (`sales_venue_id`, `format`, `status`, generated
`is_active`, `external_id`, `external_url`), `sales_order.sales_venue_id` and
`external_order_id`, and `sales_venue_kind` in the vocabulary table. In
section 13 (deviations actually implemented), note that `is_active` is
generated from `status` and why.

- [ ] **Step 2: System administration**

Add a "Sales platforms" subsection: the Platforms page, that the web store
exists from the migration and cannot be retired, that fees are defaults for
estimates entered from the owner's accounts, and the `vendor_cleanup` command
with its dry-run default.

- [ ] **Step 3: Spec status**

In `docs/specs/selling-design.md`, change the status line to
`Design. Status: **agreed with the owner 2026-09-17**; phases 0-1 built.`

- [ ] **Step 4: Commit**

```cmd
git add docs
git commit -m "Document sales platforms and the purchase-source clean-up"
```

---

### Task 9: Apply to live (owner-gated)

**Files:** none.

- [ ] **Step 1: Owner's go-ahead** to apply the migration and remove the five
  demo listings (CC-007657..CC-007661). Do not proceed without it.

- [ ] **Step 2: Backup** as in Task 2 step 3.

- [ ] **Step 3: Scratch copy first.** Create `ccwebdb_platformcheck` from live
  (`createdb -T ccwebdb` with live connections paused, or `app.backup` to the
  new URL), run `alembic upgrade head` against it with `DATABASE_URL` set only
  for that command, and show the owner:

```sql
select count(*), count(*) filter (where status = 'active') from listing;
select count(*) from sales_order;
select code, name, is_own_store from sales_venue;
```

  The listing counts must equal live's `count(*)` and
  `count(*) filter (where is_active)` from before. Drop the scratch copy
  afterwards.

- [ ] **Step 4: Live.** Stop the backend (`.\scripts\ccweb_shutdown.cmd --keepdb`
  from Git Bash, or the console equivalent), run `alembic upgrade head` and
  `python -m app.seeding load`, start with `.\scripts\ccweb_startup.cmd`
  (output redirected to a file, never piped). The backend runs without
  `--reload`, so the restart is required.

- [ ] **Step 5: Remove the demo listings** through the existing
  `DELETE /api/catalog/{listing_id}` (it deletes an unordered listing and its
  item) for listing ids 1-5, after confirming with
  `select l.id, i.item_code from listing l join inventory_item i on i.id = l.inventory_item_id`
  that those ids are still CC-007657..CC-007661 and have no order lines.

- [ ] **Step 6: Verify** in the browser at `http://127.0.0.1:5173/owner/platforms`
  that the web store is listed, and that `http://127.0.0.1:8000/api/catalog`
  returns an empty list.
