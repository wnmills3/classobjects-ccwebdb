# classobjects-ccwebdb

Numismatic and Currency Web Platform for Inventory and Sales.

A FastAPI backend and React frontend for managing a coin and banknote
inventory. Administrators maintain the catalogue; customers browse it and place
orders.

## Stack

| Layer    | Choice                                                      |
| -------- | ----------------------------------------------------------- |
| Backend  | FastAPI, SQLAlchemy 2.0, Alembic, Pydantic v2                |
| Database | PostgreSQL 18 (run from the conda environment, no Docker)    |
| Auth     | JWT access + refresh tokens, argon2 password hashing         |
| Frontend | React 19 + React Router 7, Vite 8, plain JavaScript          |
| Tooling  | Miniforge/conda for binaries, uv for Python dependencies     |

## Getting started

First time on a machine, follow **[docs/environment-setup.md](docs/environment-setup.md)** —
it covers Miniforge, uv, Node, PostgreSQL and the database bootstrap.

Once set up, one command starts the database, the API and the UI:

```cmd
scripts\ccweb_startup.cmd
```

| | |
|---|---|
| UI | http://127.0.0.1:5173 |
| API docs | http://127.0.0.1:8000/docs |
| Sign in | `admin@example.com` / `adminpassword` (see `.env`) |

Stop it again with `scripts\ccweb_shutdown.cmd`. See
[docs/runtime-operations.md](docs/runtime-operations.md) for what those do and
how to run a service by hand with `--reload`.

## Layout

```
backend/
  app/
    main.py        FastAPI app and CORS
    config.py      settings from .env
    database.py    engine, session, declarative base
    models.py      User, Coin, Order, OrderItem
    schemas.py     Pydantic request/response models
    security.py    argon2 hashing, JWT issue/verify
    deps.py        current-user and admin-role dependencies
    seed.py        idempotent admin + sample inventory
    routers/       auth, coins, orders
  alembic/         migrations
frontend/
  src/
    api.js         fetch wrapper with automatic token refresh
    auth.jsx       authentication context
    cart.jsx       cart state, persisted to localStorage
    pages/         Catalog, CoinDetail, Login, Register, Cart, Orders, AdminCoins
docs/
  environment-setup.md
```

## API

| Method | Path                 | Access   |
| ------ | -------------------- | -------- |
| POST   | `/api/auth/register` | public   |
| POST   | `/api/auth/login`    | public   |
| POST   | `/api/auth/refresh`  | public   |
| GET    | `/api/auth/me`       | signed in |
| GET    | `/api/catalog`       | public   |
| GET    | `/api/catalog/{id}`  | public   |
| POST   | `/api/catalog`       | admin    |
| PATCH  | `/api/catalog/{id}`  | admin    |
| DELETE | `/api/catalog/{id}`  | admin    |
| GET    | `/api/reference`     | public (vocabulary index)  |
| GET    | `/api/reference/{table}` | public               |
| POST   | `/api/reference/{table}` | admin (add a value)  |
| PATCH  | `/api/reference/{table}/{code}` | admin (rename) |
| GET    | `/api/inventory/{view}/search` | admin (`coins` \| `currency`) |
| GET    | `/api/inventory/{id}` | admin                     |
| POST   | `/api/inventory/{id}/split` | admin               |
| POST   | `/api/images`        | admin (multipart upload)   |
| GET    | `/api/images/{id}/{thumb\|web}` | public          |
| DELETE | `/api/images/{id}`   | admin    |
| POST   | `/api/orders`        | customer |
| GET    | `/api/orders`        | own orders; admins see all |
| GET    | `/api/orders/{id}`   | own order; admins see all  |
| PATCH  | `/api/orders/{id}`   | admin (status changes)     |

The `{id}` in the catalogue paths is a **listing** id. A catalogue entry is a
`listing` joined to the `inventory_item` behind it: the item is what you own,
the listing is what it is being sold for. The API presents the pair as one
resource because that is how a shop is operated, and returns the item's id
alongside as `inventory_item_id`.

Notable behaviour:

- **Classifiers cross the API as codes, not ids** -- `"item_kind": "bullion"`,
  `"grade": "MS64"`, `"country": "US"`. Ids differ between installations; codes
  are the stable contract. An unknown code is a 422 naming the field, never a
  silently null column. The API never invents classifier rows.
- **Concurrent edits are detected, not silently applied.** Every GET returns a
  `version` token; sending it back with a PATCH makes the save conditional. If
  someone else saved meanwhile the request is refused with 409 and the current
  state, instead of overwriting their work with values loaded before their
  change. Reads are never blocked -- no locks are involved.
- Placing an order locks the affected listings (`SELECT ... FOR UPDATE`, taken
  in id order) and decrements availability atomically, so concurrent buyers
  cannot oversell. Verified by tests that drive the handler from real threads
  -- a test that serialises its requests passes even with the lock removed.
- Order lines record `unit_price` at purchase time, so later price edits do not
  rewrite order history.
- Cancelling an unshipped order returns its units to availability. Cancelling
  one already packed or shipped does not: that stock has left the building.
- Selling the last unit moves the item's `disposition` to `sold`. Its `status`
  -- how it was acquired -- is untouched; the two lifecycles are independent.
- A listing that appears in an existing order cannot be deleted; withdraw it by
  setting `is_active` to false.
- Every inventory item carries a permanent `item_code` (`CC-000123`), issued
  once, never changed and never reused -- so a returned item resumes its own
  history, and a reference in an audit stays unambiguous.
- **Uploaded images have their metadata stripped at ingest, not at publish.**
  Photographs of valuables routinely carry the GPS coordinates of where they
  were taken. Orientation is applied to the pixels first, then every metadata
  segment is removed, then the written bytes are re-read and checked -- an
  image that still carries metadata is refused rather than stored. Verified
  against real collection photographs, all of which carried GPS.
- Originals are never served. Public requests are answered only from generated
  `thumb` and `web` renditions.
- Images are content-addressed by the hash of the cleansed bytes, so uploading
  the same photograph twice stores one file.
- **Coins and currency browse as separate inventories**, because the columns
  that matter differ: a coin has a mint mark and a variety, a banknote has a
  series letter, a seal colour and its own printed serial. One grid would
  leave most columns blank most of the time.
- The search panel's options come from **facets** -- value counts over the
  current result set -- not the full vocabulary. A real collection uses a
  fraction of the fifty-odd grades defined, so offering all of them buries the
  ones present. Counts reflect the filters already applied, so a choice that
  would return nothing is visibly empty before it is made.
- **An unrecognised filter is a 422, never ignored.** A silently dropped
  filter returns the whole collection and looks like a matching result.
- **Vocabularies grow with use.** A value missing from a picker can be added
  from the picker, marked `manual` so one installation's additions stay out of
  a catalogue shared with another.
- **Renaming a value changes its label everywhere at once**, because every
  record refers to it by foreign key -- there is nothing to migrate. The
  `code` never changes: it appears in saved filters and bookmarked searches,
  and renaming the label is exactly what lets a poorly worded one be fixed
  without breaking them.
- Searches read the base tables with only the joins each query needs, not the
  wide inventory views. Measured over 6,370 coins, a page plus all facets went
  from 400 ms to 23 ms -- the cost was never the normalisation, it was asking
  a fourteen-way join for one column.
- **A lot can be split into its pieces**, dividing the cost between them.
  `equal` gives every piece the same share -- right for twenty identical rounds
  in a tube. `relative` divides in proportion to a value supplied per piece --
  right for a mint set, where charging the cent and the half dollar the same
  cost basis would make one look like a disaster and the other a windfall.
  `price` and `shipping` always reconcile to the penny; the allocation floors
  each share and hands the remainder to the parts cut hardest. `taxes` is
  generated per row, so the pieces' rounded taxes can total a cent or two away
  from the lot's -- that difference is reported, never absorbed silently.
- The lot is kept and marked `split_at`, because it holds the purchase order
  and the price actually paid. Everything that counts inventory or money
  excludes it, so a lot and its pieces are never both counted.

## Code quality

```
scripts\ccweb_check.cmd          format, lint, types, tests, frontend
scripts\ccweb_check.cmd fix      auto-fix first, then check
```

`ruff` formats and lints the Python, `eslint` and `prettier` the frontend,
`mypy` reports on types. Every public class, method and function carries a
docstring and every function is annotated, enforced by ruff's `D` and `ANN`
rule sets. See [docs/code-quality.md](docs/code-quality.md) for what is
checked, and for the three exceptions and why each exists.

## Dependencies

Python dependencies are managed **exclusively** by uv (`uv add`, `uv sync`) and
land in the conda `ccwebdb` environment. Do not `conda install` or `pip install`
Python packages into that environment — see the gotchas in
[docs/environment-setup.md](docs/environment-setup.md).

## Tests

```cmd
uv run pytest
```

69 tests covering authentication and token handling, catalogue reads and
admin-only writes, the purchase flow, and model/migration drift. The suite
builds and drops its own `ccwebdb_test` database, so it never touches
development data — see [docs/environment-setup.md](docs/environment-setup.md)
for the one-time `CREATEDB` grant it needs.
