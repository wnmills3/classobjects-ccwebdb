# For-sale guards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Warn before a change to an item a buyer is looking at, on the five
write paths that currently change such items silently.

**Architecture:** One public `sale_state.guard()` raises 409 unless the caller
sent `acknowledge_for_sale`. Six endpoints call it and the two existing
inline guards are refactored onto it, making eight call sites and one place
that decides what the refusal says. Receiving additionally ends the listing it
warned about, through `offering_writes` -- never by assigning listing status.
Three console surfaces carry the acknowledgement; two guarded endpoints have
no console caller and are guarded anyway.

**Tech Stack:** FastAPI + SQLAlchemy 2 (backend), React 19 + Vite (owner
console), pytest, vitest + Testing Library, ruff, mypy, eslint, prettier.

**Spec:** `docs/specs/for-sale-guards-design.md`

## Global Constraints

- **No migration.** Nothing in this plan changes the schema. If a task seems
  to need one, stop and raise it.
- **`offering_writes` is the only writer of listing status and claims.** Never
  assign `listing.status` or touch `offer_claim` directly. Receiving ends a
  listing by calling `offering_writes.end_offer(db, listing)`.
- **Docstrings and annotations are enforced**, ruff `D` and `ANN`, at zero.
  Every public function, method and class needs a docstring; every function
  needs full annotations. `backend/tests/*` is exempt from `D103` only --
  test functions still need `-> None` and annotated parameters.
- **Branch:** create `feat/for-sale-guards` from `main`. Never commit to
  `main`.
- **The gate is `scripts\ccweb_check.cmd`** (add `fix` to auto-fix first). Run
  it as its own command and read the exit code separately -- a trailing
  `; echo $?` reports the echo, not the gate. Never pipe it.
- **cmd/batch only.** No PowerShell, no `.ps1`, and no `powershell -Command`
  from inside a `.cmd`. Invoke scripts as `.\script.cmd` or by full path:
  `NoDefaultCurrentDirectoryInExePath=1` is set on this machine, so
  `cmd /c script.cmd` fails with "not recognized" even in the current
  directory.
- **One pytest session at a time.** The suite uses its own `ccwebdb_test`
  database; two concurrent runs produce failures that mimic real bugs.
- **Run pytest from the repository root.** `pyproject.toml` sets
  `testpaths = ["backend/tests"]` and `pythonpath = ["backend"]`, and
  `addopts = "-q --strict-markers"` -- do not add another `-q`.
- **Use the Write tool for new files, Edit for surgical changes.** No shell
  heredocs.

---

## File Structure

**Backend, modified:**

- `backend/app/sale_state.py` -- gains the public `guard()`. Already owns
  `for_sale()` and `refusal()`; this makes it own the refusal too.
- `backend/app/routers/inventory.py` -- deletes the private
  `_refuse_unacknowledged_sale` (line 743), repoints its two call sites
  (lines 1126 and 1238), and adds guards to `receive_items`, `split` and
  `set_item_errors`. `receive_items` also ends listings.
- `backend/app/routers/images.py` -- guards `upload_image` and `delete_image`.
- `backend/app/routers/reference.py` -- guards `merge_value` and passes the
  for-sale report through.
- `backend/app/reference_merge.py` -- `MergePlan` keeps the affected item
  codes that are for sale instead of discarding the id set.
- `backend/app/schemas.py` -- `acknowledge_for_sale` on `ReceiveRequest`,
  `SplitRequest`, `ItemErrorsRequest` and `ReferenceMergeIn`; `for_sale` and
  `for_sale_count` on `ReferenceMergeOut`.

**Backend, created:**

- `backend/tests/test_for_sale_guards.py` -- the whole policy in one file.

**Frontend, created:**

- `frontend/src/owner/pages/ForSaleNotice.jsx` -- the shared inline notice and
  checkbox, replacing two hand-rolled copies.
- `frontend/src/owner/pages/ForSaleConfirm.jsx` -- the modal for the batch
  case, beside the existing `EndOfferConfirm.jsx`.
- Test files beside each.

**Frontend, modified:**

- `frontend/src/owner/api.js` -- `setItemErrors`, `receiveItems`,
  `uploadImage` and `mergeReferenceValue` carry the acknowledgement.
- `frontend/src/owner/pages/inventory/ItemEditForm.jsx` -- uses
  `ForSaleNotice`; passes `saleState` to `ErrorsPanel`.
- `frontend/src/owner/pages/inventory/BulkEditBar.jsx` -- uses
  `ForSaleNotice`.
- `frontend/src/owner/pages/inventory/ErrorsPanel.jsx` -- takes `saleState`,
  holds a sticky acknowledgement.
- `frontend/src/owner/pages/receiving/ReceiptPanel.jsx` -- keeps `sale_state`
  from the fetch it already makes; shows `ForSaleConfirm` on a 409.
- `frontend/src/owner/pages/Vocabularies.jsx` -- the preview reports for-sale
  items; confirming sends the acknowledgement.

**Docs, modified (Task 11):**

- `docs/specs/for-sale-guards-design.md` -- status line to "built".
- `docs/system-administration.md` -- the refusal list.

---

### Task 1: The shared guard

**Files:**
- Modify: `backend/app/sale_state.py`
- Modify: `backend/app/routers/inventory.py:743` (delete), `:1126`, `:1238`
- Test: `backend/tests/test_for_sale_guards.py` (create)

**Interfaces:**
- Consumes: `sale_state.for_sale(db, item_ids) -> dict[int, list[SaleUse]]`,
  `sale_state.refusal(codes_and_uses) -> str`, both already public.
- Produces: `sale_state.guard(db, items, *, acknowledged, kinds=None) -> None`.
  `items` is a `Collection[InventoryItem]` (loaded items, not ids -- the
  refusal names them by `item_code`). `kinds` is a collection of `SaleUse.kind`
  values, `"listing"` and `"order"`; `None` means all. Raises
  `fastapi.HTTPException` with status 409. Every later task calls this.

- [ ] **Step 1: Write the failing tests**

Create `backend/tests/test_for_sale_guards.py`:

```python
"""The warning before a change to an item that is for sale.

One file for the whole policy: `app.sale_state.guard` and each endpoint that
calls it. The existing coverage of the two original call sites lives in
`test_sale_snapshots.py`.
"""

from __future__ import annotations

import pytest
from app import sale_state
from app.models import InventoryItem, Listing, ListingStatus
from fastapi import HTTPException
from sqlalchemy.orm import Session


def test_the_guard_passes_an_item_that_is_not_for_sale(
    db: Session, listing: Listing
) -> None:
    other = db.get(InventoryItem, listing.inventory_item_id)
    assert other is not None
    # Ended, so nothing offers it. Assigned directly because this is a test
    # fixture being put into a state, not the application ending an offer --
    # `offering_writes` remains the only writer in app code.
    listing.status = ListingStatus.ended
    db.commit()
    sale_state.guard(db, [other], acknowledged=False)


def test_the_guard_refuses_a_listed_item_and_names_it(
    db: Session, listing: Listing
) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    with pytest.raises(HTTPException) as raised:
        sale_state.guard(db, [item], acknowledged=False)
    assert raised.value.status_code == 409
    assert item.item_code in raised.value.detail
    assert f"listing #{listing.id}" in raised.value.detail


def test_an_acknowledged_caller_is_let_through(db: Session, listing: Listing) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    sale_state.guard(db, [item], acknowledged=True)


def test_kinds_narrows_which_reasons_count(db: Session, listing: Listing) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    # The only reason this item is for sale is a listing, so asking about
    # orders alone finds nothing to refuse.
    sale_state.guard(db, [item], acknowledged=False, kinds={"order"})
    with pytest.raises(HTTPException):
        sale_state.guard(db, [item], acknowledged=False, kinds={"listing"})


def test_no_items_is_not_a_refusal(db: Session) -> None:
    sale_state.guard(db, [], acknowledged=False)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -v`
Expected: FAIL, `AttributeError: module 'app.sale_state' has no attribute 'guard'`

- [ ] **Step 3: Add the guard**

In `backend/app/sale_state.py`, add `HTTPException` to the imports and
`InventoryItem` to the model imports:

```python
from fastapi import HTTPException
```

```python
from .models import (
    InventoryItem,
    Listing,
    ListingStatus,
    SalesOrder,
    SalesOrderItem,
    SalesOrderStatus,
    SalesVenue,
)
```

Extend `__all__` and append the function:

```python
__all__ = ["OPEN_ORDER_STATUSES", "SaleUse", "for_sale", "guard", "refusal"]
```

```python
def guard(
    db: Session,
    items: Collection[InventoryItem],
    *,
    acknowledged: bool,
    kinds: Collection[str] | None = None,
) -> None:
    """409 unless the caller has said it knows these items are for sale.

    `kinds` narrows which reasons count, by `SaleUse.kind`. Split passes
    `{"listing"}` because `app.splitting` refuses an item in an order
    outright: an acknowledgement that does not let the caller through would
    teach an operator to tick past warnings that mean something.

    A 409 rather than a 422: the request is well formed and would be accepted
    at another moment. The status code matches the two call sites this
    replaces, and the console tells the case apart by the message's opening
    words ("For sale").
    """
    if acknowledged:
        return
    wanted = list(items)
    found = for_sale(db, [item.id for item in wanted])
    if kinds is not None:
        narrowed = set(kinds)
        found = {
            item_id: kept
            for item_id, uses in found.items()
            if (kept := [use for use in uses if use.kind in narrowed])
        }
    if not found:
        return
    codes = {item.id: item.item_code for item in wanted}
    # A bare 409 rather than `status.HTTP_409_CONFLICT`: `for_sale` above
    # binds a local named `status` in its order loop, and importing fastapi's
    # `status` into this module would put a shadowed name one function away
    # from a live one. Both spellings are already used in this codebase.
    raise HTTPException(
        status_code=409,
        detail=refusal({codes[item_id]: uses for item_id, uses in found.items()}),
    )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Repoint the two existing call sites**

In `backend/app/routers/inventory.py`, delete the whole
`_refuse_unacknowledged_sale` function (line 743 and its body).

At the bulk call site (was line 1126):

```python
    if data and not acknowledged:
        sale_state.guard(db, list(items), acknowledged=False)
```

At the PATCH call site (was line 1238):

```python
        sale_state.guard(db, [item], acknowledged=False)
```

Keep the surrounding `if` conditions exactly as they are -- they decide
*whether* to ask, and only the asking moves.

- [ ] **Step 6: Run the existing coverage of those two paths**

Run: `python -m pytest backend/tests/test_sale_snapshots.py -v`
Expected: PASS, including
`test_a_listed_item_is_not_changed_without_saying_so` and
`test_bulk_edit_names_the_items_for_sale`

- [ ] **Step 7: Run the gate**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

- [ ] **Step 8: Commit**

```bash
git add backend/app/sale_state.py backend/app/routers/inventory.py backend/tests/test_for_sale_guards.py
git commit -m "Give the for-sale refusal one home"
```

---

### Task 2: Item errors warn

**Files:**
- Modify: `backend/app/schemas.py:838` (`ItemErrorsRequest`)
- Modify: `backend/app/routers/inventory.py:1502` (`set_item_errors`)
- Test: `backend/tests/test_for_sale_guards.py`

**Interfaces:**
- Consumes: `sale_state.guard` from Task 1.
- Produces: `ItemErrorsRequest.acknowledge_for_sale: bool = False`. The
  console sends it in Task 8.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_for_sale_guards.py`:

```python
def test_errors_on_a_listed_item_are_refused_until_acknowledged(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item_id = listing.inventory_item_id
    body = {"errors": [{"error_type": "off_center", "details": None}]}

    refused = client.put(
        f"/api/inventory/{item_id}/errors", json=body, headers=admin_headers
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]
    unchanged = client.get(
        f"/api/inventory/{item_id}/errors", headers=admin_headers
    ).json()
    assert unchanged["errors"] == []

    made = client.put(
        f"/api/inventory/{item_id}/errors",
        json={**body, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert made.status_code == 200, made.text
    assert [e["error_type"] for e in made.json()["errors"]] == ["off_center"]
```

Add to that file's imports:

```python
from fastapi.testclient import TestClient
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest backend/tests/test_for_sale_guards.py::test_errors_on_a_listed_item_are_refused_until_acknowledged -v`
Expected: FAIL -- the PUT returns 200 and the first assertion fails

If it fails instead with a 422 naming `off_center`, that error type is not in
the seeded vocabulary for a coin. Run
`python -m pytest backend/tests/test_item_errors.py -v`, take a code that file
uses, and substitute it here.

- [ ] **Step 3: Add the field to the request schema**

In `backend/app/schemas.py`, inside `class ItemErrorsRequest`, below `errors`:

```python
    #: Set after a refusal to say the caller knows the item is for sale
    #: (app.sale_state).
    acknowledge_for_sale: bool = False
```

- [ ] **Step 4: Guard the endpoint**

In `backend/app/routers/inventory.py`, in `set_item_errors`, directly after
`item = _get_item(db, item_id)`:

```python
    sale_state.guard(db, [item], acknowledged=payload.acknowledge_for_sale)
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -v`
Expected: PASS, 6 passed

- [ ] **Step 6: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/schemas.py backend/app/routers/inventory.py backend/tests/test_for_sale_guards.py
git commit -m "Warn before recording errors against an item that is for sale"
```

---

### Task 3: Split warns, and an order still refuses outright

**Files:**
- Modify: `backend/app/schemas.py:386` (`SplitRequest`)
- Modify: `backend/app/routers/inventory.py:1308` (`split`)
- Test: `backend/tests/test_for_sale_guards.py`

**Interfaces:**
- Consumes: `sale_state.guard` with `kinds={"listing"}`.
- Produces: `SplitRequest.acknowledge_for_sale: bool = False`. No console
  caller -- `api.splitItem` exists in `owner/api.js` and nothing calls it.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_for_sale_guards.py`:

```python
def _two_pieces() -> list[dict[str, object]]:
    return [
        {"source_title": "Piece one", "piece_count": 1},
        {"source_title": "Piece two", "piece_count": 1},
    ]


def test_splitting_a_listed_lot_is_refused_until_acknowledged(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item_id = listing.inventory_item_id
    body = {"mode": "equal", "pieces": _two_pieces()}

    refused = client.post(
        f"/api/inventory/{item_id}/split", json=body, headers=admin_headers
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    made = client.post(
        f"/api/inventory/{item_id}/split",
        json={**body, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert made.status_code == 200, made.text


def test_an_order_refuses_a_split_that_cannot_be_acknowledged(
    client: TestClient,
    db: Session,
    listing: Listing,
    customer_headers: dict[str, str],
    admin_headers: dict[str, str],
) -> None:
    ordered = client.post(
        "/api/orders",
        json={"items": [{"listing_id": listing.id, "quantity": 1}]},
        headers=customer_headers,
    )
    assert ordered.status_code == 201, ordered.text

    # Acknowledging is not a way past an order: `app.splitting` refuses it
    # outright, and the guard deliberately does not offer to override that.
    refused = client.post(
        f"/api/inventory/{listing.inventory_item_id}/split",
        json={
            "mode": "equal",
            "pieces": _two_pieces(),
            "acknowledge_for_sale": True,
        },
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert "appears in an order" in refused.json()["detail"]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -k split -v`
Expected: the first FAILS (the split succeeds without a warning). The second
may already pass, because `splitting.split_item` already refuses an order --
that is the behaviour it is there to lock down, so it must keep passing
through every later step.

- [ ] **Step 3: Add the field to the request schema**

In `backend/app/schemas.py`, inside `class SplitRequest`, below `pieces`:

```python
    #: Set after a refusal to say the caller knows the lot is offered
    #: somewhere (app.sale_state). An order refuses the split regardless.
    acknowledge_for_sale: bool = False
```

- [ ] **Step 4: Guard the endpoint**

In `backend/app/routers/inventory.py`, in `split`, directly after
`parent = _get_item(db, item_id)`:

```python
    # Listings only. An item in an order is refused by `split_item` below and
    # that refusal is not negotiable, so offering to acknowledge it would be a
    # confirmation that does not let the caller through.
    sale_state.guard(
        db,
        [parent],
        acknowledged=payload.acknowledge_for_sale,
        kinds={"listing"},
    )
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -v`
Expected: PASS, 8 passed

- [ ] **Step 6: Run the split suite, which must be unaffected**

Run: `python -m pytest backend/tests/test_split.py -v`
Expected: PASS, no change

- [ ] **Step 7: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/schemas.py backend/app/routers/inventory.py backend/tests/test_for_sale_guards.py
git commit -m "Say a lot is offered before splitting it"
```

---

### Task 4: Receiving warns, and ends the listing it warned about

**Files:**
- Modify: `backend/app/schemas.py:800` (`ReceiveRequest`)
- Modify: `backend/app/routers/inventory.py:297` (`receive_items`)
- Test: `backend/tests/test_for_sale_guards.py`

**Interfaces:**
- Consumes: `sale_state.guard`; `offering_writes.end_offer(db, listing)` and
  `offering_writes.ON_OFFER`.
- Produces: `ReceiveRequest.acknowledge_for_sale: bool = False`. The console
  sends it in Task 9.

**Why this is three outcomes wide:** `offering_writes.offer()` refuses an item
whose status is not `received`, and an order can only hold a listing, so an
item that has not been received cannot be for sale. `received` therefore needs
no guard; the existing "already received" 409 covers repeat receipts.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_for_sale_guards.py`:

```python
def test_marking_a_listed_item_missing_is_refused_until_acknowledged(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item_id = listing.inventory_item_id
    body = {"item_ids": [item_id], "outcome": "missing"}

    refused = client.post(
        "/api/inventory/receive", json=body, headers=admin_headers
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]
    item = db.get(InventoryItem, item_id)
    assert item is not None
    db.refresh(item)
    assert item.status.code == "received"


def test_receiving_an_item_that_is_not_for_sale_asks_nothing(
    client: TestClient, make_item: Callable[..., InventoryItem],
    admin_headers: dict[str, str]
) -> None:
    item = make_item()
    made = client.post(
        "/api/inventory/receive",
        json={"item_ids": [item.id], "outcome": "received"},
        headers=admin_headers,
    )
    assert made.status_code == 200, made.text


def test_an_acknowledged_missing_ends_the_listing_and_releases_the_claim(
    client: TestClient, db: Session, admin_headers: dict[str, str]
) -> None:
    # Built through `offering_writes.offer` rather than `build_listing`,
    # because the fixture makes a CLAIMLESS listing: it would prove the
    # listing ended while saying nothing about the claim.
    item = build_item(db)
    venue = db.scalars(select(SalesVenue).where(SalesVenue.is_own_store)).first()
    assert venue is not None
    made = offering_writes.offer(
        db,
        item=item,
        venue=venue,
        listing_format=ListingFormat.fixed_price,
        price=Decimal("50.00"),
        title="",
        description="",
        external_id=None,
        quantity=1,
    )
    db.commit()

    response = client.post(
        "/api/inventory/receive",
        json={
            "item_ids": [item.id],
            "outcome": "missing",
            "acknowledge_for_sale": True,
        },
        headers=admin_headers,
    )
    assert response.status_code == 200, response.text

    db.expire_all()
    ended = db.get(Listing, made.id)
    assert ended is not None
    assert ended.status is ListingStatus.ended
    # Asserted against the claim row itself, NOT through
    # `offering_writes.claims_for`. That helper is built on `_holding_claims`,
    # whose WHERE joins `OfferClaim.state.in_(HELD_BY)` AND
    # `Listing.status.in_(ON_OFFER)` -- so once the listing is ended it returns
    # nothing whatever the claim's own state is, and an assertion through it
    # would pass against a claim left `active`. This is the assertion that
    # proves the release.
    claim = db.scalars(select(OfferClaim).where(OfferClaim.listing_id == made.id)).one()
    assert claim.state is ClaimState.released
    refreshed = db.get(InventoryItem, item.id)
    assert refreshed is not None
    assert refreshed.status.code == "missing"
```

Add to that file's imports:

```python
from collections.abc import Callable
from decimal import Decimal

from app import offering_writes, sale_state
from app.models import (
    ClaimState,
    InventoryItem,
    Listing,
    ListingFormat,
    ListingStatus,
    OfferClaim,
    SalesVenue,
)
from sqlalchemy import select
from tests.conftest import build_item
```

`offer()` is **keyword-only** apart from `db`, and `listing_format`, `title`,
`description` and `external_id` are all required with no defaults -- this was
verified against `backend/app/offering_writes.py:340` and against the calls in
`backend/tests/test_offering_writes.py:135`. Pass them exactly as written
above.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -k "missing or receiving" -v`
Expected: the two guard tests FAIL (the receipt succeeds unwarned)

- [ ] **Step 3: Add the field to the request schema**

In `backend/app/schemas.py`, inside `class ReceiveRequest`, below `note`:

```python
    #: Set after a refusal to say the caller knows an item is for sale
    #: (app.sale_state). Only the outcomes that are not `received` can be
    #: refused: an item that has not been received cannot be offered.
    acknowledge_for_sale: bool = False
```

- [ ] **Step 4: Guard the endpoint and end the listings**

In `backend/app/routers/inventory.py`, in `receive_items`, after the "already
received" block and before the `storage_location_id` validation:

```python
    # An item that has not been received cannot be offered
    # (`offering_writes.offer` refuses it) and an order can only hold a
    # listing, so a plain receipt has nothing to warn about. The other three
    # outcomes say a coin will not be delivered, and that is exactly what a
    # buyer needs protecting from.
    if payload.outcome != "received":
        sale_state.guard(
            db, list(items), acknowledged=payload.acknowledge_for_sale
        )
```

After the `for item in items:` status loop and before `db.commit()`:

```python
    # A coin that cannot be delivered must not stay offered. Ended through
    # `offering_writes`, the only writer of listing status and claims -- never
    # by assigning `listing.status` here.
    if payload.outcome != "received":
        offered = db.scalars(
            select(Listing).where(
                Listing.inventory_item_id.in_([item.id for item in items]),
                Listing.status.in_(offering_writes.ON_OFFER),
            )
        ).all()
        for live in offered:
            offering_writes.end_offer(db, live)
```

Add the imports `receive_items` needs at the top of the module if they are not
already there: `from .. import offering_writes, sale_state` and
`from ..models import Listing`. Check the existing import block first -- 
`sale_state` is already imported at line 23.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -v`
Expected: PASS, 11 passed

- [ ] **Step 6: Run the receiving suite, which must be unaffected**

Run: `python -m pytest backend/tests/test_receiving.py backend/tests/test_acquisitions.py -v`
Expected: PASS, no change

- [ ] **Step 7: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/schemas.py backend/app/routers/inventory.py backend/tests/test_for_sale_guards.py
git commit -m "Withdraw a coin from sale when receiving says it will not arrive"
```

---

### Task 5: Images warn, on upload and on delete

**Files:**
- Modify: `backend/app/routers/images.py:124` (`upload_image`), `:207`
  (`delete_image`)
- Test: `backend/tests/test_for_sale_guards.py`

**Interfaces:**
- Consumes: `sale_state.guard`.
- Produces: an `acknowledge_for_sale` `Form` field on `POST /api/images` and
  an `acknowledge_for_sale` query parameter on `DELETE /api/images/{id}`. The
  console sends the Form field in Task 9; nothing calls the delete.

**Why the transports differ:** four endpoints take a JSON body and carry the
flag in it. `POST /api/images` is `multipart/form-data`, so the flag is a
`Form` field beside `inventory_item_id`. `DELETE /api/images/{image_id}` has
no body at all -- request bodies on DELETE are permitted by specification but
unreliable through intermediaries and awkward from `fetch` -- so it is a query
parameter. This is settled; do not change it.

- [ ] **Step 1: Write the failing tests**

Append to `backend/tests/test_for_sale_guards.py`:

```python
def _png() -> bytes:
    """The smallest PNG the ingest path will accept."""
    import base64

    return base64.b64decode(
        b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAAC0lEQVR4nGP4"
        b"z8AAAAMBAQDJ/pLvAAAAAElFTkSuQmCC"
    )


def test_attaching_a_photograph_to_a_listed_item_is_refused(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    files = {"file": ("coin.png", _png(), "image/png")}
    refused = client.post(
        "/api/images",
        files=files,
        data={"inventory_item_id": str(listing.inventory_item_id)},
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    made = client.post(
        "/api/images",
        files={"file": ("coin.png", _png(), "image/png")},
        data={
            "inventory_item_id": str(listing.inventory_item_id),
            "acknowledge_for_sale": "true",
        },
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text


def test_an_unattached_photograph_is_never_refused(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    made = client.post(
        "/api/images",
        files={"file": ("loose.png", _png(), "image/png")},
        headers=admin_headers,
    )
    assert made.status_code == 201, made.text


def test_deleting_a_photograph_of_a_listed_item_is_refused(
    client: TestClient, listing: Listing, admin_headers: dict[str, str]
) -> None:
    attached = client.post(
        "/api/images",
        files={"file": ("coin.png", _png(), "image/png")},
        data={
            "inventory_item_id": str(listing.inventory_item_id),
            "acknowledge_for_sale": "true",
        },
        headers=admin_headers,
    )
    assert attached.status_code == 201, attached.text
    image_id = attached.json()["id"]

    refused = client.delete(f"/api/images/{image_id}", headers=admin_headers)
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    gone = client.delete(
        f"/api/images/{image_id}?acknowledge_for_sale=true", headers=admin_headers
    )
    assert gone.status_code == 204, gone.text
```

Read `backend/tests/test_images.py` first and reuse whatever it already uses
to build an acceptable image and to read the created id -- if that file has a
byte-string fixture or a helper, use it instead of `_png()` above and delete
`_png()`.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -k "photograph" -v`
Expected: FAIL -- both writes succeed unwarned

- [ ] **Step 3: Guard the upload**

In `backend/app/routers/images.py`, add the parameter to `upload_image`'s
signature, after `is_primary`:

```python
    acknowledge_for_sale: bool = Form(default=False),
```

and guard inside the `if inventory_item_id is not None:` block, directly after
the 404 for an unknown item:

```python
        sale_state.guard(db, [item], acknowledged=acknowledge_for_sale)
```

- [ ] **Step 4: Guard the delete**

In `backend/app/routers/images.py`, change `delete_image` to learn what it is
deleting before it deletes it:

```python
@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_image(
    image_id: int,
    db: DbSession,
    _admin: AdminUser,
    acknowledge_for_sale: bool = False,
) -> None:
    """Remove an image, its renditions and its stored bytes.

    `acknowledge_for_sale` is a query parameter rather than a body field
    because DELETE has no body here. The shop serves an item's primary image
    (`routers.catalog`), so deleting one changes what a buyer is looking at.
    """
    image = db.get(Image, image_id)
    if image is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Image not found"
        )

    # This endpoint knew only an image id. The items it is attached to are
    # what the for-sale rule is about, so they are read before anything is
    # removed.
    attached = db.scalars(
        select(InventoryItem)
        .join(ItemImage, ItemImage.inventory_item_id == InventoryItem.id)
        .where(ItemImage.image_id == image.id)
    ).all()
    sale_state.guard(db, list(attached), acknowledged=acknowledge_for_sale)

    storage = get_storage()
    for derivative in image.derivatives:
        storage.delete(derivative.storage_key)
    storage.delete(image.storage_key)

    db.delete(image)
    db.commit()
```

Add whatever of `select`, `InventoryItem`, `ItemImage` and `sale_state` the
module does not already import. Check the existing import block first;
`ItemImage` is already imported for `upload_image`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -v`
Expected: PASS, 14 passed

- [ ] **Step 6: Run the images suite, which must be unaffected**

Run: `python -m pytest backend/tests/test_images.py -v`
Expected: PASS, no change

- [ ] **Step 7: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/routers/images.py backend/tests/test_for_sale_guards.py
git commit -m "Ask before changing the photograph a buyer is looking at"
```

---

### Task 6: Merge reports for-sale items, then warns

**Files:**
- Modify: `backend/app/reference_merge.py` (`MergePlan`, `plan`)
- Modify: `backend/app/schemas.py:964` (`ReferenceMergeIn`), `:972`
  (`ReferenceMergeOut`)
- Modify: `backend/app/routers/reference.py:452` (`merge_value`)
- Test: `backend/tests/test_for_sale_guards.py`

**Interfaces:**
- Consumes: `sale_state.guard`; `sale_state.for_sale`.
- Produces: `MergePlan.for_sale: list[str]` (item codes, **at most ten**) and
  `MergePlan.for_sale_count: int` (the true total); the same two fields on
  `ReferenceMergeOut`; `ReferenceMergeIn.acknowledge_for_sale: bool = False`.
  The console reads all three in Task 10.

- [ ] **Step 1: Write the failing test**

Append to `backend/tests/test_for_sale_guards.py`:

```python
def test_a_merge_reports_the_items_for_sale_then_refuses(
    client: TestClient, db: Session, listing: Listing, admin_headers: dict[str, str]
) -> None:
    item = db.get(InventoryItem, listing.inventory_item_id)
    assert item is not None
    code = item.grade.code
    into = "MS63" if code != "MS63" else "MS62"

    preview = client.post(
        f"/api/reference/grade/{code}/merge",
        json={"into": into, "dry_run": True},
        headers=admin_headers,
    )
    assert preview.status_code == 200, preview.text
    assert item.item_code in preview.json()["for_sale"]
    assert preview.json()["for_sale_count"] >= 1

    refused = client.post(
        f"/api/reference/grade/{code}/merge",
        json={"into": into, "dry_run": False},
        headers=admin_headers,
    )
    assert refused.status_code == 409
    assert "For sale" in refused.json()["detail"]

    made = client.post(
        f"/api/reference/grade/{code}/merge",
        json={"into": into, "dry_run": False, "acknowledge_for_sale": True},
        headers=admin_headers,
    )
    assert made.status_code == 200, made.text
```

`grade` may be one of the vocabularies `routers.reference.retirable` refuses
to merge. Read `backend/tests/test_reference_merge.py` first and use whichever
table and codes it merges successfully; substitute them throughout this test
rather than fighting the refusal.

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -k merge -v`
Expected: FAIL with `KeyError: 'for_sale'`

- [ ] **Step 3: Keep the item ids the plan already computes**

In `backend/app/reference_merge.py`, add two fields to `MergePlan`:

```python
    #: Item codes among `items` that are for sale, at most ten of them.
    for_sale: list[str] = field(default_factory=list)
    #: How many are for sale in total, however many are named above.
    for_sale_count: int = 0
```

and, in `plan()`, directly after `result.items = len(items)`:

```python
    # `items` is discarded below; the codes that are for sale are what the
    # console has to show before anyone confirms a merge.
    from . import sale_state

    for_sale = sale_state.for_sale(db, items)
    if for_sale:
        codes = db.scalars(
            select(InventoryItem.item_code)
            .where(InventoryItem.id.in_(list(for_sale)))
            .order_by(InventoryItem.item_code)
        ).all()
        result.for_sale_count = len(codes)
        result.for_sale = list(codes[:10])
```

`sale_state` is imported inside the function because `sale_state` imports
`offering_writes`, and a module-level import here would make a cycle -- the
same reason `offering_writes` imports `sale_state` inside a function.

- [ ] **Step 4: Carry the fields through the schemas**

In `backend/app/schemas.py`, inside `class ReferenceMergeIn`:

```python
    #: Set after a refusal to say the caller knows some of the items the
    #: merge moves are for sale (app.sale_state).
    acknowledge_for_sale: bool = False
```

and inside `class ReferenceMergeOut`, below `aliases`:

```python
    #: Item codes among those moved that are for sale, at most ten.
    for_sale: list[str] = Field(default_factory=list)
    #: How many are for sale in total, however many are named above.
    for_sale_count: int = 0
```

- [ ] **Step 5: Guard the endpoint and return the report**

In `backend/app/routers/reference.py`, in `merge_value`, replace the
`else:` branch so the plan is computed and guarded before the merge runs:

```python
        if payload.dry_run:
            result, _, _ = reference_merge.plan(db, model, code, payload.into)
        else:
            planned, _, _ = reference_merge.plan(db, model, code, payload.into)
            if planned.for_sale_count:
                for_sale_items = db.scalars(
                    select(InventoryItem).where(
                        InventoryItem.item_code.in_(planned.for_sale)
                    )
                ).all()
                sale_state.guard(
                    db,
                    list(for_sale_items),
                    acknowledged=payload.acknowledge_for_sale,
                )
            result = reference_merge.merge(
                db, model, code, payload.into, user_id=admin.id
            )
```

and add the two new fields to the returned `ReferenceMergeOut(...)`:

```python
        for_sale=result.for_sale,
        for_sale_count=result.for_sale_count,
```

Add `sale_state`, `select` and `InventoryItem` to this module's imports if
they are not already there.

One acknowledgement covers the whole merge, and the refusal names at most the
ten codes the plan kept. Naming two hundred item codes in a message helps
nobody.

- [ ] **Step 6: Run the tests to verify they pass**

Run: `python -m pytest backend/tests/test_for_sale_guards.py -v`
Expected: PASS, 15 passed

- [ ] **Step 7: Run the merge suite, and expect it to need updating**

Run: `python -m pytest backend/tests/test_reference_merge.py backend/tests/test_reference.py -v`

Expected: PASS **unless** a test merges a vocabulary value held by an item
that is for sale — those now get a 409, correctly. That is not a regression:
adding a refusal to an endpoint changes the contract for every existing
caller that hits the refused state. Fix such a test by adding
`"acknowledge_for_sale": True` to its request, with a one-line comment saying
the test is about the merge, not the guard, and **keep its original
assertions unchanged**. Tasks 3 and 5 hit exactly this in `test_split.py` and
`test_images.py` and resolved it the same way.

- [ ] **Step 8: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add backend/app/reference_merge.py backend/app/schemas.py backend/app/routers/reference.py backend/tests/test_for_sale_guards.py
git commit -m "Say which items are for sale before a vocabulary merge moves them"
```

---

### Task 7: One shared notice, replacing two copies

**Files:**
- Create: `frontend/src/owner/pages/ForSaleNotice.jsx`
- Create: `frontend/src/owner/pages/ForSaleNotice.test.jsx`
- Modify: `frontend/src/owner/pages/inventory/ItemEditForm.jsx:492-507`
- Modify: `frontend/src/owner/pages/inventory/BulkEditBar.jsx:115-124`

**Interfaces:**
- Produces: `<ForSaleNotice uses={[]} checked={bool} onChange={fn}
  action="Change it anyway" heading="This item is for sale" show={undefined} />`.
  `uses` is the `sale_state` array from `ItemDetailOut` (`[{kind, id, text}]`).
  `action` is the checkbox's label. `heading` is the sentence before the
  reasons, so a multi-item caller is not made to say "This item". `show`
  forces the notice for a caller that knows an item is for sale but has no
  `uses` to list -- the bulk bar, which learns it from a server message it
  already renders elsewhere; it defaults to `uses.length > 0`. Task 8 reuses
  it.

- [ ] **Step 1: Write the failing test**

Create `frontend/src/owner/pages/ForSaleNotice.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import ForSaleNotice from './ForSaleNotice'

describe('ForSaleNotice', () => {
  it('renders nothing when the item is not for sale', () => {
    const { container } = render(
      <ForSaleNotice uses={[]} checked={false} onChange={vi.fn()} action="Anyway" />,
    )
    expect(container).toBeEmptyDOMElement()
  })

  it('shows on request with no reasons to list, for a multi-item caller', () => {
    render(
      <ForSaleNotice
        show
        heading="Some of the selected items are for sale"
        checked={false}
        onChange={vi.fn()}
        action="Anyway"
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent(
      'Some of the selected items are for sale',
    )
  })

  it('names every reason the item is for sale', () => {
    render(
      <ForSaleNotice
        uses={[
          { kind: 'listing', id: 3, text: 'listing #3 at 120.00' },
          { kind: 'order', id: 7, text: 'order #7 (paid)' },
        ]}
        checked={false}
        onChange={vi.fn()}
        action="Anyway"
      />,
    )
    expect(screen.getByRole('alert')).toHaveTextContent('listing #3 at 120.00')
    expect(screen.getByRole('alert')).toHaveTextContent('order #7 (paid)')
  })

  it('reports a tick to its caller', async () => {
    const onChange = vi.fn()
    render(
      <ForSaleNotice
        uses={[{ kind: 'listing', id: 3, text: 'listing #3' }]}
        checked={false}
        onChange={onChange}
        action="Change it anyway"
      />,
    )
    await userEvent.click(screen.getByLabelText('Change it anyway'))
    expect(onChange).toHaveBeenCalledWith(true)
  })
})
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/owner/pages/ForSaleNotice.test.jsx`
Expected: FAIL, cannot resolve `./ForSaleNotice`

- [ ] **Step 3: Write the component**

Create `frontend/src/owner/pages/ForSaleNotice.jsx`:

```jsx
/**
 * The warning shown before changing an item a buyer is looking at.
 *
 * One component for what were two hand-rolled copies, in `ItemEditForm` and
 * `BulkEditBar`, and a third was about to be written for `ErrorsPanel`. The
 * wording names every reason rather than saying "this is for sale": a person
 * with two offers open needs to know which one they are about to change.
 *
 * `uses` is the `sale_state` array an item detail carries. Empty means there
 * is nothing to warn about, and the component renders nothing at all --
 * callers do not need their own conditional.
 */
export default function ForSaleNotice({
  uses = [],
  checked,
  onChange,
  action,
  heading = 'This item is for sale',
  show,
}) {
  // `show` is for a caller that knows an item is for sale without holding the
  // reasons: the bulk bar learns it from a refusal it already displays, and
  // repeating that message here would print it twice.
  if (!(show ?? uses.length > 0)) return null
  return (
    <div className="for-sale" role="alert">
      <strong>{heading}</strong>
      {uses.length > 0 && `: ${uses.map((use) => use.text).join(', ')}`}. A change
      shows to buyers at once; each sale keeps the item as it was sold.
      <label className="checkbox">
        <input
          type="checkbox"
          checked={checked}
          onChange={(e) => onChange(e.target.checked)}
        />
        {/* */}
        {action}
      </label>
    </div>
  )
}
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/owner/pages/ForSaleNotice.test.jsx`
Expected: PASS, 4 passed

- [ ] **Step 5: Use it in `ItemEditForm`**

Replace the `{forSale && (...)}` block (lines 492-507) with:

```jsx
      <ForSaleNotice
        uses={item.sale_state ?? []}
        checked={acknowledged}
        onChange={setAcknowledged}
        action="Change it anyway"
      />
```

and add the import beside the others:

```jsx
import ForSaleNotice from '../ForSaleNotice'
```

`forSale` is still used by `canSave` and by the `save()` payload, so leave
that constant in place.

- [ ] **Step 6: Use it in `BulkEditBar`**

Replace the `{forSale && (...)}` checkbox block (lines 115-124) with:

```jsx
      <ForSaleNotice
        show={forSale}
        heading="Some of the selected items are for sale"
        checked={acknowledged}
        onChange={setAcknowledged}
        action="Change the items for sale too"
      />
```

The bulk bar learns about the sale from the server's message, which it already
renders in its own `<span className="error">` below -- so it passes `show`
and no `uses`, and the message is printed once. Add the import:

```jsx
import ForSaleNotice from '../ForSaleNotice'
```

- [ ] **Step 7: Run both surfaces' tests**

Run: `cd frontend && npx vitest run src/owner/pages/inventory/ItemEditForm.test.jsx src/owner/pages/inventory/BulkEditBar.test.jsx`
Expected: PASS. If a test asserted on the old markup's exact text, update the
assertion to the new wording rather than changing the component back.

- [ ] **Step 8: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add frontend/src/owner/pages/ForSaleNotice.jsx frontend/src/owner/pages/ForSaleNotice.test.jsx frontend/src/owner/pages/inventory/ItemEditForm.jsx frontend/src/owner/pages/inventory/BulkEditBar.jsx
git commit -m "Write the for-sale warning once"
```

---

### Task 8: The errors panel carries a sticky acknowledgement

**Files:**
- Modify: `frontend/src/owner/api.js:90` (`setItemErrors`)
- Modify: `frontend/src/owner/pages/inventory/ErrorsPanel.jsx`
- Modify: `frontend/src/owner/pages/inventory/ItemEditForm.jsx:678`
- Modify: `frontend/src/owner/pages/receiving/ReceiptPanel.jsx:124-138, :346`
- Test: `frontend/src/owner/pages/inventory/ErrorsPanel.test.jsx`

**Interfaces:**
- Consumes: `ForSaleNotice` from Task 7; the backend field from Task 2.
- Produces: `ErrorsPanel` takes a new `saleState` prop (default `[]`);
  `api.setItemErrors(id, errors, { acknowledgeForSale })`.

**Why sticky:** the panel has no Save button -- it PUTs the whole set on every
row added, removed or edited (`ErrorsPanel.jsx:107`). An acknowledgement asked
per save would be asked on every keystroke-ish action, which teaches an
operator to tick it blind. It is ticked once and holds for the editing
session.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/owner/pages/inventory/ErrorsPanel.test.jsx`:

```jsx
describe('ErrorsPanel, an item that is for sale', () => {
  it('warns, then sends the acknowledgement on every later save', async () => {
    const user = userEvent.setup()
    api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    api.setItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(
      <ErrorsPanel
        itemId={12}
        kind="currency"
        saleState={[{ kind: 'listing', id: 3, text: 'listing #3 at 120.00' }]}
      />,
      // StrictMode: this panel already lost an effect guard to the mismatch
      // between how it is tested and how the console actually runs.
      { reference: vocabularies, strict: true },
    )

    expect(await screen.findByRole('alert')).toHaveTextContent('listing #3 at 120.00')
    await user.click(screen.getByLabelText('Record it anyway'))

    await user.selectOptions(
      screen.getByRole('combobox', { name: 'error_type' }),
      'miscut',
    )
    await user.click(screen.getByRole('button', { name: 'Add error' }))
    expect(api.setItemErrors).toHaveBeenLastCalledWith(
      12,
      [{ error_type: 'miscut', details: null }],
      { acknowledgeForSale: true },
    )

    // Sticky: the second change does not ask again.
    await user.selectOptions(
      screen.getByRole('combobox', { name: 'error_type' }),
      'inverted_overprint',
    )
    await user.click(screen.getByRole('button', { name: 'Add error' }))
    expect(api.setItemErrors).toHaveBeenLastCalledWith(12, expect.anything(), {
      acknowledgeForSale: true,
    })
  })

  it('renders no warning for an item that is not for sale', async () => {
    api.getItemErrors.mockResolvedValue({ inventory_item_id: 12, errors: [] })
    renderWithProviders(<ErrorsPanel itemId={12} kind="currency" />, {
      reference: vocabularies,
    })
    await screen.findByRole('combobox', { name: 'error_type' })
    expect(screen.queryByRole('alert')).not.toBeInTheDocument()
  })
})
```

`vocabularies`, `api` and `renderWithProviders` are already defined at the top
of that file; `miscut` and `inverted_overprint` are two of the three error
types it seeds, and `'currency'` is the kind both fit.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/owner/pages/inventory/ErrorsPanel.test.jsx`
Expected: FAIL -- no alert is rendered, and `setItemErrors` is called with two
arguments

- [ ] **Step 3: Carry the flag in the API call**

In `frontend/src/owner/api.js`:

```js
  setItemErrors: (id, errors, { acknowledgeForSale = false } = {}) =>
    send(`/api/inventory/${id}/errors`, {
      method: 'PUT',
      body: { errors, acknowledge_for_sale: acknowledgeForSale },
    }),
```

- [ ] **Step 4: Take the prop and hold the acknowledgement**

In `frontend/src/owner/pages/inventory/ErrorsPanel.jsx`, change the signature
and add the state:

```jsx
export default function ErrorsPanel({ itemId, kind, value, onChange, saleState }) {
  const controlled = itemId == null
  // Sticky for the editing session: this panel PUTs on every change, and an
  // acknowledgement asked per save would be asked on every keystroke.
  const [acknowledged, setAcknowledged] = useState(false)
```

change `save` to send it:

```jsx
  function save(next) {
    if (controlled) return
    api
      .setItemErrors(itemId, withNoEmptyDetails(next), {
        acknowledgeForSale: acknowledged,
      })
      .then(() => setError(''))
      .catch((err) => setError(err.message))
  }
```

and render the notice at the top of the panel's returned markup:

```jsx
      <ForSaleNotice
        uses={saleState ?? []}
        checked={acknowledged}
        onChange={setAcknowledged}
        action="Record it anyway"
      />
```

with the import:

```jsx
import ForSaleNotice from '../ForSaleNotice'
```

- [ ] **Step 5: Pass it from both mounts that have an item**

In `ItemEditForm.jsx:678`:

```jsx
      <ErrorsPanel
        itemId={itemId}
        kind={value('item_kind')}
        saleState={item.sale_state ?? []}
      />
```

In `ReceiptPanel.jsx`, keep `sale_state` from the fetch the panel already
makes. Add the state beside `itemKind`, clear it in the same place `itemKind`
is cleared, and set it in the same `.then`:

```jsx
      .then((body) => {
        if (!cancelled) {
          setItemKind(body.item_kind ?? null)
          setItemSaleState(body.sale_state ?? [])
        }
      })
      .catch(() => {
        if (!cancelled) {
          setItemKind(null)
          setItemSaleState([])
        }
      })
```

and pass it at line 346:

```jsx
        <ErrorsPanel
          key={singleItemId}
          itemId={singleItemId}
          kind={itemKind}
          saleState={itemSaleState}
        />
```

No extra request: `api.getInventoryItem(singleItemId)` is already called and
its response already carries `sale_state`.

`NewItemForm` mounts the panel with no `itemId` -- the controlled mode, for an
item that does not exist yet. It passes no `saleState`, the default empty
array applies, and the notice renders nothing.

- [ ] **Step 6: Update the assertions the new argument breaks**

`setItemErrors` now takes three arguments, and the existing tests assert on
two. Every `expect(api.setItemErrors).toHaveBeenCalledWith(12, [...])` in
`ErrorsPanel.test.jsx` gains the options object:

```jsx
    expect(api.setItemErrors).toHaveBeenCalledWith(
      12,
      [{ error_type: 'miscut', details: "miscut at 3 o'clock" }],
      { acknowledgeForSale: false },
    )
```

Find them all first:

Run: `cd frontend && npx vitest run src/owner/pages/inventory/ErrorsPanel.test.jsx`
and fix each failure it names. `NewItemForm.jsx` also calls `setItemErrors`
(lines 343 and 377) with two arguments -- that still works, because the third
parameter defaults, but check `NewItemForm.test.jsx` for assertions on the
call shape.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/owner/pages/inventory/ErrorsPanel.test.jsx src/owner/pages/inventory/ItemEditForm.test.jsx src/owner/pages/receiving/ReceiptPanel.test.jsx src/owner/pages/entry/NewItemForm.test.jsx`
Expected: PASS

- [ ] **Step 8: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add frontend/src/owner/api.js frontend/src/owner/pages/inventory/ErrorsPanel.jsx frontend/src/owner/pages/inventory/ErrorsPanel.test.jsx frontend/src/owner/pages/inventory/ItemEditForm.jsx frontend/src/owner/pages/receiving/ReceiptPanel.jsx
git commit -m "Ask once before recording errors against a coin on offer"
```

---

### Task 9: Receiving asks, and says the listing will end

**Files:**
- Create: `frontend/src/owner/pages/ForSaleConfirm.jsx`
- Create: `frontend/src/owner/pages/ForSaleConfirm.test.jsx`
- Modify: `frontend/src/owner/api.js:130` (`receiveItems`), `:177`
  (`uploadImage`)
- Modify: `frontend/src/owner/pages/receiving/ReceiptPanel.jsx:147` (`submit`)
- Test: `frontend/src/owner/pages/receiving/ReceiptPanel.test.jsx`

**Interfaces:**
- Consumes: the backend fields from Tasks 4 and 5; `ModalDialog` from
  `frontend/src/owner/ModalDialog.jsx`, as `EndOfferConfirm` uses it.
- Produces: `<ForSaleConfirm detail={string} outcome={string} busy={bool}
  onConfirm={fn} onCancel={fn} />`; `api.receiveItems(payload)` accepts
  `acknowledge_for_sale`; `api.uploadImage(id, file, { acknowledgeForSale })`.

- [ ] **Step 1: Write the failing tests**

Create `frontend/src/owner/pages/ForSaleConfirm.test.jsx`:

```jsx
import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import ForSaleConfirm from './ForSaleConfirm'

describe('ForSaleConfirm', () => {
  it('names the items and says the listing ends', () => {
    render(
      <ForSaleConfirm
        detail="For sale -- CC-000123: listing #3 at 120.00."
        outcome="missing"
        busy={false}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    )
    expect(screen.getByRole('dialog')).toHaveTextContent('CC-000123')
    expect(screen.getByRole('dialog')).toHaveTextContent('withdrawn from sale')
  })

  it('reports a confirmation', async () => {
    const onConfirm = vi.fn()
    render(
      <ForSaleConfirm
        detail="For sale -- CC-000123: listing #3."
        outcome="missing"
        busy={false}
        onConfirm={onConfirm}
        onCancel={vi.fn()}
      />,
    )
    await userEvent.click(screen.getByRole('button', { name: /record it anyway/i }))
    expect(onConfirm).toHaveBeenCalled()
  })
})
```

Append to `frontend/src/owner/pages/receiving/ReceiptPanel.test.jsx`:

```jsx
  it('asks before marking a for-sale item missing, then resends acknowledged', async () => {
    const detail = 'For sale -- CC-000412: listing #3 at 189.00.'
    api.receiveItems
      .mockRejectedValueOnce(
        Object.assign(new Error(detail), { status: 409, body: { detail } }),
      )
      .mockResolvedValueOnce({ received: 1 })

    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /missing/i }))

    expect(await screen.findByRole('dialog')).toHaveTextContent('CC-000412')
    await userEvent.click(screen.getByRole('button', { name: 'Record it anyway' }))

    await waitFor(() => expect(api.receiveItems).toHaveBeenCalledTimes(2))
    expect(api.receiveItems.mock.calls[1][0]).toMatchObject({
      outcome: 'missing',
      acknowledge_for_sale: true,
    })
  })

  it('leaves the panel alone when the refusal is declined', async () => {
    const detail = 'For sale -- CC-000412: listing #3 at 189.00.'
    api.receiveItems.mockRejectedValueOnce(
      Object.assign(new Error(detail), { status: 409, body: { detail } }),
    )

    renderWithProviders(<ReceiptPanel itemIds={[412]} onDone={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: /missing/i }))
    await userEvent.click(
      await screen.findByRole('button', { name: 'Leave it on sale' }),
    )

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(api.receiveItems).toHaveBeenCalledTimes(1)
  })
```

`ITEM` in that file is `CC-000412`, so the refusal names it; `renderWithProviders`,
`waitFor` and the `/missing/i` button are all already used there. The existing
`beforeEach` sets `api.receiveItems.mockResolvedValue({ received: 2 })`, and
`mockRejectedValueOnce` takes precedence over it for the first call only,
which is exactly the sequence these tests need.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd frontend && npx vitest run src/owner/pages/ForSaleConfirm.test.jsx src/owner/pages/receiving/ReceiptPanel.test.jsx`
Expected: FAIL, cannot resolve `./ForSaleConfirm`; no dialog appears

- [ ] **Step 3: Write the confirm dialog**

Create `frontend/src/owner/pages/ForSaleConfirm.jsx`:

```jsx
import ModalDialog from '../ModalDialog'

/**
 * The question asked before a receipt changes a coin someone is buying.
 *
 * Shown from the server's refusal rather than from a loaded item: receiving
 * works on a batch and holds no `sale_state` of its own, so the 409 is what
 * says which items are affected. The message is passed through verbatim --
 * `app.sale_state.refusal` already names each item and each listing or order,
 * and rewording it here would mean maintaining the same sentence twice.
 *
 * The consequence is spelled out because it is not obvious: an outcome that
 * is not "received" says the coin will not be delivered, so the backend also
 * ends the listing offering it. One click with no question is how a live
 * offer disappears by accident.
 */
export default function ForSaleConfirm({ detail, outcome, busy, onConfirm, onCancel }) {
  const question = `Record "${outcome}" for an item that is for sale?`
  return (
    <ModalDialog label={question} onClose={onCancel}>
      <h2>{question}</h2>
      <p>{detail}</p>
      <p>
        Recording this says the coin will not be delivered, so it is also{' '}
        <strong>withdrawn from sale</strong>: any listing offering it is ended.
        Nothing brings that listing back; offering the item again makes a new one.
      </p>
      <div className="row">
        <button disabled={busy} onClick={onConfirm}>
          {busy ? 'Recording...' : 'Record it anyway'}
        </button>
        <button className="link" onClick={onCancel}>
          Leave it on sale
        </button>
      </div>
    </ModalDialog>
  )
}
```

- [ ] **Step 4: Carry the flag in the two API calls**

In `frontend/src/owner/api.js`, `receiveItems` already forwards a whole
payload, so it needs no change -- the caller adds `acknowledge_for_sale`.
Change `uploadImage` to accept the flag:

```js
  uploadImage: (inventoryItemId, file, { imageRole, isPrimary = false, acknowledgeForSale = false } = {}) => {
    const form = new FormData()
    form.append('file', file)
    if (inventoryItemId != null) form.append('inventory_item_id', inventoryItemId)
    if (imageRole) form.append('image_role', imageRole)
    form.append('is_primary', String(isPrimary))
    form.append('acknowledge_for_sale', String(acknowledgeForSale))
    return send('/api/images', { method: 'POST', body: form })
  },
```

Keep the existing body of that function exactly as it is apart from the new
`form.append`; the lines above are a guide, not a replacement to paste over a
function that differs.

- [ ] **Step 5: Drive the dialog from `submit`**

In `ReceiptPanel.jsx`, add state:

```jsx
  // The refusal the server sent, held while the operator answers it.
  const [forSaleRefusal, setForSaleRefusal] = useState(null)
```

Give `submit` an acknowledgement parameter and catch the 409:

```jsx
  async function submit(outcome, acknowledged = false) {
    setBusy(true)
    try {
      await api.receiveItems({
        item_ids: itemIds,
        outcome,
        arrived_on: arrivedOn || undefined,
        storage_location_id: storageLocationId ? Number(storageLocationId) : undefined,
        note: note || undefined,
        ...(acknowledged ? { acknowledge_for_sale: true } : {}),
      })
      setForSaleRefusal(null)
```

and in the existing `catch (err)`, before `setError(err.message)`:

```jsx
      // A refusal is a question, not a failure: hold it for the dialog rather
      // than writing it into the panel's error line, where it would read as
      // something that went wrong.
      if (err.status === 409 && err.message.startsWith('For sale')) {
        setForSaleRefusal({ detail: err.message, outcome })
        return
      }
```

Render the dialog beside the panel's other conditional content:

```jsx
      {forSaleRefusal && (
        <ForSaleConfirm
          detail={forSaleRefusal.detail}
          outcome={forSaleRefusal.outcome}
          busy={busy}
          onConfirm={() => submit(forSaleRefusal.outcome, true)}
          onCancel={() => setForSaleRefusal(null)}
        />
      )}
```

with the import:

```jsx
import ForSaleConfirm from '../ForSaleConfirm'
```

- [ ] **Step 6: Leave the photograph path alone**

Do **not** route an upload refusal into this dialog. The upload already runs
inside its own `Promise.allSettled` with a comment explaining that a failed
upload must never roll the receipt back; a 409 from `POST /api/images` is
reported by `setUploadError` exactly like any other upload failure. Confirm by
reading the block around `uploadFailed` and changing nothing in it.

- [ ] **Step 7: Run the tests to verify they pass**

Run: `cd frontend && npx vitest run src/owner/pages/ForSaleConfirm.test.jsx src/owner/pages/receiving/ReceiptPanel.test.jsx`
Expected: PASS

- [ ] **Step 8: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add frontend/src/owner/pages/ForSaleConfirm.jsx frontend/src/owner/pages/ForSaleConfirm.test.jsx frontend/src/owner/api.js frontend/src/owner/pages/receiving/ReceiptPanel.jsx frontend/src/owner/pages/receiving/ReceiptPanel.test.jsx
git commit -m "Ask before a receipt withdraws a coin from sale"
```

---

### Task 10: The merge preview says what is for sale

**Files:**
- Modify: `frontend/src/owner/api.js:44` (`mergeReferenceValue`)
- Modify: `frontend/src/owner/pages/Vocabularies.jsx` (`MergePanel`)
- Test: `frontend/src/owner/pages/Vocabularies.test.jsx`

**Interfaces:**
- Consumes: `for_sale` and `for_sale_count` on `ReferenceMergeOut` from
  Task 6.
- Produces: `api.mergeReferenceValue(table, code, into, dryRun,
  { acknowledgeForSale })`.

- [ ] **Step 1: Write the failing test**

Append to `frontend/src/owner/pages/Vocabularies.test.jsx`:

```jsx
  it('names the for-sale items in the preview and acknowledges on merge', async () => {
    const user = userEvent.setup()
    api.mergeReferenceValue.mockImplementation(async (table, code, into, dryRun) => ({
      table,
      code,
      into,
      dry_run: dryRun,
      moved: { 'inventory_item.note_type_id': 3 },
      items: 3,
      dropped: 0,
      aliases: [],
      for_sale: ['CC-000412'],
      for_sale_count: 1,
    }))

    renderWithProviders(<Vocabularies />, { auth: adminAuth(), reference })
    await user.click(
      await screen.findByRole('button', {
        name: 'Merge National Bank Note into another value',
      }),
    )
    await user.selectOptions(
      screen.getByLabelText('Merge National Bank Note into'),
      'us_note',
    )

    expect(await screen.findByText(/CC-000412/)).toBeInTheDocument()
    expect(screen.getByRole('alert')).toHaveTextContent('1 of them is for sale')

    await user.click(screen.getByRole('button', { name: 'Merge' }))
    expect(api.mergeReferenceValue).toHaveBeenLastCalledWith(
      'note_type',
      'national_bank_note',
      'us_note',
      false,
      { acknowledgeForSale: true },
    )
  })
```

That file's existing merge test at line 269 uses exactly this sequence -- the
`Merge National Bank Note into another value` button, the
`Merge National Bank Note into` select, and the `Merge` button -- so match it
rather than inventing labels. Note the merge button is disabled until a
preview has arrived; the existing test asserts that, and this one relies on it.

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd frontend && npx vitest run src/owner/pages/Vocabularies.test.jsx`
Expected: FAIL -- the codes are not rendered

- [ ] **Step 3: Carry the flag in the API call**

In `frontend/src/owner/api.js`:

```js
  mergeReferenceValue: (table, code, into, dryRun, { acknowledgeForSale = false } = {}) =>
    send(`/api/reference/${table}/${encodeURIComponent(code)}/merge`, {
      method: 'POST',
      body: { into, dry_run: dryRun, acknowledge_for_sale: acknowledgeForSale },
    }),
```

Keep the rest of the existing function exactly as it is.

- [ ] **Step 4: Show it in the preview and send it on merge**

In `Vocabularies.jsx`, in `MergePanel`, render the report wherever the preview
is displayed:

```jsx
      {preview?.for_sale_count > 0 && (
        <p className="for-sale" role="alert">
          <strong>
            {preview.for_sale_count} of them{' '}
            {preview.for_sale_count === 1 ? 'is' : 'are'} for sale
          </strong>
          : {preview.for_sale.join(', ')}
          {preview.for_sale_count > preview.for_sale.length && ', and others'}. Merging
          changes what a buyer is looking at.
        </p>
      )}
```

and acknowledge on the real merge, which the operator has just been shown:

```jsx
  async function merge() {
    setBusy(true)
    try {
      onMerged(
        await api.mergeReferenceValue(table, value.code, into, false, {
          // The preview above named them; confirming is the acknowledgement.
          acknowledgeForSale: (preview?.for_sale_count ?? 0) > 0,
        }),
      )
    } catch (err) {
      setError(err.message)
      setBusy(false)
    }
  }
```

- [ ] **Step 5: Update the assertions the new argument breaks**

Only the **merge** assertion changes. `choose()` still calls
`mergeReferenceValue` with four arguments for the dry run, and
`toHaveBeenCalledWith` compares the actual argument list -- so asserting a
fifth on that call would fail on the count, not pass by defaulting. Leave the
dry-run assertion exactly as it is.

The merge assertion, previously four arguments, becomes:

```jsx
    expect(api.mergeReferenceValue).toHaveBeenLastCalledWith(
      'note_type',
      'national_bank_note',
      'us_note',
      false,
      { acknowledgeForSale: false },
    )
```

`false` for the flag in that existing test, because its mocked preview reports
no for-sale items.

Run: `cd frontend && npx vitest run src/owner/pages/Vocabularies.test.jsx`
and fix each failure it names.

- [ ] **Step 6: Run the test to verify it passes**

Run: `cd frontend && npx vitest run src/owner/pages/Vocabularies.test.jsx`
Expected: PASS

- [ ] **Step 7: Run the gate and commit**

Run: `scripts\ccweb_check.cmd fix`
Expected: exit code 0

```bash
git add frontend/src/owner/api.js frontend/src/owner/pages/Vocabularies.jsx frontend/src/owner/pages/Vocabularies.test.jsx
git commit -m "Name the items for sale before a merge moves them"
```

---

### Task 11: Mutation pass and documentation

**Files:**
- Modify: `docs/specs/for-sale-guards-design.md` (status line)
- Modify: `docs/system-administration.md` (refusal list)
- Create: nothing

**Interfaces:**
- Consumes: every call site from Tasks 1-6.
- Produces: a recorded mutation result; no code change survives this task.

**Why:** a guard that is never actually reached passes review looking correct.
The only proof that each call site is live is to remove it and watch a named
test go red.

- [ ] **Step 1: Confirm the whole suite is green first**

Run: `scripts\ccweb_check.cmd`
Expected: exit code 0. A mutation pass against a red suite proves nothing.

- [ ] **Step 2: Mutate each of the eight call sites in turn**

For each one: delete the `sale_state.guard(...)` call (only that call), run the
named test, confirm it FAILS, then restore the line and confirm it passes
again. Do them one at a time -- two at once cannot tell you which test covers
which site.

| # | File | Call site | Test that must go red |
|---|---|---|---|
| 1 | `routers/inventory.py` | `bulk_edit` | `test_sale_snapshots.py::test_bulk_edit_names_the_items_for_sale` |
| 2 | `routers/inventory.py` | `update_item` (PATCH) | `test_sale_snapshots.py::test_a_listed_item_is_not_changed_without_saying_so` |
| 3 | `routers/inventory.py` | `set_item_errors` | `test_for_sale_guards.py::test_errors_on_a_listed_item_are_refused_until_acknowledged` |
| 4 | `routers/inventory.py` | `split` | `test_for_sale_guards.py::test_splitting_a_listed_lot_is_refused_until_acknowledged` |
| 5 | `routers/inventory.py` | `receive_items` | `test_for_sale_guards.py::test_marking_a_listed_item_missing_is_refused_until_acknowledged` |
| 6 | `routers/images.py` | `upload_image` | `test_for_sale_guards.py::test_attaching_a_photograph_to_a_listed_item_is_refused` |
| 7 | `routers/images.py` | `delete_image` | `test_for_sale_guards.py::test_deleting_a_photograph_of_a_listed_item_is_refused` |
| 8 | `routers/reference.py` | `merge_value` | `test_for_sale_guards.py::test_a_merge_reports_the_items_for_sale_then_refuses` |

Run each as, for example:

`python -m pytest "backend/tests/test_for_sale_guards.py::test_splitting_a_listed_lot_is_refused_until_acknowledged" -v`

**If any mutation does not go red, stop.** Either the call site is unreachable
or the test does not exercise it, and both are defects to fix before this
feature is called done.

- [ ] **Step 3: Mutate the `kinds` filter as well**

This one is not a deletion. In `routers/inventory.py`'s `split`, change
`kinds={"listing"}` to `kinds=None` so the guard considers orders too, and run:

`python -m pytest "backend/tests/test_for_sale_guards.py::test_kinds_listing_lets_an_ordered_but_unlisted_split_reach_split_item" -v`

Expected: FAIL, on the assertion that `"For sale"` is **not** in the detail --
the guard now intercepts an order-only item and refuses first, instead of
letting it through to `split_item`'s own refusal. Restore the filter and
confirm the test passes again.

**Do not** use `test_an_order_refuses_a_split_that_cannot_be_acknowledged`
for this mutation. It cannot fail: `guard()` returns on `acknowledged=True`
before `kinds` is read, and that test always acknowledges. This plan
originally named it here, the Tasks 2+3 review caught it, and the test above
was written in that fix round precisely to close the hole.

- [ ] **Step 4: Record the result**

Write the nine outcomes (eight deletions and the `kinds` mutation) into the
task's report or commit message: which test went red for which site. A
mutation pass with no record is indistinguishable from one that was skipped.

- [ ] **Step 5: Update the documentation**

In `docs/specs/for-sale-guards-design.md`, change the status line to:

```markdown
Design. Status: **agreed with the owner 2026-09-18**; built.
```

In `docs/system-administration.md`, find the list of refusals and add the new
ones: receiving an outcome that is not `received` for an item that is for
sale, splitting a listed lot, recording errors against one, attaching or
deleting its photograph, and merging a vocabulary value that moves it. Read
the surrounding section first and match its wording; if no such list exists in
that file, put the entry where the other endpoint refusals are described and
say so in the commit message.

- [ ] **Step 6: Run the gate and commit**

Run: `scripts\ccweb_check.cmd`
Expected: exit code 0

```bash
git add docs/specs/for-sale-guards-design.md docs/system-administration.md
git commit -m "Confirm every for-sale guard is reached, and say so in the docs"
```

- [ ] **Step 7: Confirm the branch is ready**

Run: `git log --oneline main..HEAD`
Expected: eleven commits, one per task.

Run: `git merge-base --is-ancestor main HEAD` then read `$?` on its own line.
Expected: 0, so a fast-forward merge is guaranteed.

Do not merge. The owner says when.
