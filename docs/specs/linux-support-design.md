# Linux support

2026-10-20 · GitHub issue #2

## Why

The repository was written for Windows 11 alone: every script is cmd, every
documented command is cmd, and the hosting guide builds one particular
Windows server. The owner wants the same code and the same documentation to
let anyone develop, test and host the system on **Ubuntu 26.04 LTS** (the current LTS; 24.04 is
expected to work but is not tested), on a
computer of its own or under WSL, starting from a fresh install and without
ever having seen the Windows setup. The owner may use it that way too.

## Goals

- Development on Ubuntu: environment, database cluster, dev stack, the test
  cluster, the quality gate, SonarQube.
- Hosting on Ubuntu: PostgreSQL, the API and Caddy as systemd services; the
  routine backup as a systemd timer.
- **Nothing hard-coded.** Every machine- or deployment-specific value -- a
  port, a host, a folder, a time zone, a tool location -- is a setting read
  from `.env`, with a default, and documented in `.env.example`.
- Paths resolve for the OS the code runs on.
- Documentation a stranger can follow on either OS.

## Non-goals

- Distributions other than Ubuntu LTS. They may well work; nothing promises
  it.
- Syncing a database between computers on Linux. The OneDrive shared
  cluster is described as one example installation on Windows, nothing more.
- Public hosting from WSL. WSL is for development; hosting is on a real
  Ubuntu machine.
- macOS.

## Anti-goals: what would make this a failure even if Linux worked

- Any change in what the Windows scripts do, what they print, or where they
  put things, on a machine that sets no new setting. The running Windows
  deployment, the development computer, the gate, the scheduled backup and
  the API task must all carry on unchanged.
- Two copies of a value (a port in a script and again in Python) that can
  drift apart.
- Windows documents that read worse than they do now.

## Decided by the owner

- Every `scripts\*.cmd` stays; a bash `scripts/*.sh` is added beside each.
- Paths in code resolve by OS.
- Locations and other machine values become `.env` settings; the owner's own
  `.env` files gain the new variables where the defaults would not describe
  their installation.
- Linux content lives in new documents parallel to the Windows ones. The
  Windows documents become generic: they describe "a server", "your domain",
  "a backup folder", and use the owner's installation (OneDrive,
  classobjects.com) only as clearly labelled examples.
- Ubuntu LTS; hosting mirrors the Windows layout with systemd.

## Design

### 1. One source for every setting

`backend/app/config.py` gains the missing settings. The scripts get their
values from the same Python, never from literals of their own:

- **`app/runtime_settings.py`** -- a `RuntimeSettings` class holding only
  what the scripts and the dev server need (ports, hosts, folders, the test
  cluster). It reads the same `.env`, ignores the application's other keys,
  and validates nothing secret, so `ccweb_status` works with no `JWT_SECRET`
  set. `Settings` keeps `extra="forbid"` and declares the same fields, so a
  misspelt key is still refused at API start-up; one test pins that every
  `RuntimeSettings` field is also a `Settings` field.
- **`python -m app.runtime_settings --format cmd|sh`** prints every value as
  a shell assignment (`set "CCWEB_API_PORT=8000"` / `CCWEB_API_PORT='8000'`
  plus `export`), paths made absolute and native to the OS. A `.cmd` reads it
  with `for /f`, a `.sh` with `eval`. Values a script needs before Python can
  run -- the conda environment's name and where conda is -- are read from
  `.env` by a few lines of shell in `ccweb_env`.
- Every new setting's default is today's behaviour, so a `.env` that sets
  none of them changes nothing.

The settings (names final in the plan; shown with today's value as default):

| Setting | Default | Replaces |
|---|---|---|
| `CCWEB_CONDA_ENV`, `CCWEB_CONDA_ROOT` | `ccwebdb`, found from `CONDA_EXE` / `~/miniforge3` | literals in `ccweb_env` |
| `CCWEB_API_HOST`, `CCWEB_API_PORT` | `127.0.0.1`, `8000` | startup/shutdown/status, Vite proxy, docs |
| `CCWEB_FRONTEND_HOST`, `CCWEB_FRONTEND_PORT` | `127.0.0.1`, `5173` | scripts, `vite.config.js`, `CORS_ORIGINS` default |
| `CCWEB_DB_CLUSTER_DIR` | `.pgdata` | scripts |
| database host, port, name, user | parsed from `DATABASE_URL` | `ccweb_psql` defaults, port checks |
| `CCWEB_TEST_DB_CLUSTER_DIR`, `CCWEB_TEST_DB_PORT`, `TEST_DATABASE_URL` | `.pgdata.test`, `5433`, derived | `ccweb_testdb` literals |
| `CCWEB_LOG_DIR`, `CCWEB_LOG_KEEP`, `CCWEB_LOG_MAX_BYTES` | `logs`, `3`, 1 GiB | env-only today |
| `CCWEB_BACKUP_DIR` | `~/OneDrive/coins_backup` on Windows; on Linux `~/ccwebdb-backup` | `ccweb_backup` literal |
| `CCWEB_BACKUP_TIME` | `20:00` | `ccweb_backup` literal |
| `CCWEB_ARCHIVE_DIR` | `<repo>/../ccwebdb-backups` | two code defaults in `media_backup`, `workbook_backup` |
| `TIME_ZONE` | empty = the host's local zone | `date.today()` / `datetime.now()` in reports and stamps |
| `CCWEB_PGADMIN_PORT`, `CCWEB_SONAR_PORT` | `5050`, `9000` | pgAdmin and SonarQube scripts, `pgadmin-servers.json` |
| `MEDIA_ROOT`, `PHOTO_LIBRARY_ROOT`, `CORS_ORIGINS`, ... | as now | existing, but missing from `.env.example` |

Relative paths are taken from the repository root, as `CCWEB_LOG_DIR` is
today. The backup default differs by OS only because OneDrive is a Windows
default today; the owner's two `.env` files set it explicitly so the
default can become generic later without touching them.

`TIME_ZONE` goes through one helper (`app/clock.py`: `local_now()`,
`local_today()`) that every local-time call uses; empty keeps the host's
zone, so Windows is unchanged and a UTC Linux server can name
`America/New_York`. `tzdata` becomes a dependency on every OS so a named zone
always resolves.

Vite reads the frontend and API host and port from the environment (the
scripts export them) or the root `.env` (`loadEnv`, `envDir: '..'`), and
falls back to `frontend/dev-server.json`, the file `runtime_settings` takes
the same four defaults from, so neither language keeps a copy of the
other's. Client code reads only `VITE_` variables; the
DigiCert seal (tied to one certificate) becomes `VITE_DIGICERT_SEAL_ID`, and
with none set the shop shows no seal. The owner's `.env` sets it.

Every key the repository's `.env` may hold is a `Settings` field, the
Caddy and Vite ones included (`CCWEB_SITE_DOMAIN`, `CCWEB_TLS_CERT`,
`CCWEB_TLS_KEY`, `VITE_DIGICERT_SEAL_ID`), so `extra="forbid"` keeps
refusing a misspelt key and nothing needs a second file.

### 2. Scripts

- `.cmd` files keep their names, arguments, output and exit codes. Their
  literals are replaced by the values `runtime_settings` prints; that is the
  only change. Where Python cannot run at all -- no conda environment --
  or `.env` holds a value it refuses, a script cannot know the configured
  ports and folders, and no literal default is kept in a script to guess
  with. So in those failure modes, and only there, the output changes:
  `ccweb_status` reports every service "UNKNOWN - no environment to ask
  with" (or "- a setting in .env was refused") with no address;
  `ccweb_shutdown` stops what its PID file names and says it skipped the
  port sweep and the database; `ccweb_backup` reports the failure on its
  console without a `backup.log` line, since the log folder is a setting.
  The scripts' "see docs\..." lines name the renamed documents.
- Each `.sh` matches its `.cmd`: same name, same modes, same output wording
  where it applies, same exit codes. Slash options have `--` forms only
  (`--lan`, `--keepdb`, `--check`, `--nodb`, `--schedule`).
- `ccweb_env.sh`, `ccweb_settings.sh`, `ccweb_logdir.sh` and
  `ccweb_testdb.sh use` are sourced (`. scripts/ccweb_env.sh`), because
  they set the caller's environment. The settings are loaded by
  `ccweb_settings` in each script's own scope, never by `ccweb_env`, which
  people also run in their own shell, where the values would then outrank
  later edits to `.env`.
- Detached processes start with `setsid nohup`, their pids recorded in
  `.runtime/ccweb.pids` as the cmd scripts do; shutdown kills the process
  group. Port-to-pid lookups use `ss`.
- `ccweb_backup.sh --schedule` installs a systemd **user** timer
  (`ccwebdb-backup.timer`) and enables lingering so it runs signed out --
  the counterpart of the signed-out scheduled task -- or explains how to use
  cron where systemd is absent.
- Repeated logic (wait for a port, pid on a port) stays in each shell
  family; it is not moved into Python unless a script would otherwise grow
  a second copy of a value.
- `.gitattributes` pins `*.sh text eol=lf`; the files are committed
  executable.
- `.mcp.json` and the `.claude` hooks are machine-local and gitignored; the
  docs say what a Linux machine puts in them.

### 3. Deployment artefacts

Tracked templates replace configuration that today exists only as text in a
document:

- `deploy/Caddyfile` -- one file for both OSes, using Caddy's `{$VAR}`
  placeholders for the domain, the frontend folder, the certificate and
  key, and the API address, so the template is valid Caddy by itself.
- `deploy/linux/ccwebdb-postgres.service`, `ccwebdb-api.service`,
  `ccwebdb-backup.service`, `ccwebdb-backup.timer` -- with `@NAME@`
  placeholders for the repository, the environment, the account and the
  settings they need.

Every template is filled by one command,
`python -m app.runtime_settings --render TEMPLATE`, on both systems: Caddy's
placeholders with forward slashes, which Caddy wants on Windows too. Caddy
therefore reads no environment of its own on either system -- the Windows
service has none, and apt's `caddy.service` is left as installed -- and no
unit names `.env`: the API reads it itself, and a second reader with
systemd's different rules for quotes and `#` would be a second opinion of
one file. A template rendered with the defaults leaves no placeholder
behind, and the Caddyfile rendered with the owner's values is exactly the
one `deployment-windows.md` shows; tests hold both.

The running Windows server is not re-deployed. Its hand-written
`C:\caddy\Caddyfile` is what `deploy/Caddyfile` renders to with its values,
and `deployment-windows.md` renders it the same way for a new server.

### 3a. The owner's two installations

The owner asked for their `.env` files to gain the new variables, so the
builder edits them: on this computer, and on the server over SSH. Values
are written by a script, never printed. Order: the server's `.env` gains
`VITE_DIGICERT_SEAL_ID` and `CCWEB_BACKUP_DIR` (its OneDrive folder)
**before** the merged code is pulled and the frontend rebuilt there, and the
rebuilt shop is checked for the seal; then the API is restarted and the
scheduled backup's next run is checked in `backup.log`. Before-and-after
script output on Windows is captured on this computer with its real `.env`,
and the empty-`.env` comparison runs from a scratch copy of the repository.

### 4. Documentation

Parallel documents, one per OS, for everything that is a sequence of
commands; shared documents for what is the same on both:

| Shared (generic) | Windows | Linux |
|---|---|---|
| README, project purpose, database design, reference data, workflow, specs | `environment-setup-windows.md` | `environment-setup-linux.md` |
| `system-administration.md` (the console, passes, backups as a concept) | `runtime-operations-windows.md` | `runtime-operations-linux.md` |
| `code-quality.md`, `logs/README.md` | `deployment-windows.md` | `deployment-linux.md` |

- The three Windows documents are the current ones, renamed, with the
  owner's installation turned into labelled examples ("for example, a
  server named classobjects serving classobjects.com"; "for example, a
  folder in OneDrive"). Every link in the repository is updated.
- Shared documents keep commands that are identical in both shells
  (`python -m app...`, `npm ...`, `git ...`). Where a block differs only by
  OS (setting a variable, a folder path) the shared document shows the
  Windows and Linux forms one after the other, each labelled; anything
  longer moves to the OS document and is linked.
- Linux command blocks are fenced ```` ```console ```` like the Windows ones
  (the owner's viewer offers copy only there) and paste into bash as
  written; the cmd paste rules get their bash counterparts in `CLAUDE.md`.
- `CLAUDE.md`'s "cmd only" rule becomes "cmd on Windows (no PowerShell),
  bash on Linux".

### 5. Proving it

- **Windows unchanged:** the gate green; for every `.cmd`, its output and exit
  code before and after the change compared on this machine (status, testdb,
  check, psql, backup `/scheduled`, startup/shutdown while the stack can
  run); `runtime_settings --format cmd` with an empty `.env` printing exactly
  today's literals, pinned by a test.
- **Linux working:** the WSL Ubuntu 26.04 already on this computer,
  in a fresh home folder or a new distribution if that one is not clean, set up by following `environment-setup-linux.md`
  from nothing as a stranger would, then the gate, the dev stack, the test
  cluster and a scratch-restored collection; hosting checked as far as WSL
  allows (systemd services up, Caddy serving on localhost), the remainder
  stated as unproved.
- Tests that pin OS-specific behaviour run on both, or skip with the reason.

## Left to the builder

- Exact names of settings beyond those above, and the split between
  `RuntimeSettings` fields and application-only fields.
- How each `.sh` is structured internally, so long as the behaviour matches.
- The order of the work, with one constraint: settings first, then `.cmd`
  migration with the Windows proof, then `.sh`, then documentation, then the
  Linux proof.
