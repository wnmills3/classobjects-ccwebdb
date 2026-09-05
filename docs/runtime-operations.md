# Runtime operations

Starting and stopping the development runtime. For first-time machine setup see
[environment-setup.md](environment-setup.md).

All commands here are **cmd**, not PowerShell.

---

## Quick reference

Run from the repository root:

```cmd
scripts\ccweb_startup.cmd              start database, backend and frontend
scripts\ccweb_shutdown.cmd             stop everything
scripts\ccweb_shutdown.cmd /keepdb     stop the servers, leave PostgreSQL running
```

The scripts locate the repository from their own path, so they also work
when called from anywhere else:

```cmd
C:\Users\wnmil\dev\classobjects-ccwebdb\scripts\ccweb_startup.cmd
```

> This machine has `NoDefaultCurrentDirectoryInExePath=1`, so a **bare**
> script name is not found even when standing in its directory. Always
> include a path separator: `scripts\ccweb_startup.cmd` is fine, and
> from inside `scripts\` you would need `.\ccweb_startup.cmd`.

| | |
|---|---|
| UI | http://127.0.0.1:5173 |
| API | http://127.0.0.1:8000 |
| API docs | http://127.0.0.1:8000/docs |
| Database | localhost:5432/ccwebdb |
| Sign in | `admin@example.com` / `adminpassword` |

---

## What startup does

1. **Preflight** — verifies the conda env, the `.pgdata` cluster and
   `frontend\node_modules` exist, and fails with a pointer to the setup doc
   rather than a confusing error further along.
2. **PostgreSQL** — `pg_ctl -D .pgdata -w start` unless `pg_isready` says it is
   already up.
3. **Backend** — `uvicorn app.main:app` on 127.0.0.1:8000, minimised window.
4. **Frontend** — Vite on 127.0.0.1:5173, minimised window.
5. **Waits** for each to answer over HTTP before reporting success.
6. **Records PIDs** to `.runtime\ccweb.pids`.

**It is safe to re-run.** Anything already listening is left alone:

```
[1/3] postgresql   already running
[2/3] backend      already running on 8000 (pid 18944)
[3/3] frontend     already running on 5173 (pid 15348)
```

## What shutdown does

1. Stops the backend and frontend by the PIDs in `.runtime\ccweb.pids`, using
   `taskkill /T` so child processes go too.
2. **Sweeps ports 8000 and 5173** for survivors — this is the safety net when
   the PID file is missing or stale, which happens if a service was started by
   hand.
3. Stops PostgreSQL with `pg_ctl -m fast`.
4. **Verifies** and exits non-zero if anything is still holding a port or the
   database is still accepting connections.

`/keepdb` skips step 3, which is convenient when restarting only the servers.

---

## Files it produces

```
.runtime\ccweb.pids       backend / frontend PIDs and start time
.runtime\backend.log      uvicorn output
.runtime\frontend.log     vite output
.runtime\pg_start.log     pg_ctl start output
.runtime\pg_stop.log      pg_ctl stop output
.pgdata\server.log        PostgreSQL server log
```

`.runtime\` is gitignored. When something fails to start, those logs are the
first place to look — the scripts print the relevant path on failure.

---

## Design notes

**PostgreSQL is stopped with `pg_ctl`, never `taskkill`.** A forced stop leaves
the cluster needing crash recovery on the next start. `-m fast` rolls back open
transactions and writes a shutdown checkpoint; the server log should end with
`database system is shut down`.

**Success is verified, not assumed.** An early version reported "stopped
cleanly" while PostgreSQL was still running: `.runtime\` did not exist, so the
log redirect failed, and because the redirect failed the `pg_ctl` command
attached to it never ran at all. The exit code reflected the redirect rather
than the command. Both scripts now create `.runtime\` before anything writes
there, and shutdown trusts `pg_isready` over the exit code.

**No `--reload`.** The script runs uvicorn as a single process so the PID that
holds the port is the one to kill. `--reload` adds a supervisor plus a worker,
and killing the wrong one leaves the other to respawn. For active backend work,
run it by hand instead (see below).

**Pure cmd.** No PowerShell, including internally — ports come from `netstat`,
readiness from `curl` (shipped with Windows since 10 1803), and process trees
from `taskkill /T`.

---

## Running a service by hand

Useful when you want `--reload`, or to watch output directly. Start the database
first, then in separate windows:

```cmd
:: database
conda activate ccwebdb
pg_ctl -D .pgdata -l .pgdata\server.log start

:: backend, with auto-reload
cd backend
uv run uvicorn app.main:app --reload --port 8000

:: frontend
cd frontend
npm run dev
```

`scripts\ccweb_shutdown.cmd` still cleans these up — the port sweep catches
whatever the PID file does not know about.

---

## Troubleshooting

**`[WinError 10013] An attempt was made to access a socket in a way forbidden by
its access permissions`**

Despite the wording this is almost never a permissions problem. Two causes:

```cmd
:: 1. something is already listening
netstat -ano | findstr ":8000" | findstr LISTENING

:: 2. Windows has reserved the port range (Hyper-V / WSL)
netsh interface ipv4 show excludedportrange protocol=tcp
```

For the first, `scripts\ccweb_shutdown.cmd` clears it. For the second, the port must
be changed or the reservation removed — it will show as a range containing 8000
with nothing actually listening.

**`pg_ctl: command not found`** — the conda environment is not active. Either
`conda activate ccwebdb` first, or use the scripts, which call the binaries by
absolute path and do not care.

**Startup reports FAILED but a service seems fine** — the readiness probe waits
60 seconds. A first Vite run after `npm install` can exceed that while it
pre-bundles dependencies. Check `.runtime\frontend.log`, then simply re-run
startup; it will adopt whatever is already listening.

**Stale PID file** — if the machine was rebooted without a clean shutdown,
`.runtime\ccweb.pids` refers to processes that no longer exist. Shutdown handles
this: it reports `pid N already gone` and falls through to the port sweep.
