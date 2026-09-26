@echo off
setlocal EnableExtensions
rem ---------------------------------------------------------------------------
rem  ccweb_pgstart.cmd - start PostgreSQL in a console of its own, logging to
rem  postgres.log in the log directory. Does not wait: the caller polls
rem  pg_isready.
rem
rem  Expects PGBIN, ENVDIR (ccweb_env.cmd), PGDATA and LOGS (ccweb_logdir.cmd).
rem  Called by ccweb_startup.cmd and ccweb_claude.cmd, so PostgreSQL logs the
rem  same way whichever of them started it. See logs\README.md.
rem
rem  Why a console of its own: the postmaster spawns a process for every
rem  connection and background task, each inheriting its console. Started from
rem  a shell that later goes away - Claude Code's, or a window someone closes -
rem  it keeps running while every process it spawns afterwards dies with
rem  0xC0000142.
rem ---------------------------------------------------------------------------

if not exist "%LOGS%" mkdir "%LOGS%"
set "LOGPIPE=%~dp0..\backend\app\logpipe.py"
set "PYTHONUNBUFFERED=1"

rem  Without -l, pg_ctl hands its own output to the server it starts, so the
rem  server writes into this pipe for as long as it runs, and logpipe.py keeps
rem  the last CCWEB_LOG_KEEP files of at most CCWEB_LOG_MAX_BYTES each - the
rem  same limits as the backend's log. PostgreSQL's own logging collector can
rem  bound a file's size but not how many files it leaves behind, so it is
rem  turned off here even if postgresql.conf turns it on. The console, and the
rem  pipe with it, closes when the server stops.
start "ccweb-postgres" /MIN cmd /c ""%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -o "-c logging_collector=off" start 2>&1 | "%ENVDIR%\python.exe" "%LOGPIPE%" "%LOGS%\postgres.log""
exit /b 0
