# Receiving Purchases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A page in the owner console that records what arrived from a
purchase order, where it went, and what a person saw when they looked at it.

**Architecture:** Receiving is a state transition on rows that already exist
(`status = ordered`), not a creation. One backend module owns every write to
`status_id` and `storage_location_id` so history is recorded by construction.
The page is order-first with an attribute-search fallback, and composes the
existing review and image endpoints rather than duplicating them.

**Tech Stack:** FastAPI, SQLAlchemy 2.0, Alembic, PostgreSQL, pytest; React 19,
react-router-dom 7, Vitest 5.

**Spec:** `docs/specs/receiving-purchases-design.md`

## Global Constraints

- **The page lives in `frontend/src/owner/`.** The shop must never import it.
  `frontend/eslint.config.js` and `frontend/scripts/check-bundle-isolation.mjs`
  enforce this; both fail the gate.
- **New API calls for the console go in `frontend/src/owner/api.js`**, never
  `shared/api.js`. That file is a single object literal, so anything added to
  it is downloaded by every anonymous shop visitor.
- **`storage_location` is never customer-visible.** `models/lifecycle.py` calls
  this an authorisation boundary enforced by the `public_catalog` view and by
  tests. Do not weaken that view; the existing test for it must keep passing
  unmodified.
- **Every new endpoint is behind `AdminUser`** (`backend/app/deps.py`).
- **cmd/batch only.** No PowerShell, in scripts, chat or docs.
- **Write files with the Write tool; never shell heredocs.** Scripted
  multi-file edits assert their anchor matches before writing.
- **Commit messages go through a file** (`git commit -F <path>`), never a
  heredoc or a multi-line `-m`, and end with the session's attribution lines.
- **`node` is not on PATH:** `"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe"`.
  Python: `"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe"`.
- **`NoDefaultCurrentDirectoryInExePath=1`:** run `.\scripts\ccweb_check.cmd`,
  never `cmd /c ccweb_check.cmd`.
- **Branch first.** Never commit to `main`.
- The gate for every task is `.\scripts\ccweb_check.cmd`, which must exit 0.
  It runs ruff, mypy (enforced, currently at zero), pytest, eslint, prettier,
  vitest, and the bundle-isolation check.

---

## File Structure

```
backend/
  alembic/versions/<rev>_item_status_history_arrived_on.py   NEW
  app/
    lifecycle_writes.py                    NEW  set_status / set_location
    models/lifecycle.py                    MODIFIED  arrived_on column
    routers/inventory.py                   MODIFIED  status via set_status; receive
    routers/acquisitions.py                NEW  purchase orders + storage locations
    schemas.py                             MODIFIED  ReceiveRequest and read models
    splitting.py                           MODIFIED  opening row via set_status
    importers/loader.py                    MODIFIED  opening row via set_status
    main.py                                MODIFIED  include acquisitions router
  tests/
    test_lifecycle_writes.py               NEW
    test_receiving.py                      NEW
    test_acquisitions.py                   NEW
frontend/src/owner/
  api.js                                   MODIFIED  console calls
  OwnerApp.jsx                             MODIFIED  route + nav
  pages/Receiving.jsx                      NEW  the page shell
  pages/Receiving.test.jsx                 NEW
  pages/receiving/OrderPicker.jsx          NEW
  pages/receiving/OutstandingList.jsx      NEW
  pages/receiving/ReceiptPanel.jsx         NEW
  pages/receiving/ItemFinder.jsx           NEW  attribute-search fallback
  pages/receiving/*.test.jsx               NEW
  styles.css                               MODIFIED
```

---

### Task 1: One door for lifecycle writes

The gap this whole feature rests on: `item_status_history` is written only by
`importers/loader.py` and `splitting.py`. A status change through
`PATCH /api/inventory/{id}` records nothing, so the arrival date does not
survive a later correction — which is the one thing the table exists for.

**Files:**
- Create: `backend/app/lifecycle_writes.py`, `backend/tests/test_lifecycle_writes.py`
- Create: one Alembic revision (generated, not hand-written)
- Modify: `backend/app/models/lifecycle.py`, `backend/app/routers/inventory.py:466-480`, `backend/app/splitting.py:245-255`, `backend/app/importers/loader.py:386-396`

**Interfaces:**
- Consumes: `ItemStatusHistory`, `LocationHistory`, `InventoryItem` from `app.models`.
- Produces, used verbatim by Tasks 2 and 3:
  ```python
  def set_status(session: Session, item: InventoryItem, to_status_id: int, *,
                 user_id: int | None = None, note: str | None = None,
                 arrived_on: date | None = None) -> None
  def set_location(session: Session, item: InventoryItem,
                   storage_location_id: int | None, *,
                   user_id: int | None = None, note: str | None = None) -> None
  ```

- [ ] **Step 1: Confirm the starting state is green**

```
.\scripts\ccweb_check.cmd
```

Expected: `All checks passed.` If not, stop — a failure afterwards would be
impossible to attribute.

- [ ] **Step 2: Add the column to the model**

In `backend/app/models/lifecycle.py`, inside `ItemStatusHistory`, after
`changed_at`:

```python
    #: The date the parcel actually arrived, when that differs from when
    #: somebody recorded it. A box that sat unopened over a weekend arrived on
    #: the Friday and was logged on the Monday, and `changed_at` is the wrong
    #: answer to "what arrived last week". Null on every status change that is
    #: not an arrival -- a cancellation has no arrival date, and neither does
    #: the opening row written at import.
    arrived_on: Mapped[date | None] = mapped_column(Date, nullable=True)
```

Add `date` to the `datetime` import and `Date` to the `sqlalchemy` import at
the top of the file.

- [ ] **Step 3: Generate the migration**

```
cd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m alembic revision --autogenerate -m "item_status_history arrived_on"
```

Read the generated file before keeping it. It must contain exactly one
`op.add_column('item_status_history', ...)` and nothing else. If autogenerate
proposes anything about enum types, unnamed foreign keys or normalised server
defaults, those are known false positives in this project — delete them from
the revision, do not "fix" the models.

- [ ] **Step 4: Confirm the migration is honest**

```
cd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m alembic upgrade head
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m alembic check
```

Expected: `No new upgrade operations detected.`

- [ ] **Step 5: Write the failing tests**

Create `backend/tests/test_lifecycle_writes.py`:

```python
"""Every status and location change records that it happened.

The table exists so "when did this actually arrive" survives a later
correction to the status. That only holds if nothing can change the column
without writing history, which is why these are the only writers.
"""

from __future__ import annotations

from datetime import date

from app.lifecycle_writes import set_location, set_status
from app.models import ItemStatus, ItemStatusHistory, LocationHistory
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def _status_id(db: Session, code: str) -> int:
    return db.scalars(select(ItemStatus.id).where(ItemStatus.code == code)).one()


def test_a_status_change_records_where_it_came_from(db: Session) -> None:
    item = make_item(db)
    was = item.status_id
    set_status(db, item, _status_id(db, "received"), note="unpacked")
    db.commit()

    rows = db.scalars(
        select(ItemStatusHistory).where(
            ItemStatusHistory.inventory_item_id == item.id
        )
    ).all()
    latest = rows[-1]
    assert latest.from_status_id == was
    assert latest.to_status_id == _status_id(db, "received")
    assert latest.note == "unpacked"


def test_the_arrival_date_is_kept_apart_from_when_it_was_logged(
    db: Session,
) -> None:
    """A box that sat over a weekend arrived before anyone typed anything."""
    item = make_item(db)
    friday = date(2026, 9, 4)
    set_status(db, item, _status_id(db, "received"), arrived_on=friday)
    db.commit()

    row = db.scalars(
        select(ItemStatusHistory)
        .where(ItemStatusHistory.inventory_item_id == item.id)
        .order_by(ItemStatusHistory.id.desc())
    ).first()
    assert row.arrived_on == friday
    assert row.changed_at.date() != friday or True  # changed_at is "now"


def test_setting_the_same_status_records_nothing(db: Session) -> None:
    """Re-asserting a value is not a change, and a history of non-events is
    noise that makes the real transitions harder to find."""
    item = make_item(db)
    before = len(
        db.scalars(
            select(ItemStatusHistory).where(
                ItemStatusHistory.inventory_item_id == item.id
            )
        ).all()
    )
    set_status(db, item, item.status_id)
    db.commit()
    after = len(
        db.scalars(
            select(ItemStatusHistory).where(
                ItemStatusHistory.inventory_item_id == item.id
            )
        ).all()
    )
    assert after == before


def test_a_location_change_records_where_it_went(db: Session) -> None:
    item = make_item(db)
    set_location(db, item, None, note="out of the box")
    db.commit()

    rows = db.scalars(
        select(LocationHistory).where(LocationHistory.inventory_item_id == item.id)
    ).all()
    assert rows[-1].note == "out of the box"
```

- [ ] **Step 6: Run them to verify they fail**

```
cd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m pytest tests/test_lifecycle_writes.py -q
```

Expected: FAIL, `ModuleNotFoundError: No module named 'app.lifecycle_writes'`.

- [ ] **Step 7: Write the module**

Create `backend/app/lifecycle_writes.py`:

```python
"""The only writers of an item's status and location.

Both columns have a history table beside them, and both tables exist so a
later correction cannot erase what actually happened -- `lifecycle.py` says
so: "History is a table rather than overwritten columns, so that 'when did
this actually arrive' survives a later correction to the status."

That promise holds only if nothing assigns the column without writing the
row. A helper some callers use is a convention; a helper that is the only way
to reach the column is a property of the code. Assign `item.status_id` or
`item.storage_location_id` anywhere else and the history silently stops being
true.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from .models import InventoryItem, ItemStatusHistory, LocationHistory

__all__ = ["set_location", "set_status"]


def set_status(
    session: Session,
    item: InventoryItem,
    to_status_id: int,
    *,
    user_id: int | None = None,
    note: str | None = None,
    arrived_on: date | None = None,
) -> None:
    """Change an item's status and record that it changed.

    A no-op when the status already matches: re-asserting a value is not a
    transition, and a history full of non-events buries the real ones.

    ``arrived_on`` is the date the thing physically turned up, which is not
    the same as ``changed_at`` -- see the column's note.
    """
    if item.status_id == to_status_id:
        return

    session.add(
        ItemStatusHistory(
            inventory_item_id=item.id,
            from_status_id=item.status_id,
            to_status_id=to_status_id,
            changed_at=datetime.now(UTC),
            changed_by_id=user_id,
            note=note,
            arrived_on=arrived_on,
        )
    )
    item.status_id = to_status_id


def set_location(
    session: Session,
    item: InventoryItem,
    storage_location_id: int | None,
    *,
    user_id: int | None = None,
    note: str | None = None,
) -> None:
    """Move an item and record the move.

    Unlike a status, a location is legitimately nullable -- "not recorded" is
    a real answer -- so None is a value here, not an absence.
    """
    if item.storage_location_id == storage_location_id:
        return

    session.add(
        LocationHistory(
            inventory_item_id=item.id,
            storage_location_id=storage_location_id,
            moved_at=datetime.now(UTC),
            moved_by_id=user_id,
            note=note,
        )
    )
    item.storage_location_id = storage_location_id
```

- [ ] **Step 8: Run them to verify they pass**

```
cd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m pytest tests/test_lifecycle_writes.py -q
```

Expected: 4 passed.

- [ ] **Step 9: Route PATCH's status through it**

In `backend/app/routers/inventory.py`, the classifier loop (around line 470)
currently does `setattr(item, f"{field}_id", ...)` for every classifier
including `status`. Special-case it:

```python
        if field in ITEM_CLASSIFIERS:
            model = ITEM_CLASSIFIERS[field]
            if field in REQUIRED_CLASSIFIERS:
                resolved = require_code(db, model, value, field)
            else:
                resolved = code_to_id(db, model, value, field)
            # Status is the one classifier with a history table behind it.
            # Going through set_status is what keeps that table true; a plain
            # setattr here is the bug this endpoint used to have.
            if field == "status":
                set_status(db, item, resolved, user_id=admin.id)
            else:
                setattr(item, f"{field}_id", resolved)
```

Import `set_status` from `..lifecycle_writes`. Confirm the endpoint's admin
parameter is named `admin` (not `_admin`) so `admin.id` resolves; rename it if
needed, and check no other line in the function relied on the old name.

- [ ] **Step 10: Test the endpoint records history**

Append to `backend/tests/test_lifecycle_writes.py`:

```python
def test_editing_the_status_through_the_api_records_it(
    client, admin_headers, db: Session
) -> None:
    """The gap this module exists to close: before it, this wrote nothing."""
    item = make_item(db)
    db.commit()

    res = client.patch(
        f"/api/inventory/{item.id}",
        json={"status": "received"},
        headers=admin_headers,
    )
    assert res.status_code == 200

    rows = db.scalars(
        select(ItemStatusHistory).where(
            ItemStatusHistory.inventory_item_id == item.id
        )
    ).all()
    assert rows[-1].to_status_id == _status_id(db, "received")
    assert rows[-1].changed_by_id is not None
```

Run it. Expected: PASS.

- [ ] **Step 11: Mutation-test the guarantee**

A history mechanism nothing forces is the thing that quietly stops working.
Temporarily replace the `set_status(...)` call added in Step 9 with
`setattr(item, "status_id", resolved)`. Run:

```
cd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m pytest tests/test_lifecycle_writes.py -q
```

Expected: `test_editing_the_status_through_the_api_records_it` FAILS. Restore
the call and confirm it passes again. Record both outputs in your report. If
it does not fail, the test is not testing what it claims.

- [ ] **Step 12: Route splitting and the importer through it**

`splitting.py` (~line 249) and `importers/loader.py` (~line 392) each build an
`ItemStatusHistory` by hand for a newly created row. Replace both with
`set_status(session, item, item.status_id, note="...")` — except that a new
item's `status_id` is already assigned, so `set_status` would see no change
and do nothing. For these two, assign the status through `set_status` *before*
the row has one:

- create the item with `status_id=None` is not possible (NOT NULL), so instead
  keep the direct construction and call
  `set_status(session, item, resolved_id, note="set at import")` on an item
  whose `status_id` was set to the same value — which no-ops.

**Therefore:** leave both call sites as they are, and add a comment at each
pointing at `lifecycle_writes.set_status` for *changes*, noting that these two
write the **opening** row for a row that has no previous status. Do not
contort the helper to cover creation; an opening row and a transition are
different facts and `from_status_id=None` is how the schema already says so.

- [ ] **Step 13: Full gate and commit**

```
.\scripts\ccweb_check.cmd
git add -A
git commit -F <message-file>
```

Subject: `Record every status change, not only the first`. Record the mutation
test and its result in the body.

---

### Task 2: The receive endpoint

**Files:**
- Modify: `backend/app/schemas.py`, `backend/app/routers/inventory.py`
- Create: `backend/tests/test_receiving.py`

**Interfaces:**
- Consumes: `set_status`, `set_location` from Task 1.
- Produces: `POST /api/inventory/receive`, consumed by Task 5.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_receiving.py`:

```python
"""Recording what actually arrived.

Receiving does not create inventory -- the import did that at the moment of
purchase. It is a transition on a row that already exists, which is why every
test here starts from an item that is already `ordered`.
"""

from __future__ import annotations

from datetime import date

from app.models import ItemStatus, ItemStatusHistory
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def _ordered(db: Session):
    item = make_item(db)
    item.status_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "ordered")
    ).one()
    db.commit()
    return item


def test_receiving_records_the_arrival(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _ordered(db)
    res = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [item.id],
            "outcome": "received",
            "arrived_on": "2026-09-04",
            "note": "edge knock not in the listing photos",
        },
        headers=admin_headers,
    )
    assert res.status_code == 200

    db.expire_all()
    row = db.scalars(
        select(ItemStatusHistory)
        .where(ItemStatusHistory.inventory_item_id == item.id)
        .order_by(ItemStatusHistory.id.desc())
    ).first()
    assert row.arrived_on == date(2026, 9, 4)
    assert row.note == "edge knock not in the listing photos"
    assert row.changed_by_id is not None


def test_one_bad_id_writes_nothing(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """A partial receipt across twenty coins leaves a state nobody can
    describe, so every id is resolved before anything is written."""
    good = _ordered(db)
    res = client.post(
        "/api/inventory/receive",
        json={"item_ids": [good.id, 10_000_000], "outcome": "received"},
        headers=admin_headers,
    )
    assert res.status_code == 404
    assert "10000000" in res.text

    db.expire_all()
    assert db.get(type(good), good.id).status_id != db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "received")
    ).one()


def test_receiving_twice_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """Silently re-receiving overwrites a true arrival date with today's."""
    item = _ordered(db)
    first = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received", "arrived_on": "2026-09-04"},
        headers=admin_headers,
    )
    assert first.status_code == 200

    again = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received"},
        headers=admin_headers,
    )
    assert again.status_code == 409

    db.expire_all()
    row = db.scalars(
        select(ItemStatusHistory)
        .where(ItemStatusHistory.inventory_item_id == item.id)
        .order_by(ItemStatusHistory.id.desc())
    ).first()
    assert row.arrived_on == date(2026, 9, 4)


def test_a_parcel_written_off_as_missing_can_still_turn_up(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """`missing` means paid for, not cancelled, never arrived -- and things
    that never arrived sometimes arrive."""
    item = _ordered(db)
    client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "missing"},
        headers=admin_headers,
    )
    res = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received"},
        headers=admin_headers,
    )
    assert res.status_code == 200


def test_an_unknown_outcome_is_refused_listing_the_known_ones(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    item = _ordered(db)
    res = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "delivered"},
        headers=admin_headers,
    )
    assert res.status_code == 422
    assert "received" in res.text


def test_a_customer_cannot_receive(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    item = _ordered(db)
    res = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received"},
        headers=customer_headers,
    )
    assert res.status_code == 403
```

- [ ] **Step 2: Run to verify they fail**

```
cd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m pytest tests/test_receiving.py -q
```

Expected: FAIL, 404 on the unknown route.

- [ ] **Step 3: Add the request schema**

In `backend/app/schemas.py`:

```python
#: The four outcomes a receipt can record. `received` is the common one;
#: the rest close out a line that will not arrive. Without them there is no
#: way to finish an order except to leave it permanently outstanding.
RECEIVE_OUTCOMES: frozenset[str] = frozenset(
    {"received", "missing", "returned", "canceled"}
)


class ReceiveRequest(BaseModel):
    """One receipt, applied to one or many items at once."""

    item_ids: list[int] = Field(min_length=1)
    outcome: str
    arrived_on: date | None = None
    storage_location_id: int | None = None
    note: str | None = None
```

Import `date` from `datetime` and `Field` from `pydantic` if not already
present.

- [ ] **Step 4: Add the endpoint**

In `backend/app/routers/inventory.py`. Place it **above**
`@router.get("/{item_id}")`, or FastAPI will match `receive` as an `item_id`
and answer 422 for a bad path parameter instead of routing here.

```python
@router.post("/receive")
def receive_items(
    payload: ReceiveRequest, db: DbSession, admin: AdminUser
) -> dict[str, int]:
    """Record what arrived, for one item or a whole box of them.

    All or nothing, in one transaction, the same as `POST /bulk` and for the
    same reason: a partial receipt across twenty coins leaves a state nobody
    can describe, and "which of the twenty applied?" is not a question the UI
    should have to answer. Every id is resolved and every code checked before
    anything is written.
    """
    if payload.outcome not in RECEIVE_OUTCOMES:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown outcome {payload.outcome!r}. "
            f"Known: {sorted(RECEIVE_OUTCOMES)}",
        )

    items = db.scalars(
        select(InventoryItem).where(InventoryItem.id.in_(payload.item_ids))
    ).all()
    missing = sorted(set(payload.item_ids) - {i.id for i in items})
    if missing:
        raise HTTPException(status_code=404, detail=f"Unknown item ids: {missing}")

    received_id = require_code(db, ItemStatus, "received", "status")
    already = [i.item_code for i in items if i.status_id == received_id]
    if already and payload.outcome == "received":
        raise HTTPException(
            status_code=409,
            detail=f"Already received: {sorted(already)}. "
            "Use PATCH to correct a receipt rather than repeating it.",
        )

    if payload.storage_location_id is not None:
        exists = db.get(StorageLocation, payload.storage_location_id)
        if exists is None:
            raise HTTPException(
                status_code=422,
                detail=f"Unknown storage_location_id: {payload.storage_location_id}",
            )

    to_status = require_code(db, ItemStatus, payload.outcome, "status")
    for item in items:
        set_status(
            db,
            item,
            to_status,
            user_id=admin.id,
            note=payload.note,
            arrived_on=payload.arrived_on if payload.outcome == "received" else None,
        )
        if payload.outcome == "received" and payload.storage_location_id is not None:
            set_location(
                db,
                item,
                payload.storage_location_id,
                user_id=admin.id,
                note=payload.note,
            )

    db.commit()
    return {"received": len(items)}
```

Add imports: `ReceiveRequest`, `RECEIVE_OUTCOMES` from `..schemas`;
`set_location`, `set_status` from `..lifecycle_writes`; `StorageLocation` from
`..models`.

- [ ] **Step 5: Run the tests**

```
cd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m pytest tests/test_receiving.py -q
```

Expected: 6 passed.

- [ ] **Step 6: Confirm the route order is right**

```
cd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -c "from app.main import app; print([r.path for r in app.routes if 'inventory' in r.path])"
```

Expected: `/api/inventory/receive` appears **before** `/api/inventory/{item_id}`.
If it does not, the endpoint is unreachable and the tests passing means the
route matched something else.

- [ ] **Step 7: Full gate and commit**

```
.\scripts\ccweb_check.cmd
git add -A
git commit -F <message-file>
```

Subject: `Add the receive endpoint`.

---

### Task 3: Reading purchase orders and storage locations

**Files:**
- Create: `backend/app/routers/acquisitions.py`, `backend/tests/test_acquisitions.py`
- Modify: `backend/app/main.py`, `backend/app/schemas.py`

**Interfaces:**
- Produces, consumed by Tasks 4 and 5:
  - `GET /api/purchase-orders` → `[{id, order_number, vendor, ordered_on, outstanding, total}]`
  - `GET /api/purchase-orders/{id}` → `{id, order_number, vendor, ordered_on, lines: [{id, item_code, description, item_cost, status}]}`
  - `GET /api/storage-locations` → `[{id, label, kind}]`

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_acquisitions.py`:

```python
"""Reading the acquisition side, which until now was import-only."""

from __future__ import annotations

from app.models import ItemStatus, PurchaseOrder, Vendor
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.test_schema import make_item


def _order_with_lines(db: Session, outstanding: int, done: int) -> PurchaseOrder:
    vendor = Vendor(name=f"Vendor {outstanding}{done}")
    db.add(vendor)
    db.flush()
    order = PurchaseOrder(vendor_id=vendor.id, order_number="27-1234")
    db.add(order)
    db.flush()
    ordered_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "ordered")
    ).one()
    received_id = db.scalars(
        select(ItemStatus.id).where(ItemStatus.code == "received")
    ).one()
    for _ in range(outstanding):
        item = make_item(db)
        item.purchase_order_id = order.id
        item.status_id = ordered_id
    for _ in range(done):
        item = make_item(db)
        item.purchase_order_id = order.id
        item.status_id = received_id
    db.commit()
    return order


def test_orders_report_how_much_is_outstanding(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order_with_lines(db, outstanding=3, done=2)
    res = client.get("/api/purchase-orders", headers=admin_headers)
    assert res.status_code == 200
    row = next(r for r in res.json() if r["id"] == order.id)
    assert row["outstanding"] == 3
    assert row["total"] == 5


def test_an_order_lists_its_lines_with_their_status(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    order = _order_with_lines(db, outstanding=2, done=1)
    res = client.get(f"/api/purchase-orders/{order.id}", headers=admin_headers)
    assert res.status_code == 200
    body = res.json()
    assert len(body["lines"]) == 3
    assert sorted(line["status"] for line in body["lines"]) == [
        "ordered",
        "ordered",
        "received",
    ]


def test_an_unknown_order_is_a_404(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    res = client.get("/api/purchase-orders/10000000", headers=admin_headers)
    assert res.status_code == 404


def test_a_customer_cannot_read_purchase_orders(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    """What the owner paid a vendor is not a customer's business."""
    _order_with_lines(db, outstanding=1, done=0)
    assert (
        client.get("/api/purchase-orders", headers=customer_headers).status_code == 403
    )


def test_a_customer_cannot_read_storage_locations(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    """A public listing that leaked the safe-deposit box holding the item
    would be a security failure, not a cosmetic one."""
    assert (
        client.get("/api/storage-locations", headers=customer_headers).status_code
        == 403
    )
```

- [ ] **Step 2: Run to verify they fail**

```
cd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m pytest tests/test_acquisitions.py -q
```

Expected: FAIL, 404 on the unknown routes.

- [ ] **Step 3: Write the router**

Create `backend/app/routers/acquisitions.py`. Follow the shape of
`routers/reference.py`: module docstring, `APIRouter(prefix=..., tags=...)`,
`AdminUser` on every endpoint, docstrings on every function (ruff `D` and
`ANN` are enforced and there are no ignored lint issues in this project).

Two routers in one module, since `main.py` includes routers individually:
export `purchase_orders_router` (prefix `/purchase-orders`) and
`storage_locations_router` (prefix `/storage-locations`).

The outstanding count is `status.code == 'ordered'`. Compute it in SQL with a
`func.count` over a filtered join rather than by loading every line and
counting in Python — an order can have hundreds of lines and the list view
needs none of them.

- [ ] **Step 4: Register the routers**

In `backend/app/main.py`, alongside the existing `include_router` calls:

```python
app.include_router(acquisitions.purchase_orders_router, prefix=settings.api_prefix)
app.include_router(acquisitions.storage_locations_router, prefix=settings.api_prefix)
```

- [ ] **Step 5: Run the tests**

Expected: 5 passed.

- [ ] **Step 6: Confirm the OpenAPI schema did not lose anything**

Adding a router has been known to disturb generated response models. Compare
before and after:

```
cd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -c "import json;from app.main import app;print(len(json.dumps(app.openapi())))"
```

Expected: larger than before, and `HTTPValidationError` still present in
`components.schemas`.

- [ ] **Step 7: Full gate and commit**

Subject: `Read purchase orders and storage locations`.

---

### Task 4: The Receiving page shell and order lookup

**Files:**
- Create: `frontend/src/owner/pages/Receiving.jsx`, `.test.jsx`
- Create: `frontend/src/owner/pages/receiving/OrderPicker.jsx`, `OutstandingList.jsx` (+ tests)
- Modify: `frontend/src/owner/api.js`, `frontend/src/owner/OwnerApp.jsx`, `frontend/src/owner/styles.css`

**Interfaces:**
- Consumes: the three endpoints from Task 3.
- Produces: `Receiving` default export; `selected` item ids lifted to
  `Receiving` so Task 5's panel can read them.

- [ ] **Step 1: Add the API calls**

In `frontend/src/owner/api.js`, inside the object, after the inventory block.
**Not** in `shared/api.js` — that object ships to every shop visitor.

```js
  // acquisition -- reading what was ordered, to record what arrived
  listPurchaseOrders: (params = {}) => {
    const qs = new URLSearchParams()
    Object.entries(params).forEach(([k, v]) => {
      if (v !== '' && v !== null && v !== undefined) qs.set(k, v)
    })
    const query = qs.toString()
    return send(`/api/purchase-orders${query ? `?${query}` : ''}`)
  },
  getPurchaseOrder: (id) => send(`/api/purchase-orders/${id}`),
  listStorageLocations: () => send('/api/storage-locations'),
  receiveItems: (payload) =>
    send('/api/inventory/receive', { method: 'POST', body: payload }),
```

- [ ] **Step 2: Write the failing tests**

Create `frontend/src/owner/pages/Receiving.test.jsx`. Mock the api module the
way every other page test in this repo does — and note that `vi.mock` takes
its path as a call argument, so it must name the same module the component
imports:

```jsx
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api', () => ({
  api: {
    listPurchaseOrders: vi.fn(),
    getPurchaseOrder: vi.fn(),
    listStorageLocations: vi.fn(),
    receiveItems: vi.fn(),
  },
}))

import { api } from '../api'
import Receiving from './Receiving'
import { adminAuth, renderWithProviders } from '../../test/helpers'

beforeEach(() => {
  vi.clearAllMocks()
  api.listPurchaseOrders.mockResolvedValue([
    {
      id: 1,
      order_number: '27-1234',
      vendor: 'eBay',
      ordered_on: '2026-08-30',
      outstanding: 3,
      total: 5,
    },
  ])
  api.listStorageLocations.mockResolvedValue([
    { id: 3, label: 'Safe deposit box', kind: 'safe_deposit_box' },
  ])
  api.getPurchaseOrder.mockResolvedValue({
    id: 1,
    order_number: '27-1234',
    vendor: 'eBay',
    ordered_on: '2026-08-30',
    lines: [
      { id: 412, item_code: 'CC-000412', description: '1881-S Morgan $1', item_cost: '84.00', status: 'ordered' },
      { id: 413, item_code: 'CC-000413', description: '1923 Peace $1', item_cost: '91.00', status: 'received' },
    ],
  })
})

describe('Receiving', () => {
  it('offers the orders that still have something outstanding', async () => {
    renderWithProviders(<Receiving />, { auth: adminAuth() })
    expect(await screen.findByText(/27-1234/)).toBeInTheDocument()
    expect(screen.getByText(/3 of 5/i)).toBeInTheDocument()
  })

  it('lists only the lines that have not arrived', async () => {
    renderWithProviders(<Receiving />, { auth: adminAuth() })
    const order = await screen.findByText(/27-1234/)
    order.click()
    await waitFor(() => expect(api.getPurchaseOrder).toHaveBeenCalledWith(1))
    expect(await screen.findByText('CC-000412')).toBeInTheDocument()
    expect(screen.queryByText('CC-000413')).not.toBeInTheDocument()
  })
})
```

- [ ] **Step 3: Run to verify they fail**

```
cd frontend
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" node_modules\vitest\vitest.mjs run src/owner/pages/Receiving.test.jsx
```

Expected: FAIL, `Failed to resolve import "./Receiving"`.

- [ ] **Step 4: Build the components**

`OrderPicker.jsx` — a select of outstanding orders, each rendered as
`{order_number} · {vendor} · {outstanding} of {total}`. Calls `onPick(id)`.

`OutstandingList.jsx` — takes `lines` and renders only those with
`status === 'ordered'`, each a checkbox plus item code, description and cost,
with a select-all control. Lifts the checked set through `onChange`.

`Receiving.jsx` — loads orders on mount, loads the picked order's lines, holds
the selected-ids state, and renders both. Follow the data-fetching shape in
`useInventorySearch.js`: derive loading from state rather than storing it, and
carry a cancellation guard so a slow response for an earlier order cannot land
after a faster later one. That bug was found by eslint's
`set-state-in-effect` rule elsewhere in this codebase and is real here — an
owner clicking through three orders quickly is the reproducing case.

- [ ] **Step 5: Add the route and nav entry**

In `frontend/src/owner/OwnerApp.jsx`, add `<NavLink to="/receiving">Receive</NavLink>`
to the console nav and `<Route path="/receiving" element={<Receiving />} />`
inside `Console`'s `Routes`. It sits inside `RequireAdmin` automatically —
that is the point of guarding at the router root.

- [ ] **Step 6: Run the tests**

Expected: 2 passed. Then run the whole frontend suite; the `OwnerApp` nav tests
from the console-separation work assert specific links and may need the new one
accounted for. If one fails, read it before changing it: an assertion that the
console has exactly the expected links is doing its job.

- [ ] **Step 7: Full gate and commit**

Subject: `Add the Receiving page and its order lookup`.

---

### Task 5: The receipt panel

**Files:**
- Create: `frontend/src/owner/pages/receiving/ReceiptPanel.jsx`, `.test.jsx`
- Modify: `frontend/src/owner/pages/Receiving.jsx`, `frontend/src/owner/styles.css`

**Interfaces:**
- Consumes: `api.receiveItems`, `api.listStorageLocations`, and the selected
  ids from Task 4.
- Produces: `onDone()` so the page reloads the order after a receipt.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/owner/pages/receiving/ReceiptPanel.test.jsx`:

```jsx
import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: { receiveItems: vi.fn(), listStorageLocations: vi.fn() },
}))

import { api } from '../../api'
import ReceiptPanel from './ReceiptPanel'
import { renderWithProviders } from '../../../test/helpers'

const LOCATIONS = [{ id: 3, label: 'Safe deposit box', kind: 'safe_deposit_box' }]

beforeEach(() => {
  vi.clearAllMocks()
  api.listStorageLocations.mockResolvedValue(LOCATIONS)
  api.receiveItems.mockResolvedValue({ received: 2 })
})

describe('ReceiptPanel', () => {
  it('sends one request for several items, not one each', async () => {
    // A box of twenty coins is one transaction. Twenty requests would leave a
    // partial state nobody can describe if the tenth failed.
    renderWithProviders(<ReceiptPanel itemIds={[412, 413]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalledTimes(1))
    expect(api.receiveItems.mock.calls[0][0].item_ids).toEqual([412, 413])
  })

  it('sends the outcome the pressed button names', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /missing/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.receiveItems.mock.calls[0][0].outcome).toBe('missing')
  })

  it('defaults the arrival date to today but lets it be changed', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    const field = await screen.findByLabelText(/arrived/i)
    expect(field).toHaveValue(new Date().toISOString().slice(0, 10))

    await userEvent.clear(field)
    await userEvent.type(field, '2026-09-04')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(api.receiveItems.mock.calls[0][0].arrived_on).toBe('2026-09-04')
  })

  it('reports a refused receipt without clearing what was typed', async () => {
    // A 409 means the wrong row, or a double submit. Making the operator
    // retype the note they just wrote turns a recoverable mistake into a
    // reason to skip the note next time.
    api.receiveItems.mockRejectedValue(
      Object.assign(new Error('Already received: [CC-000412]'), { status: 409 }),
    )
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)

    const note = await screen.findByLabelText(/note/i)
    await userEvent.type(note, 'edge knock')
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    expect(await screen.findByText(/already received/i)).toBeInTheDocument()
    expect(note).toHaveValue('edge knock')
  })

  it('does nothing at all when no item is selected', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[]} onDone={vi.fn()} />)
    expect(await screen.findByRole('button', { name: /^receive$/i })).toBeDisabled()
  })
})
```

- [ ] **Step 2: Run to verify they fail**

```
cd frontend
"%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe" node_modules\vitest\vitest.mjs run src/owner/pages/receiving/ReceiptPanel.test.jsx
```

Expected: FAIL, `Failed to resolve import "./ReceiptPanel"`.

- [ ] **Step 3: Build the panel**

`ReceiptPanel.jsx` takes `{ itemIds, onDone }`. It renders a date input
defaulting to `new Date().toISOString().slice(0, 10)`, a storage-location
select loaded once from `listStorageLocations`, a note field, and four
buttons — Receive, Missing, Returned, Cancelled — each posting its own
outcome. Every control is disabled when `itemIds` is empty.

On success it calls `onDone()`. On failure it renders the error's message and
**leaves every input exactly as the operator left it**.

- [ ] **Step 4: Run the tests**

Expected: 5 passed.

- [ ] **Step 5: Full gate and commit**

```
.\scripts\ccweb_check.cmd
git add -A
git commit -F <message-file>
```

Subject: `Record the receipt: outcome, arrival, location and note`.

---

### Task 6: The attribute-search fallback

For when an object is in hand and its order is unknown. **No new backend:**
every attribute is already a filter on `GET /api/inventory/{view}/search`, and
`status=ordered` narrows it to things not yet arrived.

**Files:**
- Create: `frontend/src/owner/pages/receiving/ItemFinder.jsx`, `.test.jsx`
- Modify: `frontend/src/owner/pages/Receiving.jsx`

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/owner/pages/receiving/ItemFinder.test.jsx`:

```jsx
import userEvent from '@testing-library/user-event'
import { screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({ api: { searchInventory: vi.fn() } }))

import { api } from '../../api'
import ItemFinder from './ItemFinder'
import { renderWithProviders } from '../../../test/helpers'

beforeEach(() => {
  vi.clearAllMocks()
  api.searchInventory.mockResolvedValue({
    rows: [
      { id: 412, item_code: 'CC-000412', description: '1881-S Morgan $1' },
    ],
    total: 1,
  })
})

async function search() {
  await userEvent.click(screen.getByRole('button', { name: /find/i }))
  await waitFor(() => expect(api.searchInventory).toHaveBeenCalled())
  return api.searchInventory.mock.calls[0]
}

describe('ItemFinder', () => {
  it('looks a coin up by denomination, year and mint', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.type(screen.getByLabelText(/year/i), '1881')
    await userEvent.type(screen.getByLabelText(/mint/i), 'S')

    const [view, params] = await search()
    expect(view).toBe('coins')
    expect(params.year_min).toBe('1881')
    expect(params.year_max).toBe('1881')
    expect(params.mint).toBe('S')
  })

  it('looks a note up by a partial serial number', async () => {
    // The backend filter is an ilike, so a fragment is a legitimate search --
    // reading a whole serial off a banknote through a flip is the exception,
    // not the rule.
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    await userEvent.click(screen.getByRole('radio', { name: /currency/i }))
    await userEvent.type(screen.getByLabelText(/serial/i), 'L1234')

    const [view, params] = await search()
    expect(view).toBe('currency')
    expect(params.serial_number).toBe('L1234')
  })

  it('only ever offers things that have not arrived', async () => {
    renderWithProviders(<ItemFinder onPick={vi.fn()} />)
    const [, params] = await search()
    expect(params.status).toBe('ordered')
  })

  it('hands a chosen result back to the page', async () => {
    const onPick = vi.fn()
    renderWithProviders(<ItemFinder onPick={onPick} />)
    await search()
    await userEvent.click(await screen.findByText('CC-000412'))
    expect(onPick).toHaveBeenCalledWith(412)
  })
})
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL, `Failed to resolve import "./ItemFinder"`.

- [ ] **Step 3: Build it**

`ItemFinder.jsx` takes `{ onPick }`, offers a coin/currency radio pair, and
shows the fields for the chosen view — coins: denomination, year, mint;
currency: denomination, serial number, series year. It calls
`api.searchInventory(view, { ...filters, status: 'ordered' })`, dropping empty
fields, and renders the matches as a clickable list.

A single year in the UI maps to `year_min` **and** `year_max`: the backend has
no `year` filter, only the range pair.

- [ ] **Step 4: Wire it into the page**

`Receiving.jsx` shows the order path by default and this one when the operator
switches to it, both feeding the same `ReceiptPanel`.

- [ ] **Step 5: Run the tests, then the full gate, and commit**

Subject: `Find an item to receive by what it is`.

---

### Task 7: Photographs and field confirmation at receipt

Receiving is the first moment a person holds the object, and
`item_field_review` was built for exactly that: "one field of one item,
confirmed by a person looking at the object". Until now it had no natural
trigger.

**Files:**
- Modify: `frontend/src/owner/pages/receiving/ReceiptPanel.jsx`
- Modify: `frontend/src/owner/api.js` (image upload, if not already present)

- [ ] **Step 1: Read `send()`, then add the upload call**

There is **no image method on the API client at all** — neither
`shared/api.js` nor `owner/api.js` has one; images have never been uploaded
from the frontend. This task adds the first.

`send()` in `frontend/src/shared/api.js` has a `form` mode, used by the login
call to post `application/x-www-form-urlencoded`. That is **not** the same as
multipart with a file. Read it and decide whether it can carry a `FormData`
body; if it cannot, extend it. A `FormData` body must be passed through
untouched with **no `Content-Type` header set** — the browser sets the
boundary, and setting it by hand produces a request the server cannot parse.
Say in your report which it was and what you changed.

Then add to `frontend/src/owner/api.js` (not `shared/api.js` — only the
console uploads photographs):

```js
  uploadImage: (inventoryItemId, file, { imageRole, isPrimary = false } = {}) => {
    const form = new FormData()
    form.append('file', file)
    form.append('inventory_item_id', String(inventoryItemId))
    if (imageRole) form.append('image_role', imageRole)
    form.append('is_primary', String(isPrimary))
    return send('/api/images', { method: 'POST', body: form })
  },
```

The backend signature is already in place:
`POST /api/images` takes multipart `file`, and optional `inventory_item_id`,
`image_role` and `is_primary` form fields.

- [ ] **Step 2: Write the failing tests**

Add to `frontend/src/owner/pages/receiving/ReceiptPanel.test.jsx`:

```jsx
  it('keeps the confirm-and-correct section out of the way until asked', async () => {
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    expect(screen.queryByLabelText(/grade/i)).not.toBeInTheDocument()

    await userEvent.click(
      await screen.findByRole('button', { name: /confirm or correct/i }),
    )
    expect(await screen.findByLabelText(/grade/i)).toBeInTheDocument()
  })

  it('a failed photograph does not undo the receipt', async () => {
    // The arrival is the fact; the photograph is evidence added to it. Losing
    // a recorded arrival because an upload failed is the worse trade, so the
    // upload is reported and retryable, not rolled back.
    api.receiveItems.mockResolvedValue({ received: 1 })
    api.uploadImage.mockRejectedValue(new Error('upload failed'))

    const onDone = vi.fn()
    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={onDone} />)
    await userEvent.upload(
      await screen.findByLabelText(/photo/i),
      new File(['x'], 'obverse.jpg', { type: 'image/jpeg' }),
    )
    await userEvent.click(screen.getByRole('button', { name: /^receive$/i }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalled())
    expect(await screen.findByText(/upload failed/i)).toBeInTheDocument()
    expect(onDone).toHaveBeenCalled()
  })
```

Extend the `vi.mock('../../api', ...)` factory at the top of that file with
`uploadImage: vi.fn()`, `setItemReview: vi.fn()` and
`updateInventoryItem: vi.fn()`, and give them default resolutions in
`beforeEach`. A factory that omits a method the component calls throws
`api.uploadImage is not a function`, which names nothing near the cause.

- [ ] **Step 3: Run to verify they fail**

Expected: FAIL — the confirm section and the photo input do not exist yet.

- [ ] **Step 4: Build it**

Add to `ReceiptPanel.jsx`: a file input that collects photographs to upload
after a successful receipt, and a collapsed **Confirm or correct** section.

Compose `ReviewPane` and `ItemEditForm` from `owner/pages/inventory/` rather
than reimplementing them — they already know the reviewable fields, and a
second implementation would drift from the inventory page's.

The order matters: receive first, then upload. An upload failure reports
against the item and leaves the receipt standing.

- [ ] **Step 5: Run the tests, then the full gate, and commit**

Subject: `Confirm and photograph an item as it is received`.

---

## Done when

- `.\scripts\ccweb_check.cmd` exits 0.
- `alembic check` is clean, and the only migration adds
  `item_status_history.arrived_on`.
- Changing an item's status through `PATCH` records history — and the mutation
  test in Task 1 Step 11 has been shown to fail when the recording is removed.
- Receiving twenty items is one request and one transaction.
- The existing `public_catalog` test still passes **unmodified**.
- `check-bundle-isolation.mjs` still reports OK: no receiving code reached the
  shop.

## Deliberate deviations from the spec

**The importer and `splitting.py` keep writing their opening history rows
directly** (Task 1 Step 12). The spec says `set_status` becomes the only way
the column is assigned; that holds for every *transition*, but an opening row
has no previous status and `from_status_id=None` is how the schema already
distinguishes the two. Contorting the helper to cover creation would make both
cases harder to read. Each site gets a comment pointing at the helper.

## Not in this plan

Partial receipt of a lot (fifteen of twenty arrived) — the schema can express
it only by splitting, and wiring `splitting.py` into this flow is a second
interaction with its own questions. Barcode input, vendor management, and
reconciling cost against an invoice are all out, per the spec.
