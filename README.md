# classobjects-ccwebdb

Numismatic and Currency Web Platform for Inventory and Sales.

A FastAPI backend and two React applications over one PostgreSQL database: an
**owner console** for cataloguing, receiving, photographing and selling a coin
and banknote collection, and a **shop** where customers browse and order. Why
it exists and what it deliberately does not do is in
[docs/project-purpose.md](docs/project-purpose.md).

The `ccwebdb` database is the system of record for the collection. The
spreadsheet it was imported from is historic; data is corrected in the console
or by passes over stored items, never by re-importing
([docs/data-import-plan.md](docs/data-import-plan.md)).

## Stack

| Layer    | Choice                                                      |
| -------- | ----------------------------------------------------------- |
| Backend  | FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2                |
| Database | PostgreSQL 18, run from the conda environment (no Docker)    |
| Auth     | JWT access + refresh tokens, argon2 password hashing         |
| Frontend | React 19 + React Router 7, Vite 8, Vitest, plain JavaScript  |
| Tooling  | Miniforge/conda for binaries, uv for Python dependencies     |

## Getting started

First time on a machine, follow
**[docs/environment-setup.md](docs/environment-setup.md)**. After that, one
command starts the database, the API and both applications:

```cmd
scripts\ccweb_startup.cmd
```

| | |
|---|---|
| Shop | http://127.0.0.1:5173 |
| Owner console | http://127.0.0.1:5173/owner |
| API docs (OpenAPI) | http://127.0.0.1:8000/docs |

Stop with `scripts\ccweb_shutdown.cmd`; see what is running with
`scripts\ccweb_status.cmd`. [docs/runtime-operations.md](docs/runtime-operations.md)
covers every script.

## Layout

```
backend/
  app/
    main.py, config.py, database.py, deps.py, security.py, schemas.py
    models/              the schema by subject area: core, reference,
                         identification, valuation, lifecycle, images,
                         sales, auctions, views
    routers/             the HTTP API (see below)
    *_writes.py          the single writers: lifecycle (status, location),
                         offering (listings, claims, disposition), orders,
                         sales, lots
    inventory_search.py  owner search and facets over the base tables
    issues.py            named diagnostics (no year, no grade, ...)
    classifier_defaults.py, series_match.py, series_classify.py,
    serial_patterns.py, rating_pass.py, photo_import.py
                         passes over stored items; dry run unless --commit
    seeding.py           load and export reference data (backend/data/reference/)
    seed.py              first administrator plus demo items (never on live)
    backup.py            database-to-database copy with --verify
    importers/           workbook import: durable engine, disposable profile
  alembic/               migrations
  data/reference/        shipped vocabularies as versioned JSON
  tests/                 pytest, against its own ccwebdb_test database
frontend/
  index.html, owner.html two entries, built as two isolated bundles
  src/store/             the shop
  src/owner/             the owner console
  src/shared/            API client, auth, formatting, vocabularies
scripts/                 ccweb_*.cmd: startup, shutdown, status, check, psql,
                         pgadmin, claude, rebuild, sonar
docs/                    project, operations and design documents; designs
                         for individual features are in docs/specs/
```

## API

Everything is under `/api`; the live OpenAPI page at `/docs` is the complete
reference. By area:

| Area | Prefix | Access |
|---|---|---|
| Accounts | `/auth`, `/users`, `/customers` | sign-in public; administration admin |
| Shop catalogue | `/catalog` | public, read only |
| Customer orders | `/orders` | customers their own; admins all |
| Inventory | `/inventory` (search, create, bulk, edit, receive, split, errors, reviews, delete) | admin |
| Purchases | `/vendors`, `/purchase-orders`, `/storage-locations` | admin |
| Vocabularies | `/reference` (read public; add, rename, alias, merge admin), `/defaults` | mixed |
| Photographs | `/images`, `/image-links` (renditions public, by content hash) | mixed |
| Selling | `/sales-venues`, `/offers`, `/listings` (end, record a sale), `/sales-lots`, `/auctions` | admin |
| Friedberg numbers | `/friedberg`, `/inventory/{id}/friedberg` | admin; the owner's own numbers only |

Rules that hold across the API:

- **Classifiers cross the API as codes, not ids** (`"grade": "MS64"`). An
  unknown code is a 422 naming the field; the API never invents a vocabulary
  row. An unrecognised search filter is also a 422, never ignored -- a dropped
  filter returns the whole collection and looks like a result.
- **Concurrent edits are detected.** Reads return a `version`; a write that
  sends a stale one is refused with 409 and the current state.
- **No item is offered twice.** At most one active offer per item, enforced
  by a partial unique index on `offer_claim`; checkout locks the listings it
  buys in id order.
- **Order lines record the price paid**, so later price edits do not rewrite
  history.
- **Every item has a permanent `item_code`** (`CC-000123`), issued once and
  never reused.
- **Photographs are stripped of metadata at ingest** (they carry GPS), and
  only generated `thumb` and `web` renditions are ever served. Storage
  location and inventory photographs never reach a customer; `routers/catalog.py`
  builds each public response field by field.
- **Money is `NUMERIC` and `Decimal`**, never a float; lot splits reconcile to
  the penny.

## Looking at the data

In a browser, with pgAdmin:

```cmd
scripts\ccweb_pgadmin.cmd
```

It opens pgAdmin on http://127.0.0.1:5050 with the `ccwebdb` connection
already registered (password `devpassword`). pgAdmin is not a project
dependency; install it once into its own uv tool environment, pinned to 3.13
because `pywinpty` has no wheel for the newer interpreter uv would otherwise
pick:

```cmd
uv tool install --python 3.13 pgadmin4
```

At the command line, with psql:

```cmd
scripts\ccweb_psql.cmd                              interactive psql
scripts\ccweb_psql.cmd -c "select * from metal;"    one statement
scripts\ccweb_psql.cmd -f query.sql                 a file
```

The views `coin_inventory`, `currency_inventory`, `item_valuation` and
`public_catalog` resolve foreign keys to readable codes and exclude split and
deleted rows, which makes them the easy place to query by hand. The
application itself does not read them.

One trap: `source_title` holds the workbook's denomination text (`0.25`,
`Mint Set`), not a name. The words a person searches for are in
`description`. Search both:

```sql
select item_code, description, year_start, grade from coin_inventory
where coalesce(source_title, '') || ' ' || coalesce(description, '') ilike '%morgan%';
```

## Code quality and tests

```cmd
scripts\ccweb_check.cmd          every gate: ruff, mypy, pytest, eslint, prettier, vitest, bundle isolation
scripts\ccweb_check.cmd fix      auto-fix first, then check
```

Every gate is at zero, so any finding is new.
[docs/code-quality.md](docs/code-quality.md) lists what runs and the three
justified lint exceptions. The Python suite builds and drops its own
`ccwebdb_test` database and never touches `ccwebdb`.

## Dependencies

Python dependencies are managed **only** by uv (`uv add`, `uv sync`) and land
in the conda `ccwebdb` environment. Never `conda install` or `pip install` a
Python package there; see [docs/environment-setup.md](docs/environment-setup.md).
