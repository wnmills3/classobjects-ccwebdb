# Development environment setup on Linux

How to build the `ccwebdb` development environment on a clean Ubuntu
machine, from nothing to a running shop and management console: the tools,
the local PostgreSQL cluster, the configuration, and filling the database.
It is the Linux counterpart of
[environment-setup-windows.md](environment-setup-windows.md), with the same
steps in the same order, so a step number means the same thing in both.

Tested on **Ubuntu 26.04 LTS**, on a computer of its own and under WSL; 24.04
is expected to work. Everything is installed **per-user or
per-environment**; only step 0 needs `sudo`. All commands are **bash**, and
each block pastes into a terminal as written. For day-to-day starting and
stopping once set up, see
[runtime-operations-linux.md](runtime-operations-linux.md).

Versions in use; newer patch releases should be fine:

| Component  | Version   | Provided by                     |
| ---------- | --------- | ------------------------------- |
| Miniforge3 | 26.5.3    | its installer                   |
| Python     | 3.13.15   | conda env `ccwebdb`             |
| uv         | 0.12.9    | its installer                   |
| Node.js    | 24.19.0   | conda env `ccwebdb`             |
| PostgreSQL | 18.6      | conda env `ccwebdb`             |
| GitHub CLI | 2.100.0   | conda env `ccwebdb`             |

**Under WSL**, keep the repository in the Linux home folder (`~/dev/...`),
never under `/mnt/c`: files there are reached through a slow translation
layer, and PostgreSQL refuses a data folder whose permissions Windows
controls.

---

## 0. The system's own tools

`git` to fetch the repository, `curl` to fetch the installers, and `iproute2`
for `ss`, which the scripts use to see which program holds a port. Most
Ubuntu installations have them already; this installs whatever is missing
and changes nothing that is there:

```console
sudo apt-get update
sudo apt-get install -y git curl iproute2 rsync
```

## 1. Miniforge3

Miniforge is conda configured for the `conda-forge` channel only, so there are
no Anaconda Terms-of-Service prompts.

**First check whether it is already installed:**

```console
if [ -x ~/miniforge3/bin/conda ]; then ~/miniforge3/bin/conda --version; else echo "Miniforge is not installed in ~/miniforge3"; fi
```

- **It prints a version:** Miniforge is installed. Skip the install below
  and bring conda up to date:

  ```console
  ~/miniforge3/bin/conda update -n base conda
  ```

- **It is not installed:** install it, into `~/miniforge3`:

  ```console
  curl -fsSLo /tmp/Miniforge3.sh "https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-$(uname)-$(uname -m).sh"
  bash /tmp/Miniforge3.sh -b -p ~/miniforge3
  rm /tmp/Miniforge3.sh
  ```

Make `conda` available to your shells. This adds conda's hook to
`~/.bashrc`; run again, it reports `no change`:

```console
~/miniforge3/bin/conda init bash
```

**Open a new terminal** before continuing.

## 2. The `ccwebdb` conda environment

First check whether it exists:

```console
if [ -x ~/miniforge3/envs/ccwebdb/bin/python ]; then echo "The ccwebdb environment exists: skip creating it"; else echo "No ccwebdb environment yet: create it below"; fi
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
[runtime-operations-linux.md](runtime-operations-linux.md)); activate it by
hand only for commands you type yourself.

## 3. uv

uv owns Python dependency management for this project. Check first whether
it is installed; this prints its version if it is:

```console
uv --version
```

If bash says `uv: command not found`, install it with Astral's installer,
which puts `uv` in `~/.local/bin`:

```console
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then open a new terminal, so `~/.local/bin` is on `PATH`.

### Two environment variables

Add them to `~/.bashrc`, so every new terminal has them. Run this in a
terminal where `ccwebdb` is active, so the path comes from the activated
environment; it adds each line only once:

```console
conda activate ccwebdb
grep -q UV_PROJECT_ENVIRONMENT ~/.bashrc || echo "export UV_PROJECT_ENVIRONMENT=\"$CONDA_PREFIX\"" >> ~/.bashrc
grep -q UV_CACHE_DIR ~/.bashrc || echo 'export UV_CACHE_DIR="$HOME/dev/uv/cache"' >> ~/.bashrc
```

- `UV_PROJECT_ENVIRONMENT` -- **the important one.** It makes `uv sync`,
  `uv add` and `uv run` operate on the conda `ccwebdb` environment instead of
  creating a project-local `.venv`.
- `UV_CACHE_DIR` -- keeps uv's cache somewhere you chose. Any folder will
  do; the one above is only a suggestion.

They take effect in **new** terminals. Check with
`tail -n 2 ~/.bashrc`.

> **`UV_PROJECT_ENVIRONMENT` applies to every uv project on the machine.** For
> an unrelated uv project, run `export UV_PROJECT_ENVIRONMENT=.venv` in that
> terminal first.

## 4. Node.js, PostgreSQL and the GitHub CLI (inside the conda env)

None of these is a Python package, so `uv sync` never prunes them. This line
is the record of what conda owns in `ccwebdb`; Python packages are recorded in
`pyproject.toml` and `uv.lock` and are never installed with conda.

```console
conda install -n ccwebdb -c conda-forge nodejs=24.19.0 postgresql gh=2.100.0
```

On an environment already in use, preview with `--dry-run` first. Pin
`nodejs` to the 24.x LTS line; conda-forge's newest `nodejs` builds have
been pre-releases. They land in `~/miniforge3/envs/ccwebdb/bin`, so they are
on `PATH` only with the environment active. Sign in to GitHub once with
`gh auth login`.

Verify, with the environment active:

```console
node --version
psql --version
gh --version
```

They print `v24.19.0`, `psql (PostgreSQL) 18.6` and `gh version 2.100.0`.

## 5. Fetch the code and create the local database cluster

Clone the repository into `~/dev/classobjects-ccwebdb` (the example's
address is shown; use yours if it differs). From here on, each block of
commands starts by changing to it, so it can be pasted on its own; change
that line if you cloned it elsewhere.

```console
mkdir -p ~/dev
cd ~/dev
if [ -d classobjects-ccwebdb ]; then echo "already cloned"; else git clone https://github.com/wnmills3/classobjects-ccwebdb.git; fi
```

PostgreSQL runs directly from the conda environment -- no Docker, no
system PostgreSQL. First check whether the cluster exists:

```console
if [ -f ~/dev/classobjects-ccwebdb/.pgdata/PG_VERSION ]; then echo "The database cluster exists: skip creating it"; else echo "No database cluster yet: create it below"; fi
```

If it does not, create it (`initdb` refuses a `.pgdata` that is not empty,
so this never overwrites one). Local connections need a password too:
on Linux, unlike Windows, the cluster also listens on a socket file, which
`--auth-local` secures.

```console
cd ~/dev/classobjects-ccwebdb
conda activate ccwebdb
pwfile="$(mktemp)" && echo devpassword > "$pwfile"
initdb -D .pgdata -U postgres --pwfile="$pwfile" --auth-host=scram-sha-256 --auth-local=scram-sha-256 --encoding=UTF8
rm -f "$pwfile"
```

`.pgdata/` is gitignored, and `initdb` makes it readable by you alone
(mode 0700), which PostgreSQL insists on.

Start the server by hand. `./scripts/ccweb_startup.sh` cannot start it yet:
it stops before PostgreSQL unless the dependencies of step 7 are installed.

```console
cd ~/dev/classobjects-ccwebdb
mkdir -p logs
pg_ctl -D .pgdata -l logs/postgres-by-hand.log start
```

Wait until this says `accepting connections`:

```console
pg_isready -h localhost -p 5432
```

Then create the application role and database:

```console
cd ~/dev/classobjects-ccwebdb
export PGPASSWORD=devpassword
psql -h localhost -U postgres -d postgres -c "CREATE ROLE ccwebdb WITH LOGIN PASSWORD 'devpassword' CREATEDB;"
psql -h localhost -U postgres -d postgres -c "CREATE DATABASE ccwebdb OWNER ccwebdb ENCODING 'UTF8';"
```

`CREATEDB` is for the test suite, which creates and drops its own
`ccwebdb_test` database. Run again, the two `CREATE` lines answer
`role "ccwebdb" already exists` and `database "ccwebdb" already exists`,
and change nothing.

The server runs until step 8 stops it, or until the machine restarts; after
a restart, start it again with the `pg_ctl` line above. From the end of step
8 on, `./scripts/ccweb_startup.sh` starts it.

**Under WSL, port 5432 may be taken already.** A PostgreSQL running on the
Windows side is reached from Linux as `localhost:5432` when WSL's networking
is in *mirrored* mode, and then the cluster cannot listen there. Stop the
Windows one, or give this cluster another port: add `port = 5434` to
`.pgdata/postgresql.conf` and use 5434 in `DATABASE_URL` (step 6) and in the
commands above.

## 6. Configure the application

The application reads its settings from a file named `.env` in the
repository folder. Each line is `NAME=value`, with no spaces around the `=`;
a line starting with `#` is a comment. The file holds this machine's
secrets, so it is never committed (git ignores it) and made fresh on each
machine from the committed template, `.env.example`.

**1. Make the file from the template.** An existing `.env` is kept:

```console
cd ~/dev/classobjects-ccwebdb
cp -n .env.example .env
```

**2. Make a sign-in secret.** This prints 64 random characters, different
every time; copy them:

```console
conda activate ccwebdb
python -c "import secrets; print(secrets.token_hex(32))"
```

**3. Open the file in an editor** and fill it in:

```console
cd ~/dev/classobjects-ccwebdb
nano .env
```

| Setting | What to put there |
| --- | --- |
| `JWT_SECRET` | **Required.** The 64 characters from 2, pasted after the `=`. It signs every sign-in, so it is this machine's alone: the API refuses to start without one, with one under 32 characters, or with an example value published in this repository. |
| `FIRST_ADMIN_PASSWORD` | A password of your own, 12 characters or more, for the administrator `app.seed` makes on a **trial installation** (step 8). Restoring a collection does not run `app.seed`, so it may stay empty there. |
| `FIRST_ADMIN_EMAIL` | The trial administrator's sign-in; `admin@example.com` will do. |
| `DATABASE_URL` | Leave it: it names the `ccwebdb` database and the `devpassword` of step 5. Change it only if step 5 used another password or port. |
| `SALES_TAX_RATE`, `SALES_TAX_INCLUDES_SHIPPING` | Your sales tax on purchases, as a fraction (`0.0635` is 6.35%), and whether shipping is taxed (`true` or `false`). |
| `ACCESS_TOKEN_EXPIRE_MINUTES`, `REFRESH_TOKEN_EXPIRE_DAYS` | Leave them. |
| Everything commented out (`# CCWEB_API_PORT=8000` and the rest) | Leave it: each line shows its setting's default. Uncomment one only to change it. On Linux the backup folder's default is `~/ccwebdb-backup`. |

In nano, save with Ctrl+O and Enter, leave with Ctrl+X. Keep the names
exactly as the template spells them: a misspelt name stops the API from
starting rather than being ignored.

**4. Check it** -- after step 7, which installs what it needs. This loads
the settings the way the API does and prints `settings OK`, or an error
naming the setting that is wrong, never a value:

```console
cd ~/dev/classobjects-ccwebdb/backend && uv run python -c "from app.config import settings; print('settings OK')"
```

[system-administration.md](system-administration.md) (*Settings*) lists every
setting and says which must change before anything outside this machine can
reach the system.

## 7. Install dependencies

```console
cd ~/dev/classobjects-ccwebdb
uv sync
cd frontend
npm install
```

Then run step 6's check.

## 8. Fill the database

Two cases, and they are **not interchangeable**. Both need the PostgreSQL
server of step 5; first check that it answers:

```console
pg_isready -h localhost -p 5432
```

`accepting connections` is ready. `no response` means it is not running:
start it with the `pg_ctl` line of step 5.

### A collection, on a new machine

The collection moves as the backup workbook and its photographs: the folder
the backup writes to (`CCWEB_BACKUP_DIR`), made on the machine that holds
the collection by its backup script (`ccweb_backup.cmd` on Windows,
`ccweb_backup.sh` here). Copy that whole folder to this machine first --
with `rsync`, `scp`, or a drive. The example installation keeps its backups
in a OneDrive folder on Windows; under WSL that folder can be read directly,
at `/mnt/c/Users/` and the Windows user name `/OneDrive/coins_backup`.

The same version of the code is needed as on the machine the backup came
from (a workbook loads only into a database at its own migration revision).
In one terminal, name the folder the backup is in -- this is the example's,
read from Windows under WSL; put yours:

```console
BACKUP=/mnt/c/Users/wnmil/OneDrive/coins_backup
```

1. **Make sure the whole folder is there** -- the workbook and all of
   `media`:

   ```console
   ls "$BACKUP"
   ```

2. **Make a new database for it and give it the schema.** The import never
   loads into the database `.env` names, so the collection goes into
   `ccwebdb_restored` first:

   ```console
   cd ~/dev/classobjects-ccwebdb/backend
   export PGPASSWORD=devpassword
   psql -h localhost -U ccwebdb -d postgres -c "CREATE DATABASE ccwebdb_restored OWNER ccwebdb ENCODING 'UTF8';"
   DATABASE_URL=postgresql+psycopg://ccwebdb:devpassword@localhost:5432/ccwebdb_restored uv run alembic upgrade head
   ```

3. **Load the newest workbook into it.** The first line finds it; the
   import checks every link in the workbook before writing anything, and
   refuses the whole workbook if one points at nothing:

   ```console
   cd ~/dev/classobjects-ccwebdb/backend
   WORKBOOK="$(ls -t "$BACKUP"/ccwebdb_*.xlsx | head -n 1)" && echo "$WORKBOOK"
   uv run python -m app.workbook_backup import "$WORKBOOK" --to postgresql+psycopg://ccwebdb:devpassword@localhost:5432/ccwebdb_restored
   ```

4. **Put it in place as `ccwebdb`**, the name `.env` and the scripts use.
   This drops the empty `ccwebdb` made in step 5 and renames the restored
   one -- but only when `ccwebdb` holds no tables, so it never drops a
   database with anything in it:

   ```console
   export PGPASSWORD=devpassword
   if [ "$(psql -h localhost -U ccwebdb -d ccwebdb -tAc "select count(*) from information_schema.tables where table_schema = 'public'")" = "0" ]; then psql -h localhost -U ccwebdb -d postgres -c "DROP DATABASE ccwebdb;" && psql -h localhost -U ccwebdb -d postgres -c "ALTER DATABASE ccwebdb_restored RENAME TO ccwebdb;"; else echo "ccwebdb already holds tables: nothing was dropped"; fi
   ```

   It prints `DROP DATABASE` and `ALTER DATABASE`. If it says `ccwebdb`
   already holds tables, something was loaded into it already: look before
   going on.

5. **Copy the photographs** into the folder the application keeps them in
   (`media` in the repository, unless `MEDIA_ROOT` in `.env` names
   another), then check every one against its record:

   ```console
   rsync -a "$BACKUP/media/" ~/dev/classobjects-ccwebdb/media/
   ```

   ```console
   cd ~/dev/classobjects-ccwebdb/backend
   uv run python -m app.media_backup check ~/dev/classobjects-ccwebdb/media
   ```

   The check names any photograph missing or not matching its record.

6. **Stop the hand-started server** (step 5) and start everything the usual
   way (*Running the application*, below):

   ```console
   cd ~/dev/classobjects-ccwebdb
   pg_ctl -D .pgdata -m fast stop
   ```

The restore is then complete: **skip *A trial installation* below.** Sign
in with an account from the old machine -- the restored database holds its
own accounts. **Do not run `app.seed`.**

### A trial installation, with demo data

```console
cd ~/dev/classobjects-ccwebdb/backend
uv run alembic upgrade head
uv run python -m app.seeding load
uv run python -m app.seed
```

`app.seeding load` loads the reference vocabularies from
`backend/data/reference/`. `app.seed` creates the administrator from
`FIRST_ADMIN_EMAIL` / `FIRST_ADMIN_PASSWORD` **plus five demo items with shop
listings**. Both are idempotent. Never run `app.seed` against a real
collection's database: it refuses one that already holds a purchase, or an
item other than the five demo ones, and writes nothing.

---

## Running the application

Stop a server still running from step 5 (`pg_ctl -D .pgdata -m fast stop`),
then:

```console
cd ~/dev/classobjects-ccwebdb
./scripts/ccweb_startup.sh
```

The addresses are in [runtime-operations-linux.md](runtime-operations-linux.md)
(*Quick reference*); under WSL, a browser on Windows reaches them at the
same `http://127.0.0.1:...` addresses. Sign in to the management console
with a manager account: on a trial installation, the administrator from
`.env`; on a restored one, an account from the backup.

## Running the tests

```console
cd ~/dev/classobjects-ccwebdb
./scripts/ccweb_check.sh
```

runs every gate ([code-quality.md](code-quality.md)). The Python suite
creates `ccwebdb_test`, builds the schema, runs each test in a rolled-back
transaction, and drops the database afterwards. A cluster of the
computer's own for the tests, apart from the collection's, is
`./scripts/ccweb_testdb.sh` ([runtime-operations-linux.md](runtime-operations-linux.md),
*A cluster for the tests*). Never run two pytest sessions at once: they
share `ccwebdb_test`, and the resulting failures look like real bugs.

---

## Changing the code

**Setup is finished at step 8; nothing in this section is part of it.** The
commands for adding a dependency or a migration are the same in bash as in
cmd: see [environment-setup-windows.md](environment-setup-windows.md),
*Changing the code*, with `~/dev/classobjects-ccwebdb` for the folder.

---

## Gotchas

**Never install Python packages into the conda env with `conda` or `pip`.**
uv and conda share `site-packages` through `UV_PROJECT_ENVIRONMENT`, and
`uv sync` uninstalls anything not in `uv.lock`. Use conda only for non-Python
tools.

**`ccweb_env.sh`, `ccweb_settings.sh` and `ccweb_testdb.sh use` are
sourced** (`. scripts/ccweb_env.sh`), because they set your shell's
variables; the other scripts are run (`./scripts/ccweb_status.sh`). Each
says so if used the other way.

**A script checked out with Windows line endings fails** with
`$'\r': command not found`. `.gitattributes` keeps every `.sh` LF, so this
happens only to a file copied in by other means; `git checkout -- FILE`
restores it.

**Under WSL, keep `systemd` on** (the default in current Ubuntu images), or
`ccweb_backup.sh --schedule` has no timer to install; it then prints the
crontab line to use instead.
