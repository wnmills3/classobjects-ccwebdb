# Reports Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. This plan is written at task level; expand each task into test-first steps (failing test, run, implement, run, commit) when it is taken up.

**Goal:** A read-only Reports page, API and command line over one registry of reports, starting with the five v1 reports.

**Architecture:** `app/reports/` holds a registry (`REPORTS`) of `Report` entries, each running SQLAlchemy Core aggregates on the base tables and returning one `ReportResult` shape; a router serves the catalog, a result as JSON and as a workbook; the console renders any report from the catalog without report-specific code.

**Tech Stack:** FastAPI, SQLAlchemy 2 (Core), PostgreSQL, openpyxl, pytest; React, Vitest.

**Spec:** `docs/specs/reporting-design.md`

## Global Constraints

- Read-only: no report writes to the database.
- Manager only (`AdminUser`) on every endpoint.
- Live rows only: no `deleted_at`, no `split_at`, through one shared predicate.
- Money is `Decimal` in Python, summed in SQL, and a decimal string on the wire.
- No proprietary numbering or price-guide values are derived, seeded or exported.
- Kind-aware: a field that does not apply to a kind is not missing for it; a note's year is its `series_year`.
- Every report runs in under a second on the live collection (measured today: 3-9 ms per aggregate).
- Never touch the live database from a test; never run two pytest sessions at once.
- Docs describe current state; cmd only in examples.

## Review Focus

1. A deleted or split item counted in any total.
2. A field reported missing for a kind it does not apply to (a note's metal).
3. A money total that differs by a cent from the sum of its rows (float anywhere).
4. A drill-down whose search returns a different count from the report row.
5. A parameter taken from the request into SQL text rather than bound or allowlisted.

## Phase 1 -- the frame and the v1 reports

### Task 1: Registry, result shape and the shared live-row predicate

**Files:** create `backend/app/reports/__init__.py`, `backend/app/reports/base.py`; move `_ITEM_IS_LIVE` from `routers/acquisitions.py` to `backend/app/live.py` (imported by both); test `backend/tests/test_reports_registry.py`.

**Produces:** `Column(key, label, kind)` where kind is one of text / count / money / percent / date / ounces; `ReportResult(columns, rows, totals, drills, notes)`; `Report(id, group, title, purpose, params: type[BaseModel], run)`; `REPORTS: dict[str, Report]`; `register(report)` refusing a duplicate id; `live_item()` returning the predicate.

**Tests:** a registered report is found by id; a duplicate id is refused; `live_item()` excludes a deleted and a split item (built data).

### Task 2: `dq_issues` and `dq_completeness`

**Files:** `backend/app/reports/data_quality.py`; `backend/app/inventory_search.py` (add `missing=<field>`); tests `test_reports_data_quality.py`, `test_inventory_search.py` (the new filter).

**Consumes:** `issues.SHARED_ISSUES`, `COIN_ISSUES`, `CURRENCY_ISSUES`; `inventory_search.count_issues`.

**Tests:** issue counts equal the search's own `count_issues`; completeness per kind with a note's year read from `series_year` and metal not applicable to notes; each drill-down's search returns the row's count.

### Task 3: `cb_holdings`

**Files:** `backend/app/reports/collection.py`; test `test_reports_collection.py`.

**Tests:** kind x denomination counts, pieces and total cost with per-kind and overall totals equal to the sum of rows to the cent; status and disposition parameters; a deleted item excluded.

### Task 4: `pr_outstanding`

**Files:** `backend/app/reports/purchasing.py`; test `test_reports_purchasing.py`.

**Tests:** the same purchases, in the same counts, as `GET /api/purchase-orders`' `outstanding`; days waiting from `ordered_on`; the overdue mark at the parameter's threshold; an undated purchase shown without a day count.

### Task 5: `sl_offered`

**Files:** `backend/app/reports/selling.py`; test `test_reports_selling.py`.

**Tests:** active and paused listings only; a sales lot's cost basis as the sum of its items'; days listed from `listed_at`.

### Task 6: API and workbook export

**Files:** `backend/app/routers/reports.py`, `backend/app/main.py`, `backend/app/reports/workbook.py`; test `test_reports_api.py`.

**Produces:** `GET /api/reports`, `GET /api/reports/{id}`, `GET /api/reports/{id}/workbook`.

**Tests:** catalog shape with parameter defaults and choices; 404 unknown report; 422 bad parameter; 401/403 for a signed-out user and a customer; the workbook's rows equal the JSON's; money cells numeric, formatted to cents.

### Task 7: Command line

**Files:** `backend/app/reports/__main__.py`; test `test_reports_cli.py`.

**Tests:** `list` prints every id; `run <id>` prints the table; `--workbook FILE` writes the same workbook the API serves (to `tmp_path`).

### Task 8: The Reports page

**Files:** `frontend/src/management/pages/Reports.jsx`, `Reports.test.jsx`; `api.js` (`listReports`, `runReport`, report workbook URL); `ManagementApp.jsx` (menu item and route); `fieldHelp.js` (each parameter).

**Tests:** the catalog by group; a report runs from the address and its parameters round-trip through it; columns sort; money shown from strings; Export requests the workbook with the same parameters; a drill-down links to the right search.

### Task 9: Performance guard and docs

**Files:** `backend/tests/test_reports_performance.py`; `docs/system-administration.md` (a Reports section); `README.md` layout.

**Tests:** every registered report runs against the test database in under a second.

## Phase 2 -- the rest of the catalog

One task per group, each adding its reports to the module Phase 1 created, with tests of the same kind: `dq_photos`, `dq_derived`, `dq_purchases`, `dq_locations`; `cb_designs`, `cb_notes`, `cb_grades`, `cb_metal`, `cb_attributes`; `pr_spend`, `pr_sources`, `pr_received`; `sl_sales`, `sl_fulfilment`, `sl_aging`, `sl_auctions`; `mn_basis`, `mn_tax`, `mn_value`. The console needs no change per report: it renders any catalog entry.

Before `sl_sales` and `mn_value` are built, the owner answers the spec's open questions 2 and 3, or the defaults stand, labelled.
