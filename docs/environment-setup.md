# Development environment setup

How to recreate the `ccwebdb` development environment from a clean Windows
machine. Every tool below is installed **per-user or per-environment** — none
of it needs administrator rights, and nothing is installed machine-wide except
Miniforge and uv themselves.

All commands are **cmd**, not PowerShell. For day-to-day starting and stopping
once set up, see [runtime-operations.md](runtime-operations.md).

Recorded from the actual setup on Windows 11 Pro (26100). Versions are the ones
verified working; newer patch releases should be fine.

| Component  | Version   | Provided by                     |
| ---------- | --------- | ------------------------------- |
| Miniforge3 | 26.5.3    | winget                          |
| Python     | 3.13.15   | conda env `ccwebdb`             |
| uv         | 0.12.9    | winget                          |
| Node.js    | 24.19.0   | conda env `ccwebdb`             |
| PostgreSQL | 18.6      | conda env `ccwebdb`             |

---

## 1. Miniforge3

Miniforge is Anaconda-free conda, configured for the `conda-forge` channel only,
so there are no Anaconda Terms-of-Service prompts.

```cmd
winget install --id CondaForge.Miniforge3 --source winget --scope user ^
  --accept-package-agreements --accept-source-agreements
```

Installs to `%USERPROFILE%\miniforge3`. Then make `conda` available to your
shells:

```cmd
"%USERPROFILE%\miniforge3\Scripts\conda.exe" init cmd.exe bash
```

For cmd this adds an AutoRun entry under
`HKCU\Software\Microsoft\Command Processor`, which loads conda's hook in every
new `cmd` session. Reverse it any time with `conda init --reverse cmd.exe bash`.

**Open a new terminal** before continuing — the change does not affect
already-running shells.

## 2. The `ccwebdb` conda environment

```cmd
conda create -n ccwebdb python=3.13
conda activate ccwebdb
```

> Activate this environment **before** starting work (including before
> launching Claude Code, if you use it — the status line reads
> `CONDA_DEFAULT_ENV`).

## 3. uv

uv owns Python dependency management for this project.

```cmd
winget install --id astral-sh.uv --source winget ^
  --accept-package-agreements --accept-source-agreements
```

> Astral's own installer is a PowerShell script, so winget is the cmd-native
> route. Update it the same way — `winget upgrade astral-sh.uv` — rather than
> `uv self update`, which is for the standalone build.

### Two environment variables make uv behave the way this project expects

```cmd
setx UV_CACHE_DIR "%USERPROFILE%\dev\uv\cache"
setx UV_PROJECT_ENVIRONMENT "%USERPROFILE%\miniforge3\envs\ccwebdb"
```

- `UV_CACHE_DIR` — keeps uv's package and interpreter cache somewhere you chose
  rather than `%LOCALAPPDATA%`.
- `UV_PROJECT_ENVIRONMENT` — **the important one.** It makes `uv sync` /
  `uv add` / `uv run` operate on the conda `ccwebdb` environment instead of
  creating a project-local `.venv`.

`setx` writes to the user environment and takes effect in **new** shells, not
the one you typed it in.

> **This variable is machine-wide.** Any *other* uv project on the same machine
> will also target the `ccwebdb` conda env unless you override it. When you
> start an unrelated uv project, run `set "UV_PROJECT_ENVIRONMENT=.venv"` in
> that shell first. There is no `pyproject.toml` equivalent — uv rejects
> `project-environment` as a `[tool.uv]` key, so an environment variable is the
> only mechanism.

> **Do not use `setx` on `PATH`.** It truncates at 1024 characters and would
> flatten the `%USERPROFILE%` token this machine's `PATH` relies on. Edit `PATH`
> through *System Properties → Environment Variables* instead.

## 4. Node.js and PostgreSQL (both inside the conda env)

Neither of these is a Python package, so `uv sync` will never prune them.

```cmd
conda install -n ccwebdb nodejs=24.19.0 postgresql
```

> Pin `nodejs=24.19.0`. conda-forge's newest `nodejs` at time of writing was
> `26.8.0`, which reports itself as `v26.8.0-alpha.0.0.0` — a pre-release. The
> 24.x line is the current LTS.

Verify, with the environment activated:

```
node --version     -> v24.19.0
npm --version      -> 11.17.0
psql --version     -> psql (PostgreSQL) 18.6
```

## 5. Create the local database cluster

Postgres runs directly from the conda environment — **no Docker required.**

```cmd
cd /d <repo root>
echo devpassword>"%TEMP%\initpw.txt"
initdb -D .pgdata -U postgres --pwfile="%TEMP%\initpw.txt" --auth-host=scram-sha-256 --encoding=UTF8
del "%TEMP%\initpw.txt"
```

> Write the password file with **no space before the `>`**. `echo devpassword >file`
> would put a trailing space in the password.

`.pgdata\` is gitignored. The "enabling trust authentication for local
connections" warning is harmless on Windows: there are no Unix domain sockets,
so only the `host` rules (`scram-sha-256`) apply.

### Start and stop the server

```cmd
pg_ctl -D .pgdata -l .pgdata\server.log start
pg_ctl -D .pgdata -m fast stop
pg_isready -h localhost -p 5432
```

> In an automated or non-interactive shell, `pg_ctl start` can appear to hang
> because the postmaster inherits stdout. The server *is* running — check with
> `pg_isready` rather than waiting.

Day to day, `scripts\ccweb_startup.cmd` handles this for you.

### Create the application role and database

```cmd
set "PGPASSWORD=devpassword"
psql -h localhost -U postgres -d postgres -c "CREATE ROLE ccwebdb WITH LOGIN PASSWORD 'devpassword';"
psql -h localhost -U postgres -d postgres -c "CREATE DATABASE ccwebdb OWNER ccwebdb ENCODING 'UTF8';"
```

## 6. Configure the application

```cmd
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste that value into `JWT_SECRET` in `.env`. `.env` is gitignored;
`.env.example` is committed and documents every key.

## 7. Install dependencies

```cmd
cd /d <repo root>
uv sync
cd frontend
npm install
```

## 8. Create the schema and seed data

There are two paths and they are **not interchangeable**. Choose by whether a
real collection is going into this database.

### A trial installation, with demo data

```cmd
cd backend
uv run alembic upgrade head
uv run python -m app.seeding load
uv run python -m app.seed
```

`app.seeding load` populates the 29 reference vocabularies. `app.seed` then
creates the administrator from `FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD`
**plus five demo inventory items with listings**. Both are idempotent.

### A real collection

```cmd
cd backend
uv run alembic upgrade head
uv run python -m app.seeding load
uv run python -m app.importers.cli --file <spreadsheet.xlsx> --commit
uv run python -m app.seed
```

**`app.seed` must come after the import, and its demo items then deleted.**
Run before, its five demo coins consume `CC-000002` through `CC-000006`, so
every real item is permanently offset by five and five coins that do not exist
sit in the collection. Nothing warns you; the import simply starts at
`CC-000007`.

To remove them after seeding the administrator:

```sql
DELETE FROM listing WHERE inventory_item_id IN
  (SELECT id FROM inventory_item WHERE price = 0 AND parent_item_id IS NULL);
DELETE FROM inventory_item WHERE price = 0 AND parent_item_id IS NULL;
```

Then verify the collection totals before trusting anything downstream: the item
count, the cost basis and `sum(fine_weight_ozt * storage_quantity)`. **Multiply
by `storage_quantity`** — a row holding twenty coins carries twenty coins'
worth of metal, and the unweighted sum understates the holding by about 10%.

---

## Running the application

```cmd
scripts\ccweb_startup.cmd
```

That starts the database, the API and the UI, and waits for each to answer. See
[runtime-operations.md](runtime-operations.md) for what it does, how to stop it,
and how to run a service by hand with `--reload`.

| | |
|---|---|
| UI | http://127.0.0.1:5173 |
| API docs | http://127.0.0.1:8000/docs |
| Sign in | `admin@example.com` / `adminpassword` |

Vite proxies `/api` to port 8000, so the browser only ever talks to one origin
and CORS is not involved during development.

---

## Running the tests

```cmd
uv run pytest
```

The suite creates its own database (`ccwebdb_test`), builds the schema, and
drops it again afterwards, so it never touches development data. Each test runs
inside a transaction that is rolled back, which keeps tests independent.

This requires the application role to be able to create databases — a one-time
grant:

```cmd
set "PGPASSWORD=devpassword"
psql -h localhost -U postgres -d postgres -c "ALTER ROLE ccwebdb CREATEDB;"
```

Point the suite somewhere else with `TEST_DATABASE_URL` if you prefer.

Two of the suites are worth knowing about:

- `test_migrations.py` builds a throwaway database purely by running
  `alembic upgrade head`, then asserts that autogenerate finds no difference
  against the models. It fails if a model changes without a migration.
- `test_concurrency.py` deliberately bypasses `TestClient` and drives the order
  handler from real threads. `TestClient` serialises requests through a single
  portal, so a race written against it passes even when the row lock is
  removed — which makes it worthless as a concurrency test.

---

## Daily workflow

| Task                        | Command                                          |
| --------------------------- | ------------------------------------------------ |
| Start everything            | `scripts\ccweb_startup.cmd`                      |
| Stop everything             | `scripts\ccweb_shutdown.cmd`                     |
| Add a Python dependency     | `uv add <pkg>` (from repo root)                  |
| Add a dev-only dependency   | `uv add --dev <pkg>`                             |
| Run a Python command        | `uv run <cmd>`                                   |
| Add a JS dependency         | `npm install <pkg>` (from `frontend\`)           |
| New migration               | `cd backend && uv run alembic revision --autogenerate -m "..."` |
| Apply migrations            | `cd backend && uv run alembic upgrade head`      |
| Run the tests               | `uv run pytest`                                  |

Note `&&` rather than `;` — cmd uses `&&` to chain on success, `&` to chain
unconditionally.

---

## Gotchas worth knowing

**Never install Python packages into the conda env with `conda` or `pip`.**
uv and conda share the same `site-packages` because of
`UV_PROJECT_ENVIRONMENT`. A `conda install <python-package>` followed by
`uv sync` will have uv *uninstall* it, because it is not in `uv.lock`. `pip`
itself is exempt (uv treats it as a seed package), but nothing else is. Python
dependencies go in `pyproject.toml`; use conda only for non-Python tools like
Node and Postgres.

**A bare script name is not found, even in its own directory.** This machine has
`NoDefaultCurrentDirectoryInExePath=1`, so `cmd /c ccweb_startup.cmd` fails with
*"not recognized"*. Always include a path separator: `scripts\ccweb_startup.cmd`,
or `.\name.cmd` when standing in the folder.

**Do not build Windows paths with `sed`.** Backslash is an escape in the
replacement text, so `sed "s|x|scripts\ccweb|"` silently writes a control
character instead of `\c`. Edit files directly, or use Python with `chr(92)`.

**Git Bash rewrites leading slashes and eats backslashes.** `conda /info`
becomes `conda "C:/Program Files/Git/info"`, and `.pgdata\server.log` becomes
`.pgdataserver.log`. Prefix with `MSYS_NO_PATHCONV=1`, use `//flag`, or just
use forward slashes — Windows accepts them in most paths.

**`jq` is not available on conda-forge for win-64.** Only `xstatic-jquery`
matches a search, which is an unrelated Python package. Parse JSON with Python
in scripts rather than depending on jq.

**Python on Windows writes CRLF to stdout.** If a Python helper feeds a shell
script, `print()` emits `\r\n` and the trailing `\r` silently corrupts the
consuming shell variables. Use `sys.stdout.reconfigure(newline="\n")`.

**Money is `NUMERIC(12, 2)` and maps to `Decimal`.** Never let a price become a
float anywhere in the stack.
