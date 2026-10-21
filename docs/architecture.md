# Architecture

Last revised: 2026-10-21.

How the system is built: its processes, the layers of the backend, the data
model and the invariants it keeps, the two frontends, and how a request
travels through them. Why it is built this way is in
[project-purpose.md](project-purpose.md); the schema in detail is in
[database-design.md](database-design.md); each feature's design is in
[specs/](specs/).

## The pieces

```
           browser                                   browser
      (shop: /, /coins/..)                   (console: /management/...)
               \                                     /
                \            HTTPS                  /
                 +-------- Caddy (production) -----+
                 |  static files from frontend/dist |
                 |  /api/*  -> reverse proxy        |
                 +---------------+------------------+
                                 |
                      uvicorn: FastAPI app (one process)
                      backend/app/main.py, routers under /api
                                 |
                      SQLAlchemy 2 sessions (psycopg 3)
                                 |
                           PostgreSQL 17+
                                 |
                media/ (photograph bytes by content hash)
```

- **Backend.** One FastAPI application object, `app` in
  `backend/app/main.py`, served by uvicorn as a **single process** (sign-in
  throttling counts in process memory, `app/throttle.py`). It mounts every
  router under `/api` and answers `GET /health` outside it. Behind Caddy it
  runs with `--proxy-headers`, trusting forwarded addresses only from
  Caddy's own.
- **Frontend.** Two React applications built by Vite from one source tree:
  the **shop** (`frontend/index.html`, `src/store/`) and the **management
  console** (`frontend/management.html`, `src/management/`, served under
  `/management`). They share `src/shared/` and are kept apart in three
  ways: ESLint forbids either importing the other, the build emits a module
  graph that `frontend/scripts/check-bundle-isolation.mjs` checks for
  leakage in either direction, and console API calls live only in
  `src/management/api.js`. The shop bundle therefore carries none of the
  console's code.
- **Database.** PostgreSQL 17 or later with the `fuzzystrmatch` extension,
  run from the conda environment in development; the schema is managed by
  Alembic from a squashed baseline (`backend/alembic/`).
- **Photographs.** Bytes live on disk under `MEDIA_ROOT`, addressed by the
  SHA-256 of their cleansed content; the database holds rows that name
  them. The API checks at start-up that `MEDIA_ROOT` holds the newest of
  them, and refuses to start when it holds none (`app/media_check.py`).
- **Reverse proxy.** In production Caddy serves the built files, proxies
  `/api/*` to the API, terminates TLS and sets the security headers
  (`deploy/Caddyfile`, a template rendered from settings). The backend
  serves no static files.
- **Scripts.** `scripts/` holds every operation as a cmd script for Windows
  and a bash twin for Linux with the same name, modes, wording and exit
  codes (a test fails when either lacks the other). Every port, host and
  folder they use comes from `backend/app/runtime_settings.py` through
  `ccweb_settings`; none is written into a script.
- **Scheduled backup.** A daily task (Windows Task Scheduler, or a systemd
  user timer on Linux) runs `python -m app.backup_run`: export, prove by
  restoring, then tidy.

## Configuration

One source: environment variables and the repository's `.env`, read by
pydantic settings.

- `backend/app/runtime_settings.py` (`RuntimeFields`) holds what the
  scripts need too: the database URL and cluster folders, the test
  database, API and frontend host and port, log and backup folders, the
  site's domain and certificate. `python -m app.runtime_settings` prints
  them for cmd or bash and renders the deployment templates.
- `backend/app/config.py` (`Settings`, extending `RuntimeFields`) adds what
  only the API reads: the JWT secret (required, at least 32 characters, not
  a published example), token lifetimes, CORS origins, time zone, image
  limits, the shop gate `SHOP_OPEN`, the shop's name, the sales-tax
  defaults stamped on new purchases.
- An unknown key in `.env` is an error (`extra="forbid"`), so a misspelt
  setting is never silently ignored. `.env.example` lists every key; a test
  keeps it complete.

## Backend layers

```
routers/        HTTP: parse, authorise, resolve codes, call one writer, commit
   |
writers         the only code that changes a given table (flush, never commit)
helpers         rules and queries shared by routers and writers
   |
models/         SQLAlchemy declarations, constraints, views
```

### Routers and who may call them

Access is decided by FastAPI dependencies in `backend/app/deps.py`:
`AdminUser` (role `manager`), `CurrentUser` (any active signed-in account)
and `OptionalUser`.

| Prefix | Router | Access |
|---|---|---|
| `/auth` | `auth.py` | register (closed while the shop is closed, throttled), login (throttled), refresh, logout and `me` |
| `/shop` | `catalog.py` | public: is the shop open |
| `/catalog` | `catalog.py` | public while `SHOP_OPEN`; signed-in only otherwise; responses built field by field (`to_catalog_item`) so cost, location and history never leak |
| `/images/{sha256}/{kind}` | `images.py` | public: the `thumb` and `web` renditions only, never an original |
| `/orders` | `orders.py` | a customer places and reads their own orders (another's is 404); managers revise and change status |
| `/reference` | `reference.py` | the shop's vocabularies readable by anyone; everything else, and every write, manager only |
| everything else | `inventory`, `acquisitions`, `images` (writes), `image_links`, `friedberg`, `defaults`, `users`, `customers`, `sales_venues`, `metal_prices`, `offers`, `lots`, `auctions`, `reports` | manager |

Two helpers keep routers uniform: `routers/_resolve.py` turns what a client
sent into rows (an unknown code in a body is 422, an unknown id in a path is
404) and `routers/_tx.py` commits, mapping a stale version
(`StaleDataError`) and known unique-index races to 409.

### Single writers

Every table with a history has exactly one module that changes it, so a
rule about that table lives in one place. Writers flush and never commit;
the router commits once.

| Module | Writes |
|---|---|
| `lifecycle_writes` | an item's status and storage location, and their history |
| `offering_writes` | listing status, offer claims, lot release, the shop rule and the lock order |
| `order_writes` | sales orders and lines (shop, on behalf, platform sales, settlements), stock, item snapshots, cost shares |
| `sales_writes` | recorded sales' fees and their allocation |
| `lot_writes` | sales lots while being assembled |
| `auctions` | auction transitions, custody and settlement |
| `image_links` | an item's photographs, their order and the one primary |
| `field_changes` | the item change log (History) |
| `field_sources` | which fields hold a value a rule filled in |

A listing's price and wording are edited by `routers/offers.py` directly;
an order's status by `routers/orders.py`, which records the change through
`order_writes`.

### Command-line passes

Corrections over stored items run as `python -m app.<name>` from
`backend/`, a dry run unless `--commit`. Those that write History require
`--by EMAIL` (`app/pass_cli.py`). They fill blanks and never overwrite a
value a person typed or one marked as held: `classifier_defaults`,
`series_match`, `series_classify`, `serial_patterns`, `bullion_weights`,
`seller_titles`, `listing_links`, `ebay_orders`, `vendor_cleanup`,
`photo_import`, `image_sources`, `image_enlarge`. Other command lines:
`app.seeding` (load or export the reference data),
`app.create_admin` (the first manager of a new installation), `app.seed`
(a demonstration catalogue, refused on a real collection), `app.reports`,
and the backup tools below.

### Authentication

`backend/app/security.py`: argon2 password hashes (rehashed on sign-in when
the parameters change, constant time whether or not the account exists);
HS256 JWTs carrying the account, a type (`access` or `refresh`), and the
account's `token_version`. Access tokens last 30 minutes, refresh tokens 14
days. Changing a password or signing out increments `token_version`,
which retires every token issued before. The browser keeps the pair in
`localStorage` and sends the access token as a bearer header; the shop and
console share one sign-in because they share one origin.

### Photographs

`imaging.cleanse` decodes an upload within byte and pixel limits, applies
its orientation, re-encodes it without any metadata (GPS above all), checks
the written bytes carry none, and hashes them. `image_store.ingest` stores
the original and two renditions (`thumb` 320 px, `web` 1600 px) under keys
derived from the hash, once per content (`storage.LocalStorage`, writes
atomic, keys confined to the media folder). Fetching by web address
(`image_fetch`) accepts only http(s) to public addresses, re-checks every
redirect, pins the resolved address, and caps size and time.

### Reports

`backend/app/reports/`: a registry of read-only reports in five groups
(collection, data quality, purchasing and receiving, selling, money), each a
parametrised query with typed columns, served at `/api/reports`, as a
workbook download, and from `python -m app.reports`. Spreadsheet cells
written from stored text are always text, never a formula.

### Backups

- `app.workbook_backup` writes the whole database to one workbook, a sheet
  per table, from a single snapshot, and restores it into an empty migrated
  database in one transaction.
- `app.backup_run` exports, restores the export into a scratch database and
  compares every row of every table, checks the photograph copy, and only
  then removes older workbooks and photographs nothing names.
- `app.backup` copies database to database; `app.media_backup` copies,
  checks and prunes photograph bytes.

## Data model at a glance

About 84 tables, grouped in `backend/app/models/`:

- **Items** -- `inventory_item` with exactly one of `coin_detail` or
  `currency_detail` (the kind decides which); a permanent `item_code`
  (`CC-000123`) from a sequence; generated `sales_tax` and `total_cost`;
  lots split into children (`parent_item_id`, `split_at`); soft delete.
- **Buying** -- `vendor`, `seller`, `purchase_order`.
- **Vocabularies** -- 33 classifier tables sharing one shape (`code`,
  `label`, `sort_order`, `is_active`), plus aliases and merge records.
- **Identification** -- grading certificates, errors, attributes, the
  owner's own Friedberg numbers (never seeded).
- **Valuation** -- `composition` (metal and weights by design and years),
  spot prices.
- **Lifecycle** -- status and location history, the change log, which
  fields a rule filled.
- **Images** -- `image`, `image_derivative`, `item_image`.
- **Selling** -- `sales_venue` (platforms), `listing`, `offer_claim`,
  `sales_lot`, `auction`, `auction_lot`, `customer`, `address`,
  `sales_order` with its lines, cost shares, fees and change log.
- **Accounts** -- `users`.

Views (`coin_inventory`, `currency_inventory`, `item_valuation`,
`public_catalog`) resolve codes for people querying by hand; the
application reads the base tables.

### Invariants and how they are kept

- **No item is offered twice.** A partial unique index on active offer
  claims (`uq_offer_claim_active`) holds however an item is offered: alone,
  in a lot, in the shop, on a platform or in an auction.
- **Concurrent edits are detected.** Items, platforms, listings, lots,
  orders and auctions carry a version column; a write naming a stale
  version is refused with 409 and the current state. Item edits can also
  send each field's base value, so unrelated concurrent edits merge.
- **Selling writes lock in one order** -- lots, then items, then listings,
  each by id -- so they cannot deadlock
  ([specs/lock-order-design.md](specs/lock-order-design.md)).
- **Money is exact.** `NUMERIC` in the database, `Decimal` in Python,
  strings on the wire; divisions allocate to the penny by largest
  remainder.
- **Check constraints** state what must always hold (an order line's
  share is never negative, a listing offers an item or a lot but not
  both, fine weight never exceeds gross, ...); the migration tests compare
  every one, and every generated column, with the models.

## Frontend

- **Shop** (`src/store/`): asks `/api/shop` whether it is open, then shows
  the catalogue, a listing's page, sign-in and registration, the cart
  (kept in `localStorage`) and the customer's orders.
- **Console** (`src/management/`): every route but `/login` requires a
  manager. The menu groups Inventory, Procurement, Selling, Reports and
  Settings; a help band at the bottom explains whichever field has focus
  (`fieldHelp.js`, `HelpScope`).
- **Shared** (`src/shared/`): the one `fetch` wrapper (`api.js`: one shared
  token refresh for concurrent 401s, then a replay), authentication context,
  vocabularies, money in integer cents (`cents.js`), formatting.

## Request flows

**A customer buys an item.** The catalogue lists the shop's own fixed-price
active listings. At checkout `POST /api/orders` locks the listings and
their items in the canonical order, refuses anything not on sale or out of
stock or whose price changed since the cart showed it, records each line
with the price paid and a snapshot of the item, decrements stock, marks
sold-out items sold, allocates cost shares, and commits once.

**The owner receives and edits a coin.** A purchase is entered with its
items (`POST /api/purchase-orders`, `POST /api/inventory`); when the parcel
arrives, `POST /api/inventory/receive` records arrival and location through
`lifecycle_writes`; the item editor's `PATCH /api/inventory/{id}` sends a
version or per-field bases, ends offers if the item stops being sellable,
and records every changed field in History in the same transaction.

**An item is offered on a platform and sold there.** `POST /api/offers`
locks the items, refuses any that cannot be offered, creates the listing and
its active claims, and pauses the item's shop listing. When it sells,
`POST /api/listings/{id}/sale` records the buyer, price and fees as an
order through `order_writes` and `sales_writes`, ends the listing, and ends
the paused shop listing with it.

## Quality

- **Backend tests** (`backend/tests/`, pytest) run against their own
  database, created and dropped per session, each test inside a transaction
  that is rolled back; invariant checks run after every test; race tests
  use real threads.
- **Frontend tests** (`vitest`, beside the source) render components and
  read real request bodies.
- **The gate** (`scripts/ccweb_check.*`) runs ruff (format and lint), a
  guard against leftover mutation scaffolding, mypy, pytest, ESLint,
  Prettier, vitest and the bundle-isolation check; every one is at zero
  ([code-quality.md](code-quality.md)). SonarQube can be run locally in
  containers ([specs/self-hosted-sonarqube-design.md](specs/self-hosted-sonarqube-design.md)).
