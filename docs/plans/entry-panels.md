# Entry Panels Implementation Plan

Written 2026-09-15. **Spec:** `docs/specs/entry-panels-design.md` -- its "API contract" section is binding on both lanes; read it in full before starting.

**Goal:** New purchase and New item panels in the owner console, backed by vendor, purchase-order and item creation endpoints.

**Shape:** two independent lanes built in parallel against the spec's API contract, integrated by rebase, then one full gate and one whole-branch review.

## Global Constraints

- Branch `feat/entry-panels` (backend lane in the main checkout); the frontend lane works in a worktree on its own branch. Never commit to `main`.
- Tests first. Money: `Decimal` on the server, decimal strings on the wire, `isMoney`/cents helpers in the browser.
- Every public function, class and method has a docstring and full annotations; no `noqa`, no ignored lint. Request schemas forbid extra fields.
- Write files with the Write/Edit tools, never shell heredocs; cmd, never PowerShell.
- Item creation mirrors `backend/app/importers/loader.py` `SchemaLoader.load()`: resolve codes, build item, flush, one detail row (`CurrencyDetail` for `currency`, else `CoinDetail`), optional certification, `record_initial_status` last.
- Required reference codes go through `require_code`, optional ones through `code_to_id` (`backend/app/references.py`); an unknown code is a 422 naming the field.
- Years go through `backend/app/years.py` (a start with no end is a single year).
- Web addresses: `http://` or `https://` only.
- Console calls live in `frontend/src/owner/api.js`, never `frontend/src/shared/api.js` (bundle isolation).
- Accelerators: `accel`/`AccessLabel` from `frontend/src/owner/shortcuts.js` and `AccessLabel.jsx`, letters never D, E or F; `useSaveShortcut` for Ctrl+S / Ctrl+Enter.

## Task 1 (backend lane): vendors, purchase orders, item creation

**Files:** modify `backend/app/routers/acquisitions.py` (new `vendors_router`; `POST` on `purchase_orders_router`), `backend/app/main.py` (include `vendors_router`), `backend/app/routers/inventory.py` (`POST ""`), `backend/app/schemas.py` (`VendorOut`, `VendorCreate`, `PurchaseOrderCreate`, `ItemCreate`); create `backend/tests/test_entry_vendors_purchases.py`, `backend/tests/test_inventory_create.py`.

Steps: write the spec's backend tests (Testing section) and watch them fail; implement exactly the contract; `POST /api/inventory` returns the `GET /api/inventory/{id}` body by calling that route's builder; run the new tests plus `test_acquisitions.py`, `test_receiving.py`, `test_inventory_edit.py`, `test_catalog.py`; full gate; commit.

## Task 2 (frontend lane): New purchase page and New item form

**Files:** modify `frontend/src/owner/api.js` (`listVendors`, `createVendor`, `createPurchaseOrder`, `createInventoryItem`), `frontend/src/owner/OwnerApp.jsx` (route `/purchases/new`, nav **New purchase** after Receive), `frontend/src/owner/OwnerApp.test.jsx` (nav link); create `frontend/src/owner/pages/NewPurchase.jsx`, `NewPurchase.test.jsx`, `frontend/src/owner/pages/entry/NewItemForm.jsx`, `entry/NewItemForm.test.jsx`.

Steps: write the spec's frontend tests (mocking `../api` / `../../api` as existing page tests do) and watch them fail; implement to the spec's Console section; run those tests plus `OwnerApp.test.jsx`, eslint and prettier on changed files; commit. Docs are the integration step's.

## Task 3 (integration): docs, gate, review

Rebase the frontend lane onto `feat/entry-panels`; update `docs/workflow-new-collection.md` status and add "Entering a purchase" to `docs/system-administration.md`; mark the spec implemented; full gate; whole-branch review; one fix wave if needed; merge `--ff-only` and push.
