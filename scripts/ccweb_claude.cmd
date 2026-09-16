@echo off
setlocal EnableExtensions
rem ---------------------------------------------------------------------------
rem  ccweb_claude.cmd - activate the ccwebdb conda environment, make sure the
rem                     database is up, and start Claude Code in the repo root.
rem
rem  Usage:  scripts\ccweb_claude.cmd                  new session
rem          scripts\ccweb_claude.cmd ccweb            resume, searching "ccweb"
rem          scripts\ccweb_claude.cmd 01G7CtsF...      resume that session id
rem          scripts\ccweb_claude.cmd ccweb --effort high   extra flags pass through
rem          scripts\ccweb_claude.cmd /check           report state, do not start
rem          scripts\ccweb_claude.cmd /nodb ...        skip the database check
rem
rem  claude's -r/--resume takes a session id, or any other value as a search
rem  term for the interactive picker - so a session name works.
rem
rem  PostgreSQL is started if it is not already running, because nearly any work
rem  in this project needs it. The API and UI are NOT started here - use
rem  scripts\ccweb_startup.cmd for those.
rem ---------------------------------------------------------------------------

rem This script lives in scripts\, so the repo root is one level up.
for %%I in ("%~dp0..") do set "REPO=%%~fI"
set "ENVNAME=ccwebdb"
set "PGDATA=%REPO%\.pgdata"
call "%REPO%\scripts\ccweb_logdir.cmd"

set "CHECKONLY="
set "SKIPDB="
:parseargs
if /I "%~1"=="/check" ( set "CHECKONLY=1" & shift & goto parseargs )
if /I "%~1"=="/nodb"  ( set "SKIPDB=1"    & shift & goto parseargs )

rem --- activate -------------------------------------------------------------
rem  Uses ccwebdb if it is already active, and activates it if not. The helper
rem  finds conda and makes sure USERPROFILE is set, and says why when it cannot.
rem  %REPO%, not %~dp0: the argument loop above uses a plain `shift`, which
rem  shifts %0 as well, so by now %~dp0 names the folder of an argument.
call "%REPO%\scripts\ccweb_env.cmd" || exit /b 1

cd /d "%REPO%"

echo environment  %CONDA_DEFAULT_ENV%  (%CCWEB_ENV_STATE%)
echo directory    %CD%
for /f "delims=" %%V in ('python --version 2^>^&1') do echo python       %%V

rem --- database -------------------------------------------------------------
set "DBSTATE=skipped"
if not defined SKIPDB (
    if not exist "%PGDATA%\PG_VERSION" (
        set "DBSTATE=no cluster at .pgdata - see docs\environment-setup.md"
    ) else (
        "%PGBIN%\pg_isready.exe" -h localhost -p 5432 >nul 2>&1
        if errorlevel 1 (
            call "%REPO%\scripts\ccweb_pgstart.cmd"
            call :waitpg 60
            "%PGBIN%\pg_isready.exe" -h localhost -p 5432 >nul 2>&1
            if errorlevel 1 (
                set "DBSTATE=FAILED TO START - see %LOGS%\postgres.log"
            ) else (
                set "DBSTATE=started"
            )
        ) else (
            set "DBSTATE=already running"
        )
    )
)
echo postgres     %DBSTATE%

rem --- claude present? ------------------------------------------------------
rem  The installer puts claude in %USERPROFILE%\.local\bin and adds that to
rem  PATH. A shell whose profile variables were not loaded may lack the PATH
rem  entry too, so the install folder is tried before giving up.
where claude >nul 2>&1
if errorlevel 1 if exist "%USERPROFILE%\.local\bin\claude.exe" set "PATH=%USERPROFILE%\.local\bin;%PATH%"
where claude >nul 2>&1
if errorlevel 1 (
    echo ERROR: "claude" is not on PATH
    echo        expected in %USERPROFILE%\.local\bin
    exit /b 1
)

if defined CHECKONLY (
    for /f "delims=" %%V in ('claude --version 2^>^&1') do echo claude       %%V
    echo.
    echo /check only - not starting Claude Code
    exit /b 0
)

rem --- start Claude Code ----------------------------------------------------
echo.
if "%~1"=="" (
    echo starting a new session...
    claude
) else (
    echo resuming with "%~1"...
    claude --resume %*
)
exit /b %ERRORLEVEL%

rem ---------------------------------------------------------------------------
rem  :waitpg <max seconds>  - poll until PostgreSQL accepts connections
rem
rem  PostgreSQL is started through `start`, into a console of its own, because
rem  it must outlive this window and the Claude Code session run from it. The
rem  postmaster spawns a process per connection and background task, each
rem  inheriting its console; once that console is gone, every new one dies
rem  with 0xC0000142 while the postmaster itself keeps running. See
rem  ccweb_startup.cmd. pg_isready says "rejecting connections" during crash
rem  recovery, which is progress, so this waits through it. ping, not timeout:
rem  timeout does not pause when stdin is redirected.
rem ---------------------------------------------------------------------------
:waitpg
set /a _tries=0
:waitpg_loop
"%PGBIN%\pg_isready.exe" -h localhost -p 5432 >nul 2>&1
if not errorlevel 1 goto :eof
set /a _tries+=1
if %_tries% GEQ %~1 goto :eof
ping -n 2 127.0.0.1 >nul
goto waitpg_loop
