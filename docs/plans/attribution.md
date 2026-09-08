# Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the 7,591 imported items editable, diagnosable and reviewable, so a lot of fifty Morgans can be split and then attributed one coin at a time.

**Architecture:** Extend the existing inventory API and search machinery rather than build beside them. Three schema additions (`item_field_review`, `inventory_item.deleted_at`, detail rows on split children), five new endpoints on the existing `/api/inventory` router, a new `Issue` predicate type inside the existing `ViewSpec`, and a split of `Inventory.jsx` into modules as its surface grows.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy 2.0 (`Mapped`/`mapped_column`), Alembic, Pydantic v2, PostgreSQL 18.6, React 19 + Vite, pytest.

**Spec:** `docs/specs/attribution-design.md`

## Global Constraints

- **Never commit to `main`.** Work happens on `feat/attribution`, already checked out.
- **`uv` owns Python dependencies**, never `pip`. Run things as `uv run <cmd>` from the repository root, or from `backend/` when the command needs `app` importable (`alembic`).
- **The gate is `scripts\ccweb_check.cmd`** — format, lint, types, tests, frontend lint and format. It must be green before any commit. mypy findings are reported, not enforced; the current count is 56 in 15 files and must not rise.
- **Money is `NUMERIC`, never a float.** Decimals cross the API as strings — FastAPI's encoder turns a `Decimal` in a plain dict into a float.
- **Classifiers cross the API as codes, never ids.** An unknown code is a 422 naming the field, never a silently null column.
- **An unrecognised filter or sort is a 422**, never ignored: a silently dropped filter returns the whole collection and looks like a matching result.
- **Prove a guarantee by removing it.** Every guard added here gets a test, and the test is confirmed to fail when the guard is removed and to pass when it is restored.
- **The dev database holds the real collection.** 7,591 items, $534,177.89. Tests use a separate `ccwebdb_test`; never point a test at `ccwebdb`.
- **Migrations use `alter_column(new_column_name=...)` for renames**, never drop-and-add. Autogenerate proposes the latter and it destroys data.
- **A view stores its source columns by reference**, so any migration touching a column the views name must drop all four views first and recreate them after.

## What already exists — do not rebuild it

Verified in the tree at the time of writing. An executor who reimplements any of this has wasted the work:

| | Where |
|---|---|
| Search with filters, sorting, paging, free text, series-alias matching | `backend/app/inventory_search.py` |
| Facets counted by indexed FK id, then resolved to codes | `inventory_search.count_facets` |
| `GET /api/inventory/{view}/search`, `GET /api/inventory/{item_id}` | `backend/app/routers/inventory.py` |
| `POST /api/inventory/{item_id}/split`, equal and relative allocation | `routers/inventory.py`, `app/splitting.py`, `app/allocation.py` |
| Optimistic concurrency on `inventory_item.version` (`version_id_col`) | `backend/app/models/core.py:174` |
| The 409-on-stale-version pattern to copy | `backend/app/routers/catalog.py:302` |
| Coin and currency browse screens with a working filter panel | `frontend/src/pages/Inventory.jsx` |
| Series, aliases, serial patterns, order repair, backup | `app/series_match.py`, `app/serial_patterns.py`, `app/order_repair.py`, `app/backup.py` |

And what does **not** exist, confirmed by grep returning nothing: `deleted_at`, any `issue=` handling, and any bulk, reviewed, detach or `PATCH` inventory endpoint. `frontend/src/components/` is empty.

## File Structure

**Created:**

| File | Responsibility |
|---|---|
| `backend/app/models/lifecycle.py` (extend) | `ItemFieldReview` — one field of one item, confirmed by a person |
| `backend/app/issues.py` | The named diagnostics: one predicate each, defined once, used as filter and as facet |
| `frontend/src/pages/inventory/useInventorySearch.js` | URL state, fetch, cancellation, facets |
| `frontend/src/pages/inventory/specs.js` | The coin and currency column/filter/issue specifications |
| `frontend/src/pages/inventory/InventoryTable.jsx` | Rows, sorting, selection |
| `frontend/src/pages/inventory/FilterPanel.jsx` | Text inputs, facet selects, issue checks |
| `frontend/src/pages/inventory/BulkEditBar.jsx` | Appears with a selection |
| `frontend/src/pages/inventory/ItemEditForm.jsx` | Fields, parent-value hints, review marks |
| `frontend/src/pages/inventory/ReviewPane.jsx` | One item at a time, frozen queue |

**Modified:** `backend/app/splitting.py`, `backend/app/models/core.py`, `backend/app/models/views.py`, `backend/app/models/__init__.py`, `backend/app/inventory_search.py`, `backend/app/routers/inventory.py`, `backend/app/schemas.py`, `backend/alembic/versions/b78d71343405_*.py`, `frontend/src/api.js`, `frontend/src/pages/Inventory.jsx`.

---

# Phase 1 — the API

At the end of Phase 1 every item is editable, deletable, detachable and reviewable through the API, and every diagnostic in the spec is queryable. Phase 2 is the screens for it. Phase 1 is independently valuable: the Excel round trip in `docs/specs/excel-roundtrip-design.md` sits on exactly this API and needs no UI.

---

### Task 1: A split piece gets its own detail row

The spec calls this a bug rather than a design question. Doing it first means the fifty-Morgans case has somewhere to put a mint mark before anything else is built.

**Files:**
- Modify: `backend/app/splitting.py`
- Test: `backend/tests/test_split.py`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: no new signatures. `split_item(db, parent, pieces, mode)` keeps its shape; every returned child now has exactly one detail row.

- [ ] **Step 1: Write the failing tests**

Add to `backend/tests/test_split.py`. The imports at the top of that file need `CoinDetail`, `CurrencyDetail`, `ItemKind` added to the `from app.models import ...` line, and `code_id` added to the existing `from tests.test_schema import make_item`.

```python
def test_a_split_piece_gets_its_own_coin_detail_row(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Every item in the collection has exactly one; a piece must too.

    Without it, mint mark and variety have nowhere to be written -- which is
    the whole point of splitting a lot of Morgans.
    """
    parent = lot(db)
    body = do_split(client, admin_headers, parent.id, TUBE).json()
    ids = sorted(p["id"] for p in body["pieces"])

    rows = sorted(
        db.scalars(
            select(CoinDetail.inventory_item_id).where(
                CoinDetail.inventory_item_id.in_(ids)
            )
        ).all()
    )
    assert rows == ids


def test_a_currency_piece_gets_a_currency_detail_row(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The kind decides the table: a note's serial has no home on coin_detail."""
    parent = lot(db, item_kind_id=code_id(db, ItemKind, "currency"))
    body = do_split(client, admin_headers, parent.id, TUBE).json()
    ids = sorted(p["id"] for p in body["pieces"])

    rows = sorted(
        db.scalars(
            select(CurrencyDetail.inventory_item_id).where(
                CurrencyDetail.inventory_item_id.in_(ids)
            )
        ).all()
    )
    assert rows == ids
    assert not db.scalars(
        select(CoinDetail.inventory_item_id).where(
            CoinDetail.inventory_item_id.in_(ids)
        )
    ).all(), "a banknote must not also get a coin_detail row"


def test_a_piece_detail_row_starts_empty(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The lot's detail row describes the lot, and must not be copied down.

    Copying would write the seller's guess into precisely the fields someone
    is about to fill in by examining the piece -- the thing the whole
    provenance design exists to prevent.
    """
    parent = lot(db)
    db.add(CoinDetail(inventory_item_id=parent.id, variety="VAM-1A"))
    db.commit()

    body = do_split(client, admin_headers, parent.id, TUBE).json()
    ids = [p["id"] for p in body["pieces"]]

    varieties = db.scalars(
        select(CoinDetail.variety).where(CoinDetail.inventory_item_id.in_(ids))
    ).all()
    assert set(varieties) == {None}
```

- [ ] **Step 2: Run the tests to verify they fail**

```
uv run pytest backend/tests/test_split.py -k detail -v
```

Expected: 3 FAILED. The first two on `assert [] == [<ids>]`, the third collecting nothing to assert against.

- [ ] **Step 3: Write the implementation**

In `backend/app/splitting.py`, extend the `from .models import (...)` block with `CoinDetail`, `CurrencyDetail` and `ItemKind` (keep the list alphabetical, as it is now).

Inside `split_item`, immediately after the existing `held = db.scalar(...)` line, add:

```python
    # Resolved once rather than per piece: a fifty-way split would otherwise
    # run fifty identical lookups.
    currency_kind_id = db.scalar(
        select(ItemKind.id).where(ItemKind.code == "currency")
    )
```

Then inside the `for piece, cost, ship in zip(...)` loop, after the existing `db.add(child)` / `db.flush()` pair and before the `ItemStatusHistory` block, add:

```python
        # Every item in the collection has exactly one detail row, and a piece
        # is an item. Created empty rather than copied from the parent's: a
        # lot's detail row describes the lot, so copying it would write the
        # seller's guess into exactly the fields someone is about to fill in
        # by examining this piece.
        detail_model = (
            CurrencyDetail
            if child.item_kind_id == currency_kind_id
            else CoinDetail
        )
        db.add(detail_model(inventory_item_id=child.id))
```

- [ ] **Step 4: Run the tests to verify they pass**

```
uv run pytest backend/tests/test_split.py -v
```

Expected: all pass, including the existing allocation tests.

- [ ] **Step 5: Commit**

```bash
git add backend/app/splitting.py backend/tests/test_split.py
git commit -m "Give every split piece its own detail row

Every one of the 7,591 items has exactly one, and a piece is an item. Without
it a coin split out of a lot of fifty Morgans has nowhere to record a mint
mark or a variety, which is the data the split exists to make recordable.

Created empty rather than copied from the parent's. A lot's detail row
describes the lot, so copying it would write the seller's guess into exactly
the fields someone is about to fill in by examining the piece.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `item_field_review`

**Files:**
- Modify: `backend/app/models/lifecycle.py`, `backend/app/models/__init__.py`
- Create: `backend/alembic/versions/<generated>_add_item_field_review.py`
- Test: `backend/tests/test_review.py`

**Interfaces:**
- Produces: `ItemFieldReview(id, inventory_item_id, field_name, reviewed_at, reviewed_by_id)`, importable as `from app.models import ItemFieldReview`. Unique on `(inventory_item_id, field_name)`, constraint named `uq_item_field_review`. Tasks 5 and 9 depend on both.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_review.py`:

```python
"""Recording that a person has confirmed a field by looking at the object.

Per field rather than per item because the unit of work is the field:
attributing fifty Morgans means confirming grade on all fifty, then year on
all fifty, and a half-done coin is the normal state.
"""

from __future__ import annotations

import pytest
from app.models import ItemFieldReview
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def test_a_field_is_reviewed_at_most_once(db: Session) -> None:
    """A second confirmation of the same field is the same fact, not a new one.

    Without the constraint the table accumulates duplicates and "is this
    reviewed?" becomes a count rather than an existence check.
    """
    item = make_item(db)
    db.add(ItemFieldReview(inventory_item_id=item.id, field_name="grade_id"))
    db.commit()

    db.add(ItemFieldReview(inventory_item_id=item.id, field_name="grade_id"))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_two_fields_of_one_item_are_separate_records(db: Session) -> None:
    """Confirming the grade says nothing about the year."""
    item = make_item(db)
    db.add_all(
        [
            ItemFieldReview(inventory_item_id=item.id, field_name="grade_id"),
            ItemFieldReview(inventory_item_id=item.id, field_name="year_start"),
        ]
    )
    db.commit()

    names = {r.field_name for r in db.query(ItemFieldReview).all()}
    assert names == {"grade_id", "year_start"}


def test_deleting_an_item_takes_its_reviews_with_it(db: Session) -> None:
    """A review of a row that no longer exists is not a fact about anything."""
    item = make_item(db)
    db.add(ItemFieldReview(inventory_item_id=item.id, field_name="grade_id"))
    db.commit()

    db.delete(item)
    db.commit()
    assert db.query(ItemFieldReview).count() == 0
```

- [ ] **Step 2: Run the test to verify it fails**

```
uv run pytest backend/tests/test_review.py -v
```

Expected: collection error, `ImportError: cannot import name 'ItemFieldReview' from 'app.models'`.

- [ ] **Step 3: Write the model**

In `backend/app/models/lifecycle.py`, change `__all__` to `["ItemFieldReview", "ItemStatusHistory", "LocationHistory", "StorageLocation"]` and append:

```python
class ItemFieldReview(Base):
    """One field of one item, confirmed by a person looking at the object.

    Per field rather than per item because the unit of work is the field.
    Attributing fifty Morgans means confirming grade on all fifty, then year
    on all fifty, in whatever order the light and the loupe allow; an
    item-level flag cannot express a half-done coin, and a half-done coin is
    the normal state.

    Absent means unconfirmed, which is the correct default for every one of
    the 7,591 imported items and needs no backfill.

    Distinct from comparing a split child to its parent, which answers what
    the *lot claimed*. That comparison is derived and cannot drift out of
    sync; this is asserted and cannot be computed from anything. Both are
    needed and neither replaces the other.
    """

    __tablename__ = "item_field_review"

    id: Mapped[int] = mapped_column(primary_key=True)
    inventory_item_id: Mapped[int] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    #: The column confirmed, e.g. `grade_id`. Checked against a whitelist at
    #: the API boundary rather than by a constraint here: which fields are
    #: worth confirming is a product decision that will change, and a check
    #: constraint would need a migration every time it did.
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    reviewed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    #: SET NULL rather than CASCADE: deactivating a member of staff must not
    #: erase the record that the work was done.
    reviewed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    __table_args__ = (
        UniqueConstraint(
            "inventory_item_id", "field_name", name="uq_item_field_review"
        ),
    )
```

In `backend/app/models/__init__.py`, add `ItemFieldReview` to the `from .lifecycle import ...` line and to `__all__`, both in alphabetical position.

- [ ] **Step 4: Generate and check the migration**

```
cd backend && uv run alembic revision --autogenerate -m "add item_field_review"
```

Open the generated file and verify it contains exactly one `op.create_table("item_field_review", ...)` with the unique constraint, and that `downgrade` drops it. Autogenerate handles a new table correctly; this is a read, not a rewrite. Then:

```
cd backend && uv run alembic upgrade head && uv run alembic check
```

Expected: the upgrade runs, and `check` reports "No new upgrade operations detected."

- [ ] **Step 5: Run the tests to verify they pass**

```
uv run pytest backend/tests/test_review.py backend/tests/test_migrations.py -v
```

Expected: all pass. `test_migrations_match_models` builds a database from scratch and is what catches a migration that disagrees with the models.

- [ ] **Step 6: Commit**

```bash
git add backend/app/models/lifecycle.py backend/app/models/__init__.py \
        backend/alembic/versions/ backend/tests/test_review.py
git commit -m "Record which fields a person has confirmed

Per field rather than per item, decided with the owner. The unit of work is
the field: attributing fifty Morgans means confirming grade on all fifty,
then year on all fifty, and a half-done coin is the normal state.

Absent means unconfirmed, which is the right default for every imported item
and needs no backfill. This is asserted information -- that a person looked --
and so cannot be derived, which is what distinguishes it from the flag an
earlier draft proposed to restate what comparing a child to its parent
already shows.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Soft delete

**Files:**
- Modify: `backend/app/models/core.py`, `backend/app/models/views.py`, `backend/alembic/versions/b78d71343405_rename_cost_columns_and_add_series_designation.py`
- Create: `backend/alembic/versions/<generated>_add_soft_delete.py`
- Test: `backend/tests/test_schema.py`

**Interfaces:**
- Produces: `InventoryItem.deleted_at: datetime | None`; `create_views(..., soft_delete: bool = True)`; all four views gain `AND i.deleted_at IS NULL`. Tasks 6 and 9 depend on the column.

- [ ] **Step 1: Write the failing test**

Add to `backend/tests/test_schema.py`:

```python
def test_a_deleted_item_leaves_every_view(db: Session) -> None:
    """Soft delete must reach the views, not only the search.

    A row that is hidden from search but still summed by item_valuation would
    keep contributing money to a collection that no longer holds it.
    """
    item = make_item(db, item_cost=Decimal("42.00"))
    views = ("coin_inventory", "item_valuation")

    for view in views:
        present = db.execute(
            text(f"SELECT count(*) FROM {view} WHERE id = :id"), {"id": item.id}
        ).scalar_one()
        assert present == 1, f"{view} should show a live item"

    item.deleted_at = utcnow()
    db.commit()

    for view in views:
        present = db.execute(
            text(f"SELECT count(*) FROM {view} WHERE id = :id"), {"id": item.id}
        ).scalar_one()
        assert present == 0, f"{view} still shows a deleted item"
```

Add `from app.models.base import utcnow` to that file's imports.

- [ ] **Step 2: Run the test to verify it fails**

```
uv run pytest backend/tests/test_schema.py -k deleted -v
```

Expected: FAIL with `AttributeError: 'InventoryItem' object has no attribute 'deleted_at'`.

- [ ] **Step 3: Add the column**

In `backend/app/models/core.py`, immediately after the existing `split_at` column on `InventoryItem`:

```python
    #: Soft delete: this row should never have existed.
    #:
    #: Not a `disposition` value. Disposition records what happened to a coin
    #: -- held, listed, sold, shipped -- and "created by mistake" is not
    #: something that happened to a coin. Putting it there would corrupt every
    #: disposition report with rows that were never real.
    #:
    #: A column also lets `WHERE deleted_at IS NULL` sit beside the
    #: `split_at IS NULL` already in all four views: same shape of rule, same
    #: place.
    deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True, nullable=True
    )
```

- [ ] **Step 4: Add it to the four views**

In `backend/app/models/views.py`, add `  AND i.deleted_at IS NULL` to each view's WHERE clause, matching the surrounding indentation:

- `_COIN_INVENTORY`: `WHERE i.split_at IS NULL` → followed by a new line `  AND i.deleted_at IS NULL` before the existing `  AND k.code IN (...)`.
- `_CURRENCY_INVENTORY`: same, before `  AND k.code = 'currency'`.
- `_ITEM_VALUATION`: `    WHERE i.split_at IS NULL` → add `      AND i.deleted_at IS NULL` on the next line.
- `_PUBLIC_CATALOG`: after `  AND i.split_at IS NULL`, add `  AND i.deleted_at IS NULL`.

Then add the historical-variant machinery beside `_PRE_RENAME_FRAGMENTS`:

```python
#: Soft delete came later than the views, so a view created by an earlier
#: revision must not name the column. Without this a fresh `upgrade head`
#: fails partway: the view is created before the column is added.
_SOFT_DELETE_FRAGMENTS: tuple[tuple[str, str], ...] = (
    ("\n  AND i.deleted_at IS NULL", ""),
    ("\n      AND i.deleted_at IS NULL", ""),
)
```

Extend `create_views` with the flag, keeping the existing assertion style:

```python
def create_views(
    *,
    lineage: bool = True,
    item_code: bool = True,
    renamed_costs: bool = True,
    soft_delete: bool = True,
) -> tuple[str, ...]:
```

Inside it, after the `if not renamed_costs:` removal block:

```python
    if not soft_delete:
        removals.extend(_SOFT_DELETE_FRAGMENTS)
```

and inside the per-statement loop, after the `renamed_costs` assertions:

```python
        if not soft_delete:
            assert "deleted_at" not in statement, (
                "the soft-delete-stripping fragments no longer match the view "
                "SQL; a migration would create a view naming a column that "
                "does not exist at its revision"
            )
```

Finally, update the two derived constants at the bottom of the file so the historical variants stay historical:

```python
CREATE_VIEWS_WITHOUT_LINEAGE: tuple[str, ...] = create_views(
    lineage=False, renamed_costs=False, soft_delete=False
)

CREATE_VIEWS_ORIGINAL: tuple[str, ...] = create_views(
    lineage=False, item_code=False, renamed_costs=False, soft_delete=False
)
```

- [ ] **Step 5: Stop the earlier migration naming a column it does not have**

`backend/alembic/versions/b78d71343405_...py:95` currently recreates the views from bare `CREATE_VIEWS`, which will now name `deleted_at` — a column that does not exist at that revision. Change that loop to:

```python
    for statement in create_views(soft_delete=False):
        op.execute(statement)
```

The import on line 43 already brings in `create_views`; drop `CREATE_VIEWS` from it if nothing else in the file uses it, so ruff does not flag an unused import.

- [ ] **Step 6: Write the migration**

```
cd backend && uv run alembic revision -m "add soft delete"
```

Autogenerate is **not** used here: it does not know the views must be dropped and recreated around the column. Write the body by hand:

```python
"""add soft delete

`deleted_at` on `inventory_item`, and `AND i.deleted_at IS NULL` in all four
views. A view stores its source columns by reference, so the views are dropped
first and recreated from the current definitions afterwards.

Revision ID: <generated>
Revises: <the item_field_review revision>

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from app.models.views import CREATE_VIEWS, DROP_VIEWS, create_views

revision: str = "<generated>"
down_revision: str | None = "<the item_field_review revision>"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    for statement in DROP_VIEWS:
        op.execute(statement)

    op.add_column(
        "inventory_item",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_inventory_item_deleted_at", "inventory_item", ["deleted_at"]
    )

    for statement in CREATE_VIEWS:
        op.execute(statement)


def downgrade() -> None:
    for statement in DROP_VIEWS:
        op.execute(statement)

    op.drop_index("ix_inventory_item_deleted_at", table_name="inventory_item")
    op.drop_column("inventory_item", "deleted_at")

    # The pre-soft-delete view text, so the downgrade leaves a consistent
    # database rather than four views naming a dropped column.
    for statement in create_views(soft_delete=False):
        op.execute(statement)
```

- [ ] **Step 7: Apply and verify nothing moved**

```
cd backend && uv run alembic upgrade head && uv run alembic check
cd backend && uv run python -c "
from sqlalchemy import create_engine, text
from app.config import settings
with create_engine(settings.database_url).connect() as c:
    print(c.execute(text('SELECT count(*), to_char(sum(total_cost), chr(70)||chr(77)||chr(57)||chr(57)||chr(57)||chr(44)||chr(57)||chr(57)||chr(57)||chr(46)||chr(48)||chr(48)) FROM inventory_item WHERE split_at IS NULL')).one())
"
```

Expected: `check` reports no new operations, and the count is `(7591, '534,177.89')` — unchanged.

- [ ] **Step 8: Run the tests**

```
uv run pytest backend/tests/test_schema.py backend/tests/test_migrations.py -v
```

Expected: all pass. `test_migrations_match_models` is what proves a fresh `upgrade head` still works — it is the test that catches a historical view naming a column added later.

- [ ] **Step 9: Commit**

```bash
git add backend/app/models/core.py backend/app/models/views.py \
        backend/alembic/versions/ backend/tests/test_schema.py
git commit -m "Add soft delete, and put it in all four views

deleted_at rather than a disposition value. Disposition records what happened
to a coin -- held, listed, sold, shipped -- and 'this row was created by
mistake' is not something that happened to a coin; putting it there would
corrupt every disposition report with rows that were never real.

A column also lets WHERE deleted_at IS NULL sit beside the split_at IS NULL
already in all four views: same shape of rule, same place. Hiding a row from
search alone would leave item_valuation still summing money for a coin the
collection does not hold.

b78d71343405 now recreates the views with soft_delete=False. It runs before
the column exists, and a view naming a column its revision does not have
fails a fresh upgrade head partway -- the same trap item_code set earlier.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: `PATCH /api/inventory/{id}`

The keystone. With 0 listings, this is what makes any of the 7,591 items editable at all.

**Files:**
- Modify: `backend/app/schemas.py`, `backend/app/routers/inventory.py`
- Test: `backend/tests/test_inventory_edit.py`

**Interfaces:**
- Consumes: `InventoryItem.version` (existing), `code_to_id` from `app.references` (existing).
- Produces: `InventoryItemUpdate` in `app.schemas`; `PATCH /api/inventory/{item_id}` returning `InventoryItemOut`. `InventoryItemOut` gains `version: int`. Tasks 8 and 14 depend on both.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_inventory_edit.py`:

```python
"""Editing an item that is not for sale.

Which is all of them: the collection has 7,591 items and no listings, so
before this endpoint existed there was no way to correct any of them through
the API.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def test_an_unlisted_item_can_be_edited(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db, source_title="wrong", year_start=1878)

    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "1881-S Morgan Silver Dollar", "year_start": 1881},
        headers=admin_headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["source_title"] == "1881-S Morgan Silver Dollar"
    assert body["year_start"] == 1881


def test_a_classifier_is_set_by_code(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Codes, never ids -- an id is meaningless to a client and unstable."""
    item = make_item(db)

    response = client.patch(
        f"/api/inventory/{item.id}", json={"grade": "MS63"}, headers=admin_headers
    )

    assert response.status_code == 200
    db.refresh(item)
    assert item.grade_id is not None


def test_an_unknown_code_is_refused_naming_the_field(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A silently null column is how 185 junk grades got in once already."""
    item = make_item(db)

    response = client.patch(
        f"/api/inventory/{item.id}", json={"grade": "NOT_A_GRADE"}, headers=admin_headers
    )

    assert response.status_code == 422
    assert "grade" in response.json()["detail"]


def test_a_stale_version_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Two staff, one loaded form each; the second must not silently win."""
    item = make_item(db)
    stale = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()[
        "version"
    ]

    first = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "careful", "version": stale},
        headers=admin_headers,
    )
    assert first.status_code == 200

    second = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "clobbering", "version": stale},
        headers=admin_headers,
    )
    assert second.status_code == 409

    db.refresh(item)
    assert item.source_title == "careful"


def test_omitting_the_version_edits_unconditionally(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A script that means 'set this regardless' can say so."""
    item = make_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "no version sent"},
        headers=admin_headers,
    )
    assert response.status_code == 200


def test_an_omitted_field_is_left_alone(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """exclude_unset, so a partial form does not null everything it omits."""
    item = make_item(db, source_title="keep me", year_start=1921)

    client.patch(
        f"/api/inventory/{item.id}", json={"year_start": 1922}, headers=admin_headers
    )

    db.refresh(item)
    assert item.source_title == "keep me"


def test_a_customer_cannot_edit_inventory(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    """Everything on this router exposes cost basis."""
    item = make_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"source_title": "nope"},
        headers=customer_headers,
    )
    assert response.status_code == 403


def test_money_survives_a_round_trip_as_a_string(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A Decimal that becomes a float has lost the guarantee it was for."""
    item = make_item(db)
    response = client.patch(
        f"/api/inventory/{item.id}",
        json={"item_cost": "19.99"},
        headers=admin_headers,
    )
    assert response.json()["item_cost"] == "19.99"
    db.refresh(item)
    assert item.item_cost == Decimal("19.99")
```

- [ ] **Step 2: Run the tests to verify they fail**

```
uv run pytest backend/tests/test_inventory_edit.py -v
```

Expected: all FAIL with 405 Method Not Allowed — the route does not exist.

- [ ] **Step 3: Add the schemas**

In `backend/app/schemas.py`, add `version: int` to `InventoryItemOut` immediately after `item_code`, and add this class directly below it:

```python
class InventoryItemUpdate(BaseModel):
    """A partial edit to an item, in the item's own vocabulary.

    Not `CatalogItemUpdate`. That one speaks the shop's language -- a listing
    has a `title` and a `price`, meaning what the shop calls the item and what
    it is offered for. This one speaks the item's: `source_title` is what the
    row was called where it came from, and `item_cost` is what was paid for
    it. The Excel round trip uses these names too, so there is one vocabulary
    at this boundary rather than two.

    Every field optional, and applied with `exclude_unset`, so an omitted
    field is left alone rather than nulled.
    """

    #: The version read before editing. Send it and a conflicting save is a
    #: 409 rather than a silent overwrite; omit it to mean "set this
    #: regardless", which a script may legitimately want.
    version: int | None = None

    source_title: str | None = Field(default=None, min_length=1, max_length=500)
    description: str | None = None
    year_start: int | None = Field(default=None, ge=-3000, le=2200)
    year_end: int | None = Field(default=None, ge=-3000, le=2200)
    fineness: Decimal | None = Field(default=None, ge=0, le=1, decimal_places=4)
    gross_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)
    fine_weight_ozt: Decimal | None = Field(default=None, ge=0, decimal_places=6)
    piece_count: int | None = Field(default=None, ge=1)
    item_cost: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )
    shipping_cost: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )

    # Classifiers, by code.
    item_kind: str | None = Field(default=None, max_length=64)
    country: str | None = Field(default=None, max_length=64)
    denomination: str | None = Field(default=None, max_length=64)
    bullion_form: str | None = Field(default=None, max_length=64)
    grade: str | None = Field(default=None, max_length=64)
    grade_designation: str | None = Field(default=None, max_length=64)
    grading_service: str | None = Field(default=None, max_length=64)
    metal: str | None = Field(default=None, max_length=64)
    series: str | None = Field(default=None, max_length=64)
    storage_form: str | None = Field(default=None, max_length=64)
    authenticity: str | None = Field(default=None, max_length=64)
    status: str | None = Field(default=None, max_length=64)
    disposition: str | None = Field(default=None, max_length=64)
```

- [ ] **Step 4: Add the endpoint**

In `backend/app/routers/inventory.py`, extend the imports:

```python
from ..models import (
    Authenticity,
    BullionForm,
    Country,
    Denomination,
    Disposition,
    Grade,
    GradeDesignation,
    GradingService,
    InventoryItem,
    ItemKind,
    ItemStatus,
    Metal,
    Series,
    StorageForm,
)
from ..schemas import (
    InventoryItemOut,
    InventoryItemUpdate,
    InventoryPageOut,
    SplitPieceIn,
    SplitRequest,
    SplitResultOut,
)
```

and add `from sqlalchemy.orm.exc import StaleDataError` alongside the existing SQLAlchemy imports.

Then, below `PIECE_CLASSIFIERS`, add the fuller map and the endpoint:

```python
#: Editable classifiers on an item, and where each code resolves.
#:
#: Wider than PIECE_CLASSIFIERS above, which covers only what a split may
#: override per piece. Everything here is a correction someone makes while
#: attributing an item in hand.
ITEM_CLASSIFIERS: dict[str, type] = {
    "item_kind": ItemKind,
    "country": Country,
    "denomination": Denomination,
    "bullion_form": BullionForm,
    "grade": Grade,
    "grade_designation": GradeDesignation,
    "grading_service": GradingService,
    "metal": Metal,
    "series": Series,
    "storage_form": StorageForm,
    "authenticity": Authenticity,
    "status": ItemStatus,
    "disposition": Disposition,
}

#: Plain columns a client may set. Named identically on the wire and in the
#: database, unlike the catalogue router's `ITEM_SCALARS` -- that one is a
#: *mapping*, because the shop says `title` and `price` where the item says
#: `source_title` and `item_cost`. This surface speaks the item's own
#: vocabulary throughout, the same names the Excel round trip uses, so no
#: translation is needed and a tuple is enough. Deliberately not called
#: ITEM_SCALARS: two things with one name in two routers is how the wrong one
#: gets imported.
EDITABLE_SCALARS: tuple[str, ...] = (
    "source_title",
    "description",
    "year_start",
    "year_end",
    "fineness",
    "gross_weight_ozt",
    "fine_weight_ozt",
    "piece_count",
    "item_cost",
    "shipping_cost",
)


@router.patch("/{item_id}", response_model=InventoryItemOut)
def update_item(
    item_id: int,
    payload: InventoryItemUpdate,
    db: DbSession,
    _admin: AdminUser,
) -> InventoryItem:
    """Correct an item. Send `version` to be told about conflicts.

    This is the editing path for the collection. `PATCH /api/catalog/{id}`
    needs a listing, and an item is owned long before it is offered and after
    it is sold -- most of this collection will never have a listing at all.
    """
    item = _get_item(db, item_id)

    # exclude_unset so an omitted field is left alone rather than nulled.
    data = payload.model_dump(exclude_unset=True)
    expected = data.pop("version", None)

    # Checked before anything is applied, so the caller gets a useful message.
    # The database check below is the real guarantee: it closes the gap
    # between this comparison and the commit.
    if expected is not None and expected != item.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{item.item_code} was changed by someone else (you have "
                f"version {expected}, current is {item.version}). Reload and "
                f"reapply your changes."
            ),
        )

    for field, model in ITEM_CLASSIFIERS.items():
        if field in data:
            value = data[field]
            setattr(
                item,
                f"{field}_id",
                None if value is None else code_to_id(db, model, value, field),
            )

    for field in EDITABLE_SCALARS:
        if field in data:
            setattr(item, field, data[field])

    try:
        db.commit()
    except StaleDataError as exc:
        # Someone committed between the check above and this one. The UPDATE
        # carried `WHERE version = ...`, matched no rows, and overwrote
        # nothing.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{item.item_code} was changed while saving. Reload and retry.",
        ) from exc

    db.refresh(item)
    return item
```

Note the route ordering: FastAPI matches in declaration order, and `/{view}/search` is declared before `/{item_id}`, so `PATCH /{item_id}` placed after them is unambiguous — no GET route shares its method.

- [ ] **Step 5: Run the tests to verify they pass**

```
uv run pytest backend/tests/test_inventory_edit.py -v
```

Expected: all 8 pass.

- [ ] **Step 6: Mutation-check the version guard**

Comment out the `if expected is not None and expected != item.version:` block and its `raise`, then:

```
uv run pytest backend/tests/test_inventory_edit.py -k stale -v
```

Expected: `test_a_stale_version_is_refused` FAILS. Restore the block and confirm it passes again. A guard whose test cannot fail is not a guard.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/inventory.py \
        backend/tests/test_inventory_edit.py
git commit -m "Let an item be edited without being for sale

Which is every item. PATCH /api/catalog/{listing_id} needs a listing, the
collection has 7,591 items and zero listings, and a freshly split piece is
held rather than listed -- so until now there was no way to correct anything
through the API.

Speaks the item's vocabulary rather than the shop's: source_title and
item_cost, not title and price. The Excel round trip uses the same names, so
there is one vocabulary at this boundary instead of two.

Optimistic against version, 409 on conflict, checked twice -- once before
applying so the message is useful, and once by the database at commit, which
is the guarantee. Mutation-checked: removing the first check fails the stale
test.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Recording a field review

**Files:**
- Modify: `backend/app/schemas.py`, `backend/app/routers/inventory.py`
- Test: `backend/tests/test_review.py`

**Interfaces:**
- Consumes: `ItemFieldReview` (Task 2), `_get_item` (existing).
- Produces: `ReviewRequest` and `ItemReviewOut` in `app.schemas`; `POST /api/inventory/{item_id}/reviewed` and `GET /api/inventory/{item_id}/reviewed`. `REVIEWABLE_FIELDS` in `routers/inventory.py`. Task 14 consumes both endpoints.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_review.py`:

```python
def test_confirming_a_field_records_who_and_when(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)

    response = client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id"]},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["reviewed"] == ["grade_id"]

    row = db.query(ItemFieldReview).one()
    assert row.field_name == "grade_id"
    assert row.reviewed_by_id is not None
    assert row.reviewed_at is not None


def test_confirming_twice_is_not_an_error(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Looking again and agreeing is the same fact, not a failure."""
    item = make_item(db)
    for _ in range(2):
        response = client.post(
            f"/api/inventory/{item.id}/reviewed",
            json={"fields": ["grade_id"]},
            headers=admin_headers,
        )
        assert response.status_code == 200

    assert db.query(ItemFieldReview).count() == 1


def test_a_field_nobody_reviews_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A typo must not become a review record nobody can query for."""
    item = make_item(db)
    response = client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade"]},
        headers=admin_headers,
    )
    assert response.status_code == 422
    assert "grade" in response.json()["detail"]


def test_unconfirming_removes_the_record(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Someone who realises they confirmed the wrong coin needs a way back."""
    item = make_item(db)
    client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id"]},
        headers=admin_headers,
    )

    response = client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": [], "replace": True},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["reviewed"] == []
    assert db.query(ItemFieldReview).count() == 0


def test_reading_back_what_has_been_reviewed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db)
    client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id", "year_start"]},
        headers=admin_headers,
    )

    body = client.get(
        f"/api/inventory/{item.id}/reviewed", headers=admin_headers
    ).json()
    assert sorted(body["reviewed"]) == ["grade_id", "year_start"]
```

Add `from fastapi.testclient import TestClient` to that file's imports.

- [ ] **Step 2: Run the tests to verify they fail**

```
uv run pytest backend/tests/test_review.py -v
```

Expected: the five new tests FAIL with 405; the three from Task 2 still pass.

- [ ] **Step 3: Add the schemas**

In `backend/app/schemas.py`, below `InventoryItemUpdate`:

```python
class ReviewRequest(BaseModel):
    """Which fields of an item a person has confirmed by looking at it."""

    #: Column names, e.g. `grade_id`. Checked against the reviewable set, so a
    #: typo is a 422 rather than a record nobody can ever query for.
    fields: list[str] = Field(default_factory=list)
    #: False adds to what is already recorded, which is the normal case --
    #: confirming the grade says nothing about the year. True makes the given
    #: list the whole truth, which is how a mistaken confirmation is undone.
    replace: bool = False


class ItemReviewOut(BaseModel):
    """Which fields of one item stand confirmed."""

    inventory_item_id: int
    reviewed: list[str]
```

- [ ] **Step 4: Add the endpoints**

In `backend/app/routers/inventory.py`, add `ItemFieldReview` to the `..models` import and `ItemReviewOut, ReviewRequest` to the `..schemas` import, then append:

```python
#: Fields worth confirming by examination.
#:
#: A whitelist rather than "any column": a review of `created_at` means
#: nothing, and a typo that became a record would be a row nobody can ever
#: query for. Held here rather than as a check constraint because which
#: fields are worth confirming is a product decision that will change, and a
#: constraint would need a migration every time it did.
REVIEWABLE_FIELDS: frozenset[str] = frozenset(
    {
        "year_start",
        "year_end",
        "grade_id",
        "grade_designation_id",
        "grading_service_id",
        "denomination_id",
        "country_id",
        "metal_id",
        "series_id",
        "fineness",
        "fine_weight_ozt",
        "gross_weight_ozt",
        "piece_count",
        "mint_id",
        "variety",
        "serial_number",
        "series_year",
        "series_letter",
        "seal_color_id",
        "fed_district_id",
        "friedberg_id",
    }
)


def _reviewed_fields(db: Session, item_id: int) -> list[str]:
    return sorted(
        db.scalars(
            select(ItemFieldReview.field_name).where(
                ItemFieldReview.inventory_item_id == item_id
            )
        ).all()
    )


@router.get("/{item_id}/reviewed", response_model=ItemReviewOut)
def get_item_review(item_id: int, db: DbSession, _admin: AdminUser) -> ItemReviewOut:
    """Which of this item's fields a person has confirmed."""
    item = _get_item(db, item_id)
    return ItemReviewOut(
        inventory_item_id=item.id, reviewed=_reviewed_fields(db, item.id)
    )


@router.post("/{item_id}/reviewed", response_model=ItemReviewOut)
def set_item_review(
    item_id: int,
    payload: ReviewRequest,
    db: DbSession,
    admin: AdminUser,
) -> ItemReviewOut:
    """Record that a person has confirmed these fields by examination.

    Idempotent: confirming a field twice is the same fact, not an error.
    Looking at a coin again and agreeing with yourself should not be a 409.
    """
    item = _get_item(db, item_id)

    unknown = sorted(set(payload.fields) - REVIEWABLE_FIELDS)
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"Not reviewable: {unknown}. Available: {sorted(REVIEWABLE_FIELDS)}",
        )

    existing = set(_reviewed_fields(db, item.id))
    wanted = set(payload.fields)

    if payload.replace:
        for gone in existing - wanted:
            db.execute(
                delete(ItemFieldReview).where(
                    ItemFieldReview.inventory_item_id == item.id,
                    ItemFieldReview.field_name == gone,
                )
            )

    for name in wanted - existing:
        db.add(
            ItemFieldReview(
                inventory_item_id=item.id,
                field_name=name,
                reviewed_by_id=admin.id,
            )
        )

    db.commit()
    return ItemReviewOut(
        inventory_item_id=item.id, reviewed=_reviewed_fields(db, item.id)
    )
```

Change the SQLAlchemy import line to `from sqlalchemy import delete, select`.

- [ ] **Step 5: Run the tests to verify they pass**

```
uv run pytest backend/tests/test_review.py -v
```

Expected: all 8 pass.

- [ ] **Step 6: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/inventory.py \
        backend/tests/test_review.py
git commit -m "Record and read back which fields have been confirmed

Idempotent: confirming a field twice is the same fact, not an error. Looking
at a coin again and agreeing with yourself should not be a 409.

The reviewable set is a whitelist held in the router rather than a check
constraint. A review of created_at means nothing, a typo that became a record
would be a row nobody can query for, and which fields are worth confirming is
a product decision that will change -- a constraint would need a migration
every time it did.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Soft delete through the API

**Files:**
- Modify: `backend/app/routers/inventory.py`, `backend/app/inventory_search.py`
- Test: `backend/tests/test_inventory_delete.py`

**Interfaces:**
- Consumes: `InventoryItem.deleted_at` (Task 3).
- Produces: `DELETE /api/inventory/{item_id}`; `deleted=no|only|any` and `lot=CC-004120` query parameters on `/{view}/search`; `DELETED_MODES` in `inventory_search.py`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_inventory_delete.py`:

```python
"""Deleting a row that should never have existed.

Guarded, because the two ways it goes wrong are both silent: deleting a lot
whose pieces then reference nothing, and deleting something a customer has
already bought.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import build_listing
from tests.test_schema import make_item
from tests.test_split import TUBE, do_split, lot


def test_a_deleted_item_leaves_search(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = make_item(db, source_title="MISTAKE")

    assert client.delete(
        f"/api/inventory/{item.id}", headers=admin_headers
    ).status_code == 204

    rows = client.get(
        "/api/inventory/coins/search?q=MISTAKE", headers=admin_headers
    ).json()["rows"]
    assert rows == []


def test_a_deleted_item_is_findable_when_asked_for(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Otherwise a mistaken delete is unrecoverable through the UI."""
    item = make_item(db, source_title="MISTAKE")
    client.delete(f"/api/inventory/{item.id}", headers=admin_headers)

    only = client.get(
        "/api/inventory/coins/search?q=MISTAKE&deleted=only", headers=admin_headers
    ).json()
    assert [r["id"] for r in only["rows"]] == [item.id]

    both = client.get(
        "/api/inventory/coins/search?q=MISTAKE&deleted=any", headers=admin_headers
    ).json()
    assert both["total"] == 1


def test_an_unrecognised_deleted_mode_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Consistent with every other filter: never silently ignored."""
    response = client.get(
        "/api/inventory/coins/search?deleted=maybe", headers=admin_headers
    )
    assert response.status_code == 422


def test_the_lot_filter_finds_a_lot_s_pieces(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """"Show me everything from that tube" is the review queue's entry point.

    By item code rather than id: the code is what is printed on the flip and
    what a person has in front of them.
    """
    parent = lot(db)
    pieces = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"]
    make_item(db, source_title="unrelated")

    body = client.get(
        f"/api/inventory/coins/search?lot={parent.item_code}", headers=admin_headers
    ).json()

    assert sorted(r["id"] for r in body["rows"]) == sorted(p["id"] for p in pieces)


def test_an_unknown_lot_code_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Returning nothing would read as 'that lot has no pieces', which is a
    different and much more alarming answer than 'no such lot'."""
    response = client.get(
        "/api/inventory/coins/search?lot=CC-999999", headers=admin_headers
    )
    assert response.status_code == 422
    assert "CC-999999" in response.json()["detail"]


def test_a_lot_with_pieces_cannot_be_deleted(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Its pieces hold cost basis allocated from it and would be orphaned."""
    parent = lot(db)
    do_split(client, admin_headers, parent.id, TUBE)

    response = client.delete(f"/api/inventory/{parent.id}", headers=admin_headers)
    assert response.status_code == 409
    assert "piece" in response.json()["detail"].lower()


def test_a_listed_item_cannot_be_deleted(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A listing is what an order line points at, through to the sale.

    Guarding on the listing rather than on the order is the wider net and the
    cheaper query: an item cannot reach an order without one.
    """
    listing = build_listing(db)
    response = client.delete(
        f"/api/inventory/{listing.inventory_item_id}", headers=admin_headers
    )
    assert response.status_code == 409
    assert "listing" in response.json()["detail"].lower()
```

- [ ] **Step 2: Run the tests to verify they fail**

```
uv run pytest backend/tests/test_inventory_delete.py -v
```

Expected: all FAIL — 405 on the DELETE tests, and the `deleted=` tests 422 with "Unknown filter 'deleted'".

- [ ] **Step 3: Handle `deleted` in the search**

In `backend/app/inventory_search.py`, add near the top of the module below the dataclasses:

```python
#: How the search treats soft-deleted rows. Not a `Filt`, because it is a
#: choice between three predicates rather than a value to compare against.
DELETED_MODES: dict[str, str] = {
    "no": "i.deleted_at IS NULL",
    "only": "i.deleted_at IS NOT NULL",
    "any": "TRUE",
}
```

In `_conditions`, replace the opening lines with:

```python
    clauses = list(spec.where)
    joins: list[tuple[str, ...]] = [(_J_KIND,)]  # every spec filters on kind
    bound: dict[str, Any] = {}

    # Consumed here rather than in spec.filters: it selects between three
    # predicates rather than comparing a column to a value. Excluded by
    # default, so a deleted row does not reappear because someone forgot.
    params = dict(params)
    mode = params.pop("deleted", None) or "no"
    if mode not in DELETED_MODES:
        raise ValueError(
            f"unknown deleted mode {mode!r}; expected one of {sorted(DELETED_MODES)}."
        )
    clauses.append(DELETED_MODES[mode])

    # Everything split from one lot, named by the lot's item code -- what is
    # printed on the flip and what a person has in front of them, rather than
    # a database id they would have to look up.
    lot_code = params.pop("lot", None)
    if lot_code:
        clauses.append(
            "i.parent_item_id = (SELECT id FROM inventory_item WHERE item_code = :p_lot)"
        )
        bound["p_lot"] = lot_code
```

An item code that names nothing makes the subquery `NULL` and the filter match no rows, which reads as "that lot has no pieces" -- a different and much more alarming answer than "no such lot". So the router checks it exists; see Step 4b.

The `raise ValueError` is already caught by the router and turned into a 422 — but its message appends "Sortable: ..." which would be misleading here. Fix the router in the next step.

Also add `"deleted"` to the `reserved` set in `search_inventory`? **No** — it must reach `params` so `_conditions` can read it. Leave `reserved` alone.

`count_facets` calls `_conditions` too, so it inherits the same handling with no change.

- [ ] **Step 4: Refuse an item code that names nothing**

In `backend/app/routers/inventory.py`, inside `search_inventory`, immediately after the `params` dict is built:

```python
    lot_code = params.get("lot")
    if lot_code and not db.scalar(
        select(InventoryItem.id).where(InventoryItem.item_code == lot_code)
    ):
        raise HTTPException(
            status_code=422,
            detail=f"No item has code {lot_code!r}.",
        )
```

Checked in the router rather than in `_conditions` because it is a lookup, and `_conditions` builds SQL without running any.

- [ ] **Step 4b: Separate the two 422 messages**

In `backend/app/routers/inventory.py`, the current `except ValueError` block appends the sortable list to every `ValueError`. Change it to:

```python
    except ValueError as exc:
        # Two different failures reach here: an unsortable column, and an
        # unrecognised `deleted` mode. Appending the sortable list to both
        # sends the wrong person looking in the wrong place.
        hint = (
            f" Sortable: {sorted(spec.sortable)}"
            if "sort" in str(exc)
            else ""
        )
        raise HTTPException(status_code=422, detail=f"{exc}{hint}") from exc
```

and change the `search` function's sort error to say the word: `raise ValueError(f"cannot sort by {sort_key!r}.")` already contains "sort", so no change is needed there.

- [ ] **Step 5: Add the delete endpoint**

In `backend/app/routers/inventory.py`, add `Listing` and `SalesOrderItem` to the `..models` import and `from datetime import UTC, datetime` at the top, then append:

```python
@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_item(item_id: int, db: DbSession, _admin: AdminUser) -> None:
    """Soft delete: this row should never have existed.

    Guarded twice, because both failures are silent. A lot with pieces holds
    the cost basis they were allocated from, and deleting it would leave four
    coins descended from nothing. An item that has been listed or sold is
    referenced by order history, which would then point at a row the reports
    exclude.

    **Reachable from the lot panel, not only from search.** After its last
    child is detached a parent still has `split_at` set, so it is invisible in
    every view and not deleted -- a row that exists and cannot be found.
    Search is exactly where it is not.
    """
    item = _get_item(db, item_id)

    if item.deleted_at is not None:
        return  # Already gone. Deleting twice is not an error.

    pieces = db.scalar(
        select(func.count())
        .select_from(InventoryItem)
        .where(InventoryItem.parent_item_id == item.id)
    )
    if pieces:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{item.item_code} has {pieces} piece(s) split from it and "
                f"cannot be deleted. Detach them first."
            ),
        )

    listed = db.scalar(
        select(Listing.id).where(Listing.inventory_item_id == item.id).limit(1)
    )
    if listed is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"{item.item_code} has a listing and cannot be deleted. "
                f"Withdraw the listing first."
            ),
        )

    item.deleted_at = datetime.now(UTC)
    db.commit()
```

Add `func` to the SQLAlchemy import: `from sqlalchemy import delete, func, select`.

- [ ] **Step 6: Run the tests to verify they pass**

```
uv run pytest backend/tests/test_inventory_delete.py backend/tests/test_inventory_search.py -v
```

Expected: all pass, including the existing search tests — the default `deleted=no` must not change any of them. Add `from tests.test_schema import make_item` to the delete test file's imports for the `lot` filter test.

- [ ] **Step 7: Mutation-check both guards**

Comment out the `if pieces:` block, run `-k lot_with_pieces`, confirm FAIL, restore, confirm PASS. Repeat for the `if listed is not None:` block against `-k listed_item`. Both must fail *for the stated reason* rather than incidentally.

- [ ] **Step 8: Commit**

```bash
git add backend/app/routers/inventory.py backend/app/inventory_search.py \
        backend/tests/test_inventory_delete.py
git commit -m "Soft delete an item, guarded, and let search see deleted rows

Excluded by default, findable with deleted=only or deleted=any, and an
unrecognised mode is a 422 -- consistent with every other filter here, because
a silently dropped one returns the whole collection and looks like a result.

Two guards, both mutation-checked, because both failures are silent. A lot
with pieces holds the cost basis they were allocated from, and deleting it
would leave four coins descended from nothing. An item with a listing is
referenced by order history, which would point at a row the reports exclude.

The 422 for an unsortable column and the 422 for a bad deleted mode no longer
share a message: appending the sortable list to both sends the wrong person
looking in the wrong place.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Detach a piece from its lot

**Files:**
- Modify: `backend/app/routers/inventory.py`
- Test: `backend/tests/test_inventory_delete.py`

**Interfaces:**
- Produces: `DELETE /api/inventory/{item_id}/parent`, returning `InventoryItemOut`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_inventory_delete.py`:

```python
def test_detaching_leaves_a_standalone_item(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """No parent is the normal state, not an orphan.

    7,591 of 7,591 items have none, so nothing may treat a null parent as a
    problem to be repaired.
    """
    parent = lot(db)
    pieces = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"]
    child_id = pieces[0]["id"]

    response = client.delete(
        f"/api/inventory/{child_id}/parent", headers=admin_headers
    )

    assert response.status_code == 200
    assert response.json()["parent_item_id"] is None


def test_detaching_does_not_move_money(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A detached piece keeps the cost it was allocated."""
    parent = lot(db)
    pieces = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"]
    before = pieces[0]["item_cost"]

    body = client.delete(
        f"/api/inventory/{pieces[0]['id']}/parent", headers=admin_headers
    ).json()

    assert body["item_cost"] == before


def test_detaching_an_item_with_no_parent_is_harmless(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Idempotent, because the end state is what was asked for."""
    item = make_item(db)
    response = client.delete(f"/api/inventory/{item.id}/parent", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["parent_item_id"] is None


def test_a_lot_can_be_deleted_once_its_last_piece_is_detached(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The round trip the guard in Task 6 would otherwise make impossible."""
    parent = lot(db)
    pieces = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"]

    for piece in pieces:
        client.delete(f"/api/inventory/{piece['id']}/parent", headers=admin_headers)

    assert client.delete(
        f"/api/inventory/{parent.id}", headers=admin_headers
    ).status_code == 204
```

- [ ] **Step 2: Run the tests to verify they fail**

```
uv run pytest backend/tests/test_inventory_delete.py -k detach -v
```

Expected: FAIL with 405.

- [ ] **Step 3: Add the endpoint**

Append to `backend/app/routers/inventory.py`:

```python
@router.delete("/{item_id}/parent", response_model=InventoryItemOut)
def detach_item(item_id: int, db: DbSession, _admin: AdminUser) -> InventoryItem:
    """Set an item's `parent_item_id` back to null.

    An item with no parent is complete, not orphaned -- 7,591 of 7,591 have
    none. So this moves nothing and repairs nothing: the piece keeps the cost
    it was allocated, and simply stops recording where it came from.

    Idempotent, because the end state is exactly what was asked for.
    """
    item = _get_item(db, item_id)
    item.parent_item_id = None
    db.commit()
    db.refresh(item)
    return item
```

- [ ] **Step 4: Run the tests to verify they pass**

```
uv run pytest backend/tests/test_inventory_delete.py -v
```

Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add backend/app/routers/inventory.py backend/tests/test_inventory_delete.py
git commit -m "Detach a piece from its lot

An item with no parent is complete, not orphaned -- 7,591 of 7,591 have none,
so nothing may treat a null parent as damage to repair. Detaching moves no
money and creates no row: the piece keeps the cost it was allocated and stops
recording where it came from.

Idempotent, because the end state is what was asked for. This is also the way
out of the delete guard: a lot becomes deletable once its last piece is
detached.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Bulk edit

**Files:**
- Modify: `backend/app/schemas.py`, `backend/app/routers/inventory.py`
- Test: `backend/tests/test_inventory_bulk.py`

**Interfaces:**
- Consumes: `ITEM_CLASSIFIERS`, `EDITABLE_SCALARS`, `InventoryItemUpdate` (Task 4).
- Produces: `BulkEditRequest` in `app.schemas`; `POST /api/inventory/bulk` returning `{"updated": int}`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_inventory_bulk.py`:

```python
"""Setting one field across many items at once.

All-or-nothing in one transaction. A partial bulk edit across 50 coins leaves
a state nobody can describe, and "which of the 50 applied?" is not a question
the UI should ever have to answer.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def test_a_field_is_set_across_every_selected_item(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    items = [make_item(db, year_start=None) for _ in range(3)]

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [i.id for i in items], "changes": {"year_start": 1964}},
        headers=admin_headers,
    )

    assert response.status_code == 200
    assert response.json()["updated"] == 3
    for item in items:
        db.refresh(item)
        assert item.year_start == 1964


def test_an_unselected_item_is_untouched(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    chosen = make_item(db, year_start=1878)
    other = make_item(db, year_start=1921)

    client.post(
        "/api/inventory/bulk",
        json={"ids": [chosen.id], "changes": {"year_start": 1964}},
        headers=admin_headers,
    )

    db.refresh(other)
    assert other.year_start == 1921


def test_one_bad_id_changes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """All-or-nothing. A half-applied bulk edit is unreportable."""
    items = [make_item(db, year_start=1878) for _ in range(3)]

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [i.id for i in items] + [999999], "changes": {"year_start": 1964}},
        headers=admin_headers,
    )

    assert response.status_code == 404
    for item in items:
        db.refresh(item)
        assert item.year_start == 1878, "a rejected bulk edit must apply nothing"


def test_one_bad_code_changes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    items = [make_item(db, year_start=1878) for _ in range(3)]

    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [i.id for i in items], "changes": {"grade": "NOT_A_GRADE"}},
        headers=admin_headers,
    )

    assert response.status_code == 422
    for item in items:
        db.refresh(item)
        assert item.year_start == 1878


def test_bulk_refuses_an_empty_selection(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """"Apply to nothing" is far more likely a lost selection than an intent."""
    response = client.post(
        "/api/inventory/bulk",
        json={"ids": [], "changes": {"year_start": 1964}},
        headers=admin_headers,
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

```
uv run pytest backend/tests/test_inventory_bulk.py -v
```

Expected: FAIL — `POST /api/inventory/bulk` matches no route.

- [ ] **Step 3: Add the schema**

In `backend/app/schemas.py`, below `InventoryItemUpdate`:

```python
class BulkEditRequest(BaseModel):
    """One set of changes, applied to many items in one transaction.

    `changes` is validated as an `InventoryItemUpdate`, so bulk and single
    edits accept exactly the same fields and the same codes. Two field lists
    would drift.
    """

    #: At least one. "Apply to nothing" is far more likely a selection that
    #: was lost than something anyone meant.
    ids: list[int] = Field(min_length=1)
    changes: InventoryItemUpdate
```

- [ ] **Step 4: Add the endpoint**

In `backend/app/routers/inventory.py`, add `BulkEditRequest` to the `..schemas` import. Add this endpoint **above** `PATCH /{item_id}`, so the literal path segment is matched before the parameterised one:

```python
@router.post("/bulk")
def bulk_edit(
    payload: BulkEditRequest, db: DbSession, _admin: AdminUser
) -> dict[str, int]:
    """Set the same fields across many items, all or nothing.

    One transaction on purpose. A partial bulk edit across 50 coins leaves a
    state nobody can describe, and "which of the 50 applied?" is not a
    question the UI should ever have to answer -- so every id is resolved and
    every code checked before anything is written.
    """
    data = payload.changes.model_dump(exclude_unset=True)
    data.pop("version", None)  # Meaningless across a set of rows.

    items = db.scalars(
        select(InventoryItem).where(InventoryItem.id.in_(payload.ids))
    ).all()
    found = {item.id for item in items}
    missing = sorted(set(payload.ids) - found)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No such item(s): {missing}. Nothing was changed.",
        )

    # Every code resolved before anything is set, so a typo in the last field
    # does not leave the first three applied.
    resolved: dict[str, object] = {}
    for field, model in ITEM_CLASSIFIERS.items():
        if field in data:
            value = data[field]
            resolved[f"{field}_id"] = (
                None if value is None else code_to_id(db, model, value, field)
            )
    for field in EDITABLE_SCALARS:
        if field in data:
            resolved[field] = data[field]

    for item in items:
        for column, value in resolved.items():
            setattr(item, column, value)

    db.commit()
    return {"updated": len(items)}
```

`code_to_id` raises the 422 naming the field before any `setattr` runs, which is what makes the bad-code test pass without a rollback.

- [ ] **Step 5: Run the tests to verify they pass**

```
uv run pytest backend/tests/test_inventory_bulk.py -v
```

Expected: all 5 pass.

- [ ] **Step 6: Verify route ordering did not break the split endpoint**

```
uv run pytest backend/tests/test_split.py backend/tests/test_inventory_edit.py -v
```

Expected: all pass. If `POST /bulk` had been declared after `POST /{item_id}/split`, FastAPI would still match correctly here (different shapes), but the literal-before-parameter habit is what keeps that true as routes are added.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/inventory.py \
        backend/tests/test_inventory_bulk.py
git commit -m "Set fields across a selection, all or nothing

One transaction. A partial bulk edit across 50 coins leaves a state nobody can
describe, and 'which of the 50 applied?' is not a question the UI should ever
have to answer -- so every id is resolved and every code checked before
anything is written.

changes is validated as an InventoryItemUpdate, so bulk and single edits take
the same fields and the same codes rather than two lists that drift.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Named diagnostics

**Files:**
- Create: `backend/app/issues.py`
- Modify: `backend/app/inventory_search.py`, `backend/app/routers/inventory.py`, `backend/app/schemas.py`
- Test: `backend/tests/test_issues.py`

**Interfaces:**
- Consumes: `ItemFieldReview` (Task 2), `deleted_at` (Task 3).
- Produces: `Issue` dataclass and `COIN_ISSUES` / `CURRENCY_ISSUES` in `app.issues`; `ViewSpec.issues`; `UnknownIssue`; `count_issues(db, spec, *, params, query)`; an `issue=` filter and an `issues` key on `InventoryPageOut`.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_issues.py`:

```python
"""Anomalies as named, kind-aware checks rather than generic field filters.

`issue=no_grade` means *a coin or banknote with no grade*. Bullion has no
grade by nature and 712 rounds have no weight either, so a generic grade=null
would bury 2,965 real cases under rounds that will never have one. The domain
knowledge belongs in the check, defined once, rather than in the head of
whoever types the filter.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import ItemKind
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.test_schema import code_id, make_item


def search(client: TestClient, headers: dict[str, str], query: str) -> dict:
    return client.get(f"/api/inventory/coins/search?{query}", headers=headers).json()


def test_a_named_check_finds_only_its_own_anomaly(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    missing = make_item(db, year_start=None)
    make_item(db, year_start=1881)

    rows = search(client, admin_headers, "issue=no_year")["rows"]
    assert [r["id"] for r in rows] == [missing.id]


def test_no_grade_ignores_bullion(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A round will never have a grade; counting it as an anomaly buries the
    2,965 coins that should have one."""
    coin = make_item(db, grade_id=None)
    make_item(db, grade_id=None, item_kind_id=code_id(db, ItemKind, "bullion"))

    rows = search(client, admin_headers, "issue=no_grade")["rows"]
    assert [r["id"] for r in rows] == [coin.id]


def test_no_weight_applies_only_to_bullion(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """The mirror image: a Morgan's weight is not the interesting gap."""
    round_ = make_item(
        db, fine_weight_ozt=None, item_kind_id=code_id(db, ItemKind, "bullion")
    )
    make_item(db, fine_weight_ozt=None)

    rows = search(client, admin_headers, "issue=no_weight_bullion")["rows"]
    assert [r["id"] for r in rows] == [round_.id]


def test_zero_cost_catches_null_and_zero_alike(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    zero = make_item(db, item_cost=Decimal("0.00"))
    make_item(db, item_cost=Decimal("19.99"))

    rows = search(client, admin_headers, "issue=zero_cost")["rows"]
    assert [r["id"] for r in rows] == [zero.id]


def test_unreviewed_is_the_default_state(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Absent means unconfirmed, which is right for every imported item."""
    item = make_item(db)
    assert search(client, admin_headers, "issue=unreviewed")["total"] == 1

    client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id"]},
        headers=admin_headers,
    )
    assert search(client, admin_headers, "issue=unreviewed")["total"] == 0


def test_a_near_duplicate_serial_is_found_within_one_order(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Exact matching misses `O` for `U`, a dropped digit, `6` for `3`.

    Scoped to one purchase order because that is where mistranscriptions
    cluster, and because two unrelated notes one digit apart are two unrelated
    notes.
    """
    from app.models import CurrencyDetail, PurchaseOrder, Vendor

    vendor = Vendor(name="test-vendor")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id, order_number="X1")
    db.add(order)
    db.flush()

    kind = code_id(db, ItemKind, "currency")
    pair = [
        make_item(db, item_kind_id=kind, purchase_order_id=order.id) for _ in range(2)
    ]
    apart = make_item(db, item_kind_id=kind, purchase_order_id=order.id)
    db.add_all(
        [
            CurrencyDetail(inventory_item_id=pair[0].id, serial_number="B08084501A"),
            CurrencyDetail(inventory_item_id=pair[1].id, serial_number="B08084S01A"),
            CurrencyDetail(inventory_item_id=apart.id, serial_number="Z99999999A"),
        ]
    )
    db.commit()

    body = client.get(
        "/api/inventory/currency/search?issue=near_duplicate_serial",
        headers=admin_headers,
    ).json()

    assert sorted(r["id"] for r in body["rows"]) == sorted(i.id for i in pair)


def test_an_unknown_issue_is_refused_listing_the_known_ones(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Silently ignoring it returns the whole collection as a clean bill."""
    response = client.get(
        "/api/inventory/coins/search?issue=no_such_check", headers=admin_headers
    )
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert "no_such_check" in detail
    assert "no_year" in detail


def test_issue_counts_come_back_with_the_page(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """So the size of a job is visible before committing to it."""
    make_item(db, year_start=None)
    make_item(db, year_start=None, country_id=None)

    body = client.get(
        "/api/inventory/coins/search?facets=true", headers=admin_headers
    ).json()

    assert body["issues"]["no_year"] == 2
    assert body["issues"]["no_country"] == 1


def test_issue_counts_ignore_the_issue_filter_itself(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Otherwise every count but the selected one collapses, and the panel
    stops being a way to choose the next job."""
    make_item(db, year_start=None)
    make_item(db, country_id=None)

    body = client.get(
        "/api/inventory/coins/search?issue=no_year&facets=true", headers=admin_headers
    ).json()

    assert body["total"] == 1
    assert body["issues"]["no_country"] == 1


def test_a_currency_only_check_is_not_offered_to_coins(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A star note check over the coin view would be a 422, not empty."""
    response = client.get(
        "/api/inventory/coins/search?issue=star_mismatch", headers=admin_headers
    )
    assert response.status_code == 422
```

- [ ] **Step 2: Run the tests to verify they fail**

```
uv run pytest backend/tests/test_issues.py -v
```

Expected: all FAIL — `Unknown filter 'issue'`, and `KeyError: 'issues'`.

- [ ] **Step 3: Write the issue definitions**

Create `backend/app/issues.py`:

```python
"""Named, kind-aware diagnostics over the inventory.

An anomaly is a check with a name, not a generic field filter.
`issue=no_grade` means *a coin or banknote with no grade*, because bullion has
no grade by nature and 712 rounds have no weight either -- a generic
`grade=null` would bury 2,965 real cases under rounds that will never have
one. The domain knowledge belongs here, written once, rather than in the head
of whoever types the filter.

Each check appears three ways from this one definition: a filter
(`?issue=no_year`), a count returned with the page so the size of a job is
visible before committing to it, and a badge on the row. Adding a check later
means adding one entry.
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["COIN_ISSUES", "CURRENCY_ISSUES", "Issue", "SHARED_ISSUES"]


@dataclass(frozen=True)
class Issue:
    """One diagnostic: the predicate that finds it, and the joins it needs.

    `sql` is parenthesised by the caller before being ANDed with the rest of
    the query, so a predicate containing OR is safe to write here plainly.
    """

    sql: str
    join: tuple[str, ...] = ()
    #: What the check means, for the filter panel. Not a label for the code:
    #: the code is the stable contract and appears in bookmarked URLs.
    description: str = ""


#: Joins duplicated from inventory_search rather than imported, to keep the
#: import one-way: the search module owns the query, this module only supplies
#: predicates. The strings must match exactly, since deduplication compares
#: whole clauses.
_J_CUR_DETAIL = "LEFT JOIN currency_detail cud ON cud.inventory_item_id = i.id"


SHARED_ISSUES: dict[str, Issue] = {
    "no_year": Issue(
        "i.year_start IS NULL",
        description="No year recorded",
    ),
    "no_country": Issue(
        "i.country_id IS NULL",
        description="No country recorded",
    ),
    "no_grade": Issue(
        "i.grade_id IS NULL AND k.code IN ('coin', 'currency')",
        description="A coin or banknote with no grade; bullion is excluded",
    ),
    "no_denomination": Issue(
        "i.denomination_id IS NULL AND k.code IN ('coin', 'currency')",
        description="A coin or banknote with no denomination",
    ),
    "kind_unknown": Issue(
        "k.code = 'unknown'",
        description="The import could not classify it",
    ),
    "zero_cost": Issue(
        "coalesce(i.item_cost, 0) = 0",
        description="No cost recorded, or zero",
    ),
    "mixed_marker": Issue(
        "i.grade_raw ILIKE '%mixed%' OR i.description ILIKE '%mixed%'",
        # Deliberately not folded into no_grade. `Mixed` means "known to
        # vary", which is a positive statement that the row stands for several
        # different coins -- so the remedy is to decompose the lot, not to
        # fill in the field.
        description="Known to vary: the row stands for several different items",
    ),
    "unreviewed": Issue(
        "NOT EXISTS (SELECT 1 FROM item_field_review r "
        "WHERE r.inventory_item_id = i.id)",
        description="Nobody has confirmed any field by examination",
    ),
}


COIN_ISSUES: dict[str, Issue] = {
    **SHARED_ISSUES,
    "no_weight_bullion": Issue(
        "i.fine_weight_ozt IS NULL AND k.code = 'bullion'",
        description="Bullion with no weight; its value cannot be computed",
    ),
    "repeated_identity": Issue(
        "EXISTS (SELECT 1 FROM item_certification c "
        "WHERE c.inventory_item_id = i.id AND c.cert_number IN ("
        "SELECT cert_number FROM item_certification "
        "WHERE coalesce(cert_number, '') <> '' "
        "GROUP BY cert_number HAVING count(*) > 1))",
        # Candidates only, never merged. Three different problems look alike
        # here -- the same item entered twice, one purchase recorded twice,
        # and a year parsed into the cert field -- and only the shape of the
        # value tells them apart, so a person decides.
        description="A certification number that appears on more than one row",
    ),
}


CURRENCY_ISSUES: dict[str, Issue] = {
    **SHARED_ISSUES,
    "star_mismatch": Issue(
        "coalesce(cud.serial_number LIKE '*%' OR cud.serial_number LIKE '%*', false) "
        "<> EXISTS (SELECT 1 FROM item_note_attribute x "
        "JOIN note_attribute na ON na.id = x.note_attribute_id "
        "WHERE x.inventory_item_id = i.id AND na.code = 'star')",
        join=(_J_CUR_DETAIL,),
        # Visible only as a disagreement between two fields, which is what
        # makes it valuable: neither field looks wrong alone. Star notes carry
        # a premium, so a wrong attribute either overprices a note or sells a
        # star note as an ordinary one.
        description="The serial and the star attribute disagree",
    ),
    "malformed_serial": Issue(
        "cud.serial_number ~ '[0-9][A-Z][0-9]'",
        join=(_J_CUR_DETAIL,),
        # A warning, never a refusal. Three of the first four serials flagged
        # by this rule were valid notes it had not anticipated.
        description="An interior letter in the serial; usually a typo, sometimes real",
    ),
    "repeated_identity": Issue(
        "cud.serial_number IN (SELECT serial_number FROM currency_detail "
        "WHERE coalesce(serial_number, '') <> '' "
        "GROUP BY serial_number HAVING count(*) > 1)",
        join=(_J_CUR_DETAIL,),
        # Four of the nine groups in this collection are legitimate: matched
        # serials across issues are a deliberate pursuit. Candidates, not
        # errors.
        description="A serial that appears on more than one note",
    ),
    "near_duplicate_serial": Issue(
        # Exact matching is not enough. Three duplicates in this collection
        # hide behind single-character errors -- `O` for `U`, a dropped digit,
        # `6` for `3` -- and are invisible to equality.
        #
        # Scoped to one purchase order, which is both where they cluster and
        # what keeps this affordable: the comparison is quadratic within a
        # group and the largest order holds 85 items. Collection-wide it would
        # be 1,015 x 1,015 and would find mostly noise, because two unrelated
        # notes differing by one digit are two unrelated notes.
        "EXISTS (SELECT 1 FROM currency_detail o "
        "JOIN inventory_item oi ON oi.id = o.inventory_item_id "
        "WHERE oi.id <> i.id "
        "AND oi.purchase_order_id = i.purchase_order_id "
        "AND i.purchase_order_id IS NOT NULL "
        "AND coalesce(o.serial_number, '') <> '' "
        "AND coalesce(cud.serial_number, '') <> '' "
        "AND levenshtein("
        "upper(regexp_replace(o.serial_number, '[^A-Za-z0-9]', '', 'g')), "
        "upper(regexp_replace(cud.serial_number, '[^A-Za-z0-9]', '', 'g'))"
        ") = 1)",
        join=(_J_CUR_DETAIL,),
        description=(
            "A serial one character from another in the same order; "
            "usually a mistranscription"
        ),
    ),
}
```

- [ ] **Step 3b: Enable `fuzzystrmatch`**

`near_duplicate_serial` uses `levenshtein`, which lives in the `fuzzystrmatch` contrib module. It is available in this installation and not yet installed. Give it its own migration, so the dependency is recorded rather than assumed:

```
cd backend && uv run alembic revision -m "enable fuzzystrmatch for near-duplicate detection"
```

```python
def upgrade() -> None:
    # levenshtein(), for finding serials one character apart. A contrib module
    # shipped with PostgreSQL, so this adds no external dependency -- but it
    # is a dependency, and a migration is where one gets recorded rather than
    # discovered when a query fails on a fresh installation.
    op.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS fuzzystrmatch")
```

Then `cd backend && uv run alembic upgrade head`. Confirm with:

```
cd backend && uv run python -c "
from sqlalchemy import create_engine, text
from app.config import settings
with create_engine(settings.database_url).connect() as c:
    print(c.execute(text(\"SELECT levenshtein('B08084501', 'B08084S01')\")).scalar())
"
```

Expected: `1`.

`alembic check` will not notice an extension either way — it compares tables and columns — so the proof it worked is the query above, plus `test_issues.py` passing against the database `test_migrations_match_models` builds from scratch.

- [ ] **Step 4: Wire issues into the search**

In `backend/app/inventory_search.py`:

Add `from .issues import COIN_ISSUES, CURRENCY_ISSUES, Issue` to the imports, and `"UnknownIssue"` and `"count_issues"` to `__all__`.

Add the exception below the dataclasses:

```python
class UnknownIssue(KeyError):
    """An `issue=` value the view does not define.

    Distinct from a plain KeyError so the router can name the available
    checks rather than the available filters.
    """
```

Add `issues` to `ViewSpec`:

```python
    issues: dict[str, Issue] = field(default_factory=dict)
```

In `_conditions`, after the `deleted` handling added in Task 6:

```python
    issue_key = params.pop("issue", None)
    if issue_key:
        issue = spec.issues.get(issue_key)
        if issue is None:
            raise UnknownIssue(issue_key)
        # Parenthesised: a predicate containing OR would otherwise bind
        # loosely against the other clauses and match far too much.
        clauses.append(f"({issue.sql})")
        joins.append(issue.join)
```

Add `issues=COIN_ISSUES` to `COIN_VIEW` and `issues=CURRENCY_ISSUES` to `CURRENCY_VIEW`.

Add the counting function beside `count_facets`:

```python
def count_issues(
    db: Session, spec: ViewSpec, *, params: dict[str, Any], query: str | None = None
) -> dict[str, int]:
    """How many rows in the current result set hit each named check.

    One query with a FILTER per check rather than one query per check: there
    are a dozen checks and they all read the same rows.

    `issue` itself is dropped from the filters first. Counting within the
    selected check would collapse every other count to zero or to a subset,
    and the panel would stop being a way to see what work is left.
    """
    params = {k: v for k, v in params.items() if k != "issue"}
    clauses, joins, bound = _conditions(
        spec, params, query, series_ids_matching(db, query)
    )
    # All checks are counted at once, so every check's joins must be present.
    joins = list(joins) + [issue.join for issue in spec.issues.values()]

    selected = ", ".join(
        f"count(*) FILTER (WHERE {issue.sql}) AS {name}"
        for name, issue in spec.issues.items()
    )
    row = (
        db.execute(
            text(
                f"SELECT {selected} FROM {spec.base} {spec.joins_for(joins)} "
                f"WHERE {' AND '.join(clauses)}"
            ),
            bound,
        )
        .mappings()
        .one()
    )
    # Zero-count checks are omitted: a panel listing a dozen checks that all
    # say 0 hides the two that do not.
    return {name: row[name] for name in spec.issues if row[name]}
```

- [ ] **Step 5: Return the counts and the 422**

In `backend/app/schemas.py`, add to `InventoryPageOut`:

```python
    #: How many rows in this result set hit each named check. Populated when
    #: `facets=true`, and computed ignoring any `issue` filter so the sizes
    #: of the other jobs stay visible.
    issues: dict[str, int] = Field(default_factory=dict)
```

In `backend/app/routers/inventory.py`, add `UnknownIssue, count_issues` to the `..inventory_search` import, add this `except` clause **before** the existing `except KeyError`:

```python
    except UnknownIssue as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown issue {exc.args[0]!r} for {view}. Available: "
            f"{sorted(spec.issues)}",
        ) from exc
```

and add to the `InventoryPageOut(...)` construction:

```python
        issues=count_issues(db, spec, params=params, query=q) if facets else {},
```

- [ ] **Step 6: Run the tests to verify they pass**

```
uv run pytest backend/tests/test_issues.py -v
```

Expected: all 9 pass.

- [ ] **Step 7: Check the checks against the real collection**

```
cd backend && uv run python -c "
from app.config import settings
from app.inventory_search import COIN_VIEW, CURRENCY_VIEW, count_issues
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
with Session(create_engine(settings.database_url)) as db:
    for spec in (COIN_VIEW, CURRENCY_VIEW):
        print(spec.name, count_issues(db, spec, params={}))
"
```

Expected, against the figures in the spec: `no_grade` 2,965 and `no_country` 2,394 summed across the two views, `no_year` 1,230, `no_weight_bullion` 712, `kind_unknown` 33, `zero_cost` 51, `star_mismatch` 5, `unreviewed` 7,591, and `near_duplicate_serial` at least the 3 the spec names. A figure that disagrees means the predicate does not say what the spec measured — investigate before committing rather than adjusting the expectation.

- [ ] **Step 8: Commit**

```bash
git add backend/app/issues.py backend/app/inventory_search.py \
        backend/app/routers/inventory.py backend/app/schemas.py \
        backend/tests/test_issues.py
git commit -m "Add named diagnostics over the inventory

An anomaly is a check with a name, not a generic field filter. issue=no_grade
means a coin or banknote with no grade, because bullion has no grade by nature
and 712 rounds have no weight either -- a generic grade=null would bury 2,965
real cases under rounds that will never have one. The domain knowledge lives
in the check, written once, rather than in the head of whoever types it.

Counts come back with the page so the size of a job is visible before
committing, and they deliberately ignore the issue filter itself: counting
within the selected check collapses every other count and the panel stops
being a way to see what is left.

An unknown check is a 422 listing the known ones. Silently ignoring it would
return the whole collection and read as a clean bill of health.

repeated_identity and star_mismatch surface candidates and never resolve them.
Four of this collection's nine repeated serials are legitimate -- matched
serials across issues are a deliberate pursuit -- and a check that assumed
repeats were errors would fight the collection's own theme.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: The parent's value, beside each field

**Files:**
- Modify: `backend/app/schemas.py`, `backend/app/routers/inventory.py`
- Test: `backend/tests/test_inventory_edit.py`

**Interfaces:**
- Consumes: `_reviewed_fields` (Task 5).
- Produces: `ItemDetailOut` in `app.schemas`; `GET /api/inventory/{item_id}` returns it instead of `InventoryItemOut`. Task 13 consumes `lot_claims` and `reviewed`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_inventory_edit.py`:

```python
def test_a_piece_reports_what_its_lot_claimed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """So it is always visible what is being overridden, and what is still
    only the seller's word about the lot."""
    from tests.test_split import TUBE, do_split, lot

    parent = lot(db, year_start=1881)
    piece_id = do_split(client, admin_headers, parent.id, TUBE).json()["pieces"][0]["id"]

    body = client.get(f"/api/inventory/{piece_id}", headers=admin_headers).json()

    assert body["parent_item_code"] == parent.item_code
    assert body["lot_claims"]["year_start"] == 1881


def test_an_item_with_no_parent_claims_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """7,591 of 7,591 have no parent. Absent is the normal case, not an error."""
    item = make_item(db)
    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()

    assert body["parent_item_code"] is None
    assert body["lot_claims"] == {}


def test_the_detail_carries_what_has_been_reviewed(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """One round trip for the edit form, not two."""
    item = make_item(db)
    client.post(
        f"/api/inventory/{item.id}/reviewed",
        json={"fields": ["grade_id"]},
        headers=admin_headers,
    )

    body = client.get(f"/api/inventory/{item.id}", headers=admin_headers).json()
    assert body["reviewed"] == ["grade_id"]
```

- [ ] **Step 2: Run the tests to verify they fail**

```
uv run pytest backend/tests/test_inventory_edit.py -k "lot_claimed or claims_nothing or reviewed" -v
```

Expected: FAIL with `KeyError: 'parent_item_code'`.

- [ ] **Step 3: Add the schema**

In `backend/app/schemas.py`, below `InventoryItemOut`:

```python
class ItemDetailOut(InventoryItemOut):
    """One item, with everything the edit form needs in one round trip.

    The two provenance mechanisms answer different questions and both appear
    here. `lot_claims` is *derived* by comparing the item to its parent and
    says what the lot claimed -- it cannot drift out of sync because it is
    recomputed. `reviewed` is *asserted* and says a person looked -- it cannot
    be computed from anything. Neither replaces the other.
    """

    #: The lot this piece came out of, if any. Absent for 7,591 of 7,591
    #: items today: no parent is the normal state, not an orphan.
    parent_item_code: str | None = None
    #: What the lot said, for the fields a piece inherits. The form shows
    #: these beside the item's own values, so it is always visible what is
    #: being overridden and what is still only the seller's word.
    lot_claims: dict[str, object] = Field(default_factory=dict)
    #: Fields a person has confirmed by examination.
    reviewed: list[str] = Field(default_factory=list)
```

- [ ] **Step 4: Return it**

In `backend/app/routers/inventory.py`, add `ItemDetailOut` to the `..schemas` import and replace the existing `get_item` endpoint with:

```python
#: Fields a piece inherits from its lot, and so may be overriding.
#:
#: A subset of splitting.INHERITED: only what a person actually re-decides
#: while attributing. Showing the lot's storage_form beside a piece's would be
#: noise, since a piece always comes out of the tube as a single.
LOT_CLAIM_FIELDS: tuple[str, ...] = (
    "year_start",
    "year_end",
    "grade_id",
    "grade_designation_id",
    "grading_service_id",
    "denomination_id",
    "country_id",
    "metal_id",
    "series_id",
    "fineness",
    "fine_weight_ozt",
    "authenticity_id",
)


@router.get("/{item_id}", response_model=ItemDetailOut)
def get_item(item_id: int, db: DbSession, _admin: AdminUser) -> ItemDetailOut:
    """One item, with what its lot claimed and what has been confirmed.

    Both in one response because the edit form needs both on every field, and
    three round trips per coin is three per coin across 7,591 of them.
    """
    item = _get_item(db, item_id)

    parent_code: str | None = None
    claims: dict[str, object] = {}
    if item.parent_item_id is not None:
        parent = db.get(InventoryItem, item.parent_item_id)
        if parent is not None:
            parent_code = parent.item_code
            # Only what differs. A field the piece has not been re-decided on
            # agrees with the lot by definition, and listing it would bury the
            # overrides among forty rows saying "same".
            claims = {
                name: plain(getattr(parent, name))
                for name in LOT_CLAIM_FIELDS
                if getattr(parent, name) != getattr(item, name)
            }

    return ItemDetailOut(
        **{
            column: plain(getattr(item, column))
            for column in (
                "id",
                "item_code",
                "version",
                "source_title",
                "piece_count",
                "item_cost",
                "shipping_cost",
                "sales_tax",
                "total_cost",
                "parent_item_id",
                "split_at",
            )
        },
        parent_item_code=parent_code,
        lot_claims=claims,
        reviewed=_reviewed_fields(db, item.id),
    )
```

Rename `_plain` to `plain` in `backend/app/inventory_search.py`, update its two call sites there, add `"plain"` to that module's `__all__`, and import it here with `from ..inventory_search import plain`. A leading underscore says "private to this module" and this is now a second consumer; one conversion rule with two callers beats two rules.

`plain` on a `Decimal` yields a string, and `ItemDetailOut` inherits `item_cost: Decimal`, which Pydantic will parse back from the string correctly. The `split_at` datetime likewise. That is why it is used rather than passing the raw values: it keeps one conversion rule.

- [ ] **Step 5: Run the tests to verify they pass**

```
uv run pytest backend/tests/test_inventory_edit.py -v
```

Expected: all 11 pass.

- [ ] **Step 6: Run the whole gate**

```
scripts\ccweb_check.cmd
```

Expected: green. mypy findings must still be 56 in 15 files or fewer.

- [ ] **Step 7: Commit**

```bash
git add backend/app/schemas.py backend/app/routers/inventory.py \
        backend/tests/test_inventory_edit.py
git commit -m "Return the lot's claim and the review marks with an item

The edit form needs both on every field, and three round trips per coin is
three per coin across 7,591 of them.

The two mechanisms answer different questions and both appear. lot_claims is
derived by comparing the piece to its parent and says what the lot claimed --
recomputed, so it cannot drift. reviewed is asserted and says a person looked
-- not computable from anything. Neither replaces the other, which is why the
earlier examined_at proposal was wrong and this is not.

Only differing fields are reported. A field nobody has re-decided agrees with
the lot by definition, and listing it would bury the overrides among forty
rows saying 'same'.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

**Phase 1 checkpoint.** Every item is now editable, deletable, detachable and reviewable, and every diagnostic in the spec is queryable. Confirm before starting Phase 2:

```
scripts\ccweb_check.cmd
cd backend && uv run alembic check
```

---

# Phase 2 — the screens

`Inventory.jsx` is 321 lines carrying search, filters, facets, sorting and paging. Selection, bulk edit, review mode and an edit form would take it past 800, so it is split as part of this work rather than after, since every one of those modules is touched anyway.

---

### Task 11: Split `Inventory.jsx` into modules

A refactor with no behaviour change. Doing it first means the four features that follow land in files sized to hold them.

**Files:**
- Create: `frontend/src/pages/inventory/specs.js`, `useInventorySearch.js`, `FilterPanel.jsx`, `InventoryTable.jsx`
- Modify: `frontend/src/pages/Inventory.jsx`, `frontend/src/api.js`

**Interfaces:**
- Produces:
  - `specs.js` exports `COIN_VIEW`, `CURRENCY_VIEW`, `PAGE_SIZE`.
  - `useInventorySearch.js` exports `useInventorySearch(view)` returning `{ current, apply, clear, page, busy, error, offset }`.
  - `FilterPanel.jsx` exports `default function FilterPanel({ config, current, apply, facets, issues, total, busy, clear })`.
  - `InventoryTable.jsx` exports `default function InventoryTable({ config, rows, current, apply, selected, onSelect })` and the named helper `cell(row, key, kind)`.
- Tasks 12-14 consume all of these.

- [ ] **Step 1: Extract the specifications**

Create `frontend/src/pages/inventory/specs.js` holding, moved verbatim from `Inventory.jsx`, the `COIN_VIEW` object (lines 21-49), the `CURRENCY_VIEW` object (lines 51-82) and `PAGE_SIZE` (line 84), each with `export const`. Add the file docstring:

```js
/**
 * What each inventory view shows and filters on.
 *
 * Separated from the components because three of them read it -- the table,
 * the filter panel and the review pane -- and a specification imported in
 * three places should not live inside any one of them.
 */
```

Then add the issue checks to both specs, as a new key on each object:

```js
  // Named diagnostics. The code is the contract and appears in bookmarked
  // URLs; the wording comes from the API's `description`, so it is written
  // once on the server rather than twice.
  issueChecks: [
    'no_year',
    'no_country',
    'no_grade',
    'no_denomination',
    'no_weight_bullion',
    'mixed_marker',
    'zero_cost',
    'kind_unknown',
    'repeated_identity',
    'unreviewed',
  ],
```

For `CURRENCY_VIEW`, use the same list with `no_weight_bullion` removed and `star_mismatch`, `malformed_serial`, `near_duplicate_serial` added.

- [ ] **Step 2: Extract the search hook**

Create `frontend/src/pages/inventory/useInventorySearch.js`:

```js
import { useEffect, useState } from 'react'
import { useSearchParams } from 'react-router-dom'

import { api } from '../../api'
import { PAGE_SIZE } from './specs'

/**
 * The search, driven by the URL.
 *
 * The URL is the source of truth, so a filtered view can be bookmarked,
 * shared with someone, or survive a reload.
 *
 * The result is stored *with the query that produced it*, so "still loading"
 * is derived -- it is exactly "what is displayed does not match what is being
 * asked for". A busy flag would be a second piece of state saying the same
 * thing, able to disagree with the first.
 */
export function useInventorySearch(view) {
  const [params, setParams] = useSearchParams()
  const [result, setResult] = useState(null)
  const [error, setError] = useState('')

  const current = Object.fromEntries(params.entries())
  const offset = Number(current.offset ?? 0)

  // A plain string, so the dependency is a simple expression the linter and
  // React can both reason about.
  const query = params.toString()

  useEffect(() => {
    // Not only tidiness: typing in the search box fires a request per
    // keystroke, and without this an early slow response can land after a
    // later fast one and overwrite newer results with older ones.
    let cancelled = false

    api
      .searchInventory(view, {
        ...Object.fromEntries(new URLSearchParams(query)),
        facets: true,
        limit: PAGE_SIZE,
      })
      .then((body) => {
        if (cancelled) return
        setResult({ query, body })
        setError('')
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })

    return () => {
      cancelled = true
    }
  }, [view, query])

  function apply(changes) {
    const next = { ...current, ...changes }
    // Any change to the filters returns to the first page: staying on page 7
    // of a result set that no longer has one is disorienting.
    if (!('offset' in changes)) delete next.offset
    Object.keys(next).forEach((k) => {
      if (next[k] === '' || next[k] === null || next[k] === undefined) delete next[k]
    })
    setParams(next)
  }

  return {
    current,
    apply,
    clear: () => setParams({}),
    page: result?.body,
    busy: result?.query !== query,
    error,
    offset,
  }
}
```

- [ ] **Step 3: Extract the filter panel and the table**

Create `frontend/src/pages/inventory/FilterPanel.jsx` holding the `<div className="search-panel">` block, moved verbatim from `Inventory.jsx` lines 158-248, wrapped as a component with the signature in **Interfaces** above. Create `frontend/src/pages/inventory/InventoryTable.jsx` holding the `cell` helper (lines 86-92) and the `<table>` block (lines 254-287), likewise. Neither gains behaviour in this task; `selected` and `onSelect` are accepted and unused until Task 14, so add them now rather than changing the signature twice.

- [ ] **Step 4: Reduce `Inventory.jsx` to composition**

Rewrite `frontend/src/pages/Inventory.jsx` as:

```jsx
import FilterPanel from './inventory/FilterPanel'
import InventoryTable from './inventory/InventoryTable'
import { COIN_VIEW, CURRENCY_VIEW, PAGE_SIZE } from './inventory/specs'
import { useInventorySearch } from './inventory/useInventorySearch'

/**
 * Staff inventory browse, one screen per kind.
 *
 * Coins and currency get separate views rather than one grid with a kind
 * filter, because the columns that matter differ: a coin has a mint mark and
 * a variety, a banknote has a series letter, a seal colour and its own
 * printed serial. One grid would leave most columns blank most of the time.
 *
 * The filter options come from *facets* -- value counts over the current
 * result set -- not from the full vocabulary. A real collection uses a
 * fraction of the fifty-odd grades that exist, and offering all of them
 * buries the ones actually present.
 */
function InventoryView({ config }) {
  const { current, apply, clear, page, busy, error, offset } = useInventorySearch(
    config.view,
  )

  const total = page?.total ?? 0
  const rows = page?.rows ?? []

  return (
    <section>
      <h1>{config.title}</h1>

      <FilterPanel
        config={config}
        current={current}
        apply={apply}
        clear={clear}
        facets={page?.facets ?? {}}
        issues={page?.issues ?? {}}
        total={total}
        busy={busy}
      />

      {error && <p className="error">{error}</p>}
      {!busy && total === 0 && (
        <p className="muted">Nothing matches those filters.</p>
      )}

      {rows.length > 0 && (
        <InventoryTable config={config} rows={rows} current={current} apply={apply} />
      )}

      {total > PAGE_SIZE && (
        <div className="row pager">
          <button
            disabled={offset === 0}
            onClick={() => apply({ offset: Math.max(0, offset - PAGE_SIZE) })}
          >
            Previous
          </button>
          <span className="muted">
            {offset + 1}-{Math.min(offset + PAGE_SIZE, total)} of{' '}
            {total.toLocaleString()}
          </span>
          <button
            disabled={offset + PAGE_SIZE >= total}
            onClick={() => apply({ offset: offset + PAGE_SIZE })}
          >
            Next
          </button>
        </div>
      )}
    </section>
  )
}

export function InventoryCoins() {
  return <InventoryView config={COIN_VIEW} />
}

export function InventoryCurrency() {
  return <InventoryView config={CURRENCY_VIEW} />
}
```

- [ ] **Step 5: Add the new API calls**

In `frontend/src/api.js`, in the `// inventory (staff)` section beside `searchInventory` and `splitItem`:

```js
  getInventoryItem: (id) => send(`/api/inventory/${id}`),
  updateInventoryItem: (id, payload) =>
    send(`/api/inventory/${id}`, { method: 'PATCH', body: payload }),
  bulkEditInventory: (ids, changes) =>
    send('/api/inventory/bulk', { method: 'POST', body: { ids, changes } }),
  setItemReview: (id, fields, replace = false) =>
    send(`/api/inventory/${id}/reviewed`, {
      method: 'POST',
      body: { fields, replace },
    }),
  detachInventoryItem: (id) =>
    send(`/api/inventory/${id}/parent`, { method: 'DELETE' }),
  deleteInventoryItem: (id) => send(`/api/inventory/${id}`, { method: 'DELETE' }),
```

- [ ] **Step 6: Verify no behaviour changed**

```
cd frontend && npm run lint && npx prettier --check src
```

Then start the app with `scripts\ccweb_startup.cmd`, open `/inventory/coins`, and confirm by hand: the filter dropdowns populate, a facet filter narrows the result, sorting a column works, and paging works. This task has no automated test because it moves code without changing it; the check is that the screen still does what it did.

- [ ] **Step 7: Commit**

```bash
git add frontend/src
git commit -m "Split Inventory.jsx into modules before it grows

321 lines carrying search, filters, facets, sorting and paging. Selection,
bulk edit, review mode and an edit form take it past 800, and every one of
those modules is touched by the work anyway -- so the split happens first
rather than after.

No behaviour change. The search hook keeps the two decisions worth keeping:
the URL is the source of truth so a filtered view survives a reload, and
'busy' is derived from the displayed result belonging to an older query than
the one being asked, rather than being a second piece of state able to
disagree with the first.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: Issue checks in the filter panel

**Files:**
- Modify: `frontend/src/pages/inventory/FilterPanel.jsx`, `frontend/src/pages/inventory/InventoryTable.jsx`

**Interfaces:**
- Consumes: `issues` prop (Task 11), the API's `issues` counts (Task 9).
- Produces: no new exports.

- [ ] **Step 1: Add the check list to the panel**

In `FilterPanel.jsx`, above the existing `<div className="row">` that holds "Clear filters", add:

```jsx
      {/* Named checks, with the size of each job visible before committing
          to it. Counts ignore the selected check, so choosing one does not
          hide what else is left. A check with no hits is not offered: a list
          of a dozen zeroes buries the two that matter. */}
      <div className="issue-checks">
        {config.issueChecks
          .filter((code) => issues[code])
          .map((code) => (
            <button
              key={code}
              className={current.issue === code ? 'chip chip-on' : 'chip'}
              onClick={() =>
                apply({ issue: current.issue === code ? '' : code })
              }
            >
              {code.replace(/_/g, ' ')} ({issues[code].toLocaleString()})
            </button>
          ))}
      </div>
```

- [ ] **Step 2: Style the chips**

In `frontend/src/styles.css`, append:

```css
/* Issue chips. A selected chip must be unmistakable at a glance: the panel's
   whole job is showing which of a dozen checks is currently narrowing the
   list. */
.issue-checks {
  display: flex;
  flex-wrap: wrap;
  gap: 0.4rem;
  margin: 0.6rem 0;
}
.chip {
  background: #f2f2f2;
  border: 1px solid #ccc;
  border-radius: 999px;
  color: #333;
  cursor: pointer;
  font-size: 0.85rem;
  padding: 0.2rem 0.7rem;
}
.chip-on {
  background: #24507a;
  border-color: #24507a;
  color: #fff;
}
```

- [ ] **Step 3: Verify by hand**

Start the app, open `/inventory/coins`, and confirm: chips appear with counts, clicking one narrows the table and turns it dark, the URL gains `?issue=...`, clicking again clears it, and the other chips keep their full counts while one is selected.

The last of those is the behaviour Task 9's `test_issue_counts_ignore_the_issue_filter_itself` pins on the server; this confirms the screen shows it.

- [ ] **Step 4: Commit**

```bash
git add frontend/src
git commit -m "Offer the named checks in the filter panel

With counts, so the size of a job is visible before committing to it, and
without the checks that have no hits -- a list of a dozen zeroes buries the
two that matter.

Selecting a check does not collapse the others' counts, because the server
computes them ignoring the issue filter. Otherwise the panel would stop being
a way to see what work is left the moment you started any of it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: The edit form

**Files:**
- Create: `frontend/src/pages/inventory/ItemEditForm.jsx`
- Modify: `frontend/src/pages/inventory/InventoryTable.jsx`, `frontend/src/pages/Inventory.jsx`

**Interfaces:**
- Consumes: `api.getInventoryItem`, `api.updateInventoryItem`, `api.setItemReview` (Task 11); `lot_claims`, `reviewed`, `version` (Tasks 4, 10); the `reference` context already in `frontend/src/reference.jsx` for classifier dropdowns.
- Produces: `export default function ItemEditForm({ itemId, onSaved, onClose })`.

- [ ] **Step 1: Write the form**

Create `frontend/src/pages/inventory/ItemEditForm.jsx`:

```jsx
import { useEffect, useState } from 'react'

import { api } from '../../api'

/**
 * One item, every field, with what the lot claimed beside each.
 *
 * Two provenance marks appear on every field and they mean different things.
 * "lot says BU" is *derived* by comparing this piece to its parent: it says
 * what the seller claimed about the whole lot, and it recomputes so it cannot
 * drift. The confirm box is *asserted*: it says a person looked at this coin.
 * Neither is computable from the other, which is why both are here.
 */

const TEXT_FIELDS = [
  ['Title', 'source_title'],
  ['Description', 'description'],
]

const NUMBER_FIELDS = [
  ['Year from', 'year_start'],
  ['Year to', 'year_end'],
  ['Pieces', 'piece_count'],
]

const MONEY_FIELDS = [
  ['Item cost', 'item_cost'],
  ['Shipping', 'shipping_cost'],
]

//: Form field -> the column a review record names. Only these can be
//: confirmed; the rest have no review box.
const REVIEWABLE = {
  year_start: 'year_start',
  year_end: 'year_end',
  piece_count: 'piece_count',
  grade: 'grade_id',
  denomination: 'denomination_id',
  country: 'country_id',
  metal: 'metal_id',
}

const CLASSIFIERS = [
  ['Grade', 'grade', 'grade'],
  ['Denomination', 'denomination', 'denomination'],
  ['Country', 'country', 'country'],
  ['Metal', 'metal', 'metal'],
]

export default function ItemEditForm({ itemId, onSaved, onClose }) {
  const [item, setItem] = useState(null)
  const [draft, setDraft] = useState({})
  const [reviewed, setReviewed] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    api
      .getInventoryItem(itemId)
      .then((body) => {
        if (cancelled) return
        setItem(body)
        setReviewed(body.reviewed ?? [])
        setDraft({})
        setError('')
      })
      .catch((err) => {
        if (!cancelled) setError(err.message)
      })
    return () => {
      cancelled = true
    }
  }, [itemId])

  if (error && !item) return <p className="error">{error}</p>
  if (!item) return <p className="muted">Loading...</p>

  const value = (key) => draft[key] ?? item[key] ?? ''
  const set = (key) => (e) => setDraft({ ...draft, [key]: e.target.value })

  function toggleReview(column) {
    const next = reviewed.includes(column)
      ? reviewed.filter((c) => c !== column)
      : [...reviewed, column]
    setReviewed(next)
    // replace:true, so unticking removes the record rather than leaving a
    // confirmation nobody stands behind any more.
    api.setItemReview(itemId, next, true).catch((err) => setError(err.message))
  }

  function claim(column) {
    const claimed = item.lot_claims?.[column]
    if (claimed === undefined || claimed === null) return null
    return (
      <span className="lot-claim" title={`The lot ${item.parent_item_code} claimed this`}>
        lot says {String(claimed)}
      </span>
    )
  }

  function review(column) {
    if (!column) return null
    return (
      <label className="review-mark" title="I have confirmed this by examination">
        <input
          type="checkbox"
          checked={reviewed.includes(column)}
          onChange={() => toggleReview(column)}
        />
        confirmed
      </label>
    )
  }

  async function save() {
    setSaving(true)
    try {
      // The version read when the form was opened. A save from a form loaded
      // before someone else's change is a 409, not a silent overwrite.
      await api.updateInventoryItem(itemId, { ...draft, version: item.version })
      setError('')
      onSaved?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="edit-form">
      <div className="row">
        <h2>{item.item_code}</h2>
        {item.parent_item_code && (
          <span className="muted">split from {item.parent_item_code}</span>
        )}
        {onClose && (
          <button className="link" onClick={onClose}>
            Close
          </button>
        )}
      </div>

      {error && <p className="error">{error}</p>}

      {TEXT_FIELDS.map(([label, key]) => (
        <label key={key} className="field">
          {label}
          <input type="text" value={value(key)} onChange={set(key)} />
          {claim(key)}
          {review(REVIEWABLE[key])}
        </label>
      ))}

      {NUMBER_FIELDS.map(([label, key]) => (
        <label key={key} className="field">
          {label}
          <input type="number" value={value(key)} onChange={set(key)} />
          {claim(REVIEWABLE[key] ?? key)}
          {review(REVIEWABLE[key])}
        </label>
      ))}

      {MONEY_FIELDS.map(([label, key]) => (
        <label key={key} className="field">
          {label}
          {/* Text, not number. Money crosses the API as a string and a number
              input would hand back a float, which is the one thing this
              schema is careful never to do. */}
          <input type="text" inputMode="decimal" value={value(key)} onChange={set(key)} />
          {review(REVIEWABLE[key])}
        </label>
      ))}

      {CLASSIFIERS.map(([label, key, table]) => (
        <label key={key} className="field">
          {label}
          <ReferenceSelect table={table} value={value(key)} onChange={set(key)} />
          {claim(REVIEWABLE[key])}
          {review(REVIEWABLE[key])}
        </label>
      ))}

      <div className="row">
        <button disabled={saving || Object.keys(draft).length === 0} onClick={save}>
          {saving ? 'Saving...' : 'Save'}
        </button>
      </div>
    </div>
  )
}

```

`ReferenceSelect` comes from `frontend/src/reference.jsx` and is imported at the top of this file:

```js
import { ReferenceSelect } from '../../reference'
```

**Do not write a dropdown here.** `ReferenceSelect` already reads its vocabulary from the `ReferenceProvider` cache rather than fetching per field — which matters when a review session opens four dropdowns per coin across hundreds of coins — falls back to a plain text input while a vocabulary is loading or if it could not be fetched, so the form is degraded rather than broken, and lets a missing value be added to the vocabulary inline and selected immediately. That last behaviour is the reason vocabularies grow with use instead of entries being abandoned when nothing fits; a hand-rolled `<select>` would silently drop it.

Check that `ReferenceProvider` wraps the inventory routes in `frontend/src/App.jsx`. If it does not, wrap them — without it `useReference` returns nothing and every select degrades to a text input, which works but throws away the vocabulary.

- [ ] **Step 2: Open it from a row**

In `InventoryTable.jsx`, add `onOpen` to the props and make the item-code cell open the form. Replace the cell rendering inside the row map with:

```jsx
                {config.columns.map(([, key, kind]) => (
                  <td key={key} className={kind === 'money' ? undefined : kind}>
                    {key === 'item_code' ? (
                      <button className="link mono" onClick={() => onOpen(row.id)}>
                        {row.item_code}
                      </button>
                    ) : (
                      cell(row, key, kind)
                    )}
                  </td>
                ))}
```

The item code rather than the whole row: a row carries checkboxes and sortable columns, and making all of it clickable means selecting text opens a form.

In `Inventory.jsx`, add `import { useState } from 'react'` and `import ItemEditForm from './inventory/ItemEditForm'`, hold `const [editing, setEditing] = useState(null)`, pass `onOpen={setEditing}` to `<InventoryTable>`, and render below it:

```jsx
      {editing && (
        <ItemEditForm
          itemId={editing}
          onSaved={() => apply({})}
          onClose={() => setEditing(null)}
        />
      )}
```

`apply({})` re-runs the search so a corrected item leaves `issue=no_year` immediately.

- [ ] **Step 3: Style it**

Append to `frontend/src/styles.css`:

```css
.edit-form .field {
  align-items: center;
  display: grid;
  gap: 0.5rem;
  grid-template-columns: 9rem minmax(12rem, 24rem) auto auto;
  margin-bottom: 0.4rem;
}
/* The lot's claim must read as context, not as a value someone typed. */
.lot-claim {
  color: #8a6d3b;
  font-size: 0.85rem;
  font-style: italic;
}
.review-mark {
  color: #2b6a2b;
  display: flex;
  font-size: 0.85rem;
  gap: 0.25rem;
}
```

- [ ] **Step 4: Verify by hand**

Start the app and confirm: opening a row shows the form; changing a field enables Save; saving updates the row in the table; ticking "confirmed" survives a reload; opening a split piece shows "lot says ..." beside a field it overrides; and opening the same item in two tabs, saving in one, then saving in the other shows the 409 message rather than silently overwriting.

That last check is the one worth doing deliberately — it is the whole reason `version` is sent.

- [ ] **Step 5: Commit**

```bash
git add frontend/src
git commit -m "Add the item edit form, with the lot's claim beside each field

Two provenance marks on every field, meaning different things. 'lot says BU'
is derived by comparing the piece to its parent -- it recomputes, so it cannot
drift. The confirm box is asserted -- it says a person looked at this coin.
Neither is computable from the other, which is why both are shown.

Money fields are text inputs with inputMode=decimal, not number inputs: a
number input hands back a float, which is the one thing this schema is careful
never to do.

The version read when the form opened is sent with the save, so a form loaded
before someone else's change is a 409 rather than a silent overwrite.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 14: Selection, bulk edit, and the review queue

**Files:**
- Create: `frontend/src/pages/inventory/BulkEditBar.jsx`, `frontend/src/pages/inventory/ReviewPane.jsx`
- Modify: `frontend/src/pages/inventory/InventoryTable.jsx`, `frontend/src/pages/Inventory.jsx`

**Interfaces:**
- Consumes: `api.bulkEditInventory` (Task 11), `ItemEditForm` (Task 13).
- Produces: `export default function BulkEditBar({ ids, onApplied, onClear })` and `export default function ReviewPane({ ids, onClose })`.

- [ ] **Step 1: Add selection to the table**

In `InventoryTable.jsx`, use the `selected` and `onSelect` props that have been in the signature since Task 11. `selected` is an array of ids; `onSelect(ids)` replaces it wholesale, so the parent holds one piece of state rather than the table holding a second copy able to disagree with it.

Above the `return`:

```jsx
  const ids = rows.map((r) => r.id)
  const allShown = ids.length > 0 && ids.every((id) => selected.includes(id))

  function toggle(id) {
    onSelect(
      selected.includes(id) ? selected.filter((s) => s !== id) : [...selected, id],
    )
  }
```

A leading header cell, before the column headers:

```jsx
              <th className="select-cell">
                {/* Selects the current page, not the whole result set.
                    "Apply to 7,591" from one click on a 50-row page is not
                    something anyone means. */}
                <input
                  type="checkbox"
                  checked={allShown}
                  onChange={() =>
                    onSelect(
                      allShown
                        ? selected.filter((id) => !ids.includes(id))
                        : [...new Set([...selected, ...ids])],
                    )
                  }
                />
              </th>
```

And a leading cell in each row, before the column cells:

```jsx
                <td className="select-cell">
                  <input
                    type="checkbox"
                    checked={selected.includes(row.id)}
                    onChange={() => toggle(row.id)}
                  />
                </td>
```

Selection deliberately survives paging — `[...new Set([...selected, ...ids])]` merges rather than replaces — because "select these twenty across two pages, then set the year" is the case bulk edit exists for.

- [ ] **Step 2: Write the bulk bar**

Create `frontend/src/pages/inventory/BulkEditBar.jsx`:

```jsx
import { useState } from 'react'

import { api } from '../../api'

/**
 * Set one field across a selection.
 *
 * One field at a time on purpose. The bulk case is "these twenty are all
 * 1964" -- a form offering every field at once invites setting four of them
 * across twenty coins from one glance at one coin.
 *
 * The server applies it in a single transaction, so a rejected edit changes
 * nothing and there is never a half-applied selection to report.
 */

const BULK_FIELDS = [
  ['Year', 'year_start', 'number'],
  ['Grade', 'grade', 'text'],
  ['Country', 'country', 'text'],
  ['Denomination', 'denomination', 'text'],
  ['Metal', 'metal', 'text'],
]

export default function BulkEditBar({ ids, onApplied, onClear }) {
  const [field, setField] = useState(BULK_FIELDS[0][1])
  const [value, setValue] = useState('')
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)

  if (ids.length === 0) return null

  const type = BULK_FIELDS.find(([, key]) => key === field)?.[2] ?? 'text'

  async function apply() {
    setBusy(true)
    try {
      await api.bulkEditInventory(ids, {
        [field]: type === 'number' ? Number(value) : value,
      })
      setError('')
      setValue('')
      onApplied?.()
    } catch (err) {
      setError(err.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="bulk-bar">
      <strong>{ids.length} selected</strong>

      <select value={field} onChange={(e) => setField(e.target.value)}>
        {BULK_FIELDS.map(([label, key]) => (
          <option key={key} value={key}>
            {label}
          </option>
        ))}
      </select>

      <input
        type={type}
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder="New value"
      />

      <button disabled={busy || value === ''} onClick={apply}>
        {busy ? 'Applying...' : `Apply to ${ids.length}`}
      </button>
      <button className="link" onClick={onClear}>
        Clear selection
      </button>

      {error && <span className="error">{error}</span>}
    </div>
  )
}
```

- [ ] **Step 3: Write the review pane**

Create `frontend/src/pages/inventory/ReviewPane.jsx`:

```jsx
import { useState } from 'react'

import ItemEditForm from './ItemEditForm'

/**
 * Walk a result set one item at a time.
 *
 * **The queue is frozen at entry.** `ids` is captured once, when review
 * starts, and never re-read. If it re-ran the search at each step, fixing
 * item 3's missing year would remove it from `issue=no_year`, the set would
 * shrink to 22, and every position after it would shift -- silently skipping
 * an item. Fixed items stay in the list, marked done.
 */
export default function ReviewPane({ ids, onClose }) {
  const [at, setAt] = useState(0)
  const [done, setDone] = useState([])

  if (ids.length === 0) return null
  const itemId = ids[at]

  return (
    <div className="review-pane">
      <div className="row">
        <button disabled={at === 0} onClick={() => setAt(at - 1)}>
          Previous
        </button>
        <span className="muted">
          {at + 1} of {ids.length}
          {done.length > 0 && ` -- ${done.length} saved`}
        </span>
        <button disabled={at + 1 >= ids.length} onClick={() => setAt(at + 1)}>
          Next
        </button>
        <button className="link" onClick={onClose}>
          Leave review
        </button>
      </div>

      <ItemEditForm
        key={itemId}
        itemId={itemId}
        onSaved={() => {
          setDone((d) => (d.includes(itemId) ? d : [...d, itemId]))
          // Saving advances, because the next thing you want after finishing
          // a coin is the next coin.
          if (at + 1 < ids.length) setAt(at + 1)
        }}
      />
    </div>
  )
}
```

`key={itemId}` is required: without it React reuses the form's state across items and the second coin opens holding the first coin's unsaved draft.

- [ ] **Step 4: Wire both into the page**

In `Inventory.jsx`, add `const [selected, setSelected] = useState([])` and `const [reviewing, setReviewing] = useState(null)`. Pass `selected` and `onSelect={setSelected}` to the table. Render `<BulkEditBar ids={selected} onApplied={() => { setSelected([]); apply({}) }} onClear={() => setSelected([])} />` above the table, and beside "Clear filters" add:

```jsx
        <button
          disabled={rows.length === 0}
          onClick={() => setReviewing(rows.map((r) => r.id))}
        >
          Review these {rows.length}
        </button>
```

Render `{reviewing && <ReviewPane ids={reviewing} onClose={() => { setReviewing(null); apply({}) }} />}`. The `apply({})` on close re-runs the search once, at the end — never during.

- [ ] **Step 5: Style them**

Append to `frontend/src/styles.css`:

```css
.bulk-bar {
  align-items: center;
  background: #eef3f8;
  border: 1px solid #c3d4e5;
  border-radius: 4px;
  display: flex;
  flex-wrap: wrap;
  gap: 0.6rem;
  margin: 0.6rem 0;
  padding: 0.5rem 0.8rem;
}
.review-pane {
  border: 1px solid #ccc;
  border-radius: 4px;
  margin-top: 0.8rem;
  padding: 0.8rem;
}
.select-cell {
  width: 2rem;
}
```

- [ ] **Step 6: Verify the frozen queue by hand**

This is the behaviour most likely to be broken by a later "improvement", so check it deliberately:

1. Filter to `issue=no_year` and note the count.
2. Click "Review these N".
3. Fill in the year on the first item and save.
4. Confirm the counter still reads "2 of N" with the **same N** — not N-1 — and that pressing Previous returns to the item just fixed rather than to a different coin.

If N shrinks, the queue is re-reading the search and items are being skipped.

- [ ] **Step 7: Run the whole gate and commit**

```
scripts\ccweb_check.cmd
```

```bash
git add frontend/src
git commit -m "Add selection, bulk edit and the frozen review queue

The queue is captured once, when review starts, and never re-read. If it
re-ran the search at each step, fixing item 3's missing year would remove it
from issue=no_year, the set would shrink from 23 to 22, and every position
after it would shift -- silently skipping an item. Fixed items stay in the
list, marked done, and the search re-runs once on leaving.

The bulk bar sets one field at a time. The real case is 'these twenty are all
1964'; a form offering every field at once invites setting four of them across
twenty coins from one glance at one coin.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Done when

- `scripts\ccweb_check.cmd` is green and mypy findings have not risen above 56.
- `cd backend && uv run alembic check` reports no new operations.
- The real database still holds 7,591 items and $534,177.89.
- Every guard added here has been mutation-checked: removed, its test seen to fail, restored, seen to pass.

## Not in this plan

Named so nobody goes looking for them:

- **The Excel round trip.** Specified separately in `docs/specs/excel-roundtrip-design.md`. It sits on the API Phase 1 builds and needs none of Phase 2.
- **Photograph linking.** ~673 unlinked photographs are a bulk-matching problem and get their own document.
- **Valuation and the CDN API.** Document B. The spec's section on it records the licence terms so the design is settled before the subscription is bought, but nothing here calls it.
- **eBay order numbers from the PDFs.** The extraction is easy and the matching is not; its own piece of work.
- **The seven surplus rows, the five star mismatches, and the twelve conglomerates' piece counts.** These need the physical collection, not code. Task 6 and Task 9 give the tools to act on them once the owner has looked.
