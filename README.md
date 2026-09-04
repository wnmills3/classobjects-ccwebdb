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

Once set up, two terminals with `conda activate ccwebdb`:

```powershell
# API -> http://127.0.0.1:8000  (interactive docs at /docs)
cd backend
uv run uvicorn app.main:app --reload --port 8000

# UI  -> http://127.0.0.1:5173
cd frontend
npm run dev
```

The database must be running first:

```powershell
pg_ctl -D .pgdata -l .pgdata\server.log start
```

Default administrator: `admin@example.com` / `adminpassword` (see `.env`).

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
| GET    | `/api/coins`         | public   |
| GET    | `/api/coins/{id}`    | public   |
| POST   | `/api/coins`         | admin    |
| PATCH  | `/api/coins/{id}`    | admin    |
| DELETE | `/api/coins/{id}`    | admin    |
| POST   | `/api/orders`        | customer |
| GET    | `/api/orders`        | own orders; admins see all |
| GET    | `/api/orders/{id}`   | own order; admins see all  |
| PATCH  | `/api/orders/{id}`   | admin (status changes)     |

Notable behaviour:

- Placing an order locks the affected catalogue rows (`SELECT ... FOR UPDATE`,
  taken in id order) and decrements stock atomically, so concurrent buyers
  cannot oversell an item.
- Order lines record `unit_price` at purchase time, so later price edits do not
  rewrite order history.
- Cancelling an order returns its items to available stock.
- An item that appears in an existing order cannot be deleted; withdraw it by
  setting `is_active` to false.

## Dependencies

Python dependencies are managed **exclusively** by uv (`uv add`, `uv sync`) and
land in the conda `ccwebdb` environment. Do not `conda install` or `pip install`
Python packages into that environment — see the gotchas in
[docs/environment-setup.md](docs/environment-setup.md).

## Tests

```powershell
uv run pytest
```

69 tests covering authentication and token handling, catalogue reads and
admin-only writes, the purchase flow, and model/migration drift. The suite
builds and drops its own `ccwebdb_test` database, so it never touches
development data — see [docs/environment-setup.md](docs/environment-setup.md)
for the one-time `CREATEDB` grant it needs.
