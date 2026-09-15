# Orders on a Customer's Behalf Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

Written 2026-09-15.

**Goal:** Let an administrator place an order for any customer and edit a pending or paid order's lines, prices, customer and notes from the owner console, with who placed each order and a history row for every change.

**Architecture:** A new `backend/app/order_writes.py` is the only code that moves order stock; the shop's checkout and two new admin endpoints call it. Admin endpoints live apart from the shopper's `POST /api/orders`, whose contract does not change. The console gains an order editor dialog and a history view on the existing Orders page.

**Tech Stack:** FastAPI, SQLAlchemy 2, Alembic, PostgreSQL 17, pytest; React 19, Vitest, Testing Library.

**Spec:** `docs/specs/order-on-behalf-design.md`

## Global Constraints

- Branch `feat/orders-on-behalf`. Never commit to `main`.
- Money is `Decimal` on the server and integer cents in the browser. Never a float.
- Every public function, class and method has a docstring and full type annotations (ruff `D`, `ANN`). No `noqa`, no ignored lint.
- Write files with the Write/Edit tools, never shell heredocs. cmd, never PowerShell, in anything shipped.
- Commands run from the repo root unless stated. Python: `%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe -m pytest backend/tests/<file> -p no:cacheprovider`. Vitest, from `frontend\`: `%USERPROFILE%\miniforge3\envs\ccwebdb\node.exe node_modules\vitest\vitest.mjs run <file>`. Never run two pytest sessions at once -- they share the test database.
- New request schemas use `ConfigDict(extra="forbid")`.
- Migrations: name every foreign key, `DROP TYPE IF EXISTS` every enum type a downgrade leaves, drop children before parents. `backend/tests/test_migrations.py` must stay green.
- `__mapper_args__` is a `@declared_attr.directive`, never a dict literal (`docs/code-quality.md`).
- `scripts\ccweb_check.cmd` passes before every commit.
- Editable statuses: exactly `pending` and `paid`.
- Change kinds, exactly: `placed`, `line_added`, `line_removed`, `quantity`, `unit_price`, `customer`, `notes`, `status`, `total`.
- Money in history values is a plain two-place decimal string: `150.00`. Line values are `<quantity> @ <price>`: `2 @ 189.00`.

## File Structure

| File | Responsibility |
|---|---|
| `backend/alembic/versions/a3d9f1c27b40_record_who_placed_and_changed_orders.py` | create: `placed_by_id`, `version`, `sales_order_change`, its enum |
| `backend/app/models/sales.py` | modify: `SalesOrderChangeKind`, `SalesOrderChange`, new `SalesOrder` columns and relationships |
| `backend/app/models/__init__.py` | modify: export the new names |
| `backend/app/order_writes.py` | create: `Line`, `customer_for_user`, `place_order`, `revise_order`, `record_status_change`, `payment_adjustment_due` |
| `backend/app/schemas.py` | modify: admin order schemas, `OrderChangeOut`, `OrderOut` fields |
| `backend/app/routers/orders.py` | modify: checkout through `place_order`; `PUT /{id}`; `GET /{id}/changes`; status history; public `order_out` |
| `backend/app/routers/customers.py` | modify: `POST /{id}/orders` |
| `backend/app/routers/users.py` | modify: `POST /{id}/customer` |
| `backend/tests/test_order_writes.py` | create: placing, revising, history, access |
| `backend/tests/test_order_revision_race.py` | create: threaded edit-vs-checkout |
| `frontend/src/owner/api.js` | modify: four client calls |
| `frontend/src/owner/pages/orders/cents.js` (+ `.test.js`) | create: exact money arithmetic |
| `frontend/src/owner/pages/orders/OrderDialog.jsx` | create: modal wrapper |
| `frontend/src/owner/pages/orders/OrderEditor.jsx` (+ `.test.jsx`) | create: new/edit form |
| `frontend/src/owner/pages/orders/OrderHistory.jsx` (+ `.test.jsx`) | create: grouped change list |
| `frontend/src/owner/pages/Orders.jsx` (+ `.test.jsx`) | modify: New order, Edit, History, badges |
| `docs/system-administration.md` | modify: Orders section |

---

### Task 1: Schema -- who placed an order, its version, and its change history

**Files:**
- Create: `backend/alembic/versions/a3d9f1c27b40_record_who_placed_and_changed_orders.py`
- Modify: `backend/app/models/sales.py`, `backend/app/models/__init__.py`
- Test: `backend/tests/test_order_writes.py` (new), `backend/tests/test_migrations.py` (existing, unchanged)

**Interfaces:**
- Produces: `SalesOrderChangeKind` (StrEnum with the nine kinds); `SalesOrderChange` model (`sales_order_id`, `changed_at`, `changed_by_id`, `change`, `listing_id`, `from_value`, `to_value`, relationships `order`, `changed_by`, `listing`); `SalesOrder.placed_by_id`, `SalesOrder.placed_by`, `SalesOrder.version`, `SalesOrder.changes` (ordered by id).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/test_order_writes.py`:

```python
"""Orders placed and revised on a customer's behalf.

See docs/specs/order-on-behalf-design.md. The engine is `app.order_writes`;
these tests go through the HTTP API wherever a caller would.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import (
    Customer,
    SalesOrder,
    SalesOrderChange,
    SalesOrderChangeKind,
    SalesOrderStatus,
    User,
)
from sqlalchemy import select
from sqlalchemy.orm import Session


def test_an_order_records_its_placer_version_and_changes(
    db: Session, customer_user: User, admin_user: User
) -> None:
    customer = Customer(user_id=customer_user.id, display_name="Buyer")
    db.add(customer)
    db.flush()
    pending = db.scalar(
        select(SalesOrderStatus.id).where(SalesOrderStatus.code == "pending")
    )
    order = SalesOrder(
        customer_id=customer.id,
        sales_order_status_id=pending,
        placed_by_id=admin_user.id,
    )
    db.add(order)
    db.flush()
    db.add(
        SalesOrderChange(
            sales_order_id=order.id,
            changed_by_id=admin_user.id,
            change=SalesOrderChangeKind.placed,
            to_value=admin_user.email,
        )
    )
    db.commit()
    db.refresh(order)

    assert order.version == 1
    assert order.placed_by is not None and order.placed_by.email == admin_user.email
    assert [c.change for c in order.changes] == [SalesOrderChangeKind.placed]
    assert order.total_amount == Decimal("0.00")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `...python.exe -m pytest backend/tests/test_order_writes.py -p no:cacheprovider`
Expected: FAIL -- `ImportError: cannot import name 'SalesOrderChange'`.

- [ ] **Step 3: Add the models**

In `backend/app/models/sales.py`, add `SalesOrderChangeKind` after `AddressKind`:

```python
class SalesOrderChangeKind(enum.StrEnum):
    """What one row of an order's history records."""

    placed = "placed"
    line_added = "line_added"
    line_removed = "line_removed"
    quantity = "quantity"
    unit_price = "unit_price"
    customer = "customer"
    notes = "notes"
    status = "status"
    total = "total"
```

Add `from .scaffold import User` to the `TYPE_CHECKING` block. On `SalesOrder`, after `notes`:

```python
    #: The account that entered the order: the buyer, or an administrator
    #: acting for them. Null for orders placed before this was recorded.
    placed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    #: Optimistic concurrency, as on Listing and InventoryItem.
    version: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("1")
    )
```

and after its `shipments` relationship:

```python
    placed_by: Mapped[User | None] = relationship()
    changes: Mapped[list[SalesOrderChange]] = relationship(
        back_populates="order",
        cascade=_CASCADE_ALL_DELETE_ORPHAN,
        order_by="SalesOrderChange.id",
    )

    @declared_attr.directive
    def __mapper_args__(cls) -> dict[str, Any]:
        """Optimistic concurrency: every UPDATE checks the version it read."""
        return {"version_id_col": cls.version}
```

After `SalesOrderItem`, add:

```python
class SalesOrderChange(Base):
    """One change to an order: who, when, and what it was before and after.

    One save writes several rows sharing `changed_at` and `changed_by_id`.
    Values are text because they are of mixed kinds; `change` is closed.
    """

    __tablename__ = "sales_order_change"

    id: Mapped[int] = mapped_column(primary_key=True)
    sales_order_id: Mapped[int] = mapped_column(
        ForeignKey("sales_order.id", ondelete="CASCADE"), index=True, nullable=False
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    changed_by_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    change: Mapped[SalesOrderChangeKind] = mapped_column(
        enum_column(SalesOrderChangeKind, "sales_order_change_kind"), nullable=False
    )
    listing_id: Mapped[int | None] = mapped_column(
        ForeignKey("listing.id", ondelete="RESTRICT"), nullable=True
    )
    from_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    to_value: Mapped[str | None] = mapped_column(Text, nullable=True)

    order: Mapped[SalesOrder] = relationship(back_populates="changes")
    changed_by: Mapped[User | None] = relationship()
    listing: Mapped[Listing | None] = relationship()
```

Add `"SalesOrderChange"` and `"SalesOrderChangeKind"` to `__all__` in `sales.py`, and to the `from .sales import (...)` block and `__all__` in `backend/app/models/__init__.py`. Confirm `Text`, `DateTime`, `Integer`, `text` are already imported in `sales.py`; add any that are not.

- [ ] **Step 4: Write the migration**

Create `backend/alembic/versions/a3d9f1c27b40_record_who_placed_and_changed_orders.py`:

```python
"""record who placed and changed orders

An administrator may now place and edit orders for a customer, so an order
records the account that entered it, carries an optimistic-concurrency
version, and keeps a row per change in `sales_order_change`.

Existing orders get `version = 1` and no placer: nobody recorded who placed
them.

Revision ID: a3d9f1c27b40
Revises: e4b7a1c95d20
Create Date: 2026-09-15 12:00:00.000000

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = 'a3d9f1c27b40'
down_revision: str | None = 'e4b7a1c95d20'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_KINDS = (
    'placed', 'line_added', 'line_removed', 'quantity', 'unit_price',
    'customer', 'notes', 'status', 'total',
)


def upgrade() -> None:
    """Add the placer and version to orders, and the change history table."""
    postgresql.ENUM(*_KINDS, name='sales_order_change_kind').create(op.get_bind())

    op.add_column('sales_order', sa.Column('placed_by_id', sa.Integer(), nullable=True))
    # Named: autogenerate emits None, and drop_constraint(None) cannot run.
    op.create_foreign_key(
        'fk_sales_order_placed_by_id', 'sales_order', 'users',
        ['placed_by_id'], ['id'], ondelete='SET NULL',
    )
    op.add_column(
        'sales_order',
        sa.Column('version', sa.Integer(), server_default=sa.text('1'), nullable=False),
    )

    op.create_table(
        'sales_order_change',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sales_order_id', sa.Integer(), nullable=False),
        sa.Column('changed_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('changed_by_id', sa.Integer(), nullable=True),
        sa.Column(
            'change',
            postgresql.ENUM(*_KINDS, name='sales_order_change_kind', create_type=False),
            nullable=False,
        ),
        sa.Column('listing_id', sa.Integer(), nullable=True),
        sa.Column('from_value', sa.Text(), nullable=True),
        sa.Column('to_value', sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ['sales_order_id'], ['sales_order.id'],
            name='fk_sales_order_change_sales_order_id', ondelete='CASCADE',
        ),
        sa.ForeignKeyConstraint(
            ['changed_by_id'], ['users.id'],
            name='fk_sales_order_change_changed_by_id', ondelete='SET NULL',
        ),
        sa.ForeignKeyConstraint(
            ['listing_id'], ['listing.id'],
            name='fk_sales_order_change_listing_id', ondelete='RESTRICT',
        ),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(
        op.f('ix_sales_order_change_sales_order_id'),
        'sales_order_change', ['sales_order_id'], unique=False,
    )


def downgrade() -> None:
    """Drop the history before the type it uses, then the order columns."""
    op.drop_index(
        op.f('ix_sales_order_change_sales_order_id'), table_name='sales_order_change'
    )
    op.drop_table('sales_order_change')
    op.execute('DROP TYPE IF EXISTS sales_order_change_kind')
    op.drop_column('sales_order', 'version')
    op.drop_constraint('fk_sales_order_placed_by_id', 'sales_order', type_='foreignkey')
    op.drop_column('sales_order', 'placed_by_id')
```

- [ ] **Step 5: Run the test and the migration tests**

Run: `...python.exe -m pytest backend/tests/test_order_writes.py backend/tests/test_migrations.py -p no:cacheprovider`
Expected: all PASS. If `test_migrations_match_models` reports a difference, make the migration match the model (not the reverse), rerun.

- [ ] **Step 6: Gate and commit**

Run `scripts\ccweb_check.cmd`; expect exit 0. Then:

```
git add backend/alembic/versions/a3d9f1c27b40_record_who_placed_and_changed_orders.py backend/app/models/sales.py backend/app/models/__init__.py backend/tests/test_order_writes.py
git commit -m "Record who placed an order, its version, and its change history"
```

---

### Task 2: `order_writes.place_order`, and checkout through it

**Files:**
- Create: `backend/app/order_writes.py`
- Modify: `backend/app/routers/orders.py`, `backend/app/schemas.py`
- Test: `backend/tests/test_order_writes.py`; existing `backend/tests/test_orders.py`, `backend/tests/test_order_admin.py`, `backend/tests/test_concurrency.py` must pass unmodified

**Interfaces:**
- Consumes: Task 1 models.
- Produces:
  - `Line(listing_id: int, quantity: int, unit_price: Decimal | None = None)` -- frozen dataclass.
  - `customer_for_user(db: Session, user: User) -> Customer` (moved from `orders._customer_for`).
  - `place_order(db: Session, customer: Customer, lines: Sequence[Line], placed_by: User, notes: str | None = None) -> SalesOrder` -- flushes, never commits.
  - `money(value: Decimal) -> str` -- `"150.00"`.
  - `orders.order_out(db: Session, order_id: int) -> OrderOut` -- loads and serialises one order.
  - `OrderOut` gains `version: int`, `notes: str | None`, `placed_by_email: str | None`, `payment_adjustment_due: bool`.
  - `create_order(payload, db, user)` keeps its signature (the concurrency tests call it directly).

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_order_writes.py`:

```python
from app.models import Listing
from fastapi.testclient import TestClient

from tests.test_orders import place


def test_checkout_records_the_buyer_as_placer_and_writes_placed(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    body = place(client, customer_headers, listing.id, 2).json()

    assert body["version"] == 1
    assert body["placed_by_email"] == "customer@example.com"
    assert body["payment_adjustment_due"] is False
    changes = db.scalars(
        select(SalesOrderChange).where(SalesOrderChange.sales_order_id == body["id"])
    ).all()
    assert [(c.change, c.to_value) for c in changes] == [
        (SalesOrderChangeKind.placed, "customer@example.com")
    ]
```

- [ ] **Step 2: Run to verify it fails**

Run: `...pytest backend/tests/test_order_writes.py -p no:cacheprovider`
Expected: FAIL -- `KeyError: 'version'`.

- [ ] **Step 3: Create `backend/app/order_writes.py`**

```python
"""Writing orders: the one place an order's stock moves.

Checkout, an administrator placing an order for a customer, and an
administrator revising one all come through here, so the row locking that
stops an oversell exists once. Functions flush and never commit; the caller's
request owns the transaction.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import NoReturn

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (
    Customer,
    Disposition,
    InventoryItem,
    Listing,
    SalesOrder,
    SalesOrderChange,
    SalesOrderChangeKind,
    SalesOrderItem,
    SalesOrderStatus,
    User,
)
from .models.base import utcnow
from .references import require_code

_CENTS = Decimal("0.01")


@dataclass(frozen=True)
class Line:
    """One wanted line: a listing, how many, and optionally the price."""

    listing_id: int
    quantity: int
    unit_price: Decimal | None = None


def money(value: Decimal) -> str:
    """A money value as history stores it: two places, no symbol."""
    return str(value.quantize(_CENTS))


def _refuse(db: Session, code: int, detail: str) -> NoReturn:
    """Roll back, releasing any row locks, and raise."""
    db.rollback()
    raise HTTPException(status_code=code, detail=detail)


def customer_for_user(db: Session, user: User) -> Customer:
    """Find or create the customer record behind a login."""
    customer = db.scalar(select(Customer).where(Customer.user_id == user.id))
    if customer is None:
        customer = Customer(
            user_id=user.id,
            display_name=user.full_name or user.email,
            email=user.email,
        )
        db.add(customer)
        db.flush()
    return customer


def _lock_listings(db: Session, ids: set[int]) -> dict[int, Listing]:
    """Lock listings FOR UPDATE in id order, so contenders never deadlock."""
    rows = db.scalars(
        select(Listing).where(Listing.id.in_(ids)).order_by(Listing.id).with_for_update()
    ).all()
    found = {listing.id: listing for listing in rows}
    missing = sorted(ids - set(found))
    if missing:
        _refuse(db, status.HTTP_404_NOT_FOUND, f"Unknown listing id(s): {missing}")
    return found


def _after_stock_change(db: Session, listing: Listing, before: int) -> None:
    """Move the item's disposition when its listing's stock crosses zero."""
    after = listing.quantity_available
    item = db.get(InventoryItem, listing.inventory_item_id)
    if item is None:
        return
    if before > 0 and after == 0:
        item.disposition_id = require_code(db, Disposition, "sold", "disposition")
    elif before == 0 and after > 0 and listing.is_active:
        item.disposition_id = require_code(db, Disposition, "listed", "disposition")


def place_order(
    db: Session,
    customer: Customer,
    lines: Sequence[Line],
    placed_by: User,
    notes: str | None = None,
) -> SalesOrder:
    """Create an order, taking its stock under row locks."""
    listings = _lock_listings(db, {line.listing_id for line in lines})
    for line in sorted(lines, key=lambda line: line.listing_id):
        listing = listings[line.listing_id]
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
        sales_order_status_id=require_code(db, SalesOrderStatus, "pending", "status"),
        placed_by_id=placed_by.id,
        notes=notes,
    )
    total = Decimal("0.00")
    for line in sorted(lines, key=lambda line: line.listing_id):
        listing = listings[line.listing_id]
        before = listing.quantity_available
        listing.quantity_available -= line.quantity
        price = listing.price if line.unit_price is None else line.unit_price
        total += price * line.quantity
        order.items.append(
            SalesOrderItem(listing_id=listing.id, quantity=line.quantity, unit_price=price)
        )
        _after_stock_change(db, listing, before)
    order.total_amount = total
    db.add(order)
    db.flush()
    db.add(
        SalesOrderChange(
            sales_order_id=order.id,
            changed_at=utcnow(),
            changed_by_id=placed_by.id,
            change=SalesOrderChangeKind.placed,
            to_value=placed_by.email,
        )
    )
    db.flush()
    return order


def payment_adjustment_due(status_code: str, changes: Sequence[SalesOrderChange]) -> bool:
    """Whether a paid order's total changed after it was marked paid."""
    if status_code != "paid":
        return False
    paid = [
        c.id
        for c in changes
        if c.change is SalesOrderChangeKind.status and c.to_value == "paid"
    ]
    if not paid:
        return False
    return any(
        c.change is SalesOrderChangeKind.total and c.id > max(paid) for c in changes
    )
```

- [ ] **Step 4: Extend `OrderOut` and route checkout through `place_order`**

In `backend/app/schemas.py`, `OrderOut` after `customer_email`:

```python
    version: int
    notes: str | None
    #: The account that entered the order, when known.
    placed_by_email: str | None
    #: A paid order whose total changed after it was paid. Payments are not
    #: recorded, so this prompts a person; it never charges or refunds.
    payment_adjustment_due: bool
```

In `backend/app/routers/orders.py`:
- Delete `_customer_for`; import `Line, customer_for_user, payment_adjustment_due, place_order` from `..order_writes`.
- Add `selectinload(SalesOrder.placed_by)` and `selectinload(SalesOrder.changes)` to `_ORDER_DETAIL`.
- In `_order_out`, pass `version=order.version`, `notes=order.notes`, `placed_by_email=order.placed_by.email if order.placed_by else None`, `payment_adjustment_due=payment_adjustment_due(status_code, order.changes)`.
- Add after `_load`:

```python
def order_out(db: Session, order_id: int) -> OrderOut:
    """One order, freshly loaded, as the API returns it."""
    db.expire_all()
    order = _load(db, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND)
    return _order_out(order, _status_code(db, order))
```

- Replace the body of `create_order` (keep the signature and docstring's locking explanation, now pointing at `order_writes`):

```python
    customer = customer_for_user(db, user)
    order = place_order(
        db,
        customer,
        [Line(line.listing_id, line.quantity) for line in payload.items],
        placed_by=user,
    )
    db.commit()
    return order_out(db, order.id)
```

Remove imports the router no longer uses (ruff reports them).

- [ ] **Step 5: Run the order suites**

Run: `...pytest backend/tests/test_order_writes.py backend/tests/test_orders.py backend/tests/test_order_admin.py backend/tests/test_concurrency.py -p no:cacheprovider`
Expected: all PASS, with `test_orders.py` and `test_concurrency.py` unmodified.

- [ ] **Step 6: Gate and commit**

```
git add backend/app/order_writes.py backend/app/routers/orders.py backend/app/schemas.py backend/tests/test_order_writes.py
git commit -m "Move checkout into order_writes.place_order and record who placed it"
```

---

### Task 3: An administrator places an order for a customer

**Files:**
- Modify: `backend/app/schemas.py`, `backend/app/routers/customers.py`, `backend/app/routers/users.py`
- Test: `backend/tests/test_order_writes.py`

**Interfaces:**
- Consumes: `place_order`, `Line`, `customer_for_user`, `order_out`.
- Produces:
  - `AdminOrderLineIn {listing_id: int, quantity: int >= 1, unit_price: Decimal | None >= 0, 2 places}`
  - `AdminOrderCreate {items: list[AdminOrderLineIn] (>= 1, no listing twice), notes: str | None}`, extra forbidden.
  - `POST /api/customers/{customer_id}/orders` -> 201 `OrderOut`.
  - `POST /api/users/{user_id}/customer` -> 200 `CustomerOut`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_order_writes.py`:

```python
def _new_customer(db: Session, name: str = "Walk-in Buyer") -> Customer:
    customer = Customer(display_name=name, email=None)
    db.add(customer)
    db.commit()
    db.refresh(customer)
    return customer


def test_an_admin_places_an_order_for_a_customer_without_an_account(
    client: TestClient, listing: Listing, admin_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)

    response = client.post(
        f"/api/customers/{buyer.id}/orders",
        json={
            "items": [{"listing_id": listing.id, "quantity": 2, "unit_price": "150.00"}],
            "notes": "phone order",
        },
        headers=admin_headers,
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["customer_name"] == "Walk-in Buyer"
    assert body["placed_by_email"] == "admin@example.com"
    assert body["items"][0]["unit_price"] == "150.00"
    assert body["total_amount"] == "300.00"
    assert body["notes"] == "phone order"
    db.refresh(listing)
    assert listing.quantity_available == 3


def test_a_line_without_a_price_takes_the_listing_price(
    client: TestClient, listing: Listing, admin_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)
    body = client.post(
        f"/api/customers/{buyer.id}/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=admin_headers,
    ).json()
    assert body["items"][0]["unit_price"] == "189.00"


def test_only_an_admin_may_place_an_order_for_someone(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)
    payload = {"items": [{"listing_id": listing.id, "quantity": 1, "unit_price": "0.01"}]}
    url = f"/api/customers/{buyer.id}/orders"

    assert client.post(url, json=payload).status_code == 401
    assert client.post(url, json=payload, headers=customer_headers).status_code == 403
    assert db.scalar(select(SalesOrder.id)) is None
    db.refresh(listing)
    assert listing.quantity_available == 5


def test_placing_for_an_unknown_customer_is_a_404(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    response = client.post(
        "/api/customers/999999/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=admin_headers,
    )
    assert response.status_code == 404


def test_admin_order_payloads_are_validated(
    client: TestClient, listing: Listing, admin_headers: dict[str, str], db: Session
) -> None:
    buyer = _new_customer(db)
    url = f"/api/customers/{buyer.id}/orders"
    line = {"listing_id": listing.id, "quantity": 1}
    for bad in (
        {"items": []},
        {"items": [{**line, "quantity": 0}]},
        {"items": [{**line, "unit_price": "-1.00"}]},
        {"items": [line, line]},
        {"items": [line], "customer_id": 1},
    ):
        assert client.post(url, json=bad, headers=admin_headers).status_code == 422, bad


def test_an_account_can_be_given_a_customer_record(
    client: TestClient, admin_headers: dict[str, str], customer_user: User, db: Session
) -> None:
    first = client.post(f"/api/users/{customer_user.id}/customer", headers=admin_headers)
    again = client.post(f"/api/users/{customer_user.id}/customer", headers=admin_headers)

    assert first.status_code == 200
    assert first.json()["user_id"] == customer_user.id
    assert again.json()["id"] == first.json()["id"]


def test_only_an_admin_may_create_a_customer_record_for_an_account(
    client: TestClient, customer_headers: dict[str, str], customer_user: User
) -> None:
    url = f"/api/users/{customer_user.id}/customer"
    assert client.post(url).status_code == 401
    assert client.post(url, headers=customer_headers).status_code == 403
```

- [ ] **Step 2: Run to verify they fail**

Run: `...pytest backend/tests/test_order_writes.py -p no:cacheprovider`
Expected: the new tests FAIL with 404/405 (no routes).

- [ ] **Step 3: Add the schemas**

In `backend/app/schemas.py`, replace `OrderCreate`'s validator body with a call to a shared module function, and add the admin schemas after `OrderCreate`:

```python
def _refuse_duplicate_listings(listing_ids: list[int]) -> None:
    """Refuse the same listing twice in one order.

    Two lines for one listing would each be checked against stock separately,
    so together they could pass while overselling it.
    """
    if len(set(listing_ids)) != len(listing_ids):
        raise ValueError("each listing_id may appear at most once per order")
```

`OrderCreate.no_duplicate_listings` becomes `_refuse_duplicate_listings([i.listing_id for i in items]); return items`.

```python
class AdminOrderLineIn(BaseModel):
    """A line an administrator enters. No price means the listing's price."""

    model_config = ConfigDict(extra="forbid")

    listing_id: int
    quantity: int = Field(ge=1)
    unit_price: Decimal | None = Field(
        default=None, ge=Decimal("0"), max_digits=12, decimal_places=2
    )


class AdminOrderCreate(BaseModel):
    """An order an administrator places for a customer."""

    model_config = ConfigDict(extra="forbid")

    items: list[AdminOrderLineIn] = Field(min_length=1)
    notes: str | None = None

    @field_validator("items")
    @classmethod
    def no_duplicate_listings(cls, items: list[AdminOrderLineIn]) -> list[AdminOrderLineIn]:
        """Refuse the same listing twice in one order."""
        _refuse_duplicate_listings([item.listing_id for item in items])
        return items
```

- [ ] **Step 4: Add the endpoints**

In `backend/app/routers/customers.py`, add the imports `from ..order_writes import Line, place_order`, `from ..schemas import AdminOrderCreate, OrderOut` and `from .orders import order_out`, then:

```python
@router.post(
    "/{customer_id}/orders",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
)
def place_order_for_customer(
    customer_id: int, body: AdminOrderCreate, db: DbSession, admin: AdminUser
) -> OrderOut:
    """Place an order for a customer: a phone, walk-in or account holder's order."""
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such customer")
    order = place_order(
        db,
        customer,
        [Line(i.listing_id, i.quantity, i.unit_price) for i in body.items],
        placed_by=admin,
        notes=body.notes,
    )
    db.commit()
    return order_out(db, order.id)
```

In `backend/app/routers/users.py`, add `Customer` to the `..models` import, and `from ..order_writes import customer_for_user` and `from .customers import CustomerOut`, then:

```python
@router.post("/{user_id}/customer", response_model=CustomerOut)
def customer_for_account(user_id: int, db: DbSession, _: AdminUser) -> Customer:
    """The customer record behind an account, created if it has none yet.

    Lets an administrator place an order for an account holder who has never
    bought anything, and so has no customer record to choose.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such account")
    customer = customer_for_user(db, user)
    db.commit()
    db.refresh(customer)
    return customer
```

If `from .customers import CustomerOut` and `from .orders import order_out` create an import cycle, check with `python -c "import app.main"`; none is expected (`orders` imports neither).

- [ ] **Step 5: Run to verify they pass**

Run: `...pytest backend/tests/test_order_writes.py backend/tests/test_orders.py -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 6: Gate and commit**

```
git add backend/app/schemas.py backend/app/routers/customers.py backend/app/routers/users.py backend/tests/test_order_writes.py
git commit -m "Let an administrator place an order for any customer"
```

---

### Task 4: Revising an order

**Files:**
- Modify: `backend/app/order_writes.py`, `backend/app/schemas.py`, `backend/app/routers/orders.py`
- Test: `backend/tests/test_order_writes.py`

**Interfaces:**
- Consumes: Tasks 1-3.
- Produces:
  - `EDITABLE_STATUSES = frozenset({"pending", "paid"})` in `order_writes`.
  - `revise_order(db: Session, order: SalesOrder, *, status_code: str, customer: Customer, lines: Sequence[Line], notes: str | None, version: int, by: User) -> bool` -- True when anything changed; flushes, never commits.
  - `OrderRevisionLineIn {listing_id, quantity >= 1, unit_price: Decimal >= 0 (required)}`; `OrderRevision {version: int, customer_id: int, items: list[...] (>= 1, no duplicates), notes: str | None}`, extra forbidden.
  - `PUT /api/orders/{order_id}` -> 200 `OrderOut`.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_order_writes.py`, adding `from typing import Any` and `from httpx import Response` to its imports:

```python
def _place(
    client: TestClient, headers: dict[str, str], listing_id: int, qty: int
) -> dict[str, Any]:
    return client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing_id, "quantity": qty}]},
        headers=headers,
    ).json()


def _revise(
    client: TestClient,
    headers: dict[str, str],
    order: dict[str, Any],
    items: list[dict[str, Any]],
    *,
    customer_id: int | None = None,
    notes: str | None = None,
) -> Response:
    return client.put(
        f"/api/orders/{order['id']}",
        json={
            "version": order["version"],
            "customer_id": order["customer_id"] if customer_id is None else customer_id,
            "items": items,
            "notes": order["notes"] if notes is None else notes,
        },
        headers=headers,
    )


def _changes(db: Session, order_id: int) -> list[tuple[str, str | None, str | None]]:
    db.expire_all()
    rows = db.scalars(
        select(SalesOrderChange)
        .where(SalesOrderChange.sales_order_id == order_id)
        .order_by(SalesOrderChange.id)
    ).all()
    return [(c.change.value, c.from_value, c.to_value) for c in rows]


def test_raising_a_quantity_takes_stock_and_records_it(
    client: TestClient, listing: Listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str], db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 2)

    response = _revise(
        client, admin_headers, order,
        [{"listing_id": listing.id, "quantity": 3, "unit_price": "189.00"}],
    )

    assert response.status_code == 200, response.text
    assert response.json()["total_amount"] == "567.00"
    assert response.json()["version"] == order["version"] + 1
    db.refresh(listing)
    assert listing.quantity_available == 2
    assert _changes(db, order["id"])[1:] == [
        ("quantity", "2", "3"),
        ("total", "378.00", "567.00"),
    ]


def test_one_listing_swapped_for_another_at_an_agreed_price_in_one_save(
    client: TestClient, make_listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str], db: Session,
) -> None:
    morgan = make_listing(title="Morgan", price=Decimal("100.00"), quantity_available=5)
    dime = make_listing(title="Dime", price=Decimal("10.00"), quantity_available=5)
    order = _place(client, customer_headers, morgan.id, 2)

    response = _revise(
        client, admin_headers, order,
        [{"listing_id": dime.id, "quantity": 1, "unit_price": "8.00"}],
    )

    assert response.status_code == 200, response.text
    db.refresh(morgan)
    db.refresh(dime)
    assert (morgan.quantity_available, dime.quantity_available) == (5, 4)
    kinds = [c[0] for c in _changes(db, order["id"])]
    assert kinds == ["placed", "line_removed", "line_added", "total"]
    assert ("line_added", None, "1 @ 8.00") in _changes(db, order["id"])


def test_an_over_request_changes_nothing(
    client: TestClient, make_listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str], db: Session,
) -> None:
    plenty = make_listing(title="Plenty", quantity_available=5)
    scarce = make_listing(title="Scarce", quantity_available=1)
    order = _place(client, customer_headers, plenty.id, 1)

    response = _revise(
        client, admin_headers, order,
        [
            {"listing_id": plenty.id, "quantity": 4, "unit_price": "189.00"},
            {"listing_id": scarce.id, "quantity": 2, "unit_price": "189.00"},
        ],
    )

    assert response.status_code == 409
    assert f"listing {scarce.id}" in response.json()["detail"]
    db.refresh(plenty)
    db.refresh(scarce)
    assert (plenty.quantity_available, scarce.quantity_available) == (4, 1)
    assert [c[0] for c in _changes(db, order["id"])] == ["placed"]


def test_an_edit_that_frees_the_last_unit_relists_the_item(
    client: TestClient, make_listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str], db: Session,
) -> None:
    only = make_listing(title="Only one", quantity_available=1)
    other = make_listing(title="Other", quantity_available=5)
    order = _place(client, customer_headers, only.id, 1)
    db.refresh(only.inventory_item)
    assert only.inventory_item.disposition.code == "sold"

    _revise(client, admin_headers, order,
            [{"listing_id": other.id, "quantity": 1, "unit_price": "189.00"}])

    db.expire_all()
    assert only.inventory_item.disposition.code == "listed"


def test_a_no_change_save_writes_nothing(
    client: TestClient, listing: Listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str], db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 2)
    response = _revise(client, admin_headers, order,
                       [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}])
    assert response.json()["version"] == order["version"]
    assert [c[0] for c in _changes(db, order["id"])] == ["placed"]


def test_customer_and_notes_changes_are_recorded(
    client: TestClient, listing: Listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str], db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    other = _new_customer(db, "Grace Hopper")

    _revise(client, admin_headers, order,
            [{"listing_id": listing.id, "quantity": 1, "unit_price": "189.00"}],
            customer_id=other.id, notes="deliver Friday")

    assert _changes(db, order["id"])[1:] == [
        ("customer", "Test Customer", "Grace Hopper"),
        ("notes", None, "deliver Friday"),
    ]


def test_only_pending_or_paid_orders_can_be_revised(
    client: TestClient, listing: Listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    client.patch(f"/api/orders/{order['id']}", json={"status": "packed"}, headers=admin_headers)
    fresh = client.get(f"/api/orders/{order['id']}", headers=admin_headers).json()

    response = _revise(client, admin_headers, fresh,
                       [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}])

    assert response.status_code == 409
    assert "packed" in response.json()["detail"]


def test_a_stale_version_is_refused(
    client: TestClient, listing: Listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    line = [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}]
    assert _revise(client, admin_headers, order, line).status_code == 200

    response = _revise(client, admin_headers, order,
                       [{"listing_id": listing.id, "quantity": 3, "unit_price": "189.00"}])

    assert response.status_code == 409
    assert "reload" in response.json()["detail"].lower()


def test_only_an_admin_may_revise(
    client: TestClient, listing: Listing, customer_headers: dict[str, str], db: Session
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    line = [{"listing_id": listing.id, "quantity": 1, "unit_price": "0.01"}]
    assert _revise(client, {}, order, line).status_code == 401
    assert _revise(client, customer_headers, order, line).status_code == 403
    assert [c[0] for c in _changes(db, order["id"])] == ["placed"]
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL with 405 (no PUT route).

- [ ] **Step 3: Implement `revise_order`**

Append to `backend/app/order_writes.py`:

```python
EDITABLE_STATUSES = frozenset({"pending", "paid"})


def revise_order(
    db: Session,
    order: SalesOrder,
    *,
    status_code: str,
    customer: Customer,
    lines: Sequence[Line],
    notes: str | None,
    version: int,
    by: User,
) -> bool:
    """Make an order's contents match `lines`, moving stock by the difference.

    Every check runs before anything changes, so a refused save changes no
    line and no stock. Returns whether anything changed.
    """
    if status_code not in EDITABLE_STATUSES:
        _refuse(
            db,
            status.HTTP_409_CONFLICT,
            f"Order #{order.id} is {status_code}; only pending or paid orders can be changed.",
        )
    if version != order.version:
        _refuse(
            db,
            status.HTTP_409_CONFLICT,
            f"Order #{order.id} was changed by someone else (you have version "
            f"{version}, current is {order.version}). Reload and reapply your changes.",
        )

    current = {item.listing_id: item for item in order.items}
    desired = {line.listing_id: line for line in lines}
    ids = set(current) | set(desired)
    listings = _lock_listings(db, ids)

    deltas: dict[int, int] = {}
    for listing_id in sorted(ids):
        have = current[listing_id].quantity if listing_id in current else 0
        want = desired[listing_id].quantity if listing_id in desired else 0
        deltas[listing_id] = want - have
        listing = listings[listing_id]
        if deltas[listing_id] > 0:
            if not listing.is_active:
                _refuse(db, status.HTTP_409_CONFLICT,
                        f"Listing {listing_id} is not currently for sale")
            if listing.quantity_available < deltas[listing_id]:
                _refuse(
                    db,
                    status.HTTP_409_CONFLICT,
                    f"Only {listing.quantity_available} more of listing {listing_id} "
                    f"are available (this change needs {deltas[listing_id]})",
                )

    stamp = utcnow()
    changes: list[SalesOrderChange] = []

    def record(kind: SalesOrderChangeKind, listing_id: int | None = None,
               before: str | None = None, after: str | None = None) -> None:
        changes.append(
            SalesOrderChange(
                sales_order_id=order.id, changed_at=stamp, changed_by_id=by.id,
                change=kind, listing_id=listing_id, from_value=before, to_value=after,
            )
        )

    old_total = order.total_amount
    for listing_id in sorted(ids):
        listing = listings[listing_id]
        delta = deltas[listing_id]
        if delta:
            before = listing.quantity_available
            listing.quantity_available -= delta
            _after_stock_change(db, listing, before)
        existing = current.get(listing_id)
        line = desired.get(listing_id)
        if existing is None and line is not None:
            price = listing.price if line.unit_price is None else line.unit_price
            order.items.append(
                SalesOrderItem(listing_id=listing_id, quantity=line.quantity, unit_price=price)
            )
            record(SalesOrderChangeKind.line_added, listing_id,
                   after=f"{line.quantity} @ {money(price)}")
        elif existing is not None and line is None:
            order.items.remove(existing)
            record(SalesOrderChangeKind.line_removed, listing_id,
                   before=f"{existing.quantity} @ {money(existing.unit_price)}")
        elif existing is not None and line is not None:
            if delta:
                record(SalesOrderChangeKind.quantity, listing_id,
                       str(existing.quantity), str(line.quantity))
                existing.quantity = line.quantity
            if line.unit_price is not None and line.unit_price != existing.unit_price:
                record(SalesOrderChangeKind.unit_price, listing_id,
                       money(existing.unit_price), money(line.unit_price))
                existing.unit_price = line.unit_price

    if customer.id != order.customer_id:
        record(SalesOrderChangeKind.customer, None,
               order.customer.display_name, customer.display_name)
        order.customer = customer
    if (notes or None) != (order.notes or None):
        record(SalesOrderChangeKind.notes, None, order.notes, notes)
        order.notes = notes

    new_total = sum(
        (item.unit_price * item.quantity for item in order.items), Decimal("0.00")
    )
    if new_total != old_total:
        record(SalesOrderChangeKind.total, None, money(old_total), money(new_total))
        order.total_amount = new_total

    if changes:
        # A line-only edit leaves the order row untouched, and the version
        # only moves when that row is updated -- so touch it.
        order.updated_at = stamp
        db.add_all(changes)
        db.flush()
    return bool(changes)
```

Replace the `record` helper's multi-line signature formatting as `ruff format` dictates; give `record` a docstring (`"""Queue one history row for this save."""`).

- [ ] **Step 4: Add the schemas and the route**

`backend/app/schemas.py`:

```python
class OrderRevisionLineIn(BaseModel):
    """A line of an order's desired contents, at the price it should carry."""

    model_config = ConfigDict(extra="forbid")

    listing_id: int
    quantity: int = Field(ge=1)
    unit_price: Decimal = Field(ge=Decimal("0"), max_digits=12, decimal_places=2)


class OrderRevision(BaseModel):
    """An order's complete desired contents, and the version they were read at."""

    model_config = ConfigDict(extra="forbid")

    version: int
    customer_id: int
    items: list[OrderRevisionLineIn] = Field(min_length=1)
    notes: str | None = None

    @field_validator("items")
    @classmethod
    def no_duplicate_listings(
        cls, items: list[OrderRevisionLineIn]
    ) -> list[OrderRevisionLineIn]:
        """Refuse the same listing twice in one order."""
        _refuse_duplicate_listings([item.listing_id for item in items])
        return items
```

`backend/app/routers/orders.py` (import `OrderRevision`, `revise_order`, `Customer`):

```python
@router.put("/{order_id}")
def revise(
    order_id: int, payload: OrderRevision, db: DbSession, admin: AdminUser
) -> OrderOut:
    """Replace an order's lines, prices, customer and notes, all or nothing."""
    order = _load(db, order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND)
    customer = db.get(Customer, payload.customer_id)
    if customer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No such customer")
    revise_order(
        db,
        order,
        status_code=_status_code(db, order),
        customer=customer,
        lines=[Line(i.listing_id, i.quantity, i.unit_price) for i in payload.items],
        notes=payload.notes,
        version=payload.version,
        by=admin,
    )
    try:
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Order #{order_id} was changed while saving. Reload and retry.",
        ) from exc
    return order_out(db, order_id)
```

Import `StaleDataError` from `sqlalchemy.orm.exc`.

- [ ] **Step 5: Run to verify they pass**

Run: `...pytest backend/tests/test_order_writes.py backend/tests/test_orders.py backend/tests/test_order_admin.py -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 6: Gate and commit**

```
git add backend/app/order_writes.py backend/app/schemas.py backend/app/routers/orders.py backend/tests/test_order_writes.py
git commit -m "Let an administrator revise a pending or paid order"
```

---

### Task 5: Status history, the payment flag, and the changes endpoint

**Files:**
- Modify: `backend/app/order_writes.py`, `backend/app/schemas.py`, `backend/app/routers/orders.py`
- Test: `backend/tests/test_order_writes.py`

**Interfaces:**
- Produces:
  - `record_status_change(db: Session, order: SalesOrder, before: str, after: str, by: User) -> None` -- writes a `status` row when `before != after`.
  - `OrderChangeOut {id: int, changed_at: datetime, changed_by_email: str | None, change: str, listing_id: int | None, listing_title: str | None, from_value: str | None, to_value: str | None}`.
  - `GET /api/orders/{order_id}/changes` -> `list[OrderChangeOut]`, newest first, admin only.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_status_change_is_recorded_and_bumps_the_version(
    client: TestClient, listing: Listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str], db: Session,
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    body = client.patch(f"/api/orders/{order['id']}", json={"status": "paid"},
                        headers=admin_headers).json()
    assert body["version"] == order["version"] + 1
    assert _changes(db, order["id"])[1:] == [("status", "pending", "paid")]


def test_payment_adjustment_is_due_only_after_a_paid_total_changes(
    client: TestClient, listing: Listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = _place(client, customer_headers, listing.id, 1)

    def line(qty: int) -> list[dict[str, Any]]:
        return [{"listing_id": listing.id, "quantity": qty, "unit_price": "189.00"}]

    order = _revise(client, admin_headers, order, line(2)).json()
    assert order["payment_adjustment_due"] is False  # still pending

    order = client.patch(f"/api/orders/{order['id']}", json={"status": "paid"},
                         headers=admin_headers).json()
    assert order["payment_adjustment_due"] is False

    order = _revise(client, admin_headers, order, line(3)).json()
    assert order["payment_adjustment_due"] is True


def test_the_history_reads_newest_first_with_who_and_what(
    client: TestClient, listing: Listing, customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    _revise(client, admin_headers, order,
            [{"listing_id": listing.id, "quantity": 2, "unit_price": "189.00"}])

    rows = client.get(f"/api/orders/{order['id']}/changes", headers=admin_headers).json()

    assert [r["change"] for r in rows] == ["total", "quantity", "placed"]
    assert rows[1]["changed_by_email"] == "admin@example.com"
    assert rows[1]["listing_title"] == "1881-S Morgan Silver Dollar"
    assert rows[2]["changed_by_email"] == "customer@example.com"


def test_only_an_admin_may_read_the_history(
    client: TestClient, listing: Listing, customer_headers: dict[str, str]
) -> None:
    order = _place(client, customer_headers, listing.id, 1)
    url = f"/api/orders/{order['id']}/changes"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=customer_headers).status_code == 403
```

- [ ] **Step 2: Run to verify they fail**

Expected: FAIL. The version assertion already passes (changing the status column updates the row), but no `status` row is written, `payment_adjustment_due` stays false, and `/changes` is a 404/405.

- [ ] **Step 3: Implement**

`order_writes.py`:

```python
def record_status_change(
    db: Session, order: SalesOrder, before: str, after: str, by: User
) -> None:
    """Write a `status` history row when the status actually changed."""
    if before == after:
        return
    db.add(
        SalesOrderChange(
            sales_order_id=order.id,
            changed_at=utcnow(),
            changed_by_id=by.id,
            change=SalesOrderChangeKind.status,
            from_value=before,
            to_value=after,
        )
    )
```

`schemas.py`: add `OrderChangeOut` with the fields in Interfaces, docstring `"""One row of an order's history, with who made it and which line."""`.

`routers/orders.py`:
- In `update_order_status`, rename `_admin` to `admin`; after computing `previous` and the cancelled guard, call `record_status_change(db, order, previous, payload.status, admin)` before `db.commit()`; return `order_out(db, order.id)` instead of `_order_out(order, payload.status)`.
- Add:

```python
@router.get("/{order_id}/changes")
def list_order_changes(
    order_id: int, db: DbSession, _admin: AdminUser
) -> list[OrderChangeOut]:
    """An order's history, newest first."""
    if db.get(SalesOrder, order_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=_ORDER_NOT_FOUND)
    rows = db.scalars(
        select(SalesOrderChange)
        .where(SalesOrderChange.sales_order_id == order_id)
        .options(
            selectinload(SalesOrderChange.changed_by),
            selectinload(SalesOrderChange.listing).selectinload(Listing.inventory_item),
        )
        .order_by(SalesOrderChange.id.desc())
    ).all()
    return [
        OrderChangeOut(
            id=row.id,
            changed_at=row.changed_at,
            changed_by_email=row.changed_by.email if row.changed_by else None,
            change=row.change.value,
            listing_id=row.listing_id,
            listing_title=row.listing.inventory_item.source_title if row.listing else None,
            from_value=row.from_value,
            to_value=row.to_value,
        )
        for row in rows
    ]
```

This route must be declared before any route whose path would swallow `/{order_id}/changes`; FastAPI matches `/{order_id}` only on the exact segment count, so order does not matter here, but keep it after `get_order` for reading.

- [ ] **Step 4: Run to verify they pass**

Run: `...pytest backend/tests/test_order_writes.py backend/tests/test_orders.py backend/tests/test_order_admin.py -p no:cacheprovider`
Expected: all PASS.

- [ ] **Step 5: Gate and commit**

```
git add backend/app/order_writes.py backend/app/schemas.py backend/app/routers/orders.py backend/tests/test_order_writes.py
git commit -m "Record order status changes, flag paid totals that change, and serve the history"
```

---

### Task 6: An admin edit racing a checkout

**Files:**
- Create: `backend/tests/test_order_revision_race.py`

**Interfaces:**
- Consumes: `revise_order`, `place_order`, `customer_for_user`, `Line`, the `committed` pattern from `tests/test_concurrency.py`.

- [ ] **Step 1: Write the test**

```python
"""An administrator's edit and a shopper's checkout contending for one unit.

Threads with their own committing sessions, released together by a barrier,
as in test_concurrency.py: TestClient serialises requests and would pass with
the lock removed.
"""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

from collections.abc import Iterator

import pytest
from app.models import (
    Customer,
    InventoryItem,
    Listing,
    SalesOrder,
    SalesOrderChange,
    SalesOrderItem,
    User,
    UserRole,
)
from app.order_writes import Line, customer_for_user, place_order, revise_order
from app.routers.orders import _status_code
from fastapi import HTTPException
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from tests.test_concurrency import RACE_TITLE, _seed


@pytest.fixture
def committed(engine: Engine) -> Iterator[sessionmaker[Session]]:
    """Real, committing sessions; removes every row the race creates."""
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield factory
    with factory() as cleanup:
        cleanup.query(SalesOrderChange).delete()
        cleanup.query(SalesOrderItem).delete()
        cleanup.query(SalesOrder).delete()
        cleanup.query(Customer).delete()
        cleanup.query(Listing).delete()
        cleanup.query(InventoryItem).filter(
            InventoryItem.source_title == RACE_TITLE
        ).delete(synchronize_session=False)
        cleanup.query(User).filter(User.email.like("race%@example.com")).delete(
            synchronize_session=False
        )
        cleanup.commit()
```

```python
def test_an_edit_and_a_checkout_cannot_both_take_the_last_unit(
    committed: sessionmaker[Session],
) -> None:
    listing_id, (buyer_id, holder_id, admin_id) = _seed(committed, stock=2, buyers=3)
    with committed() as s:
        s.get(User, admin_id).role = UserRole.admin
        holder = s.get(User, holder_id)
        order = place_order(s, customer_for_user(s, holder), [Line(listing_id, 1)], holder)
        s.commit()
        order_id, version = order.id, order.version

    barrier = threading.Barrier(2)

    def checkout() -> str | int:
        s = committed()
        try:
            barrier.wait(timeout=10)
            user = s.get(User, buyer_id)
            place_order(s, customer_for_user(s, user), [Line(listing_id, 1)], user)
            s.commit()
            return "checkout"
        except HTTPException as exc:
            s.rollback()
            return exc.status_code
        finally:
            s.close()

    def edit() -> str | int:
        s = committed()
        try:
            barrier.wait(timeout=10)
            o = s.get(SalesOrder, order_id)
            revise_order(
                s, o, status_code=_status_code(s, o), customer=o.customer,
                lines=[Line(listing_id, 2, Decimal("100.00"))], notes=None,
                version=version, by=s.get(User, admin_id),
            )
            s.commit()
            return "edit"
        except HTTPException as exc:
            s.rollback()
            return exc.status_code
        finally:
            s.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = sorted(pool.map(lambda f: f(), [checkout, edit]), key=str)

    with committed() as s:
        remaining = s.get(Listing, listing_id).quantity_available
    assert remaining == 0
    assert outcomes.count(409) == 1, outcomes
    assert len([o for o in outcomes if o in ("checkout", "edit")]) == 1, outcomes
```

- [ ] **Step 2: Run it, then prove it guards something**

Run: `...pytest backend/tests/test_order_revision_race.py -p no:cacheprovider` -- expect PASS.
Temporarily delete `.with_for_update()` from `_lock_listings`, rerun, expect FAIL (both succeed or stock negative -- rerun up to 5 times if the interleaving hides it), restore, rerun PASS.

- [ ] **Step 3: Commit**

```
git add backend/tests/test_order_revision_race.py
git commit -m "Prove an order edit and a checkout cannot both take the last unit"
```

---

### Task 7: Console API calls and exact money

**Files:**
- Modify: `frontend/src/owner/api.js`
- Create: `frontend/src/owner/pages/orders/cents.js`, `frontend/src/owner/pages/orders/cents.test.js`

**Interfaces:**
- Produces (on the console `api` object): `createOrderFor(customerId, payload)` -> `POST /api/customers/{id}/orders`; `reviseOrder(orderId, payload)` -> `PUT /api/orders/{id}`; `listOrderChanges(orderId)` -> `GET /api/orders/{id}/changes`; `customerForUser(userId)` -> `POST /api/users/{id}/customer`.
- Produces (`cents.js`): `isMoney(text) -> boolean`; `toCents(text) -> number`; `fromCents(cents) -> string`; `totalCents(lines) -> number` where a line is `{quantity: string, unit_price: string}`.

- [ ] **Step 1: Write the failing test**

`frontend/src/owner/pages/orders/cents.test.js`:

```js
import { describe, expect, it } from 'vitest'

import { fromCents, isMoney, toCents, totalCents } from './cents'

describe('cents', () => {
  it('reads money text without floating point', () => {
    expect(toCents('189')).toBe(18900)
    expect(toCents('189.5')).toBe(18950)
    expect(toCents('0.07')).toBe(7)
  })

  it('writes cents back as two places', () => {
    expect(fromCents(18950)).toBe('189.50')
    expect(fromCents(7)).toBe('0.07')
  })

  it('totals exactly where floats would not', () => {
    // 19.99 * 3 in floating point is 59.97000000000001.
    expect(totalCents([{ quantity: '3', unit_price: '19.99' }])).toBe(5997)
  })

  it('knows money text from anything else', () => {
    expect(isMoney('150.00')).toBe(true)
    expect(isMoney('150')).toBe(true)
    expect(isMoney('1.234')).toBe(false)
    expect(isMoney('-1')).toBe(false)
    expect(isMoney('')).toBe(false)
  })

  it('leaves out a line that is not yet valid', () => {
    expect(totalCents([{ quantity: '', unit_price: '5.00' }])).toBe(0)
  })
})
```

- [ ] **Step 2: Run to verify it fails** -- "Failed to resolve import './cents'".

- [ ] **Step 3: Implement**

`cents.js`:

```js
/**
 * Money in whole cents, for the order editor's running total.
 *
 * Money crosses the API as decimal strings. Adding them as floats gives
 * 59.97000000000001 for three at 19.99, so the editor works in integer cents
 * and only formats at the edge. The server's total is still the real one.
 */
const MONEY = /^\d+(\.\d{1,2})?$/

export const isMoney = (text) => MONEY.test(String(text).trim())

export function toCents(text) {
  const [whole, fraction = ''] = String(text).trim().split('.')
  return Number(whole) * 100 + Number(`${fraction}00`.slice(0, 2))
}

export function fromCents(cents) {
  return `${Math.floor(cents / 100)}.${String(cents % 100).padStart(2, '0')}`
}

export function totalCents(lines) {
  return lines.reduce((sum, line) => {
    const quantity = String(line.quantity)
    if (!/^\d+$/.test(quantity) || !isMoney(line.unit_price)) return sum
    return sum + Number(quantity) * toCents(line.unit_price)
  }, 0)
}
```

`owner/api.js`, next to `listOrders`:

```js
  createOrderFor: (customerId, payload) =>
    send(`/api/customers/${customerId}/orders`, { method: 'POST', body: payload }),
  reviseOrder: (orderId, payload) =>
    send(`/api/orders/${orderId}`, { method: 'PUT', body: payload }),
  listOrderChanges: (orderId) => send(`/api/orders/${orderId}/changes`),
  customerForUser: (userId) => send(`/api/users/${userId}/customer`, { method: 'POST' }),
```

- [ ] **Step 4: Run to verify it passes**; **Step 5: commit**

```
git add frontend/src/owner/api.js frontend/src/owner/pages/orders/cents.js frontend/src/owner/pages/orders/cents.test.js
git commit -m "Add the console's order-writing API calls and exact cents arithmetic"
```

---

### Task 8: The order editor

**Files:**
- Create: `frontend/src/owner/pages/orders/OrderDialog.jsx`, `frontend/src/owner/pages/orders/OrderEditor.jsx`, `frontend/src/owner/pages/orders/OrderEditor.test.jsx`

**Interfaces:**
- Consumes: Task 7 calls; `api.listCustomers`, `api.listUsers`, `api.listCatalog`, `api.getCatalogItem`; `money` from `shared/format`.
- Produces: `<OrderDialog label onClose>{children}</OrderDialog>`; `<OrderEditor order={OrderOut | null} onSaved={() => void} onClose={() => void} />`.

- [ ] **Step 1: Write the failing tests**

`OrderEditor.test.jsx`:

```jsx
import userEvent from '@testing-library/user-event'
import { render, screen, waitFor, within } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({
  api: {
    listCustomers: vi.fn(),
    listUsers: vi.fn(),
    listCatalog: vi.fn(),
    getCatalogItem: vi.fn(),
    createOrderFor: vi.fn(),
    reviseOrder: vi.fn(),
    customerForUser: vi.fn(),
  },
}))

import { api } from '../../api'
import OrderEditor from './OrderEditor'

const ORDER = {
  id: 12, version: 3, customer_id: 5, customer_name: 'Ada Lovelace',
  status: 'paid', notes: null, total_amount: '378.00',
  items: [{ id: 1, listing_id: 3, title: 'Morgan', quantity: 2, unit_price: '189.00' }],
}

beforeEach(() => {
  vi.clearAllMocks()
  api.listCustomers.mockResolvedValue([
    { id: 5, user_id: 2, display_name: 'Ada Lovelace', email: 'ada@example.com' },
    { id: 6, user_id: null, display_name: 'Walk-in', email: null },
  ])
  api.listUsers.mockResolvedValue([
    { id: 2, email: 'ada@example.com', full_name: 'Ada Lovelace' },
    { id: 9, email: 'new@example.com', full_name: 'New Person' },
  ])
  api.getCatalogItem.mockResolvedValue({ id: 3, price: '189.00', quantity_available: 4 })
  api.listCatalog.mockResolvedValue({
    items: [{ id: 4, title: 'Dime', price: '10.00', quantity_available: 7 }],
  })
  api.createOrderFor.mockResolvedValue({ id: 13 })
  api.reviseOrder.mockResolvedValue({ id: 12 })
})

const setup = async (order = null) => {
  const user = userEvent.setup()
  const onSaved = vi.fn()
  render(<OrderEditor order={order} onSaved={onSaved} onClose={vi.fn()} />)
  await screen.findByRole('combobox', { name: 'Customer' })
  return { user, onSaved }
}

describe('OrderEditor', () => {
  it('prefills an order being edited and shows the paid warning', async () => {
    await setup(ORDER)
    expect(screen.getByRole('combobox', { name: 'Customer' })).toHaveValue('c:5')
    expect(screen.getByRole('spinbutton', { name: 'Quantity of Morgan' })).toHaveValue(2)
    expect(screen.getByRole('textbox', { name: 'Price of Morgan' })).toHaveValue('189.00')
    expect(screen.getByText(/this order is paid/i)).toBeInTheDocument()
    expect(screen.getByText('Total $378.00')).toBeInTheDocument()
  })

  it('offers accounts without a customer record', async () => {
    await setup()
    const select = screen.getByRole('combobox', { name: 'Customer' })
    const labels = Array.from(select.querySelectorAll('option')).map((o) => o.textContent)
    expect(labels).toContain('New Person (new@example.com), account, no orders yet')
    expect(labels.filter((l) => l.includes('ada@example.com'))).toHaveLength(1)
  })

  it('re-quantities, re-prices and saves a revision with the loaded version', async () => {
    const { user, onSaved } = await setup(ORDER)
    const qty = screen.getByRole('spinbutton', { name: 'Quantity of Morgan' })
    await user.clear(qty)
    await user.type(qty, '3')
    const price = screen.getByRole('textbox', { name: 'Price of Morgan' })
    await user.clear(price)
    await user.type(price, '150')
    expect(screen.getByText('Total $450.00')).toBeInTheDocument()
    expect(screen.getByText('listing price $189.00')).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: 'Save order' }))

    expect(api.reviseOrder).toHaveBeenCalledWith(12, {
      version: 3, customer_id: 5, notes: null,
      items: [{ listing_id: 3, quantity: 3, unit_price: '150.00' }],
    })
    await waitFor(() => expect(onSaved).toHaveBeenCalled())
  })

  it('adds an item from a catalogue search and removes another', async () => {
    const { user } = await setup(ORDER)
    await user.type(screen.getByRole('searchbox', { name: 'Find item' }), 'dime')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await user.click(await screen.findByRole('button', { name: 'Add Dime' }))
    await user.click(screen.getByRole('button', { name: 'Remove Morgan' }))
    await user.click(screen.getByRole('button', { name: 'Save order' }))

    expect(api.listCatalog).toHaveBeenCalledWith({ q: 'dime', in_stock: true, limit: 10 })
    expect(api.reviseOrder).toHaveBeenCalledWith(12, expect.objectContaining({
      items: [{ listing_id: 4, quantity: 1, unit_price: '10.00' }],
    }))
  })

  it('creates a customer record for an account before placing the order', async () => {
    api.customerForUser.mockResolvedValue({ id: 77 })
    const { user } = await setup()
    await user.selectOptions(screen.getByRole('combobox', { name: 'Customer' }), 'u:9')
    await user.type(screen.getByRole('searchbox', { name: 'Find item' }), 'dime')
    await user.click(screen.getByRole('button', { name: 'Search' }))
    await user.click(await screen.findByRole('button', { name: 'Add Dime' }))
    await user.click(screen.getByRole('button', { name: 'Save order' }))

    await waitFor(() => expect(api.customerForUser).toHaveBeenCalledWith(9))
    expect(api.createOrderFor).toHaveBeenCalledWith(77, {
      notes: null, items: [{ listing_id: 4, quantity: 1, unit_price: '10.00' }],
    })
  })

  it('keeps the edits when the server refuses', async () => {
    api.reviseOrder.mockRejectedValue(new Error('Only 1 more of listing 3 are available'))
    const { user, onSaved } = await setup(ORDER)
    const qty = screen.getByRole('spinbutton', { name: 'Quantity of Morgan' })
    await user.clear(qty)
    await user.type(qty, '9')
    await user.click(screen.getByRole('button', { name: 'Save order' }))

    expect(await screen.findByText(/only 1 more of listing 3/i)).toBeInTheDocument()
    expect(qty).toHaveValue(9)
    expect(onSaved).not.toHaveBeenCalled()
  })

  it('refuses to save an empty order or one with no customer', async () => {
    const { user } = await setup()
    await user.click(screen.getByRole('button', { name: 'Save order' }))
    expect(screen.getByText('Choose a customer.')).toBeInTheDocument()
    expect(api.createOrderFor).not.toHaveBeenCalled()
  })
})
```

- [ ] **Step 2: Run to verify they fail** -- "Failed to resolve import './OrderEditor'".

- [ ] **Step 3: Implement**

`OrderDialog.jsx` -- the same mechanism as `inventory/ItemEditDialog.jsx`:

```jsx
import { useEffect, useRef } from 'react'

/**
 * A modal dialog for the Orders page. Mounted only while open; the parent's
 * state says what is open. Escape closes through `onClose`, so the parent's
 * state never disagrees with the element.
 */
export default function OrderDialog({ label, onClose, children }) {
  const ref = useRef(null)

  useEffect(() => {
    const dialog = ref.current
    dialog.showModal()
    return () => dialog.close()
  }, [])

  return (
    <dialog
      ref={ref}
      className="edit-dialog"
      aria-label={label}
      onCancel={(e) => {
        e.preventDefault()
        onClose()
      }}
    >
      {children}
    </dialog>
  )
}
```

`OrderEditor.jsx`:

```jsx
import { useEffect, useState } from 'react'

import { api } from '../../api'
import { money } from '../../../shared/format'
import { fromCents, isMoney, toCents, totalCents } from './cents'

/**
 * Placing or revising an order on a customer's behalf.
 *
 * `order` is null for a new order. The whole desired contents are sent at
 * once -- the server moves stock by the difference, all or nothing -- with the
 * version the order was loaded at, so a save over someone else's change is
 * refused rather than silently applied.
 */
export default function OrderEditor({ order, onSaved, onClose }) {
  const [customers, setCustomers] = useState(null)
  const [accounts, setAccounts] = useState([])
  const [customerKey, setCustomerKey] = useState(order ? `c:${order.customer_id}` : '')
  const [find, setFind] = useState('')
  const [lines, setLines] = useState(
    (order?.items ?? []).map((item) => ({
      listing_id: item.listing_id,
      title: item.title,
      quantity: String(item.quantity),
      unit_price: String(item.unit_price),
      listing_price: null,
      available: null,
    })),
  )
  const [notes, setNotes] = useState(order?.notes ?? '')
  const [query, setQuery] = useState('')
  const [results, setResults] = useState([])
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    Promise.all([api.listCustomers(), api.listUsers()])
      .then(([customerRows, userRows]) => {
        if (cancelled) return
        setCustomers(customerRows)
        setAccounts(userRows.filter((u) => !customerRows.some((c) => c.user_id === u.id)))
      })
      .catch((err) => !cancelled && setError(err.message))
    // Listing price and stock for lines already on the order.
    for (const item of order?.items ?? []) {
      api
        .getCatalogItem(item.listing_id)
        .then((listing) => {
          if (cancelled) return
          setLines((current) =>
            current.map((line) =>
              line.listing_id === item.listing_id
                ? { ...line, listing_price: listing.price, available: listing.quantity_available }
                : line,
            ),
          )
        })
        .catch(() => {})
    }
    return () => {
      cancelled = true
    }
  }, [order])

  const options = [
    ...(customers ?? []).map((c) => ({
      key: `c:${c.id}`,
      label: c.email ? `${c.display_name} (${c.email})` : c.display_name,
    })),
    ...accounts.map((u) => ({
      key: `u:${u.id}`,
      label: `${u.full_name || u.email} (${u.email}), account, no orders yet`,
    })),
  ]
  const shown = options.filter(
    (o) => o.key === customerKey || o.label.toLowerCase().includes(find.toLowerCase()),
  )

  const setLine = (listingId, field) => (e) =>
    setLines(lines.map((l) => (l.listing_id === listingId ? { ...l, [field]: e.target.value } : l)))

  async function search() {
    try {
      const page = await api.listCatalog({ q: query, in_stock: true, limit: 10 })
      setResults(page.items)
    } catch (err) {
      setError(err.message)
    }
  }

  function add(listing) {
    if (lines.some((l) => l.listing_id === listing.id)) return
    setLines([
      ...lines,
      {
        listing_id: listing.id,
        title: listing.title,
        quantity: '1',
        unit_price: String(listing.price),
        listing_price: String(listing.price),
        available: listing.quantity_available,
      },
    ])
  }

  async function save() {
    setError('')
    if (!customerKey) return setError('Choose a customer.')
    if (lines.length === 0) {
      return setError('An order needs at least one item. To empty an order, cancel it.')
    }
    const bad = lines.find(
      (l) => !/^\d+$/.test(l.quantity) || Number(l.quantity) < 1 || !isMoney(l.unit_price),
    )
    if (bad) return setError(`Check the quantity and price of ${bad.title}.`)

    setSaving(true)
    try {
      let customerId = Number(customerKey.slice(2))
      if (customerKey.startsWith('u:')) customerId = (await api.customerForUser(customerId)).id
      const items = lines.map((l) => ({
        listing_id: l.listing_id,
        quantity: Number(l.quantity),
        unit_price: fromCents(toCents(l.unit_price)),
      }))
      const notesValue = notes.trim() === '' ? null : notes
      if (order) {
        await api.reviseOrder(order.id, {
          version: order.version,
          customer_id: customerId,
          items,
          notes: notesValue,
        })
      } else {
        await api.createOrderFor(customerId, { items, notes: notesValue })
      }
      onSaved()
    } catch (err) {
      setError(err.message)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="edit-form">
      <div className="row">
        <h2>{order ? `Order #${order.id}` : 'New order'}</h2>
        <button className="link" onClick={onClose}>
          Close
        </button>
      </div>

      {order?.status === 'paid' && (
        <p className="notice">
          This order is paid. Changing its total will flag a payment adjustment.
        </p>
      )}
      {error && <p className="error">{error}</p>}

      <div className="row">
        <input
          type="search"
          aria-label="Find customer"
          placeholder="Find customer"
          value={find}
          onChange={(e) => setFind(e.target.value)}
        />
        <select
          aria-label="Customer"
          value={customerKey}
          onChange={(e) => setCustomerKey(e.target.value)}
        >
          <option value="">Choose a customer</option>
          {shown.map((o) => (
            <option key={o.key} value={o.key}>
              {o.label}
            </option>
          ))}
        </select>
      </div>

      <table className="table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Quantity</th>
            <th>Unit price</th>
            <th>Available</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {lines.map((line) => (
            <tr key={line.listing_id}>
              <td>{line.title}</td>
              <td>
                <input
                  type="number"
                  min="1"
                  aria-label={`Quantity of ${line.title}`}
                  value={line.quantity}
                  onChange={setLine(line.listing_id, 'quantity')}
                />
              </td>
              <td>
                <input
                  type="text"
                  inputMode="decimal"
                  aria-label={`Price of ${line.title}`}
                  value={line.unit_price}
                  onChange={setLine(line.listing_id, 'unit_price')}
                />
                {line.listing_price !== null &&
                  isMoney(line.unit_price) &&
                  toCents(line.unit_price) !== toCents(line.listing_price) && (
                    <div className="muted">listing price {money(line.listing_price)}</div>
                  )}
              </td>
              <td className="muted">{line.available ?? '-'}</td>
              <td>
                <button
                  className="link"
                  aria-label={`Remove ${line.title}`}
                  onClick={() => setLines(lines.filter((l) => l.listing_id !== line.listing_id))}
                >
                  Remove
                </button>
              </td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="row">
        <input
          type="search"
          aria-label="Find item"
          placeholder="Find item"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
        />
        <button onClick={search}>Search</button>
      </div>
      {results.map((listing) => (
        <div key={listing.id} className="row">
          <span>
            {listing.title} <span className="muted">{money(listing.price)}, {listing.quantity_available} available</span>
          </span>
          <button className="link" aria-label={`Add ${listing.title}`} onClick={() => add(listing)}>
            Add
          </button>
        </div>
      ))}

      <label className="field">
        Notes
        <textarea value={notes} onChange={(e) => setNotes(e.target.value)} />
      </label>

      <div className="row">
        <strong>Total {money(fromCents(totalCents(lines)))}</strong>
        <button disabled={saving} onClick={save}>
          {saving ? 'Saving...' : 'Save order'}
        </button>
      </div>
    </div>
  )
}
```

Note: `money()` from `shared/format` formats a decimal string via `Intl`, so `money('450.00')` renders `$450.00`; the float it creates internally is display-only on an already-exact value.

- [ ] **Step 4: Run to verify they pass**; adjust markup, never the assertions' meaning.
- [ ] **Step 5: Commit**

```
git add frontend/src/owner/pages/orders/OrderDialog.jsx frontend/src/owner/pages/orders/OrderEditor.jsx frontend/src/owner/pages/orders/OrderEditor.test.jsx
git commit -m "Add the console's order editor"
```

---

### Task 9: History view, and wiring the Orders page

**Files:**
- Create: `frontend/src/owner/pages/orders/OrderHistory.jsx`, `frontend/src/owner/pages/orders/OrderHistory.test.jsx`
- Modify: `frontend/src/owner/pages/Orders.jsx`, `frontend/src/owner/pages/Orders.test.jsx`, `docs/system-administration.md`

**Interfaces:**
- Consumes: `OrderDialog`, `OrderEditor`, `api.listOrderChanges`.
- Produces: `<OrderHistory order onClose />`; `describeChange(row) -> string` exported from `OrderHistory.jsx`.

- [ ] **Step 1: Write the failing tests**

`OrderHistory.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

vi.mock('../../api', () => ({ api: { listOrderChanges: vi.fn() } }))

import { api } from '../../api'
import OrderHistory, { describeChange } from './OrderHistory'

const at = '2026-09-15T14:40:00Z'
const row = (id, change, extra = {}) => ({
  id, change, changed_at: at, changed_by_email: 'admin@example.com',
  listing_id: 3, listing_title: 'Morgan', from_value: null, to_value: null, ...extra,
})

describe('OrderHistory', () => {
  it('describes each kind of change', () => {
    expect(describeChange(row(1, 'quantity', { from_value: '2', to_value: '3' })))
      .toBe('Morgan quantity 2 -> 3')
    expect(describeChange(row(2, 'unit_price', { from_value: '189.00', to_value: '150.00' })))
      .toBe('Morgan price $189.00 -> $150.00')
    expect(describeChange(row(3, 'line_added', { to_value: '1 @ 10.00' }))).toBe('added Morgan: 1 @ 10.00')
    expect(describeChange(row(4, 'line_removed', { from_value: '2 @ 189.00' })))
      .toBe('removed Morgan (was 2 @ 189.00)')
    expect(describeChange(row(5, 'total', { listing_title: null, from_value: '378.00', to_value: '450.00' })))
      .toBe('total $378.00 -> $450.00')
    expect(describeChange(row(6, 'placed', { to_value: 'admin@example.com' })))
      .toBe('placed by admin@example.com')
  })

  it('groups one save into one entry', async () => {
    api.listOrderChanges.mockResolvedValue([
      row(3, 'total', { from_value: '378.00', to_value: '567.00' }),
      row(2, 'quantity', { from_value: '2', to_value: '3' }),
      row(1, 'placed', { changed_at: '2026-09-14T10:00:00Z', to_value: 'customer@example.com' }),
    ])
    render(<OrderHistory order={{ id: 12 }} onClose={vi.fn()} />)
    const entries = await screen.findAllByRole('listitem')
    expect(entries).toHaveLength(2)
    expect(entries[0]).toHaveTextContent('Morgan quantity 2 -> 3; total $378.00 -> $567.00')
  })
})
```

Append to `frontend/src/owner/pages/Orders.test.jsx` (add `listOrderChanges`, `listCustomers`, `listUsers`, `getCatalogItem`, `listCatalog`, `createOrderFor`, `reviseOrder`, `customerForUser` to the mock; resolve `listCustomers`/`listUsers` to `[]` in `beforeEach`; add `version: 1, notes: null, placed_by_email: ..., payment_adjustment_due: ...` to both fixtures -- order 12 `placed_by_email: 'admin@example.com'`, `payment_adjustment_due: true`; order 11 `placed_by_email: 'grace@example.com'`, `false`):

```jsx
  it('offers Edit only on pending or paid orders', async () => {
    await renderPage()
    expect(within(rowFor(12)).getByRole('button', { name: 'Edit' })).toBeInTheDocument()
    expect(within(rowFor(11)).queryByRole('button', { name: 'Edit' })).toBeNull()
  })

  it('opens the editor for a new order', async () => {
    const user = await renderPage()
    await user.click(screen.getByRole('button', { name: 'New order' }))
    expect(await screen.findByRole('heading', { name: 'New order' })).toBeInTheDocument()
  })

  it('flags a payment adjustment and who entered an order', async () => {
    await renderPage()
    expect(within(rowFor(12)).getByText('payment adjustment due')).toBeInTheDocument()
    expect(within(rowFor(12)).getByText('entered by admin@example.com')).toBeInTheDocument()
    expect(within(rowFor(11)).queryByText(/entered by/)).toBeNull()
  })

  it('opens an order history', async () => {
    api.listOrderChanges.mockResolvedValue([])
    const user = await renderPage()
    await user.click(within(rowFor(12)).getByRole('button', { name: 'History' }))
    expect(api.listOrderChanges).toHaveBeenCalledWith(12)
  })
```

- [ ] **Step 2: Run to verify they fail.**

- [ ] **Step 3: Implement**

`OrderHistory.jsx`:

```jsx
import { useEffect, useState } from 'react'

import { api } from '../../api'

const title = (row) => row.listing_title ?? `listing ${row.listing_id}`

/** One history row as a phrase. Money values arrive as plain decimals. */
export function describeChange(row) {
  const from = row.from_value
  const to = row.to_value
  switch (row.change) {
    case 'placed':
      return `placed by ${to}`
    case 'line_added':
      return `added ${title(row)}: ${to}`
    case 'line_removed':
      return `removed ${title(row)} (was ${from})`
    case 'quantity':
      return `${title(row)} quantity ${from} -> ${to}`
    case 'unit_price':
      return `${title(row)} price $${from} -> $${to}`
    case 'customer':
      return `customer ${from} -> ${to}`
    case 'notes':
      return 'notes changed'
    case 'status':
      return `status ${from} -> ${to}`
    case 'total':
      return `total $${from} -> $${to}`
    default:
      return row.change
  }
}

/**
 * An order's history, one entry per save. Rows arrive newest first; a save's
 * rows share a time and an account, so consecutive rows sharing both are one
 * entry, read oldest change first.
 */
export default function OrderHistory({ order, onClose }) {
  const [rows, setRows] = useState(null)
  const [error, setError] = useState('')

  useEffect(() => {
    let cancelled = false
    api
      .listOrderChanges(order.id)
      .then((body) => !cancelled && setRows(body))
      .catch((err) => !cancelled && setError(err.message))
    return () => {
      cancelled = true
    }
  }, [order.id])

  const groups = []
  for (const row of rows ?? []) {
    const last = groups[groups.length - 1]
    if (last && last.at === row.changed_at && last.by === row.changed_by_email) {
      last.rows.unshift(row)
    } else {
      groups.push({ at: row.changed_at, by: row.changed_by_email, rows: [row] })
    }
  }

  return (
    <div className="edit-form">
      <div className="row">
        <h2>History of order #{order.id}</h2>
        <button className="link" onClick={onClose}>
          Close
        </button>
      </div>
      {error && <p className="error">{error}</p>}
      {rows && groups.length === 0 && <p className="muted">No recorded changes.</p>}
      <ul>
        {groups.map((group) => (
          <li key={`${group.at}-${group.by}`}>
            <strong>{new Date(group.at).toLocaleString()}</strong>{' '}
            {group.by ?? 'unknown account'}: {group.rows.map(describeChange).join('; ')}
          </li>
        ))}
      </ul>
    </div>
  )
}
```

`Orders.jsx` changes:
- State: `const [editing, setEditing] = useState(null)` (`'new'` or an order) and `const [historyOf, setHistoryOf] = useState(null)`.
- Above the filter row: `<button onClick={() => setEditing('new')}>New order</button>`.
- Add a last column header `<th />` and, per row, a cell:

```jsx
<td>
  {['pending', 'paid'].includes(order.status) && (
    <button className="link" onClick={() => setEditing(order)}>Edit</button>
  )}{' '}
  <button className="link" onClick={() => setHistoryOf(order)}>History</button>
</td>
```

- In the customer cell, after the email: `{order.placed_by_email && order.placed_by_email !== order.customer_email && (<div className="muted">entered by {order.placed_by_email}</div>)}`.
- In the total cell, after the amount: `{order.payment_adjustment_due && <span className="badge">payment adjustment due</span>}`.
- After the table:

```jsx
{editing && (
  <OrderDialog label="Order" onClose={() => setEditing(null)}>
    <OrderEditor
      order={editing === 'new' ? null : editing}
      onClose={() => setEditing(null)}
      onSaved={() => {
        setEditing(null)
        setReloads((n) => n + 1)
      }}
    />
  </OrderDialog>
)}
{historyOf && (
  <OrderDialog label="Order history" onClose={() => setHistoryOf(null)}>
    <OrderHistory order={historyOf} onClose={() => setHistoryOf(null)} />
  </OrderDialog>
)}
```

`docs/system-administration.md`, Orders section: add paragraphs for placing an order for a customer (`POST /api/customers/{id}/orders`, prices default to the listing's), revising (`PUT /api/orders/{id}`, pending or paid only, whole contents, version, stock by difference), `payment_adjustment_due`, and the history (`GET /api/orders/{id}/changes`).

- [ ] **Step 4: Run the page, editor, history and shell tests**; **Step 5: gate and commit**

```
git add frontend/src/owner/pages/orders/OrderHistory.jsx frontend/src/owner/pages/orders/OrderHistory.test.jsx frontend/src/owner/pages/Orders.jsx frontend/src/owner/pages/Orders.test.jsx docs/system-administration.md
git commit -m "Place, edit and trace orders from the console's Orders page"
```

---

### Task 10: Mutation checks and a live check on a copy

**Files:** none committed except fixes the checks reveal.

- [ ] **Step 1: Mutation checks.** Write a scratch script (in the session scratchpad, never the repo) that copies each target file to a backup directory on disk first, then for each mutation asserts its anchor matches exactly once, applies it, runs the named tests, restores, and finally verifies every file is byte-identical to its backup. Mutations, each must FAIL its tests:
  1. `_lock_listings`: drop `.with_for_update()` -- `test_order_revision_race.py`.
  2. `revise_order`: `listing.quantity_available -= delta` -> removed -- `test_order_writes.py`.
  3. `EDITABLE_STATUSES` -> `frozenset({"pending", "paid", "packed"})`.
  4. The `version != order.version` check -> `if False:`.
  5. `place_order_for_customer`: `admin: AdminUser` -> `admin: CurrentUser` (import it).
  6. `db.add_all(changes)` -> removed.
  7. `payment_adjustment_due`: `c.id > max(paid)` -> `True`.
  8. `cents.js` `toCents`: return `Math.round(Number(text) * 100)` and `totalCents` using `Number(line.unit_price) * Number(quantity) * 100` -- `cents.test.js`.
  9. `OrderEditor`: send `version: order.version` -> omitted.

- [ ] **Step 2: Live check on a copy.** The working database is never written. From cmd, with `PGBIN=%USERPROFILE%\miniforge3\envs\ccwebdb\Library\bin` and `PGPASSWORD=devpassword`:

```
"%PGBIN%\psql.exe" -h localhost -U ccwebdb -d ccwebdb -tAc "select count(*) from sales_order"
"%PGBIN%\createdb.exe" -h localhost -U ccwebdb -T template0 ccwebdb_orders_check
"%PGBIN%\pg_dump.exe" -h localhost -U ccwebdb ccwebdb | "%PGBIN%\psql.exe" -q -h localhost -U ccwebdb -d ccwebdb_orders_check
```

Then, from `backend\`, in a console of its own so its `DATABASE_URL` cannot leak into this one:

```
start "ccweb orders check" cmd /k "set DATABASE_URL=postgresql+psycopg://ccwebdb:devpassword@localhost:5432/ccwebdb_orders_check&& %USERPROFILE%\miniforge3\envs\ccwebdb\python.exe -m alembic upgrade head&& %USERPROFILE%\miniforge3\envs\ccwebdb\python.exe -m uvicorn app.main:app --port 8001"
```

Drive the flows with a scratch httpx script against `http://127.0.0.1:8001/api`: log in as `admin@example.com`; create an account with `POST /api/users`, then its customer record with `POST /api/users/{id}/customer`; place an order for that customer with `POST /api/customers/{id}/orders` at an overridden price; revise it; `PATCH` it to `paid`; revise the total; read `/changes`; confirm `payment_adjustment_due` is true. Close the "ccweb orders check" console, then:

```
"%PGBIN%\dropdb.exe" -h localhost -U ccwebdb ccwebdb_orders_check
"%PGBIN%\psql.exe" -h localhost -U ccwebdb -d ccwebdb -tAc "select count(*) from sales_order"
```

The two counts on `ccwebdb` must match (0 today).

- [ ] **Step 3: Gate.** `scripts\ccweb_check.cmd` exit 0.

- [ ] **Step 4: Report** results to the user and wait for "merge it into main and push".
