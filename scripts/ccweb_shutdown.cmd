@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ---------------------------------------------------------------------------
rem  ccweb_shutdown.cmd - stop the ccwebdb development runtime.
rem
rem  Usage:  scripts\ccweb_shutdown.cmd            stop everything
rem          scripts\ccweb_shutdown.cmd /keepdb    stop the servers, leave PostgreSQL
rem          scripts\ccweb_shutdown.cmd --keepdb   the same; use this from git bash
rem
rem  Stops the servers by recorded PID, then sweeps the API and frontend
rem  ports (CCWEB_API_PORT, CCWEB_FRONTEND_PORT) for survivors, then stops
rem  PostgreSQL with pg_ctl. The sweep stops a port's listener only when it
rem  is the program startup runs there (uvicorn, vite); any other is named,
rem  left alone, and makes the exit code 1.
rem
rem  PostgreSQL is never killed: a forced stop leaves the cluster needing crash
rem  recovery. pg_ctl -m fast rolls back open transactions and checkpoints.
rem
rem  Pure cmd - no PowerShell.
rem ---------------------------------------------------------------------------

rem This script lives in scripts\, so the repo root is one level up.
rem %%~fI resolves the "..\" to a real absolute path with no trailing slash.
for %%I in ("%~dp0..") do set "REPO=%%~fI"

rem  Put ccwebdb in play for pg_ctl. Unlike startup, a failure here
rem  does NOT abort: stopping things by port needs no environment, and a
rem  broken environment is exactly when you most want a way out. Only the
rem  PostgreSQL step depends on it, and it says so when skipped.
set "PGBIN="
call "%~dp0ccweb_env.cmd"
if errorlevel 1 (
    echo WARNING: ccwebdb environment unavailable - PostgreSQL cannot be
    echo          stopped from here; the servers still will be.
    set "PGBIN="
)
rem  Ports and folders are settings, read by the environment's Python.
rem  Without them the recorded PIDs are still stopped; the port sweep and
rem  the database are left, since their addresses are not known.
set "SETOK="
if defined PGBIN call "%~dp0ccweb_settings.cmd" && set "SETOK=1"
set "PGDATA="
set "LOGS="
if defined SETOK (
    set "PGDATA=%CCWEB_DB_CLUSTER_DIR%"
    call "%~dp0ccweb_logdir.cmd"
)
set "RUNTIME=%REPO%\.runtime"
set "PIDFILE=%RUNTIME%\ccweb.pids"

rem An unrecognised argument is refused, never ignored. Git bash rewrites
rem /keepdb into a path such as C:/Program Files/Git/keepdb, and ignoring that
rem would stop a database the caller asked to keep running.
set "KEEPDB="
if /I "%~1"=="/keepdb" set "KEEPDB=1"
if /I "%~1"=="--keepdb" set "KEEPDB=1"
if "%~1"=="" goto :args_ok
if defined KEEPDB goto :args_ok
echo ERROR: unrecognised argument: %1
echo        usage: scripts\ccweb_shutdown.cmd [/keepdb ^| --keepdb]
echo        from git bash write --keepdb: bash rewrites /keepdb into a path
exit /b 2
:args_ok

rem The log directory must exist before anything redirects into it; otherwise
rem the redirect fails and the command attached to it never runs at all.
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
if defined LOGS if not exist "%LOGS%" mkdir "%LOGS%"

echo ============================================
echo   ccwebdb runtime shutdown
echo ============================================

rem --- 1. stop by recorded PID ---------------------------------------------
if exist "%PIDFILE%" (
    for /f "usebackq tokens=1,2 delims==" %%K in ("%PIDFILE%") do (
        if /I "%%K"=="backend"  call :killpid %%L backend python.exe
        if /I "%%K"=="frontend" call :killpid %%L frontend node.exe
    )
) else (
    echo [1/3] no PID file - relying on the port sweep
)

rem --- 2. sweep the ports ---------------------------------------------------
if defined SETOK (
    echo [2/3] sweeping ports %CCWEB_API_PORT% and %CCWEB_FRONTEND_PORT%...
    call :killport %CCWEB_API_PORT% uvicorn python.exe uvicorn.exe
    call :killport %CCWEB_FRONTEND_PORT% vite node.exe
) else (
    echo [2/3] port sweep   SKIPPED - without the settings the ports are not known
)

rem --- 3. PostgreSQL --------------------------------------------------------
rem  With no environment there is no pg_isready to ask, and a failed call to it
rem  looks exactly like "not running". Say it was skipped instead of guessing.
set "PGSKIP="
if not defined PGBIN (
    echo [3/3] postgresql   SKIPPED - no ccwebdb environment to stop it with
    set "PGSKIP=1"
) else if not defined SETOK (
    echo [3/3] postgresql   SKIPPED - a setting in .env was refused
    set "PGSKIP=1"
) else if defined KEEPDB (
    echo [3/3] postgresql   left running ^(/keepdb^)
) else (
    "%PGBIN%\pg_isready.exe" -h %CCWEB_DB_HOST% -p %CCWEB_DB_PORT% >nul 2>&1
    if errorlevel 1 (
        echo [3/3] postgresql   already stopped
    ) else (
        echo [3/3] postgresql   stopping ^(fast^)...
        rem  Keep the last few stop logs, like every other log. The server
        rem  still holds postgres.log open, so the stop writes a file of its own.
        if exist "%ENVDIR%\python.exe" "%ENVDIR%\python.exe" "%REPO%\backend\app\logpipe.py" "%LOGS%\pg_stop.log" --rotate
        "%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -m fast -w stop >"%LOGS%\pg_stop.log" 2>&1
        rem Trust pg_isready over the exit code: a failed redirect can mask it.
        "%PGBIN%\pg_isready.exe" -h %CCWEB_DB_HOST% -p %CCWEB_DB_PORT% >nul 2>&1
        if errorlevel 1 (
            echo       stopped cleanly
        ) else (
            echo       FAILED - still accepting connections
            echo       see !LOGS!\pg_stop.log and !LOGS!\postgres.log
        )
    )
)

if exist "%PIDFILE%" del /q "%PIDFILE%"

rem --- verify ---------------------------------------------------------------
echo.
echo verifying...
set "PROBLEM="
if not defined SETOK (
    echo   ports     NOT CHECKED - without the settings the ports are not known
    set "PROBLEM=1"
)
if defined SETOK for %%P in (%CCWEB_API_PORT% %CCWEB_FRONTEND_PORT%) do (
    call :portpid %%P HOLDER
    if defined HOLDER (
        echo   port %%P  STILL HELD by pid !HOLDER!
        set "PROBLEM=1"
    ) else (
        echo   port %%P  free
    )
    set "HOLDER="
)
if defined PGSKIP (
    echo   postgres  NOT CHECKED - no ccwebdb environment or settings
    set "PROBLEM=1"
    goto :verified
)
"%PGBIN%\pg_isready.exe" -h %CCWEB_DB_HOST% -p %CCWEB_DB_PORT% >nul 2>&1
if errorlevel 1 (
    echo   postgres  stopped
) else (
    if defined KEEPDB ( echo   postgres  running ^(as requested^) ) else ( echo   postgres  STILL RUNNING & set "PROBLEM=1" )
)
:verified
echo ============================================
if defined PROBLEM exit /b 1
exit /b 0

rem ---------------------------------------------------------------------------
:killpid
rem  %1 = pid, %2 = label, %3 = image name.  /T also takes child processes.
if "%~1"=="" goto :eof
rem  The PID must still be the program that was recorded. The PID file
rem  outlives a reboot, and Windows hands a freed PID to whatever starts
rem  next, so a PID alone could name an unrelated program -- and /T /F would
rem  take it and everything it started. A PID that is not that program is
rem  left alone; the port sweep stops whatever really holds the port.
rem  find.exe by full path. Run from Git Bash, a bare `find` is the Unix
rem  find, which takes the PID for a path and fails, so every live PID would
rem  be reported as not running and only the port sweep would stop it.
tasklist /FI "PID eq %~1" /FI "IMAGENAME eq %~3" 2>nul | "%SystemRoot%\System32\find.exe" "%~1" >nul
if errorlevel 1 (
    echo [1/3] %~2      pid %~1 is not a running %~3 - left alone
) else (
    echo [1/3] %~2      stopping pid %~1
    taskkill /PID %~1 /T /F >nul 2>&1
)
goto :eof

rem ---------------------------------------------------------------------------
:killport
rem  %1 = port, %2 = the program startup runs there (uvicorn, vite), %3 %4 =
rem  the images it runs as. A listener that is not one of those is some other
rem  program that happens to have the port: it is named and left alone, and
rem  the verify step reports the port still held. cmd has no way to read a
rem  process's command line now that wmic is gone, so the image is the test.
call :portpid %~1 SURVIVOR
if not defined SURVIVOR (
    echo       port %~1 free
    goto :eof
)
set "OURS="
for %%N in (%~3 %~4) do (
    tasklist /FI "PID eq !SURVIVOR!" /FI "IMAGENAME eq %%N" 2>nul | "%SystemRoot%\System32\find.exe" "!SURVIVOR!" >nul && set "OURS=1"
)
if defined OURS (
    echo       port %~1 held by pid !SURVIVOR! - stopping
    taskkill /PID !SURVIVOR! /T /F >nul 2>&1
    rem ping, not timeout: timeout does not pause when stdin is redirected.
    ping -n 2 127.0.0.1 >nul
) else (
    echo       WARNING: port %~1 held by pid !SURVIVOR!, which is not %~2 - left alone
)
set "SURVIVOR="
goto :eof

rem ---------------------------------------------------------------------------
:portpid
set "%~2="
for /f "tokens=5" %%A in ('netstat -ano -p TCP ^| findstr /R /C:":%~1  *[0-9]" ^| findstr "LISTENING"') do set "%~2=%%A"
goto :eof
