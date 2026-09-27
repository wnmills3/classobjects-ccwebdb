# Reports

2026-09-27 -- designed, not built.

A **Reports** page in the console answers the questions the owner asks of
the collection as a whole: what is missing or wrong in the record, what the
collection is made of, what has been bought and not yet arrived, what is on
offer and what has sold, and what it all cost. Today those answers are
scattered -- the inventory search's issue counts and facets, Receiving's "not
yet arrived" list, the selling pages, and the report each command-line pass
prints -- or not available at all: there is no summary endpoint, no
dashboard, and no way to hand a result to a spreadsheet except the whole-
database workbook backup.

Reports read; they never write. Fixing what a report finds is done where it
is done today -- the item editor, a bulk edit, Receiving, a pass -- and every
report row that names items links there.

## What exists, and is reused

| Existing | Reused as |
|---|---|
| `app/issues.py` -- named checks per kind (`no_year`, `no_denomination`, `zero_cost`, `unreviewed`, `malformed_serial`, `near_duplicate_serial`, ...), each a SQL predicate with a description | The data-quality reports count and list by these same checks; a new check is still added there, once, and appears in the search, its badges and the reports alike. |
| `app/inventory_search.py` -- `COIN_VIEW` / `CURRENCY_VIEW`, `count_facets`, `count_issues`, the live-row rule, allowlisted filters | Breakdowns group by the same facets; every drill-down is an inventory search URL built from the same filter names. |
| `routers/acquisitions.py` -- outstanding = `ordered` or `missing`, `_ITEM_IS_LIVE` | The receiving reports use the same definition, so a report and Receiving never disagree about what is outstanding. |
| `sales_order_item_share` (amount, fee per item), `sales_order_fee`, `listing`, `auction_lot` | The selling reports read these; the per-item share is what makes a sale's cost basis and gain computable per item. |
| `metal_price`, `item_valuation` view (melt, profit) | Melt valuation, when it is built, reads the latest spot the way the view does. |
| `app/workbook_backup.py` conventions -- NULL as an empty cell, ISO timestamps, column widths | Each report exports to a workbook the same way. |

Measured on the live collection (8,051 live items, 3,711 purchases), the
aggregates these reports need take 3-9 ms each on the base tables
(breakdown by kind and denomination, outstanding by purchase, spend by month
and vendor, a completeness matrix, items without a photograph, unreviewed
derived fields). Reports are therefore computed on request, from the base
tables, with no cache, snapshot table or materialized view.

## The catalog

Each report has an id, a group, a title, a one-line purpose, its parameters,
its columns, a totals row where totals mean something, and -- where its rows
name items -- a drill-down. The first release is marked **v1**; the rest
follow in the plan's later phases.

### Data quality -- what is missing or wrong

| Id | Report | Rows | Drill-down |
|---|---|---|---|
| `dq_issues` **v1** | Open issues | One per issue check per view (coins, currency): count, description | Inventory search `?issue=<check>` |
| `dq_completeness` **v1** | Field completeness | One per kind: live items, then % filled for each field that applies to that kind -- year (series year for notes), denomination, grade, country, series, metal (not notes), photograph, storage location, listing link, seller's item id | Inventory search for that kind with the field empty (needs the `missing=<field>` filter, below) |
| `dq_photos` | Photographs | Items without a photograph, by kind and status; unfiled photographs | Inventory search `?missing=photo`; Photos page |
| `dq_derived` | Filled by a rule, not yet confirmed | One per field and rule (`item_field_source` rows with no `item_field_review`) | Inventory search `?issue=unreviewed` narrowed to the field |
| `dq_purchases` | Purchases with gaps | Purchases with a generated number (`Order-0001`), no date, no web address, a zero-cost item, or no items | The purchase page |
| `dq_locations` | Where items are | Items by storage location, including "none recorded" (optional, so not an error) -- measured: every live item today | Inventory search by location |

### Collection -- what it is made of

| Id | Report | Rows |
|---|---|---|
| `cb_holdings` **v1** | Holdings | Kind x denomination: items, pieces, total cost; totals per kind and overall. Parameters: status (default held and received), disposition. |
| `cb_designs` | Coins by design | Design series (Morgan dollar, Winged Liberty Head dime, ...): items, year span held, total cost; "no series" as its own row |
| `cb_notes` | Notes | Note class x series year (and letter): items, seal colours present, Reserve Banks present, total cost; star notes and fancy serials counted from attributes |
| `cb_grades` | Grades | Grade band (1-49, 50-59, 60-64, 65-70, ungraded) x strike type x grading service (raw counted separately): items, total cost |
| `cb_metal` | Precious metal | Metal x form (coin, bar, round): items, fine troy ounces, total cost; melt value at the latest recorded spot price when one is recorded |
| `cb_attributes` | Attributes and errors | Each attribute and error type: items carrying it |

Every breakdown row drills down to the inventory search filtered to that
row's values.

### Purchasing and receiving -- what is coming

| Id | Report | Rows |
|---|---|---|
| `pr_outstanding` **v1** | Not yet arrived | One per purchase with an item `ordered` or `missing`: vendor, seller, order date, days waiting, items outstanding / total, their cost; oldest first. Parameter: overdue after N days (default 21), which marks rows. Drill: Receiving `?order=<id>`. |
| `pr_spend` | Spending | Month (or quarter, year) x vendor: purchases, items, item cost, shipping, sales tax, total. Parameter: date range. |
| `pr_sources` | Vendors and sellers | One per vendor, and per seller within a marketplace: purchases, items, total spent, first and last purchase |
| `pr_received` | Received | Items received in a date range, from `item_status_history` (`arrived_on`), by day and vendor |

### Selling -- what is on offer and what has sold

| Id | Report | Rows |
|---|---|---|
| `sl_offered` **v1** | On offer | Active and paused listings (items and sales lots) by venue: asking price, cost basis, days listed; totals of asking and cost. Drill: Listings. |
| `sl_sales` | Sales | Sales orders in a date range, by month and venue: gross, fees, net, cost basis of what sold, gain -- per item from `sales_order_item_share`. |
| `sl_fulfilment` | To ship | Sales orders placed and not shipped or delivered, oldest first |
| `sl_aging` | Held and not offered | Items received and held, not in any active listing or open lot, by months since received |
| `sl_auctions` | Auctions | Auctions by status; for settled ones, lots sold / unsold, hammer total, fees |

### Money -- what it cost and what it is worth

| Id | Report | Rows |
|---|---|---|
| `mn_basis` | Cost basis | Total cost by status (ordered, received, ...) and disposition (held, sold, ...): the money in the collection, and the money it has returned |
| `mn_tax` | Sales tax paid | Purchase sales tax by month or year and vendor -- the figure a tax return asks for |
| `mn_value` | Recorded value | Items with a numismatic value: value against cost by kind; items without one counted |

## How it works

### Backend: `app/reports/`

- **One registry.** `app/reports/__init__.py` holds `REPORTS`, keyed by id, as
  `issues.py` holds its checks: adding a report is adding one entry. Each entry
  is a `Report`: `id`, `group`, `title`, `purpose`, `params` (a pydantic model
  of the parameters it takes, with their defaults), and `run(db, params) ->
  ReportResult`.
- **One result shape.** `ReportResult` has `columns` (key, label, type: text,
  count, money, percent, date, ounces), `rows` (dicts keyed by column), an
  optional `totals` row, an optional `drill` per row (a console path with its
  query), and `notes` -- anything the reader must know to read it right, such
  as "no spot price recorded; melt value omitted".
- **Grouped by module**: `data_quality.py`, `collection.py`,
  `purchasing.py`, `selling.py`, `money.py`, each defining its reports and
  registering them.
- **The live-row rule, once.** Every report reads live items only -- no
  `deleted_at`, no `split_at` -- through one shared predicate, the one
  `_ITEM_IS_LIVE` and the search views already use (moved to a shared module
  when the first report needs it, not copied).
- **Queries** are SQLAlchemy Core on the base tables, grouped by foreign-key
  id with labels joined after, as `count_facets` does. Parameters are bound;
  column names come from allowlists, never from a request.
- **Money** is `Decimal`, summed in SQL, sent as decimal strings, as
  everywhere else.

### API

- `GET /api/reports` -- the catalog: id, group, title, purpose, and each
  parameter's name, type, default and choices.
- `GET /api/reports/{id}?<params>` -- the result as JSON. 404 for an unknown
  report, 422 for a bad parameter.
- `GET /api/reports/{id}/workbook?<params>` -- the same result as an `.xlsx`
  download: one sheet, the report's title and parameters above the table,
  totals below, money as numbers formatted to cents, NULL as an empty cell.
- Manager only (`AdminUser`), like everything that shows cost, value or
  location. Nothing here reaches the shop.

### Command line

`python -m app.reports list`, `python -m app.reports run <id> [--param
value ...] [--workbook FILE]` -- the same registry, for a report wanted from a
script or before the console is open. Read-only, so no `--commit`.

### Console: the Reports page

- A **Reports** item in the console menu, before Vocabularies.
- The page lists the catalog by group; choosing a report shows its
  parameters (as the inventory filter panel does), its table with the totals
  row, its notes, and **Export workbook**.
- The report and its parameters are kept in the address (`/reports?report=
  pr_outstanding&overdue_days=30`), so a report can be bookmarked or reopened.
- Columns sort in the browser (a report is at most a few thousand rows).
  Money is shown with `money()` from the decimal string, never parsed into a
  float.
- A row with a drill-down links to the page that fixes or shows it, in the
  same tab.
- The help band explains each parameter, as every console form does.

### What the inventory search needs

Drill-downs reuse the search's filters. One is missing: **`missing=<field>`**
-- items whose field is empty (year, denomination, grade, country, series,
metal, photo, storage location, listing link, seller's item id), kind-aware
as the completeness report is. It is added to `inventory_search.py` with the
completeness report, and is useful in the search on its own.

## Rules

- **Read-only.** No report writes, and none offers a button that does.
  Fixing is done where it is done today.
- **Admin only**, as above.
- **No proprietary numbering or prices.** Reports show the owner's own
  records. A catalog's numbering (Friedberg, Pick) is shown only as the owner
  recorded it on an item, and no report derives, seeds or exports a price
  guide's values. A valuation source under licence stays out of exports.
- **Kind-aware.** A field that does not apply to a kind (a note's metal, a
  coin's seal) is not "missing" for it; a note's year is its series year.
- **Live rows only**, as above.
- **Current state.** Reports answer from the database as it is. A report on
  what the collection looked like at a past date needs history this design
  does not add.

## Not in this design

- Charts. Tables and totals first; a chart can be added to a report later
  without changing its shape.
- Scheduled or emailed reports, dashboards on the landing page, and a
  free-form query builder.
- A tax-return form of realized gain: `sl_sales` shows gain per item (see
  Decisions); how fees and shipping are presented on a return is outside it.
- Snapshots over time (the collection's value by month).

## Decisions

| Question | Decision |
|---|---|
| When is an outstanding purchase overdue? | After **21 days** from its order date -- `pr_outstanding`'s default, still a parameter. |
| How is a sale's gain worked out? | **Per item** (specific identification): each sold item's share of the sale (`sales_order_item_share`) less that item's own cost basis. `sl_sales` uses this; no other method is offered. |
| What grade bands does `cb_grades` use? | **Numbers**, not names: 1-49, 50-59, 60-64, 65-70, and ungraded. The strike type (MS, PR) and the grading service are separate columns, not folded into the band. |
| Which reports come first? | The five marked **v1**. |
| Storage locations | **Optional.** A location can be chosen when an item is entered -- on its own or on a purchase -- and changed at any time afterwards, each change kept in its location history. Today only Receiving sets one (New item and the item editor do not), and no live item has one; `dq_locations` reports "none recorded" as a row, not as an error. |

## Testing

- Each report's `run` is tested against built data with a known answer,
  including a deleted and a split item that must not count, and an item of a
  kind a field does not apply to.
- The API: catalog shape, a parameter default, a 422, a 404, manager only.
- The workbook: a report round-trips -- the sheet's rows equal the JSON's.
- The console: the page lists the catalog, runs a report from the address,
  sorts, exports, and drills down.
- Performance: a test that runs every report against the test database and
  fails if any takes over a second -- a guard, not a benchmark.
