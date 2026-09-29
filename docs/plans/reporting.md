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

**Also:** a Print button and the print stylesheet (`@media print` in `management/styles.css`): report alone, print-only heading (title, parameters in words, run time, row count), repeating header row, rows unsplit, landscape for a wide report, no meaning in colour alone -- spec, Printing.

**Tests:** the catalog by group; a report runs from the address and its parameters round-trip through it; columns sort; money shown from strings; Export requests the workbook with the same parameters; Print calls `window.print`; the print-only heading shows the title, parameters in words and run time; an overdue row is marked in words; a drill-down links to the right search.

### Task 9: Performance guard and docs

**Files:** `backend/tests/test_reports_performance.py`; `docs/system-administration.md` (a Reports section); `README.md` layout.

**Tests:** every registered report runs against the test database in under a second.

## Phase 2 -- the rest of the catalog

One task per group, each adding its reports to the module Phase 1 created, with tests of the same kind: `dq_photos`, `dq_derived`, `dq_purchases`, `dq_locations`; `cb_designs`, `cb_notes`, `cb_grades`, `cb_metal`, `cb_attributes`; `pr_spend`, `pr_sources`, `pr_received`; `sl_sales`, `sl_fulfilment`, `sl_aging`, `sl_auctions`; `mn_basis`, `mn_tax`, `mn_value`. The console needs no change per report: it renders any catalog entry -- except date parameters, which Task 10 adds once for every report that takes them.

The spec's Decisions settle the parameters these use: overdue after 21 days, gain per item, numeric grade bands.

**Every Phase 2 report follows Phase 1's pattern** (read `app/reports/collection.py` and `selling.py` first): a titled pydantic params model (forbidding extra keys through `resolve_params`); SQLAlchemy Core over the base tables; live rows through `app/reports/tables.py` (`ITEM`, `KIND`, `LIVE`) or `live_item()`; money summed in SQL, `Decimal` throughout; drills as console paths built with `urlencode`, one per row, `None` where no page shows exactly that row; `link_column` set when the first column is not the one to click; `view_path()` and `MissingField.applies_to()` from `inventory_search`, never re-derived; a one-item row opens `/inventory/<view>?item=<code>`; registration in `registry.py` and an import in `app/reports/__init__.py`; the performance guard covers each new report automatically. Every drill that is a search is tested to return the row's count, and every total equals the sum of its rows to the cent.

### Task 10: Date parameters

**Files:** `backend/app/reports/serialize.py` (catalog type `"date"` for a `datetime.date` field), `backend/app/reports/params.py` (ISO date strings validated by the model), a shared `DateRange` params base in `backend/app/reports/base.py` (`date_from`, `date_to`, both optional, titled "From" and "To", refusing `date_from > date_to` with a 422), `frontend/src/management/pages/reports/ReportForm.jsx` (a date input for type `date`; an empty date field means "no bound", not the refusal Phase 1 applies to other emptied fields), `fieldHelp.js` (`report_date_from`, `report_date_to`), the print heading (a date parameter printed as a date).

**Tests:** catalog shape; a date round-trips through the API and the address; `date_from > date_to` refused; the form offers a date input and sends an empty one as no bound.

### Task 11: Data quality -- `dq_photos`, `dq_derived`, `dq_purchases`, `dq_locations`

**Files:** `backend/app/reports/data_quality.py`; tests in `test_reports_data_quality.py`.

- `dq_photos` "Photographs": rows = kind x status of live items with no photograph (`missing=photo` rule), items; drill = the kind's search with `status` and `missing=photo`. A final row "Unfiled photographs" counts images linked to no item (`image` with no `item_image`), drill `/photos`.
- `dq_derived` "Filled by a rule, not yet confirmed": rows = field x rule of `item_field_source` rows with no matching `item_field_review` on a live item, items; drill = the view's `issue=unreviewed` search when the field is one the search can narrow to, else None. Read `app/field_sources.py` and the `unreviewed` check in `app/issues.py` for the exact pairing of source and review.
- `dq_purchases` "Purchases with gaps": one row per purchase with at least one gap -- generated number (`Order-NNNN`), no order date, no web address (`source_url` empty), an order date later than the purchase record was created or more than a year before it (the slip that hid PO 3974), a live item with zero item cost, or no live items; columns: purchase number (`#id`), order number, vendor, ordered, gaps (the gaps in words, comma-separated); drill `/receiving?order=<id>`; link column = the purchase number.
- `dq_locations` "Where items are": live items by storage location (its label as the console's LocationSelect shows it), "None recorded" as its own row, last; items, total cost; drill: the search has no location filter, so None (add none).

### Task 12: Collection -- `cb_designs`, `cb_notes`, `cb_grades`, `cb_metal`, `cb_attributes`

**Files:** `backend/app/reports/collection.py`; tests in `test_reports_collection.py`. Each takes Holdings' `status`/`disposition` params (reuse the Literal types).

- `cb_designs` "Coins by design": design series (non-currency kinds) with items, year span held (min-max `year_start`, text "1878-1904"), total cost; "No series" last; drill = coin search `series=<code>`, "No series" -> `missing=series`.
- `cb_notes` "Notes": note type x series designation (e.g. "1935A"), items, seal colors present (labels, comma-separated), Federal Reserve districts present (letters), star notes and fancy serials (counts from the attributes whose codes mean them -- find them in the attribute vocabulary; if none exists, omit the column and add a note), total cost; drill = currency search `note_type` + `series_designation`.
- `cb_grades` "Grades": band (1-49, 50-59, 60-64, 65-70, Ungraded -- numeric grade value) x strike type x grading service ("Raw" when none), items, total cost; drill = the view's search with `grade_min`/`grade_max` plus `strike_type` and `grading_service` where the search accepts them, else None -- check how `_grade_clause` reads grade terms before building one.
- `cb_metal` "Precious metal": metal x form (coin, or the bullion form's label) over items with a fine weight: items, fine troy ounces (ounces, SQL sum of `fine_weight_ozt`), total cost, melt value (money: ounces x the latest `metal_price` for that metal) -- when a metal has no recorded price the cell is empty and a note says so; totals of cost and melt over the priced rows only, with a note.
- `cb_attributes` "Attributes and errors": each attribute (`item_attribute_link`) and each error type (`item_error`) carried by live items: kind of mark ("Attribute"/"Error"), label, items; drill = the search's `attribute=` / `error_type=` filter.

### Task 13: Purchasing -- `pr_spend`, `pr_sources`, `pr_received`

**Files:** `backend/app/reports/purchasing.py`; tests in `test_reports_purchasing.py`.

- `pr_spend` "Spending": params `DateRange` + `period` ("month" default, "quarter", "year"); rows = period x vendor over purchases whose order date is in range: purchases, items (live), item cost, shipping, sales tax, total (money, SQL sums from the live items); undated purchases are left out with a note naming how many; per-period subtotal rows as Holdings does; totals row.
- `pr_sources` "Vendors and sellers": one row per vendor, then per seller within that vendor where purchases name one: purchases, items, total spent, first and last order date; vendor rows carry the vendor's full figures, seller rows indented by name ("  deswin3834"); totals over vendor rows only.
- `pr_received` "Received": params `DateRange`; rows = arrival day x vendor from `item_status_history` rows whose `to` status is `received`, dated by `arrived_on` (falling back to the `changed_at` date when `arrived_on` is empty): items, total cost; counts only live items; drill None.

### Task 14: Selling -- `sl_sales`, `sl_fulfilment`, `sl_aging`, `sl_auctions`

**Files:** `backend/app/reports/selling.py`; tests in `test_reports_selling.py`.

- `sl_sales` "Sales": params `DateRange`; rows = month x venue of sales orders placed in range (read the sales-order statuses: a canceled order is left out): orders, gross, fees, net, cost basis of the items sold, gain (net less basis); per item from `sales_order_item_share` (spec Decisions: specific identification); totals.
- `sl_fulfilment` "To ship": one row per sales order placed and not shipped or delivered, oldest first: order number, placed, customer, items, amount, days waiting; drill `/sales`.
- `sl_aging` "Held and not offered": live items received and held, not on any active or paused listing nor in an open lot: rows by months since received (0-5, 6-11, 12-23, 24+, "Unknown" when no arrival is recorded) x kind: items, total cost; drill None.
- `sl_auctions` "Auctions": one row per auction by status; for a settled one: lots, sold, unsold, hammer total, fees; drill `/auctions`.

### Task 15: Money -- `mn_basis`, `mn_tax`, `mn_value`

**Files:** `backend/app/reports/money.py` (new group "Money", imported in `app/reports/__init__.py`); `backend/tests/test_reports_money.py`.

- `mn_basis` "Cost basis": status x disposition of live items: items, total cost; totals.
- `mn_tax` "Sales tax paid": params `DateRange` + `period` ("month" default, "year"); period x vendor: purchases, sales tax (SQL sum of live items' `sales_tax`); undated purchases left out with a note; totals.
- `mn_value` "Recorded value": per kind: items, items with a numismatic value, their cost, their numismatic value, difference; items without one counted; totals. No price-guide value is derived or exported: only the owner's own `numismatic_value`.

### Task 16: Docs

**Files:** `docs/specs/reporting-design.md` (header: every catalog report built; each Phase 2 decision above folded in as current state), `docs/system-administration.md` (the Reports section lists every report), `README.md` if the layout changed.
