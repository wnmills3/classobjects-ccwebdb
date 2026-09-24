# logs

What the development runtime writes while it runs. Everything in this directory except this README is gitignored
(`logs/*` and `!logs/README.md` in `.gitignore`), so nothing written here
reaches GitHub.

`scripts\ccweb_startup.cmd`, `scripts\ccweb_shutdown.cmd` and
`scripts\ccweb_claude.cmd` write here. When something fails, they print the
log file to read first.

## Layout

```
logs\
  README.md               this file (the only tracked file)
  backend.log             uvicorn: startup, requests, tracebacks
  backend.1.log           the file before it
  backend.2.log           the file before that
  frontend.log  .1  .2    Vite: dev server output and build errors
  postgres.log  .1  .2    PostgreSQL: pg_ctl's start messages, then the server's own log
  pg_stop.log   .1  .2    pg_ctl stop: "server stopped"
```

`.runtime\ccweb.pids` is not a log: it holds the running processes' PIDs for
shutdown, and it stays in `.runtime\`.

## What each file is for

| File | Look here when |
|---|---|
| `backend.log` | the API returns errors, or startup says the backend FAILED |
| `frontend.log` | the site will not load, or startup says the frontend FAILED |
| `postgres.log` | PostgreSQL will not start (a bad setting or a stale `postmaster.pid` is explained here), a query fails or a connection is refused, or you want to see a shutdown finish (`database system is shut down`) |
| `pg_stop.log` | shutdown says PostgreSQL is still running |

## How the files rotate

**Every type keeps its last three files, and no file grows past 1 GB.**

- The current file always has the plain name. When a new one starts, the
  older files move up to `.1` and `.2`, and whatever was in `.2` is deleted.
- A new file starts each time the program starts, and whenever the next line
  would take the current file past the size limit. A long-running server
  therefore still leaves three bounded files. A start that wrote nothing
  does not push a useful log out.
- The backend, frontend and PostgreSQL write through a pipe into
  `backend\app\logpipe.py`, which does the rotating. PostgreSQL's own logging
  collector is turned off for this: it can cap a file's size but not how many
  files it leaves behind.
- `pg_stop.log` holds a few lines from each stop. Shutdown rotates it before
  each stop and never measures it.

## Settings

Set these in the console before running the scripts, or in the system
environment to make them permanent. The defaults need no settings.

| Variable | Default | Effect |
|---|---|---|
| `CCWEB_LOG_DIR` | `.\logs` | where all of the above goes; a relative path is taken from the repo root |
| `CCWEB_LOG_KEEP` | `3` | files kept per type, the current one included (at least 1) |
| `CCWEB_LOG_MAX_BYTES` | `1073741824` (1 GiB) | the size at which a file rolls over (at least 1024) |

```cmd
set "CCWEB_LOG_DIR=D:\ccweb-logs"
set "CCWEB_LOG_KEEP=5"
set "CCWEB_LOG_MAX_BYTES=104857600"
scripts\ccweb_startup.cmd
```

A nonsense value such as `CCWEB_LOG_KEEP=zero` is refused with a message,
not quietly replaced with the default.

The settings are read when each program starts, so a change reaches a
running server only when it is restarted. PostgreSQL restarts only with a
full shutdown: `ccweb_shutdown.cmd --keepdb` leaves it running and logging
as before.

Shutdown must see the same `CCWEB_LOG_DIR` as startup, or its `pg_stop.log`
lands in a different directory. The logic that resolves the directory is in
`scripts\ccweb_logdir.cmd`.

## Deleting logs

Anything here except this README can be deleted while the runtime is stopped.
While it runs, the current files are held open and Windows refuses to delete
them; the numbered files can be deleted.
