# logs

What the development runtime writes while it runs. Everything in this directory except this README is gitignored
(`logs/*` and `!logs/README.md` in `.gitignore`), so nothing written here
reaches GitHub.

`ccweb_startup`, `ccweb_shutdown`, `ccweb_claude`, `ccweb_backup` and
`ccweb_testdb` in `scripts/` -- the `.cmd` on Windows, the `.sh` on Linux --
write here. When something fails, they print the log file to read first.
On a Windows server, the API's scheduled task writes `backend.log` here too,
through the same log writer, so it rotates as below
(`docs/deployment-windows.md`, step 6). A server installed as a systemd
service on Linux logs to the journal instead (`journalctl -u ccwebdb-api`;
see `docs/deployment-linux.md`).

## Layout

```
logs/
  README.md               this file (the only tracked file)
  backend.log             uvicorn: startup, requests, tracebacks
  backend.1.log           the file before it
  backend.2.log           the file before that
  frontend.log  .1  .2    Vite: dev server output and build errors
  postgres.log  .1  .2    PostgreSQL: pg_ctl's start messages, then the server's own log
  pg_stop.log   .1  .2    pg_ctl stop: "server stopped"
  postgres-test.log       the test cluster (ccweb_testdb), the same way
  backup.log              ccweb_backup: what each backup run did
```

`.runtime/ccweb.pids` is not a log: it holds the running processes' PIDs
(process groups, on Linux) for shutdown, and it stays in `.runtime/`.

## What each file is for

| File | Look here when |
|---|---|
| `backend.log` | the API returns errors, or startup says the backend FAILED |
| `frontend.log` | the site will not load, or startup says the frontend FAILED |
| `postgres.log` | PostgreSQL will not start (a bad setting or a stale `postmaster.pid` is explained here), a query fails or a connection is refused, or you want to see a shutdown finish (`database system is shut down`) |
| `pg_stop.log` | shutdown says PostgreSQL is still running |
| `backup.log` | a backup run says FAILED, or to see that the daily runs say `proved` |

## How the files rotate

**Every type but `backup.log` keeps its last three files, and no file of
those grows past 1 GB.**

- The current file always has the plain name. When a new one starts, the
  older files move up to `.1` and `.2`, and whatever was in `.2` is deleted.
- A new file starts each time the program starts, and whenever the next line
  would take the current file past the size limit. A long-running server
  therefore still leaves three bounded files. A start that wrote nothing
  does not push a useful log out.
- The backend, frontend and PostgreSQL write through a pipe into
  `backend/app/logpipe.py`, which does the rotating. PostgreSQL's own logging
  collector is turned off for this: it can cap a file's size but not how many
  files it leaves behind.
- On Windows, a current file that another program still has open cannot be
  moved up -- Windows refuses -- so no new file is started: the lines are
  added to that file, under one line beginning `[logpipe]` that says so, and
  the older files stay as they were. Linux allows the move, so a program
  still writing to the old file goes on writing into it under its new name,
  `.1`.
- `pg_stop.log` holds a few lines from each stop. Shutdown rotates it before
  each stop and never measures it.
- `backup.log` is one file that every backup run adds to, each under a line
  with its date and time. It is not rotated and has no size limit: delete it
  when it has grown long.

## Settings

Set these in `.env` (they are listed, commented out at their defaults, in
`.env.example`), or in the environment of the shell that runs the scripts,
which wins over `.env`. The defaults need no settings.

| Setting | Default | Effect |
|---|---|---|
| `CCWEB_LOG_DIR` | `logs` | where all of the above goes; a relative path is taken from the repo root |
| `CCWEB_LOG_KEEP` | `3` | files kept per type, the current one included (at least 1) |
| `CCWEB_LOG_MAX_BYTES` | `1073741824` (1 GiB) | the size at which a file rolls over (at least 1024) |

For example, in `.env`:

```
CCWEB_LOG_DIR=D:\ccweb-logs
CCWEB_LOG_KEEP=5
CCWEB_LOG_MAX_BYTES=104857600
```

A nonsense value such as `CCWEB_LOG_KEEP=zero` is refused with a message
naming the setting, not quietly replaced with the default.

The settings are read when each program starts, so a change reaches a
running server only when it is restarted. PostgreSQL restarts only with a
full shutdown: `ccweb_shutdown --keepdb` leaves it running and logging as
before. Startup and shutdown both read the folder from the settings, so they
always agree on it.

## Deleting logs

Anything here except this README can be deleted while the runtime is stopped.
While it runs on Windows, the current files are held open and Windows
refuses to delete them; the numbered files can be deleted.
