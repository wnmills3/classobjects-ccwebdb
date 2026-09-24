# Runtime operations

Starting, stopping and inspecting the development runtime. For first-time
machine setup see [environment-setup.md](environment-setup.md); for backups
and applying a schema release see
[system-administration.md](system-administration.md).

All commands here are **cmd**, not PowerShell.

---

## Quick reference

Run from the repository root:

```cmd
scripts\ccweb_status.cmd               what is running, and what to run next
scripts\ccweb_startup.cmd              start PostgreSQL, the API and Vite
scripts\ccweb_shutdown.cmd             stop everything
scripts\ccweb_shutdown.cmd /keepdb     stop the servers, leave PostgreSQL running
scripts\ccweb_check.cmd [fix]          every quality gate (code-quality.md)
scripts\ccweb_claude.cmd [name]        start Claude Code with the env in play
scripts\ccweb_psql.cmd [psql args]     psql against ccwebdb
scripts\ccweb_pgadmin.cmd              pgAdmin on http://127.0.0.1:5050
```

**No need to activate the conda environment first.** Every script puts
`ccwebdb` in play itself (below). The scripts locate the repository from
their own path, so they work from any directory when called by full path.

> This machine sets `NoDefaultCurrentDirectoryInExePath=1`, so a **bare**
> script name is not found even in its own directory. Always include a path
> separator: `scripts\ccweb_startup.cmd`, or `.\ccweb_startup.cmd` inside
> `scripts\`. From Git Bash use `cmd //c scripts\\ccweb_startup.cmd`, and
> `--keepdb` rather than `/keepdb`.

| | |
|---|---|
| Shop | http://127.0.0.1:5173 |
| Owner console | http://127.0.0.1:5173/owner |
| API | http://127.0.0.1:8000 |
| API docs | http://127.0.0.1:8000/docs |
| Database | localhost:5432/ccwebdb |
| Sign in | the administrator in `.env` (`FIRST_ADMIN_EMAIL`) |

### What is live

- **The frontend is the working tree.** Vite serves source files directly, so
  checking out a branch puts that branch's shop and console in front of
  anyone using them -- before it is merged. Never `git stash` or switch
  branches under a running Vite without meaning to.
- **The backend is the code it started with.** uvicorn runs without
  `--reload`, so a merge or checkout changes nothing until the servers are
  restarted (`ccweb_shutdown.cmd /keepdb`, then `ccweb_startup.cmd`).

---

## The conda environment

Every script calls `scripts\ccweb_env.cmd` before it starts anything. It uses
`ccwebdb` if that is already active and activates it if not; the activation
lasts only as long as the script, so your own shell is left as it was.

"Already active" is checked against `PATH`, not only the variables that
describe it. Git Bash rebuilds `PATH` and drops the conda entries while
`CONDA_DEFAULT_ENV`, `CONDA_PREFIX` and `CONDA_SHLVL` survive, so a shell can
claim `ccwebdb` while a bare `python` is base's. The helper accepts the claim
only when the first `python` on `PATH` is the environment's own.

Activating matters even though the scripts name their programs by full path:
the path fixes *which* python runs, not what environment it installs into or
passes on. uv follows `UV_PROJECT_ENVIRONMENT`, conda follows `CONDA_PREFIX`,
and every server a script starts inherits the same variables.

The helper also fills in `USERPROFILE`, `APPDATA` and `LOCALAPPDATA` when a
shell lacks them (from `HOMEDRIVE`+`HOMEPATH`, then
`%SystemDrive%\Users\%USERNAME%`): without a home directory, `conda activate`
stops with *Could not determine home directory*. Conda is found through
`CONDA_EXE`, then `conda.bat` on `PATH`, then the home directory.

---

## Checking what is running

```cmd
scripts\ccweb_status.cmd
```

Reports the environment and each service, then either the URLs or the command
to start what is missing. It starts and stops nothing.

The first line says whether `ccwebdb` was **active in this shell** or
**activated for this check** -- the second is normal. **NOT AVAILABLE** means
the environment could not be put in play; PostgreSQL is then *UNKNOWN*,
because with no `pg_isready` a failed check looks exactly like "stopped".

Each service is checked **twice** where it can be: a listening port says only
that something holds it. The backend is also asked for
`/api/reference/item_status`, which is public and reads the database, so a 200
means the API is up *and* talking to PostgreSQL; the frontend is asked for
`/owner`. A port held with no answer is reported as **LISTENING but not
answering**, never rounded up to RUNNING.

SonarQube is listed but never counted as down: it is inspected on demand, not
part of the runtime.

| Exit code | Meaning |
|---|---|
| 0 | everything required is up |
| 1 | something required is down or degraded |
| 2 | the environment itself is not ready -- `ccwebdb` cannot be activated, no PostgreSQL cluster, or no `node_modules` |

---

## What startup does

1. **Preflight** -- the conda environment, the `.pgdata` cluster and
   `frontend\node_modules` must exist; otherwise it stops with a pointer to
   the setup doc.
2. **PostgreSQL** -- started in a console of its own
   (`scripts\ccweb_pgstart.cmd`) unless `pg_isready` says it is up.
3. **Backend** -- `uvicorn app.main:app` on 127.0.0.1:8000, minimised window,
   no `--reload`.
4. **Frontend** -- Vite on 127.0.0.1:5173, minimised window.
5. **Waits** up to 60 seconds each for PostgreSQL, the backend's `/health`
   and Vite to answer, then reports.
6. **Records PIDs** in `.runtime\ccweb.pids`.

**It is safe to re-run.** Anything already listening is left alone and
reported as `already running`.

## What shutdown does

1. Stops the backend and frontend by the PIDs in `.runtime\ccweb.pids`, with
   `taskkill /T` so child processes go too.
2. **Sweeps ports 8000 and 5173** for survivors -- the safety net when the PID
   file is missing or stale, or a service was started by hand.
3. Stops PostgreSQL with `pg_ctl -m fast` (skipped with `/keepdb`).
4. **Verifies**, and exits non-zero if anything still holds a port or the
   database still accepts connections.

An unrecognised argument is refused rather than ignored.

## Starting a Claude Code session

```cmd
scripts\ccweb_claude.cmd                   new session
scripts\ccweb_claude.cmd ccweb             resume, searching for "ccweb"
scripts\ccweb_claude.cmd ccweb --effort high   extra flags pass through
scripts\ccweb_claude.cmd /check            report the environment, start nothing
scripts\ccweb_claude.cmd /nodb ...         skip the database check
```

It activates `ccwebdb`, starts PostgreSQL if it is not running (nearly any
work here needs it), changes to the repository root and starts Claude Code.
It does not start the API or Vite. `claude -r` takes a session id or any other
value as a search term, so a session name works as the argument.

PostgreSQL gets its own console because the postmaster spawns a process per
connection, each inheriting its console: started from a shell that later
exits, such as Claude Code's, the server keeps running while every new
connection dies.

---

## Files it produces

```
.runtime\ccweb.pids            backend / frontend PIDs and start time
logs\backend.log               uvicorn output
logs\frontend.log              Vite output
logs\postgres.log              pg_ctl start output, then the server log
logs\pg_stop.log               pg_ctl stop output
```

The log directory is `CCWEB_LOG_DIR`, `.\logs` when unset; a relative path is
taken from the repo root. Each type keeps its last three files
(`backend.log`, `backend.1.log`, `backend.2.log`), none past 1 GB.
[logs/README.md](../logs/README.md) describes every file and the
`CCWEB_LOG_KEEP` and `CCWEB_LOG_MAX_BYTES` settings. `.runtime\`, `.pgdata\`
and everything in `logs\` but its README are gitignored. When something fails
to start, the scripts print the log to read first.

---

## SonarQube (local server)

```cmd
scripts\ccweb_sonar_start.cmd          start the server and its database
scripts\ccweb_sonar_scan.cmd           run tests with coverage and publish an analysis
scripts\ccweb_sonar_stop.cmd           stop the server, keeping its data
```

Dashboard: http://localhost:9000, project `classobjects-ccwebdb`. The server
and its database run as podman containers (`sonarqube`, `sonar-db`) on the
`sonar-net` network, bound to 127.0.0.1. All state is in four named volumes
(`sonar-db-data`, `sonarqube-data`, `sonarqube-extensions`,
`sonarqube-logs`); stopping keeps them, `podman volume rm` destroys the
analysis history and admin account.

`ccweb_sonar_scan.cmd` needs `SONAR_TOKEN` in the environment -- the scanner
runs in a container and cannot read the host keychain:

```cmd
set "SONAR_TOKEN=squ_..."
```

On a fresh server, log in at http://localhost:9000 as `admin` / `admin`,
complete the forced password change, then generate a token at
http://localhost:9000/account/security. The scan runs the test suite first and
refuses to publish if it fails.

`scripts\ccweb_sonar_mcp.cmd` launches the SonarQube MCP server behind the
`mcp__sonarqube__*` tools. `sonar run mcp` cannot reach a local server (its
container's `localhost` is itself, and it has no `--network` flag); the script
joins `sonar-net` and uses `http://sonarqube:9000`. It also needs
`SONAR_TOKEN`. `.mcp.json` at the repo root points Claude Code at it; it is
gitignored and hand-managed. **Re-running `sonar integrate claude` overwrites
it** with the broken invocation; restore it to:

```json
{
  "mcpServers": {
    "sonarqube": {
      "command": "scripts\\ccweb_sonar_mcp.cmd"
    }
  }
}
```

---

## Design notes

**PostgreSQL is stopped with `pg_ctl`, never `taskkill`.** A forced stop
leaves the cluster needing crash recovery. `-m fast` rolls back open
transactions and writes a shutdown checkpoint; the log should end with
`database system is shut down`.

**Success is verified, not assumed.** Both scripts create `.runtime\` and the
log directory before anything writes there -- a failed redirect in cmd skips
the command attached to it and reports the redirect's exit code -- and
shutdown trusts `pg_isready` over `pg_ctl`'s exit code.

**Every server writes through a pipe.** Backend, frontend and PostgreSQL
output goes to `backend\app\logpipe.py`, which rolls files over at the size
limit. PostgreSQL's own logging collector is turned off because it can cap a
file's size but not how many files it leaves. `pg_ctl stop` writes to
`pg_stop.log` because the running server holds the start pipe.
`PYTHONUNBUFFERED=1` keeps uvicorn's lines from lagging.

**No `--reload`.** uvicorn runs as one process so the PID holding the port is
the one to kill; `--reload` adds a supervisor and a worker, and killing the
wrong one leaves the other to respawn.

**Pure cmd.** No PowerShell, including internally: ports from `netstat`,
readiness from `curl`, sleeps from `ping -n 2 127.0.0.1` (`timeout` returns
instantly when stdin is redirected), process trees from `taskkill /T`.

---

## Running a service by hand

For `--reload` during active backend work, or to watch output directly. Start
the database first, then in separate windows:

```cmd
:: database
conda activate ccwebdb
pg_ctl -D .pgdata -l logs\postgres-by-hand.log start
pg_isready -h localhost -p 5432

:: backend, with auto-reload
cd backend
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000

:: frontend
cd frontend
npm run dev
```

A server started this way belongs to that window's console and breaks when
the window closes (see *Troubleshooting*); prefer `ccweb_startup.cmd` for the
database. In a non-interactive shell `pg_ctl start` can appear to hang because
the server inherits stdout; check with `pg_isready` rather than waiting. A log
written this way is not rotated. `scripts\ccweb_shutdown.cmd` still cleans
these up: the port sweep catches what the PID file does not know about.

---

## Troubleshooting

**`[WinError 10013] An attempt was made to access a socket in a way forbidden
by its access permissions`** -- almost never permissions. Either something is
already listening, or Windows has reserved the port range (Hyper-V / WSL):

```cmd
netstat -ano | findstr ":8000" | findstr LISTENING
netsh interface ipv4 show excludedportrange protocol=tcp
```

`scripts\ccweb_shutdown.cmd` clears the first. For the second, change the port
or remove the reservation.

**`pg_ctl` not recognised** -- the conda environment is not active. Activate
it, or use the scripts.

**Startup reports FAILED but the service seems fine** -- the wait is 60
seconds, and a first Vite run after `npm install` can exceed it while it
pre-bundles dependencies. Check `logs\frontend.log` and re-run startup; it
adopts whatever is already listening.

**Stale PID file** after a reboot without a clean shutdown -- shutdown reports
`pid N already gone` and falls through to the port sweep.

**PostgreSQL holds port 5432 but `pg_isready` gets no response**, and the log
shows children dying with `0xC0000142` -- the server was started from a
console that has since closed. `pg_ctl stop` will not work. Kill the
postmaster with `taskkill /PID <pid> /T /F`, then any surviving
`postgres.exe` (it keeps the listening socket), and start again with
`scripts\ccweb_startup.cmd`. *Rejecting connections* during the next start is
WAL recovery, not failure. No reboot is needed.
