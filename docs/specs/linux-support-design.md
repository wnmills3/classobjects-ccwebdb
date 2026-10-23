# Linux support

Date: 2026-10-21 (last revised)

How the same repository is developed, tested and hosted on Windows 11 and
on **Ubuntu 26.04 LTS** (24.04 is expected to work but is not tested), on a
computer of its own or, for development only, under WSL. Other
distributions and macOS may work; nothing promises it.

## One source for every setting

Every machine- or deployment-specific value -- a port, a host, a folder, a
time of day, a tool location -- is a setting read from the repository's
`.env`, with a default, and listed in `.env.example`. No script writes one
of its own.

- **`backend/app/runtime_settings.py`** holds `RuntimeFields`, the values
  the scripts and the development server need: ports and hosts, the
  cluster folders, the test cluster, the log folder and limits, the backup
  folder and time, the public site's domain, certificate files and site
  root, the media folder. `RuntimeSettings` reads them from `.env`,
  ignoring the application's other keys and validating nothing secret, so
  `ccweb_status` works with no `JWT_SECRET` set. The API's `Settings`
  (`backend/app/config.py`) subclasses `RuntimeFields` and keeps
  `extra="forbid"`, so a misspelt key is refused when the API starts.
- Relative folders are taken from the repository root. An empty value for
  anything but text means the default.
- **`python -m app.runtime_settings --format cmd|sh|json`** prints every
  value as a shell assignment, paths absolute and native to the OS:
  `set "CCWEB_API_PORT=8000"` for cmd, `CCWEB_API_PORT='8000'` plus
  `export` for bash. `ccweb_settings.cmd` runs each line with `for /f`;
  `ccweb_settings.sh` `eval`s the output. A value cmd cannot carry intact
  (`" % !` or a line break) is refused, naming the setting, never the value.
- Values a script needs before Python can run -- the conda environment's
  name and where conda is (`CCWEB_CONDA_ENV`, `CCWEB_CONDA_ROOT`) -- are
  read from `.env` by a few lines of shell in `ccweb_env`, the same way
  pydantic reads them; a test holds the two readers together.
- The development server's four defaults (frontend and API host and port)
  live in `frontend/dev-server.json`, read by both `runtime_settings` and
  `vite.config.js`, so neither language keeps a copy of the other's. Vite
  takes overrides from the environment or the root `.env` (`loadEnv`).
  Client code reads only `VITE_` variables, which are `Settings` fields too.
- `TIME_ZONE` goes through `app/clock.py` (`local_now()`, `local_today()`);
  empty keeps the host's zone, and `tzdata` is a dependency on every OS so
  a named zone always resolves.

| Setting | Default |
|---|---|
| `CCWEB_CONDA_ENV`, `CCWEB_CONDA_ROOT` | `ccwebdb`, found from `CONDA_EXE`, `conda` on `PATH`, or `~/miniforge3` |
| `CCWEB_API_HOST`, `CCWEB_API_PORT` | `127.0.0.1`, `8000` (`frontend/dev-server.json`) |
| `CCWEB_FRONTEND_HOST`, `CCWEB_FRONTEND_PORT` | `127.0.0.1`, `5173` (`frontend/dev-server.json`) |
| `CCWEB_DB_CLUSTER_DIR` | `.pgdata` |
| database host, port, name, user | parsed from `DATABASE_URL` |
| `CCWEB_TEST_DB_CLUSTER_DIR`, `CCWEB_TEST_DB_PORT`, `TEST_DATABASE_URL` | `.pgdata.test`, `5433`, derived from the port |
| `CCWEB_LOG_DIR`, `CCWEB_LOG_KEEP`, `CCWEB_LOG_MAX_BYTES` | `logs`, `3`, 1 GiB |
| `CCWEB_BACKUP_DIR`, `CCWEB_BACKUP_TIME` | `ccwebdb-backup` in the home folder, `20:00` |
| `CCWEB_ARCHIVE_DIR` | `ccwebdb-backups` beside the repository |
| `CCWEB_PGADMIN_PORT`, `CCWEB_SONAR_PORT` | `5050`, `9000` |
| `CCWEB_SITE_DOMAIN`, `CCWEB_TLS_CERT`, `CCWEB_TLS_KEY`, `CCWEB_SITE_ROOT` | empty, empty, empty, `frontend/dist` |
| `CCWEB_CADDY_IMPORT` | empty |
| `MEDIA_ROOT` | `media` |
| `TIME_ZONE` | empty: the host's zone |

## Scripts

- Every `scripts/*.cmd` has a `scripts/*.sh` twin of the same name, modes,
  wording and exit codes; slash options become `--` options (`--lan`,
  `--keepdb`, `--check`, `--nodb`, `--schedule`). `test_repo_contracts.py`
  fails when either lacks its twin, when a script names a port, cluster
  folder, host or backup default of its own, and when a `.sh` is not
  committed LF and executable.
- `.cmd` files are pure cmd, CRLF (`.gitattributes`); `.sh` files are bash,
  LF, and pass `shellcheck -x` with no warning or error.
- `ccweb_env.sh`, `ccweb_settings.sh`, `ccweb_logdir.sh` and
  `ccweb_testdb.sh use` are sourced, because they set the caller's
  environment. The settings are loaded by `ccweb_settings` in each script's
  own scope, never by `ccweb_env`, which people also run in their own shell,
  where the values would then outrank later edits to `.env`.
- Without Python or with a refused setting a script cannot know the
  configured ports and folders, and keeps no literal default to guess with:
  `ccweb_status` reports every service as unknown, `ccweb_shutdown` stops
  what its PID file names and skips the port sweep and the database, and
  `ccweb_backup` reports the failure on its console only.
- On Linux, servers start with `setsid nohup`, their process groups are
  recorded in `.runtime/ccweb.pids`, and shutdown stops each group.
  Port-to-pid lookups use `ss` (Linux) and `netstat` (Windows). Shutdown's
  port sweep stops a listener only when it is the program startup runs
  there; startup takes a listener on the API's port for the API only when
  `/health` answers `{"status":"ok"}`.
- `ccweb_backup.sh --schedule` installs a systemd **user** timer and
  enables lingering, so it runs whether or not anyone is signed in, the
  counterpart of the Windows scheduled task; without a systemd user manager
  it prints the crontab line instead.

## Templates and rendering

Configuration that a server needs is a tracked template:

- `deploy/Caddyfile`, for both systems, with Caddy's own `{$NAME}`
  placeholders: the domain, the site root, the API address,
  `CCWEB_TLS_DIRECTIVE` and `CCWEB_CADDY_IMPORT_DIRECTIVE`.
- `deploy/linux/ccwebdb-postgres.service`, `ccwebdb-api.service`,
  `ccwebdb-backup.service` and `ccwebdb-backup.timer`, with `@NAME@`
  placeholders for the repository, the environment, the account, the
  folders a hardened unit may write, and the settings they need.
- `scripts/pgadmin-servers.json`, the same way.

One command fills any of them on either system:
`python -m app.runtime_settings --render TEMPLATE`, from `backend/`. Its
values are the scripts' own plus a few only templates use: `CCWEB_USER`
(the account running it), `MEDIA_ROOT`, `CCWEB_RUNTIME_DIR` and
`CCWEB_TLS_DIRECTIVE` -- `tls CERT KEY` when both certificate files are set,
nothing when neither is (Caddy then obtains a certificate itself), refused
when only one is -- and `CCWEB_CADDY_IMPORT_DIRECTIVE`, `import PATTERN`
for the server's own other sites when `CCWEB_CADDY_IMPORT` is set, nothing
when it is not. Caddy placeholders get forward slashes, which Caddy wants
on Windows too, and quotes around a value with a space; an empty one other
than the two directives is refused, naming the setting. Caddy therefore reads
no environment of its own on either system, and no unit names `.env`: the
API reads it itself.

Tests hold that every template renders with no placeholder left, that the
Caddyfile rendered with the documented example's values is the one
`deployment-windows.md` shows, and that the units carry their hardening.

## Documentation layout

A document that is a sequence of commands exists once per OS; one that is
the same on both is shared:

| Shared | Windows | Linux |
|---|---|---|
| README, `system-administration.md`, database design, reference data, specs, `code-quality.md`, `logs/README.md` | `environment-setup-windows.md` | `environment-setup-linux.md` |
| | `runtime-operations-windows.md` | `runtime-operations-linux.md` |
| | `deployment-windows.md` | `deployment-linux.md` |

- Command blocks are fenced ```` ```console ```` and paste into their
  shell as written, one command per line; a block that depends on its
  folder starts by changing to it, and anything that prompts stands alone
  in its block (`CLAUDE.md`, *Scripts and shell*).
- A shared document shows a block that differs by OS in both forms, each
  labelled; anything longer is in the OS documents and linked.
- The per-OS documents describe "a server", "your domain", "a backup
  folder"; an installation's own values appear only as labelled examples.
