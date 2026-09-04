# Development environment setup

How to recreate the `ccwebdb` development environment from a clean Windows
machine. Every tool below is installed **per-user or per-environment** — none
of it needs administrator rights, and nothing is installed machine-wide except
Miniforge and uv themselves.

Recorded from the actual setup on Windows 11 Pro (26100). Versions are the ones
verified working; newer patch releases should be fine.

| Component  | Version   | Provided by                     |
| ---------- | --------- | ------------------------------- |
| Miniforge3 | 26.5.3    | winget                          |
| Python     | 3.13.15   | conda env `ccwebdb`             |
| uv         | 0.12.9    | standalone installer            |
| Node.js    | 24.19.0   | conda env `ccwebdb`             |
| PostgreSQL | 18.6      | conda env `ccwebdb`             |

---

## 1. Miniforge3

Miniforge is Anaconda-free conda, configured for the `conda-forge` channel only,
so there are no Anaconda Terms-of-Service prompts.

```powershell
winget install --id CondaForge.Miniforge3 --source winget --scope user `
  --accept-package-agreements --accept-source-agreements
```

Installs to `%USERPROFILE%\miniforge3`. Then make `conda` available to your
shells:

```powershell
& "$env:USERPROFILE\miniforge3\Scripts\conda.exe" init powershell bash
```

This writes to `Documents\WindowsPowerShell\profile.ps1` and `~/.bash_profile`.
Reverse it any time with `conda init --reverse powershell bash`.

**Open a new terminal** before continuing — the profile change does not affect
already-running shells.

## 2. The `ccwebdb` conda environment

```powershell
conda create -n ccwebdb python=3.13
conda activate ccwebdb
```

> Activate this environment **before** starting work (including before
> launching Claude Code, if you use it — the status line reads
> `CONDA_DEFAULT_ENV`).

## 3. uv

uv owns Python dependency management for this project. Install the standalone
build so that `uv self update` keeps working:

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Installs `uv.exe`, `uvx.exe`, `uvw.exe` to `%USERPROFILE%\.local\bin` and adds
that directory to the user `PATH`.

### Two environment variables make uv behave the way this project expects

```powershell
[Environment]::SetEnvironmentVariable('UV_CACHE_DIR', 'C:\Users\<you>\dev\uv\cache', 'User')
[Environment]::SetEnvironmentVariable('UV_PROJECT_ENVIRONMENT', "$env:USERPROFILE\miniforge3\envs\ccwebdb", 'User')
```

- `UV_CACHE_DIR` — keeps uv's package and interpreter cache somewhere you chose
  rather than `%LOCALAPPDATA%`.
- `UV_PROJECT_ENVIRONMENT` — **the important one.** It makes `uv sync` /
  `uv add` / `uv run` operate on the conda `ccwebdb` environment instead of
  creating a project-local `.venv`.

> **This variable is machine-wide.** Any *other* uv project on the same machine
> will also target the `ccwebdb` conda env unless you override it. When you
> start an unrelated uv project, set `$env:UV_PROJECT_ENVIRONMENT = '.venv'` in
> that shell first. There is no `pyproject.toml` equivalent — uv rejects
> `project-environment` as a `[tool.uv]` key, so an environment variable is the
> only mechanism.

## 4. Node.js and PostgreSQL (both inside the conda env)

Neither of these is a Python package, so `uv sync` will never prune them.

```powershell
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

```powershell
cd <repo root>
$pw = Join-Path $env:TEMP 'initpw.txt'
Set-Content -Path $pw -Value 'devpassword' -Encoding ascii -NoNewline
initdb -D .pgdata -U postgres --pwfile=$pw --auth-host=scram-sha-256 --encoding=UTF8
Remove-Item $pw -Force
```

`.pgdata/` is gitignored. The "enabling trust authentication for local
connections" warning is harmless on Windows: there are no Unix domain sockets,
so only the `host` rules (`scram-sha-256`) apply.

### Start and stop the server

```powershell
pg_ctl -D .pgdata -l .pgdata\server.log start
pg_ctl -D .pgdata stop
pg_isready -h localhost -p 5432          # health check
```

> In an automated/non-interactive shell, `pg_ctl start` can appear to hang
> because the postmaster inherits stdout. The server *is* running — check with
> `pg_isready` rather than waiting.

### Create the application role and database

```powershell
$env:PGPASSWORD = 'devpassword'
psql -h localhost -U postgres -d postgres -c "CREATE ROLE ccwebdb WITH LOGIN PASSWORD 'devpassword';"
psql -h localhost -U postgres -d postgres -c "CREATE DATABASE ccwebdb OWNER ccwebdb ENCODING 'UTF8';"
```

## 6. Configure the application

```powershell
Copy-Item .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"   # paste into JWT_SECRET
```

`.env` is gitignored; `.env.example` is committed and documents every key.

## 7. Install dependencies

```powershell
cd <repo root>
uv sync                      # Python deps -> the conda env
cd frontend
npm install                  # JS deps -> frontend/node_modules
```

## 8. Create the schema and seed data

```powershell
cd backend
uv run alembic upgrade head
uv run python -m app.seed
```

The seed creates the administrator from `FIRST_ADMIN_EMAIL` /
`FIRST_ADMIN_PASSWORD` plus five sample inventory items. It is idempotent —
re-running it will not duplicate rows.

---

## Running the application

Two terminals, both with `conda activate ccwebdb`:

```powershell
# terminal 1 - API on http://127.0.0.1:8000  (docs at /docs)
cd backend
uv run uvicorn app.main:app --reload --port 8000

# terminal 2 - UI on http://127.0.0.1:5173
cd frontend
npm run dev
```

Vite proxies `/api` to port 8000, so the browser only ever talks to one origin
and CORS is not involved during development.

Default sign-in: `admin@example.com` / `adminpassword`.

---

## Running the tests

```powershell
uv run pytest              # from the repo root
```

The suite creates its own database (`ccwebdb_test`), builds the schema, and
drops it again afterwards, so it never touches development data. Each test runs
inside a transaction that is rolled back, which keeps tests independent.

This requires the application role to be able to create databases — a one-time
grant:

```powershell
$env:PGPASSWORD = 'devpassword'
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
| Add a Python dependency     | `uv add <pkg>` (from repo root)                  |
| Add a dev-only dependency   | `uv add --dev <pkg>`                             |
| Run a Python command        | `uv run <cmd>`                                   |
| Add a JS dependency         | `npm install <pkg>` (from `frontend/`)           |
| New migration               | `cd backend; uv run alembic revision --autogenerate -m "..."` |
| Apply migrations            | `cd backend; uv run alembic upgrade head`        |
| Start / stop the database   | `pg_ctl -D .pgdata -l .pgdata\server.log start` / `... stop` |

---

## Gotchas worth knowing

**Never install Python packages into the conda env with `conda` or `pip`.**
uv and conda share the same `site-packages` because of
`UV_PROJECT_ENVIRONMENT`. A `conda install <python-package>` followed by
`uv sync` will have uv *uninstall* it, because it is not in `uv.lock`. `pip`
itself is exempt (uv treats it as a seed package), but nothing else is. Python
dependencies go in `pyproject.toml`; use conda only for non-Python tools like
Node and Postgres.

**Git Bash rewrites leading slashes.** `conda /info` becomes
`conda "C:/Program Files/Git/info"`. Use `//flag` or prefix the command with
`MSYS_NO_PATHCONV=1` when you need a literal leading slash.

**`jq` is not available on conda-forge for win-64.** Only `xstatic-jquery`
matches a search, which is an unrelated Python package. Parse JSON with Python
in scripts rather than depending on jq.

**Python on Windows writes CRLF to stdout.** If a Python helper feeds a bash
script, `print()` emits `\r\n` and the trailing `\r` silently corrupts the
consuming shell variables. Use `sys.stdout.reconfigure(newline="\n")`.

**Money is `NUMERIC(12, 2)` and maps to `Decimal`.** Never let a price become a
float anywhere in the stack.
