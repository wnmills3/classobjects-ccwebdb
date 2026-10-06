# Reports

*2026-09-29.* Reports answer the questions the owner asks of the collection
as a whole: what is missing or wrong in the record, what the collection is
made of, what has been bought and not yet arrived, what is on offer and what
has sold, and what it all cost. They are for the owner and managers only,
on the management console's **Reports** page (`/management/reports`), and
the same 26 reports run from the command line and export to a workbook or
print on paper.

Reports read; they never write. Fixing what a report finds is done where it
is always done -- the item editor, a bulk edit, Receiving, a pass -- and
every report row that names items links there.

## What reports are built on

| Source | Used by reports as |
|---|---|
| `app/issues.py` -- named checks per kind (`no_year`, `no_denomination`, `zero_cost`, `unreviewed`, `malformed_serial`, `near_duplicate_serial`, ...), each a SQL predicate with a description | `dq_issues` counts and lists by these same checks; a new check is added there, once, and appears in the search, its badges and the reports alike. |
| `app/inventory_search.py` -- `COIN_VIEW` / `CURRENCY_VIEW`, `count_issues`, `MISSING_FIELDS`, the live-row rule stated as SQL text, allowlisted filters | Breakdowns group by the same facets; every drill-down is an inventory search URL built from the same filter names; `dq_completeness` and `dq_photos` reuse `MISSING_FIELDS[key].sql` verbatim. |
| `app/live.py` -- `live_item()`, `OUTSTANDING_STATUSES` (`ordered`, `missing`), also read by `routers/acquisitions.py` | Every report reads `live_item()`, and `pr_outstanding` reads both names, so a report and Receiving never disagree about what is live or outstanding. |
| `sales_order_item_share` (amount, fee per item), `sales_order_fee`, `listing`, `auction_lot` | `sl_sales`, `sl_offered` and `sl_auctions` read these; the per-item share is what makes a sale's cost basis and gain computable per item. |
| `metal_price` | `cb_metal`'s melt value reads the latest quoted `price_per_ozt` per metal. |
| `app/workbook_backup.py` conventions -- NULL as an empty cell, ISO timestamps, column widths | Each report exports to a workbook the same way. |

The aggregates these reports need take milliseconds on the base tables
(`test_reports_performance.py` fails any report over a second). Reports
are therefore computed on request, from the base tables, with no cache,
snapshot table or materialized view.

## The catalog

Each report has an id, a group, a title, a one-line purpose, its parameters
(with their defaults), its columns, a totals row where a total means
something, and -- where its rows name items -- a drill-down. Groups are
listed in the order the console shows them -- `GROUP_ORDER` in
`app/reports/registry.py` states it, and within a group reports keep the
order they are registered in.

### Collection -- what it is made of

Every Collection report takes the same two parameters -- `status` (default
`received`) and `disposition` (default `held`), each one code from the
seeded vocabulary or `all` -- so "the collection" reads, by default, as what
the owner has actually taken in and still has, not everything ever ordered
or ever sold.

| Id | Report | Rows, drill and totals |
|---|---|---|
| `cb_holdings` | Holdings | Kind x denomination: items, pieces, total cost; a subtotal row (`All <kind>`) after each kind's denominations, and an overall total. A row drills to that kind's search, narrowed to the denomination (or `missing=denomination`, where the kind can carry one and none is recorded); a subtotal row drills to the kind alone. |
| `cb_designs` | Coins by design | Design series held, across every non-currency kind (currency's own design identity is the Friedberg number, not a series, so it is excluded rather than folded into "No series"): items, year span, total cost, with an overall total. Rows follow the series vocabulary's own order (then label), "No series" last. A row drills to the coin search narrowed to that series (or `missing=series`). |
| `cb_notes` | Notes | Note type x series designation: items, seal colors present, Federal Reserve districts present, total cost, with an overall total; star notes and fancy serials counted from the attribute codes that mean them, when the vocabulary carries both (a note explains it when it does not). A row drills to the currency search narrowed to that note type and series designation, only when both are known. |
| `cb_grades` | Grades | View (Coins or Currency) x grade band (1-49, 50-59, 60-64, 65-70, or "No numeric grade") x strike type x grading service ("Raw" when none): items, total cost, with an overall total. A row's drill is decided from its own group-by key, never by running a search at report time: it is kept only when no other row would be swept into the same search, and omitted for a band with no numeric grade, since no search term states that band. |
| `cb_metal` | Precious metal | Metal x form (Coin, a bullion form, or the kind's own label), over items with a recorded fine weight: items, fine troy ounces, total cost, and melt value at the latest recorded spot price. A metal with no recorded price contributes to items, ounces and cost but not melt; the melt total is empty (not zero) when nothing is priced, and a note names every metal left out. A row's drill, like `cb_grades`, is decided structurally, not by a run-time search; currency and a bullion row with no form are never drilled. |
| `cb_attributes` | Attributes and errors | Every attribute and error type a live item carries, split by the view it was recorded on (an attribute or error type that applies to either kind is two rows, one per view): items; attributes first, then errors, each in its vocabulary's own order (then label). No total: an item can carry several at once. A row drills to that view's search narrowed to the attribute or error type. |

### Data quality -- what is missing or wrong

| Id | Report | Rows, drill and totals |
|---|---|---|
| `dq_issues` | Open issues | One row per named check per view (coins, then currency): its description and items. No total: an item can carry several issues at once. A row drills to `?issue=<check>` when it counted anything. |
| `dq_completeness` | Field completeness | One row per kind with at least one live item: live items, then percent filled for each field that applies to that kind -- year (every kind; a note's is its series year), denomination and grade (coins and notes only), country, series (not currency, whose year is its series year), metal (not currency or sets), photograph, storage location, listing link, seller's item id. A blank cell means the field does not apply to that kind, never 0% or 100%. A row drills to that kind's search; a cell drills one step further, to the same search with `missing=<key>` added, the console building that itself since a column's key is exactly the `missing=` field name. |
| `dq_photos` | Photographs | Live items with no photograph, by kind and status, then a final row for photographs filed against no item at all. A kind x status row drills to `?missing=photo` narrowed by status; the unfiled row drills to `/photos`. |
| `dq_derived` | Filled by a rule, not yet confirmed | Field x rule: `item_field_source` rows with no matching `item_field_review` for the same item and field (a field a person emptied on purpose, `HELD`, is excluded). No drills: this is a finer question than `issue=unreviewed` and the two counts can disagree in either direction, so no row claims to reproduce that search. |
| `dq_purchases` | Purchases with gaps | One row per purchase with at least one gap, newest first: order number, vendor, order date, and its gaps in a fixed order -- a generated placeholder number, no order date, no web address, an order date after the purchase's own entry date, an order date more than a year before entry, an item with zero cost, or no items at all (a purchase whose only items are deleted or split reads as "no items", not as dropped from the report). A row drills to `/receiving?order=<id>`. |
| `dq_locations` | Where items are | Live items by storage location, one row per location as the console's own location picker lists them -- two locations sharing a label are still two rows, disambiguated `<label> (#<id>)` -- plus a "None recorded" row, with items and total cost; an overall total. No drill: the inventory search has no filter for one specific location. |
| `dq_series_years` | Coins dated outside their series | One row per live coin whose year falls outside its design series' years -- a typo, a tribute piece or the wrong series: item, title, year (`1999-2009` for a range), series, and the series' years (`1986 on` for one still struck); by series, then year. Coins only (a note's year is its series year). Runs the same SQL text as `issue=year_outside_series`, so the report and the filter list the same coins. A row drills to `/inventory/coins?item_code=<code>`. |
| `dq_series_review` | Series to review | One row per live item `app.series_classify` leaves for a person, decided by that pass's own `classify` so the report and the pass's printed report cannot disagree: *description names a design the facts rule out* (a conflict), *series set, but the facts rule it out*, and *facts allow several designs* (a boundary year), in that order, then by the designs in play. Columns: item, why, designs (what the text names, the series set, or the designs allowed), denomination, year (a note's series year and letter), series, and the three texts the pass reads, each in its own column: title, description and rating -- a design named in only one of them is visible. `show` narrows to one kind. Notes count each kind. Nothing is written. A row drills to `/inventory/coins?item_code=<code>`, or `/inventory/currency` for a note. |

### Purchasing and receiving -- what has been bought and has not yet arrived

| Id | Report | Rows, drill and totals |
|---|---|---|
| `pr_outstanding` | Not yet arrived | One row per purchase carrying at least one live item still `ordered` or `missing`, oldest ordered first: vendor, seller, order date, days waiting, items outstanding and their cost, and an `Overdue` text column holding the word "Overdue" once days waiting exceeds the parameter (empty otherwise). Parameter: `overdue_days` (default 21). A row drills to `/receiving?order=<id>`; an overall total. |
| `pr_spend` | Spending | Period x vendor, over purchases with a live item (a purchase with none is not counted as a purchase at all, noted by name): purchases, items, item cost, shipping, sales tax, total, with a subtotal row per period (`All vendors`) and an overall total (`All periods`). Parameters: a date range (`date_from`/`date_to`, either or both empty meaning no bound on that side) filtering by the purchase's own order date, and `period` (`month`, default, `quarter` or `year`). No drills: a period x vendor cell has no single search page. A note counts purchases excluded for having no order date at all. |
| `pr_sources` | Vendors and sellers | One row per vendor with a counted purchase (same live-item rule as `pr_spend`), and beneath it one row per seller that vendor's purchases have named: purchases, items, total spent, first and last order date. Totals are over vendor rows only -- a seller row is a further breakdown of purchases its vendor row already counts, not more purchases. No drills. |
| `pr_received` | Received | Arrival day x vendor, from `item_status_history`'s own transitions *to* `received` (never the opening row a new item, a split child or a seed gets -- it is not an arrival, and a note says so): items and total cost; an item recorded with no purchase counts under the vendor "No purchase". A split parent's own receipt is attributed to its live children, on the parent's own day and vendor, since the pieces arrived with it. Parameter: a date range on the arrival day. No drills. An item received more than once counts once per receipt, noted. |

### Selling -- what is on offer and what has sold

| Id | Report | Rows, drill and totals |
|---|---|---|
| `sl_offered` | On offer | Active and paused listings -- items and sales lots -- by venue, then oldest listed first: listing, what it offers, status, asking price, currency, cost basis, days listed. A coin offered elsewhere or grouped into a lot keeps its paused store-listing row (status "Paused for `<venue>` listing"), but that row is left out of both totals so the coin counts once, on the listing that superseded it, not twice (noted by count); a listing not priced in USD is left out of the asking total only (also noted). Totals: asking (USD only) and cost basis. A row drills to the item's editor, or to `/lots` for a lot. |
| `sl_sales` | Sales | Month x venue, over sales orders placed in range whose status is a completed or in-progress sale -- pending, paid, packed, shipped or delivered, never cancelled or refunded, since that money went back (a note names the counted statuses and how many orders in range were excluded): orders, gross, fees, net, cost basis and gain, the last two summed per item share (`sales_order_item_share`, specific identification) so a bucket's own gain is exactly the sum of its items'. Parameter: a date range on the order's own placement date. A row drills to `/sales`; an overall total. |
| `sl_fulfilment` | To ship | One row per order still open and unshipped -- `pending`, `paid` or `packed`, the open-order statuses the sales side itself uses (`sale_state`), stated as an included list so a future status is never swept in by default -- oldest first: customer, items, amount, days waiting. A row drills to `/sales`; an overall total. A deleted or split item is left out of its order's own item count; a note counts them when there are any. |
| `sl_aging` | Held and not offered | Live items received and held, not on any active or paused listing and not an open member of a sales lot, by months since receipt (0-5, 6-11, 12-23, 24+, or "Unknown" when no receipt transition is found -- an item whose history holds only its opening row, which is not an arrival) x kind: items, total cost. A split child with no receipt of its own falls back to its split parent's. No drills; an overall total. A note counts the "Unknown" items when there are any. |
| `sl_auctions` | Auctions | One row per auction, ordered by status (draft through settled, then cancelled): lots, sold, unsold (withdrawn counts as unsold), hammer total and fees -- shown only for a settled auction, since a closed one may carry a result mid-settlement. A row drills to `/auctions`. No totals row: the figures are not meaningful summed across different auctions and statuses. |

### Money -- what it cost and what it is worth

| Id | Report | Rows, drill and totals |
|---|---|---|
| `mn_basis` | Cost basis | Status x disposition of every live item: items, total cost, with an overall total (`All statuses`). No drill: a status x disposition row spans both the coin and currency inventories, which no single search page can reproduce. |
| `mn_tax` | Sales tax paid | Period x vendor, over purchases with a live item (the same rule and note `pr_spend` uses): purchases, sales tax paid, with a subtotal row per period and an overall total. Parameters: a date range on the purchase's own order date, and `period` (`month`, default, or `year` -- `mn_tax` never buckets by quarter). No drills. A note counts purchases excluded for having no order date. |
| `mn_value` | Recorded value | Per item kind, live items by status and disposition (default `received`/`held`, the same default every Collection report uses): items, the ones carrying the owner's own recorded numismatic value, their cost and that value (summed only over the valued ones, so a zero there never reads as "worth nothing"), the difference, and items with no recorded value. A row drills to that kind's search, narrowed the same way a Collection report's row is; an overall total. A note states plainly that the recorded value is the owner's own manual entry, whatever the item's valuation basis, and that no price-guide or vendor value is derived, seeded or shown anywhere in this report. |

## How it works

### Backend: `app/reports/`

- **One registry.** `app/reports/registry.py` holds `REPORTS`, keyed by id, as
  `issues.py` holds its checks: adding a report is adding one `register()`
  call in a group module. `app/reports/__init__.py` imports every group
  module, so `REPORTS` is complete the moment anything imports `app.reports`
  itself; it iterates in catalog order (`GROUP_ORDER`, then registration
  order), and a report naming a group not in `GROUP_ORDER` is refused. Each
  entry is a `Report`: `id`, `group`, `title`, `purpose`, `params`
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
  read it right, such as "Melt value leaves out metals with no recorded
  price: ...".
- **Grouped by module**: `collection.py`, `data_quality.py`, `money.py`,
  `purchasing.py` and `selling.py`, each defining its own reports and
  registering them. Four small modules hold logic more than one group
  module shares, rather than each restating it: `tables.py` (the
  `inventory_item`/`item_kind` aliases and the live-row predicate on them,
  shared by every report that groups over live items), `live_params.py`
  (the status/disposition parameters, their filter, a kind's drill-down and
  the "Nothing matches these settings." note, shared by every Collection
  report and `mn_value`), `receipts.py` (one
  definition of "an item's receipt" -- a status transition *to* `received`,
  never an opening row -- shared by `pr_received` and `sl_aging`), and
  `live_purchases.py` (one definition of "a purchase" -- counted only when
  it carries a live item -- shared by `pr_spend`, `pr_sources` and
  `mn_tax`). A report built on top of one of these can never silently
  disagree with another about what the shared word means. The plumbing is
  `base.py` (`Report`, `ReportResult`, `DateRange`, `Period`), `params.py`
  (`resolve_params`), `serialize.py` (the JSON shapes) and `workbook.py`.
- **The live-row rule.** Every report reads live items only -- no
  `deleted_at`, no `split_at` -- through one shared predicate, `live_item()`
  in `app/live.py`. The search views are SQL text, so they state the same
  rule as text (`i.split_at IS NULL` in each view's `where`, and the
  default `DELETED_MODES["no"]`); `tests/test_live.py` pins the two as
  selecting the same rows.
- **A drill-down is decided from a row's own definition, never by running a
  search at report time.** `cb_grades` and `cb_metal`, whose group-by keys
  do not line up one-to-one with a search filter, work out from the
  report's own rows whether a wider search would sweep in more than that
  row's own count, and omit the drill when it would; no report issues a
  second query against the search itself to check.
- **Queries** are SQLAlchemy Core on the base tables, grouped by a
  foreign-key id or code together with its label. Parameters are bound;
  column names come from allowlists, never from a request.
- **Money** is `Decimal`: summed in SQL where the grouping allows, else
  added as `Decimal` in Python, which is exact; never a float. It is sent as
  decimal strings, as everywhere else.
- **Date-range parameters.** `DateRange` (`app/reports/base.py`) is the
  shared params base for any report scoped to a date range: `date_from` and
  `date_to`, both optional and independent, titled "From" and "To". A
  report subclasses it (`class SpendParams(DateRange): period: ...`) rather
  than declaring the two fields itself. `date_from` after `date_to` is
  refused (422 from the API, exit 2 from the command line); either bound
  absent means no limit on that side -- an open range is a normal question,
  never today's date or any other stand-in default.
- **Period parameters.** `Period` (`month`, `quarter` or `year`) and its
  `period_start`/`period_label` helpers in `base.py` are written once and
  shared by every report that buckets a date range into a time period --
  `pr_spend` (all three) and `mn_tax` (`month`, its default, or `year`,
  through the narrower `TaxPeriod` literal) -- so the two can never drift
  apart on what a period is or how it reads.

### API

- `GET /api/reports` -- the catalog: a list of `{id, group, title, purpose,
  params}`, each parameter carrying `name`, `label` (the pydantic field's own
  `title`), `type` (`choice`, `integer`, `date` or `text`), `default` and
  `choices` (a `Literal` parameter's own values, else `null`). A `date`
  parameter's default is always `null`; its value on the wire and in the
  address is ISO `YYYY-MM-DD`.
- `GET /api/reports/{id}?<params>` -- the result as JSON:
  `{id, group, title, params (resolved), run_at (ISO, local), columns, rows,
  totals, drills, link_column, notes}`. A `Decimal` crosses as the string it prints, a
  `date` as its ISO text. 404 for an unknown report; 422 for an unknown
  parameter name or a value its params model rejects -- validated by that
  model itself, the one thing `resolve_params` shares with the command line,
  so a report is validated identically from either.
- `GET /api/reports/{id}/workbook?<params>` -- the same result as an `.xlsx`
  download, named `<id>_<YYYY-MM-DD>.xlsx` (the run date): one sheet, the
  report's title, one row per parameter (label and the value it ran with; a
  date bound is a date cell, an absent one the word `any`), a
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
`--param` (repeatable) sets its parameters. A parameter left off the command
line runs with its own default, printed in words -- an absent date bound
prints as `any`, the same word the console's print heading uses, never the
text "None". An unknown report id or a bad `--param` is reported on stderr
and exits 2. Read-only, so no `--commit`.

### Console: the Reports page

- A **Reports** item in the console menu, before Vocabularies.
- The page lists the catalog by group; choosing a report shows its
  parameters (as the inventory filter panel does), its table with the totals
  row, its notes, and **Export workbook**.
- The report and its parameters are kept in the address (`/reports?report=
  pr_outstanding&overdue_days=30`), so a report can be bookmarked or reopened;
  Run writes only the parameters that differ from their defaults. Leaving a
  parameter field empty is refused (`Enter a value for <label>.`), rather
  than silently running that parameter's default -- except a date parameter
  (`<input type=date>`), where emptying either From or To means no bound on
  that side: the field is simply left out of the request and the address,
  not refused.
- Columns sort in the browser (a report is at most a few thousand rows), a
  header click sorting ascending, then descending, then back to the report's
  own order. Money is shown with `money()` from the decimal string, never
  parsed into a float.
- Pressing Run on an unchanged form runs the report again. Beside the button
  the form says "Running..." while the request is out and then when the
  answer on screen was run ("Ran <time>"), since the same rows often come
  back and nothing else would show that it ran.
- A row with a drill-down links to the page that fixes or shows it, in the
  same tab. A drill that names exactly one item -- an `sl_offered` row
  offering one item (`/inventory/<coins|currency>?item=CC-######`), a
  `dq_series_years` row (`?item_code=CC-######`) -- is not followed: the
  Reports page opens that item's editor over the report
  (`reports/values.js`, `itemOfDrill`), so closing it returns to the report,
  and a save runs the report again. The same address works on its own: the
  inventory page reads `item` from its own address and opens
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
  row count. A date parameter prints as a date; an absent bound prints
  "any" ("From: any").
- The table's header row repeats on every page (`thead` as a table
  header group), a row is never split across pages, and the totals row
  and the report's notes follow the last row.
- A report of more than six columns prints landscape (`report--wide`, which
  sets the `@page` size; `PORTRAIT_COLUMNS` in `ReportView.jsx`).
- Black on white: nothing is carried by colour alone (an overdue row is
  marked in words, in its own Overdue cell); money is right-aligned; a drill-down
  link prints as its plain text.
- Page numbers and the date come from the browser's own print header and
  footer.


### The `missing=` search filter

Drill-downs reuse the inventory search's filters, including
**`missing=<field>`** -- items whose field is empty, kind-aware as the
completeness report is (`inventory_search.MISSING_FIELDS`, one shared
predicate per field; a field outside its kinds matches nothing). Its field
names are exactly `dq_completeness`'s percent-column keys -- year,
denomination, grade, country, series, metal, photo, storage_location,
listing_link, sellers_item_id -- so a report cell's count and its
drill-down search's count are the same predicate. It works in the search on
its own.

## Rules

- **Read-only.** No report writes, and none offers a button that does.
  Fixing is done where it is done today.
- **Admin only**, as above.
- **No proprietary numbering or prices.** Reports show the owner's own
  records. A catalog's numbering (Friedberg, Pick) is shown only as the owner
  recorded it on an item, and no report derives, seeds or exports a price
  guide's values. `mn_value`'s recorded value is the owner's own manual
  entry, never a price-guide or vendor figure. A valuation source under
  licence stays out of exports.
- **Kind-aware.** A field that does not apply to a kind (a note's metal, a
  coin's seal) is not "missing" for it; a note's year is its series year.
- **Live rows only**, as above.
- **Current state.** Reports answer from the database as it is, not as it
  was at a past date.

## Out of scope

Charts; scheduled or emailed reports; dashboards; a free-form query
builder; a tax-return form of realized gain (`sl_sales` shows gain per item,
but how fees and shipping are presented on a return is outside it); and
snapshots over time, which need history the database does not keep.

## Decisions

| Question | Decision |
|---|---|
| When is an outstanding purchase overdue? | After **21 days** from its order date -- `pr_outstanding`'s default, still a parameter. |
| How is a sale's gain worked out? | **Per item** (specific identification): each sold item's share of the sale (`sales_order_item_share`) less that item's own cost basis. `sl_sales` uses this; no other method is offered. |
| What grade bands does `cb_grades` use? | **Numbers**, not names: 1-49, 50-59, 60-64, 65-70, and "No numeric grade". The strike type (MS, PR) and the grading service are separate columns, not folded into the band. |
| What counts as a purchase, for spending and tax totals? | One with **at least one live item** -- `pr_spend`, `pr_sources` and `mn_tax` all read the same rule, so a purchase whose only items are deleted or split is not silently counted by one report and not another. |
| What counts as a receipt? | A status **transition to** `received` -- an opening row (a new item, a split child or a seed) is never an arrival. A split parent's receipt is attributed to its live children. |
| Which sales orders are counted as sales? | **Pending, paid, packed, shipped or delivered** -- never cancelled or refunded, since that money went back. |
| What does `mn_value` and the Collection reports default to? | **Received and held** -- what the owner has actually taken in and still has -- widened to `all` on request. |
| Printed as well as shown? | **Yes**: every report prints (see Printing). |
| Storage locations | **Optional.** A location is chosen on New item, in Receiving, or in the item editor, and changed whenever the piece moves; every move is kept in its location history. `dq_locations` reports "none recorded" as a row, not as an error. |

## Testing

- Each report's `run` is tested against built data with a known answer,
  including a deleted and a split item that must not count, and an item of a
  kind a field does not apply to (`tests/test_reports_collection.py`,
  `_data_quality`, `_purchasing`, `_selling`, `_money`; date ranges and
  periods in `_dates`; the registry's ordering and refusals in `_registry`).
- The API (`test_reports_api.py`): catalog shape, a parameter default, a
  422, a 404, manager only; the workbook round-trips -- the sheet's rows
  equal the JSON's. The command line: `test_reports_cli.py`.
- The console (`Reports.test.jsx`): the page lists the catalog, runs a
  report from the address, sorts, exports, prints (the Print button calls
  the browser's print; the print-only heading carries the title, parameters
  and run time), and drills down.
- Performance (`test_reports_performance.py`): every report runs against the
  test database and fails if any takes over a second -- a guard, not a
  benchmark.
