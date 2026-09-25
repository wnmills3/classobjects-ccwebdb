# Development environment setup

How to build the `ccwebdb` development environment on a clean Windows machine.
Everything is installed **per-user or per-environment**; nothing needs
administrator rights.

All commands are **cmd**, not PowerShell. For day-to-day starting and stopping
once set up, see [runtime-operations.md](runtime-operations.md).

Versions in use; newer patch releases should be fine:

| Component  | Version   | Provided by                     |
| ---------- | --------- | ------------------------------- |
| Miniforge3 | 26.5.3    | winget                          |
| Python     | 3.13.15   | conda env `ccwebdb`             |
| uv         | 0.12.9    | winget                          |
| Node.js    | 24.19.0   | conda env `ccwebdb`             |
| PostgreSQL | 18.6      | conda env `ccwebdb`             |
| GitHub CLI | 2.100.0   | conda env `ccwebdb`             |

---

## 1. Miniforge3

Miniforge is conda configured for the `conda-forge` channel only, so there are
no Anaconda Terms-of-Service prompts.

```cmd
winget install --id CondaForge.Miniforge3 --source winget --scope user ^
  --accept-package-agreements --accept-source-agreements
```

It installs to `%USERPROFILE%\miniforge3`. Make `conda` available to your
shells:

```cmd
"%USERPROFILE%\miniforge3\Scripts\conda.exe" init cmd.exe bash
```

For cmd this adds an AutoRun entry under
`HKCU\Software\Microsoft\Command Processor` that loads conda's hook in every
new cmd window. Reverse it with `conda init --reverse cmd.exe bash`. **Open a
new terminal** before continuing.

## 2. The `ccwebdb` conda environment

```cmd
conda create -n ccwebdb python=3.13
conda activate ccwebdb
```

The project scripts activate it themselves (see
[runtime-operations.md](runtime-operations.md)); activate it by hand only for
commands you type yourself.

## 3. uv

uv owns Python dependency management for this project.

```cmd
winget install --id astral-sh.uv --source winget ^
  --accept-package-agreements --accept-source-agreements
```

Astral's own installer is a PowerShell script, so winget is the cmd-native
route. Update the same way, `winget upgrade astral-sh.uv`.

### Two environment variables

Run these in **cmd** with `ccwebdb` active, so the paths come from the
activated environment:

```cmd
conda activate ccwebdb
setx UV_PROJECT_ENVIRONMENT "%CONDA_PREFIX%"
for %I in ("%CONDA_PREFIX%\..\..\..") do setx UV_CACHE_DIR "%~fI\dev\uv\cache"
```

- `UV_PROJECT_ENVIRONMENT` -- **the important one.** It makes `uv sync`,
  `uv add` and `uv run` operate on the conda `ccwebdb` environment instead of
  creating a project-local `.venv`.
- `UV_CACHE_DIR` -- keeps uv's cache somewhere you chose rather than
  `%LOCALAPPDATA%`.

`setx` stores the expanded value and takes effect in **new** shells. Check it
with `reg query HKCU\Environment /v UV_PROJECT_ENVIRONMENT`: it should name the
`ccwebdb` folder and contain no `%`.

> **`UV_PROJECT_ENVIRONMENT` applies to every uv project on the machine.** For
> an unrelated uv project, run `set "UV_PROJECT_ENVIRONMENT=.venv"` in that
> shell first. There is no `pyproject.toml` equivalent -- uv rejects
> `project-environment` as a `[tool.uv]` key.

> **Never `setx` the `PATH`.** It truncates at 1024 characters and flattens
> the `%USERPROFILE%` references `PATH` relies on. Edit `PATH` through *System
> Properties -> Environment Variables*.

## 4. Node.js, PostgreSQL and the GitHub CLI (inside the conda env)

None of these is a Python package, so `uv sync` never prunes them. This line
is the record of what conda owns in `ccwebdb`; Python packages are recorded in
`pyproject.toml` and `uv.lock` and are never installed with conda.

```cmd
conda install -n ccwebdb -c conda-forge nodejs=24.19.0 postgresql gh=2.100.0
```

On an environment already in use, preview with `--dry-run` first: a conda
solve can change packages the running servers have loaded, and the plan
should show only what you asked for.

Pin `nodejs` to the 24.x LTS line; conda-forge's newest `nodejs` builds have
been pre-releases. `gh` lands in `envs\ccwebdb\Library\bin`, so it is on
`PATH` only with the environment active. Sign in once with `gh auth login`.

Verify, with the environment active:

```
node --version     -> v24.19.0
psql --version     -> psql (PostgreSQL) 18.6
gh --version       -> gh version 2.100.0
```

## 5. Create the local database cluster

PostgreSQL runs directly from the conda environment -- no Docker.

```cmd
cd /d <repo root>
echo devpassword>"%TEMP%\initpw.txt"
initdb -D .pgdata -U postgres --pwfile="%TEMP%\initpw.txt" --auth-host=scram-sha-256 --encoding=UTF8
del "%TEMP%\initpw.txt"
```

Write the password file with **no space before the `>`**, or the password
gains a trailing space. `.pgdata\` is gitignored. The "enabling trust
authentication for local connections" warning is harmless on Windows: there
are no Unix domain sockets, so only the `host` rules apply.

Start the server with `scripts\ccweb_startup.cmd` (or by hand, see
[runtime-operations.md](runtime-operations.md)), then create the application
role and database:

```cmd
set "PGPASSWORD=devpassword"
psql -h localhost -U postgres -d postgres -c "CREATE ROLE ccwebdb WITH LOGIN PASSWORD 'devpassword' CREATEDB;"
psql -h localhost -U postgres -d postgres -c "CREATE DATABASE ccwebdb OWNER ccwebdb ENCODING 'UTF8';"
```

`CREATEDB` is for the test suite, which creates and drops its own
`ccwebdb_test` database. On an existing role: `ALTER ROLE ccwebdb CREATEDB;`.

## 6. Configure the application

```cmd
copy .env.example .env
python -c "import secrets; print(secrets.token_hex(32))"
```

Paste that value into `JWT_SECRET` in `.env`. `.env` is gitignored;
`.env.example` is committed and documents every key, including the sales-tax
settings stamped onto each new item.
[system-administration.md](system-administration.md) (*Settings*) says which
must change before anything outside this machine can reach the system.

## 7. Install dependencies

```cmd
cd /d <repo root>
uv sync
cd frontend
npm install
```

## 8. Fill the database

Two cases, and they are **not interchangeable**.

### This collection, on a new machine

Restore the latest verified backup of `ccwebdb` -- a `pg_dump` or a workbook
backup -- into the empty database. The backups live outside the repository;
the procedure is in [system-administration.md](system-administration.md)
(*Backing up and restoring*).

### A trial installation, with demo data

```cmd
cd backend
uv run alembic upgrade head
uv run python -m app.seeding load
uv run python -m app.seed
```

`app.seeding load` loads the reference vocabularies from
`backend/data/reference/`. `app.seed` creates the administrator from
`FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` **plus five demo items with shop
listings**. Both are idempotent. Never run `app.seed` against a real
collection's database.
---

## Running the application

```cmd
scripts\ccweb_startup.cmd
```

| | |
|---|---|
| Shop | http://127.0.0.1:5173 |
| Management console | http://127.0.0.1:5173/management |
| API docs | http://127.0.0.1:8000/docs |

Sign in with the administrator from `.env`. Vite proxies `/api` to port 8000,
so the browser talks to one origin and CORS is not involved during
development.

## Running the tests

```cmd
uv run pytest
cd frontend
npm test
```

Or run every gate at once with `scripts\ccweb_check.cmd`
([code-quality.md](code-quality.md)). The Python suite creates `ccwebdb_test`,
builds the schema, runs each test in a rolled-back transaction, and drops the
database afterwards; point it elsewhere with `TEST_DATABASE_URL`. Never run
two pytest sessions at once: they share `ccwebdb_test`, and the resulting
failures look like real bugs.

Two suites worth knowing about:

- `test_migrations.py` builds a database purely by `alembic upgrade head` and
  asserts autogenerate finds no difference from the models, so a model change
  without a migration fails.
- `test_concurrency.py` drives the order handler from real threads rather
  than `TestClient`, which serialises requests -- a race written against it
  passes even with the row lock removed.

---

## Daily workflow

| Task                        | Command                                          |
| --------------------------- | ------------------------------------------------ |
| See what is running         | `scripts\ccweb_status.cmd`                       |
| Start everything            | `scripts\ccweb_startup.cmd`                      |
| Stop everything             | `scripts\ccweb_shutdown.cmd`                     |
| Run every quality gate      | `scripts\ccweb_check.cmd`                        |
| Add a Python dependency     | `uv add <pkg>` (from repo root)                  |
| Add a dev-only dependency   | `uv add --dev <pkg>`                             |
| Add a JS dependency         | `npm install <pkg>` (from `frontend\`)           |
| New migration               | `cd backend && uv run alembic revision --autogenerate -m "..."` |

A migration reaches the live database only through the release procedure in
[system-administration.md](system-administration.md) (*Applying a schema
release*): verified `pg_dump`, rehearsal on the restore, then
`alembic upgrade head` and `app.seeding load` with the servers stopped. Never
`alembic upgrade` `ccwebdb` casually.

---

## Gotchas

**Never install Python packages into the conda env with `conda` or `pip`.**
uv and conda share `site-packages` through `UV_PROJECT_ENVIRONMENT`, and
`uv sync` uninstalls anything not in `uv.lock`. Use conda only for non-Python
tools.

**A bare script name is not found, even in its own directory.** This machine
sets `NoDefaultCurrentDirectoryInExePath=1`, so `ccweb_startup.cmd` alone
fails with *"not recognized"*. Include a path: `scripts\ccweb_startup.cmd`, or
`.\ccweb_startup.cmd` inside `scripts\`.

**From Git Bash, run a `.cmd` as `cmd //c scripts\\ccweb_check.cmd`.** A
single `/c` is rewritten into a path and cmd silently does nothing, which
reads as success. Git Bash also rewrites leading-slash arguments generally
(use `--keepdb` rather than `/keepdb`) and eats unquoted backslashes.

**Git Bash can leave a stale conda claim.** It rebuilds `PATH` and drops the
environment while `CONDA_DEFAULT_ENV` survives; check which `python` is first
on `PATH`. The project scripts check this themselves.

**Do not build Windows paths with `sed`.** Backslash is an escape in the
replacement. Edit files directly, or use Python with `chr(92)`.

**Python on Windows writes CRLF in text mode.** `print()` feeding a shell
variable leaves a trailing `\r` (use `sys.stdout.reconfigure(newline="\n")`),
and `Path.write_text` produces CRLF files that prettier rejects while
`git status` looks clean (pass `newline="\n"`).

**`jq` is not available on conda-forge for win-64.** Parse JSON with Python.
