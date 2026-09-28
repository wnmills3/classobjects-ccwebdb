# Reports

2026-09-28 -- **built**: the five reports marked v1, the API, the command
line, the console page and printing. The rest of the catalog below is
designed, not built.

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
| `app/inventory_search.py` -- `COIN_VIEW` / `CURRENCY_VIEW`, `count_facets`, `count_issues`, the live-row rule stated as SQL text, allowlisted filters | Breakdowns group by the same facets; every drill-down is an inventory search URL built from the same filter names. |
| `app/live.py` -- `live_item()`, `OUTSTANDING_STATUSES` (`ordered`, `missing`), also read by `routers/acquisitions.py` | Every report reads `live_item()`, and `pr_outstanding` reads both names, so a report and Receiving never disagree about what is live or outstanding. |
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
| `cb_holdings` **v1** | Holdings | Kind x denomination: items, pieces, total cost; a subtotal row (`All <kind>`) after each kind's denominations, and an overall total. Parameters: `status` (default `received`) and `disposition` (default `held`), each one code or `all`. |
| `cb_designs` | Coins by design | Design series (Morgan dollar, Winged Liberty Head dime, ...): items, year span held, total cost; "no series" as its own row |
| `cb_notes` | Notes | Note class x series year (and letter): items, seal colours present, Reserve Banks present, total cost; star notes and fancy serials counted from attributes |
| `cb_grades` | Grades | Grade band (1-49, 50-59, 60-64, 65-70, ungraded) x strike type x grading service (raw counted separately): items, total cost |
| `cb_metal` | Precious metal | Metal x form (coin, bar, round): items, fine troy ounces, total cost; melt value at the latest recorded spot price when one is recorded |
| `cb_attributes` | Attributes and errors | Each attribute and error type: items carrying it |

Every breakdown row drills down to the inventory search filtered to that
row's values; `cb_holdings`'s "No denomination" row drills to
`missing=denomination` where the kind can carry one, and has no drill where it
cannot (bullion, for instance). A subtotal row's own drill is its kind alone,
with no denomination named.

### Purchasing and receiving -- what is coming

| Id | Report | Rows |
|---|---|---|
| `pr_outstanding` **v1** | Not yet arrived | One per purchase with an item `ordered` or `missing`: vendor, seller, order date, days waiting, items outstanding / total, their cost, and an `Overdue` text column holding the word "Overdue" (empty otherwise); oldest first, undated purchases last. Parameter: `overdue_days` (default 21) -- a row is marked once its days waiting is greater than this. Drill: Receiving `?order=<id>`. |
| `pr_spend` | Spending | Month (or quarter, year) x vendor: purchases, items, item cost, shipping, sales tax, total. Parameter: date range. |
| `pr_sources` | Vendors and sellers | One per vendor, and per seller within a marketplace: purchases, items, total spent, first and last purchase |
| `pr_received` | Received | Items received in a date range, from `item_status_history` (`arrived_on`), by day and vendor |

### Selling -- what is on offer and what has sold

| Id | Report | Rows |
|---|---|---|
| `sl_offered` **v1** | On offer | Active and paused listings (items and sales lots) by venue, oldest listed first: asking price, its currency, cost basis, days listed; totals of asking (USD only) and cost basis. A coin offered elsewhere or in a lot keeps its paused store-listing row, status `Paused for <venue> listing`, but that row is left out of both totals -- the coin is counted once, on the listing that superseded it, not twice. A listing not priced in USD is counted in the cost-basis total but left out of the asking total, noted by count. Drill: an item listing opens the item's editor (`?item=CC-######`); a lot listing opens `/lots`. |
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

- **One registry.** `app/reports/registry.py` holds `REPORTS`, keyed by id, as
  `issues.py` holds its checks: adding a report is adding one `register()`
  call in a group module. `app/reports/__init__.py` imports every group
  module, so `REPORTS` is complete the moment anything imports `app.reports`
  itself. Each entry is a `Report`: `id`, `group`, `title`, `purpose`, `params`
  (a pydantic model of the parameters it takes, with their defaults), and
  `run(db, params) -> ReportResult`.
- **One result shape.** `ReportResult` has `columns` (key, label, and
  `kind`: text, count, money, percent, date, ounces -- sent as `kind` on
  the wire, where a parameter's own sort is `type`), `rows` (dicts keyed by
  column), an optional `totals` row, `drills` -- a console path (with its
  query) per row, or `None` where that row has no drill-down,
  index-aligned with `rows` and checked to be so -- `link_column`, the key
  of the column whose cell carries a row's link (`None` for the first
  column; checked to name a column; `sl_offered` links on `offers`,
  `dq_issues` on `check`), and `notes`: anything the reader must know to
  read it right, such as "no spot price recorded; melt value omitted".
- **Grouped by module**: `data_quality.py`, `collection.py`,
  `purchasing.py`, `selling.py`, and (not yet built) `money.py`, each
  defining its reports and registering them.
- **The live-row rule.** Every report reads live items only -- no
  `deleted_at`, no `split_at` -- through one shared predicate, `live_item()`
  in `app/live.py`. The search views are SQL text, so they state the same
  rule as text (`i.split_at IS NULL` in each view's `where`, and the
  default `DELETED_MODES["no"]`); `tests/test_live.py` pins the two as
  selecting the same rows.
- **Queries** are SQLAlchemy Core on the base tables, grouped by a
  foreign-key id or code together with its label. Parameters are bound;
  column names come from allowlists, never from a request.
- **Money** is `Decimal`, summed in SQL, sent as decimal strings, as
  everywhere else.

### API

- `GET /api/reports` -- the catalog: a list of `{id, group, title, purpose,
  params}`, each parameter carrying `name`, `label` (the pydantic field's own
  `title`), `type` (`choice`, `integer` or `text`), `default` and `choices`
  (a `Literal` parameter's own values, else `null`).
- `GET /api/reports/{id}?<params>` -- the result as JSON:
  `{id, group, title, params (resolved), run_at (ISO, local), columns, rows,
  totals, drills, notes}`. A `Decimal` crosses as the string it prints, a
  `date` as its ISO text. 404 for an unknown report; 422 for an unknown
  parameter name or a value its params model rejects -- validated by that
  model itself, the one thing `resolve_params` shares with the command line,
  so a report is validated identically from either.
- `GET /api/reports/{id}/workbook?<params>` -- the same result as an `.xlsx`
  download, named `<id>_<YYYY-MM-DD>.xlsx` (the run date): one sheet, the
  report's title, one row per parameter (label and the value it ran with), a
  "Run at" row, a blank row, then the header row, the data rows, the totals row directly
  below (bold) when there is one, a blank row, then each note -- money and
  percent cells written as numbers, NULL as an empty cell, the header row
  frozen. One writer (`app/reports/workbook.py`), used by the API and the
  command line alike.
- Manager only (`AdminUser`), like everything that shows cost, value or
  location. Nothing here reaches the shop.

### Command line

```cmd
cd backend
python -m app.reports list
python -m app.reports run <id> [--param name=value ...] [--workbook FILE]
```

The same registry, for a report wanted from a script or before the console is
open. `list` prints every report's id, group and title; `run` prints it as a
plain-text table (title, parameters, header, rows, totals, notes) and
`--param` (repeatable) sets its parameters. An unknown report id or a bad
`--param` is reported on stderr and exits 2. Read-only, so no `--commit`.

### Console: the Reports page

- A **Reports** item in the console menu, before Vocabularies.
- The page lists the catalog by group; choosing a report shows its
  parameters (as the inventory filter panel does), its table with the totals
  row, its notes, and **Export workbook**.
- The report and its parameters are kept in the address (`/reports?report=
  pr_outstanding&overdue_days=30`), so a report can be bookmarked or reopened;
  Run writes only the parameters that differ from their defaults. Leaving a
  parameter field empty is refused (`Enter a value for <label>.`), rather
  than silently running that parameter's default.
- Columns sort in the browser (a report is at most a few thousand rows), a
  header click sorting ascending, then descending, then back to the report's
  own order. Money is shown with `money()` from the decimal string, never
  parsed into a float.
- A row with a drill-down links to the page that fixes or shows it, in the
  same tab. A row standing for exactly one item, rather than a count of many,
  links straight to that item's editor: `/inventory/<coins|currency>?item=
  CC-######`. The inventory page reads `item` from its own address and opens
  that item's editor on load (not only from a click in its table), then
  drops `item` from the address again when the editor closes, replacing that
  history entry rather than pushing a new one, so Back does not reopen it.
  `dq_completeness`'s percent cells link one step further: the row's own
  drill with `missing=<key>` added, where a column's `key` is exactly the
  `missing=` field name it counts. A row's link sits on the cell of its
  result's `link_column`.
- The inventory page shows an active `missing=` as a chip in its filter
  panel ("Missing: photograph"), since the filter has no control of its
  own; clicking the chip removes it from the address and searches again.
  The field names and their labels live in one frontend module,
  `pages/inventory/missingFields.js`.
- The help band explains each parameter, as every console form does.
- **Print** sits beside Export workbook; see Printing.

### Printing

Every report prints on paper as well as showing on the screen, from the
same page, with no report-specific code:

- **Print** opens the browser's print dialog (`window.print()`) for the
  report as shown -- the same rows, sort and parameters.
- A print stylesheet (`@media print`) prints the report alone: the
  console menu, the help band, the parameter form and the buttons are
  hidden.
- A heading printed only on paper names the report, its parameters in
  words, one per line ("Overdue after (days): 21"), when it was run, and its
  row count.
- The table's header row repeats on every page (`thead` as a table
  header group), a row is never split across pages, and the totals row
  and the report's notes follow the last row.
- A report wider than a portrait page is marked so the page prints
  landscape (`@page` size set by a class on the report).
- Black on white: nothing is carried by colour alone (an overdue row is
  marked in words, in its own Overdue cell); money is right-aligned; a drill-down
  link prints as its plain text.
- Page numbers and the date come from the browser's own print header and
  footer.


### What the inventory search needs

Drill-downs reuse the search's filters, plus one the search did not already
have: **`missing=<field>`** -- items whose field is empty, kind-aware as the
completeness report is (`inventory_search.MISSING_FIELDS`, one shared
predicate per field). Its field names are exactly `dq_completeness`'s own
percent-column keys -- year, denomination, grade, country, series, metal,
photo, storage_location, listing_link, sellers_item_id -- so a report cell's
count and its drill-down search's count are the same predicate, not two that
happen to agree today. It is useful in the search on its own.

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
| Printed as well as shown? | **Yes**: every report prints (see Printing). |
| Storage locations | **Optional.** A location is chosen on New item, in Receiving, or in the item editor, and changed whenever the piece moves; every move is kept in its location history. `dq_locations` reports "none recorded" as a row, not as an error. |

## Testing

- Each report's `run` is tested against built data with a known answer,
  including a deleted and a split item that must not count, and an item of a
  kind a field does not apply to.
- The API: catalog shape, a parameter default, a 422, a 404, manager only.
- The workbook: a report round-trips -- the sheet's rows equal the JSON's.
- The console: the page lists the catalog, runs a report from the address,
  sorts, exports, prints (the Print button calls the browser's print; the
  print-only heading carries the title, parameters and run time), and drills
  down.
- Performance: a test that runs every report against the test database and
  fails if any takes over a second -- a guard, not a benchmark.
