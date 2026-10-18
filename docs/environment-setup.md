# Development environment setup

How to build the `ccwebdb` development environment on a clean Windows machine,
from nothing to a running shop and management console: the tools, the local
PostgreSQL cluster, the configuration, and filling the database. It is for a
developer setting up a machine, or rebuilding one. Everything is installed
**per-user or per-environment**; nothing needs administrator rights, and
nothing lands machine-wide where another project could disturb it.

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

**First check whether it is already installed.** This finds Miniforge in its
usual place, whether or not your shells can run `conda` yet:

```console
if exist "%USERPROFILE%\miniforge3\Scripts\conda.exe" ("%USERPROFILE%\miniforge3\Scripts\conda.exe" --version) else (echo Miniforge is not installed in %USERPROFILE%\miniforge3)
```

and this lists every `conda` your shells can run now, from any installation:

```console
where conda
```

- **It prints a version (`conda 26.9.3`):** Miniforge is installed. Skip the
  install below, bring conda up to date, and go on to `conda init`. Preview
  the update, then make it:

  ```console
  "%USERPROFILE%\miniforge3\Scripts\conda.exe" update -n base conda --dry-run
  ```

  ```console
  "%USERPROFILE%\miniforge3\Scripts\conda.exe" update -n base conda
  ```

  Update conda this way, not with `winget upgrade`: winget remembers the
  installer it ran, not the conda since updated in place, and its "upgrade"
  reinstalls the base environment from an installer that can be older than
  the conda you have -- rolling it back.
- **It says Miniforge is not installed, and `where conda` finds nothing:**
  install it below.
- **It says Miniforge is not installed, but `where conda` names another
  folder** (an Anaconda or Miniconda installation): install Miniforge below
  anyway. Its `conda init` makes Miniforge the conda new windows use; the
  other installation can stay or be uninstalled.

```console
winget install --id CondaForge.Miniforge3 --source winget --scope user --accept-package-agreements --accept-source-agreements
```

On a machine where it is already installed, winget answers "Found an
existing package already installed. Trying to upgrade the installed
package..." and, with nothing newer, "No available upgrade found" and an
error code (-1978335189, 0x8A15002B). Nothing was changed; it is not a
failure. When a newer installer exists, though, winget runs
it as an upgrade -- which is why an installed Miniforge is updated with
`conda update` above, not installed again.

It installs to `%USERPROFILE%\miniforge3`. Make `conda` available to your
shells:

```console
"%USERPROFILE%\miniforge3\Scripts\conda.exe" init cmd.exe bash
```

For cmd this adds an AutoRun entry under
`HKCU\Software\Microsoft\Command Processor` that loads conda's hook in every
new cmd window. Reverse it with `conda init --reverse cmd.exe bash`. Run
again, it reports `no change` for what is already set up. **Open a
new terminal** before continuing.

## 2. The `ccwebdb` conda environment

First check whether it exists:

```console
if exist "%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" (echo The ccwebdb environment exists: skip creating it) else (echo No ccwebdb environment yet: create it below)
```

If it does not, create it:

```console
conda create -n ccwebdb python=3.13
```

**If conda asks to remove an existing environment, answer `n`.** It asks
that when `ccwebdb` already exists, and `y` deletes the environment with
everything installed in it.

Then, either way:

```console
conda activate ccwebdb
```

The project scripts activate it themselves (see
[runtime-operations.md](runtime-operations.md)); activate it by hand only for
commands you type yourself.

## 3. uv

uv owns Python dependency management for this project.

Check first whether it is installed; this prints its version if it is:

```console
uv --version
```

If cmd says `uv` is not recognized, install it:

```console
winget install --id astral-sh.uv --source winget --accept-package-agreements --accept-source-agreements
```

On a machine where it is already installed, winget answers "Found an
existing package already installed. Trying to upgrade the installed
package..." and, with nothing newer, "No available upgrade found" and an
error code (-1978335189, 0x8A15002B). Nothing was changed; it is not a
failure.

Astral's own installer is a PowerShell script, so winget is the cmd-native
route. Update the same way, `winget upgrade astral-sh.uv`.

### Two environment variables

Run these in **cmd** with `ccwebdb` active, so the paths come from the
activated environment:

```console
conda activate ccwebdb
setx UV_PROJECT_ENVIRONMENT "%CONDA_PREFIX%"
setx UV_CACHE_DIR "%USERPROFILE%\dev\uv\cache"
```

- `UV_PROJECT_ENVIRONMENT` -- **the important one.** It makes `uv sync`,
  `uv add` and `uv run` operate on the conda `ccwebdb` environment instead of
  creating a project-local `.venv`.
- `UV_CACHE_DIR` -- keeps uv's cache somewhere you chose rather than
  `%LOCALAPPDATA%`. Any folder will do; the one above is only a suggestion.

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

```console
conda install -n ccwebdb -c conda-forge nodejs=24.19.0 postgresql gh=2.100.0
```

On an environment already in use, preview with `--dry-run` first: a conda
solve can change packages the running servers have loaded, and the plan
should show only what you asked for.

Pin `nodejs` to the 24.x LTS line; conda-forge's newest `nodejs` builds have
been pre-releases. `gh` lands in `envs\ccwebdb\Library\bin`, so it is on
`PATH` only with the environment active. Sign in once with `gh auth login`.

Verify, with the environment active:

```console
node --version
psql --version
gh --version
```

They print `v24.19.0`, `psql (PostgreSQL) 18.6` and `gh version 2.100.0`.

## 5. Create the local database cluster

PostgreSQL runs directly from the conda environment -- no Docker. From here on,
each block of commands starts by changing to the repository, at
`%USERPROFILE%\dev\classobjects-ccwebdb`, so it can be pasted on its own; change
that line if you cloned it elsewhere.

First check whether the cluster exists:

```console
if exist "%USERPROFILE%\dev\classobjects-ccwebdb\.pgdata\PG_VERSION" (echo The database cluster exists: skip creating it) else (echo No database cluster yet: create it below)
```

If it does not, create it (`initdb` refuses a `.pgdata` that is not empty,
so this never overwrites one):

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
echo devpassword>"%TEMP%\initpw.txt"
initdb -D .pgdata -U postgres --pwfile="%TEMP%\initpw.txt" --auth-host=scram-sha-256 --encoding=UTF8
del "%TEMP%\initpw.txt"
```

Write the password file with **no space before the `>`**, or the password
gains a trailing space. `.pgdata\` is gitignored. The "enabling trust
authentication for local connections" warning is harmless on Windows: there
are no Unix domain sockets, so only the `host` rules apply.

Start the server by hand, then create the application role and database.
`.\scripts\ccweb_startup.cmd` cannot start it yet: it stops before
PostgreSQL unless the dependencies of step 7 are installed.

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
pg_ctl -D .pgdata -l logs\postgres-by-hand.log start
set "PGPASSWORD=devpassword"
psql -h localhost -U postgres -d postgres -c "CREATE ROLE ccwebdb WITH LOGIN PASSWORD 'devpassword' CREATEDB;"
psql -h localhost -U postgres -d postgres -c "CREATE DATABASE ccwebdb OWNER ccwebdb ENCODING 'UTF8';"
```

`CREATEDB` is for the test suite, which creates and drops its own
`ccwebdb_test` database. On an existing role: `ALTER ROLE ccwebdb CREATEDB;`.
Run again, the two `CREATE` lines answer `role "ccwebdb" already exists` and
`database "ccwebdb" already exists`, and change nothing; `pg_ctl` answers
`another server might be running` when the server is already up.

A server started this way belongs to this window and breaks when the window
closes ([runtime-operations.md](runtime-operations.md), *Running a service
by hand*). Leave the window open through step 8, then stop the server with
`pg_ctl -D .pgdata -m fast stop`; from then on `.\scripts\ccweb_startup.cmd`
starts it.

## 6. Configure the application

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
if exist .env (echo .env exists: keeping it) else (copy .env.example .env)
python -c "import secrets; print(secrets.token_hex(32))"
```

An existing `.env` is kept: a plain `copy` would ask to overwrite it, and
the next line pasted would be taken as the answer.

Paste that value into `JWT_SECRET` in `.env`; the API will not start
without one. Set `FIRST_ADMIN_PASSWORD` to a password of your own, of 12
characters or more, before `app.seed` below. `.env` is gitignored;
`.env.example` is committed and holds the keys a new installation sets,
including the sales-tax settings stamped onto each new item.
[system-administration.md](system-administration.md) (*Settings*) lists every
setting and says which must change before anything outside this machine can
reach the system.

## 7. Install dependencies

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
uv sync
cd frontend
npm install
```

## 8. Fill the database

Two cases, and they are **not interchangeable**.

### This collection, on a new machine

The collection moves as the backup workbook and its photographs, through
the folder the backup writes to and OneDrive keeps on both machines:
`%USERPROFILE%\OneDrive\coins_backup`. (A backup kept elsewhere, through
`CCWEB_BACKUP_DIR` -- see `scripts\ccweb_backup.cmd` -- needs that folder in
the commands below instead.)

**On the machine that holds the collection**, take a backup. It writes the
workbook `ccwebdb_` and the date and time `.xlsx`, copies the photographs
into `media`, and proves both against the database before it says so:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
.\scripts\ccweb_backup.cmd
```

It ends `backup proved, in C:\Users\...\OneDrive\coins_backup`. Anything else
is a failed backup; `logs\backup.log` says why.

**On the new machine**, with steps 1 to 7 done and the PostgreSQL server
from step 5 still running in its window, and with the same version of the
code as the machine the backup came from (a workbook loads only into a
database at its own migration revision):

1. **Wait for OneDrive to finish syncing** the folder -- the workbook and
   all of `media`. This lists what has arrived:

   ```console
   dir "%USERPROFILE%\OneDrive\coins_backup"
   ```

2. **Make a new database for it and give it the schema.** The import never
   loads into the database `.env` names, so the collection goes into
   `ccwebdb_restored` first:

   ```console
   cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend
   set "PGPASSWORD=devpassword"
   psql -h localhost -U ccwebdb -d postgres -c "CREATE DATABASE ccwebdb_restored OWNER ccwebdb ENCODING 'UTF8';"
   set "DATABASE_URL=postgresql+psycopg://ccwebdb:devpassword@localhost:5432/ccwebdb_restored"
   uv run alembic upgrade head
   set "DATABASE_URL="
   ```

3. **Load the newest workbook into it.** The first line finds it; the
   import checks every link in the workbook before writing anything, and
   refuses the whole workbook if one points at nothing:

   ```console
   cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend
   for /f "delims=" %F in ('dir /b /od "%USERPROFILE%\OneDrive\coins_backup\ccwebdb_*.xlsx"') do @set "WORKBOOK=%USERPROFILE%\OneDrive\coins_backup\%F"
   echo %WORKBOOK%
   uv run python -m app.workbook_backup import "%WORKBOOK%" --to postgresql+psycopg://ccwebdb:devpassword@localhost:5432/ccwebdb_restored
   ```

4. **Put it in place as `ccwebdb`**, the name `.env` and the scripts use.
   This drops the empty `ccwebdb` made in step 5 and renames the restored
   one -- but only when `ccwebdb` holds no tables, so it never drops a
   database with anything in it:

   ```console
   set "PGPASSWORD=devpassword"
   for /f %N in ('psql -h localhost -U ccwebdb -d ccwebdb -tAc "select count(*) from information_schema.tables where table_schema = 'public'"') do @if "%N"=="0" (psql -h localhost -U ccwebdb -d postgres -c "DROP DATABASE ccwebdb;" && psql -h localhost -U ccwebdb -d postgres -c "ALTER DATABASE ccwebdb_restored RENAME TO ccwebdb;") else (echo ccwebdb already holds tables: nothing was dropped)
   ```

   It prints `DROP DATABASE` and `ALTER DATABASE`. If it says `ccwebdb`
   already holds tables, something was loaded into it already: look before
   going on.

5. **Copy the photographs** into the folder the application keeps them in
   (`media` in the repository, unless `MEDIA_ROOT` in `.env` names
   another), then check every one against its record:

   ```console
   robocopy "%USERPROFILE%\OneDrive\coins_backup\media" "%USERPROFILE%\dev\classobjects-ccwebdb\media" /E
   ```

   ```console
   cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend
   uv run python -m app.media_backup check "%USERPROFILE%\dev\classobjects-ccwebdb\media"
   ```

   `robocopy` reports success with exit codes 0 to 7; 1 means files were
   copied. The check names any photograph missing or not matching its
   record.

6. **Stop the hand-started server** (step 5) and start everything the usual
   way (*Running the application*, below):

   ```console
   cd /d %USERPROFILE%\dev\classobjects-ccwebdb
   pg_ctl -D .pgdata -m fast stop
   ```

Then sign in with an account from the old machine -- the restored database
holds its own accounts. **Do not run `app.seed`.** If a manager account still
has the published password `adminpassword`, change it under **People**
before the machine is reachable from anywhere else.

The steps by which a backup is made and proved, and the other ways to
restore, are in [system-administration.md](system-administration.md)
(*Backing up and restoring*).

### A trial installation, with demo data

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
cd backend
uv run alembic upgrade head
uv run python -m app.seeding load
uv run python -m app.seed
```

`app.seeding load` loads the reference vocabularies from
`backend/data/reference/`. `app.seed` creates the administrator from
`FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` **plus five demo items with shop
listings**. Both are idempotent. Never run `app.seed` against a real
collection's database: it refuses one that already holds a purchase, or an
item other than the five demo ones, and writes nothing, unless
`--into-existing` is given.

---

## Running the application

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
.\scripts\ccweb_startup.cmd
```

The addresses are in [runtime-operations.md](runtime-operations.md)
(*Quick reference*). Sign in to the management console with a manager
account: on a trial installation, the administrator from `.env`; on a
restored one, an account from the backup. Vite
proxies `/api` to port 8000, so the browser talks to one origin and CORS is
not involved during development.

## Running the tests

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
uv run pytest
cd frontend
npm test
```

Or run every gate at once with `.\scripts\ccweb_check.cmd`
([code-quality.md](code-quality.md)). The Python suite creates `ccwebdb_test`,
builds the schema, runs each test in a rolled-back transaction, and drops the
database afterwards; point it elsewhere with `TEST_DATABASE_URL`. Never run
two pytest sessions at once: they share `ccwebdb_test`, and the resulting
failures look like real bugs.

Two suites worth knowing about:

- `test_migrations.py` builds a database purely by `alembic upgrade head`
  (from the squashed `backend/alembic/baseline.sql` onward) and holds it
  against the models, the views and the SQL functions, so a model change
  without a migration fails.
- `test_concurrency.py` drives the order handler from real threads rather
  than `TestClient`, which serializes requests -- a race written against it
  passes even with the row lock removed.

---

## Daily workflow

Starting, stopping and checking the runtime, and the quality gate, are in
[runtime-operations.md](runtime-operations.md) (*Quick reference*).

Add a Python dependency, with its name in place of `PACKAGE`:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
uv add PACKAGE
```

Add a dev-only dependency:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb
uv add --dev PACKAGE
```

Add a JS dependency:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\frontend
npm install PACKAGE
```

Make a new migration, with what it changes in place of `DESCRIPTION`:

```console
cd /d %USERPROFILE%\dev\classobjects-ccwebdb\backend
uv run alembic revision --autogenerate -m "DESCRIPTION"
```

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
fails with *"not recognized"*, and so does `cmd /c ccweb_startup.cmd`.
Include a path: `.\scripts\ccweb_startup.cmd` from the repo root, or
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
