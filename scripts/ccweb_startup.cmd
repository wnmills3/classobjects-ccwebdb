@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ---------------------------------------------------------------------------
rem  ccweb_startup.cmd - bring up the ccwebdb development runtime.
rem
rem    1. PostgreSQL   (pg_ctl, cluster in CCWEB_DB_CLUSTER_DIR)
rem    2. FastAPI      CCWEB_API_HOST:CCWEB_API_PORT
rem    3. Vite         CCWEB_FRONTEND_HOST:CCWEB_FRONTEND_PORT
rem
rem  Addresses and folders are settings (.env.example lists them), with
rem  defaults 127.0.0.1, 8000, 5173 and .pgdata.
rem
rem  Usage:  scripts\ccweb_startup.cmd         Vite answers this machine only
rem          scripts\ccweb_startup.cmd /lan    Vite answers the local network too
rem          scripts\ccweb_startup.cmd --lan   the same; use this from git bash
rem
rem  /lan binds Vite to 0.0.0.0 instead of CCWEB_FRONTEND_HOST. The API and the database
rem  stay on this machine either way: the browser reaches the API only through
rem  Vite's /api proxy. Other machines also need the firewall rule printed at
rem  the end. Vite's dev server is for a home network, never the internet.
rem
rem  PIDs are written to .runtime\ccweb.pids for ccweb_shutdown.cmd.
rem  Logs go to %CCWEB_LOG_DIR% (default .\logs), three files per type of
rem  at most 1 GB each; logs\README.md lists the files and the settings.
rem  Safe to re-run: anything already listening is left alone. A listener on
rem  the API's port counts as the API only if /health answers {"status":"ok"};
rem  otherwise the port is reported as taken and the exit code is 1.
rem
rem  Pure cmd - no PowerShell. Uses netstat for PIDs and curl for readiness
rem  (curl.exe ships with Windows 10 1803 and later).
rem ---------------------------------------------------------------------------

rem An unrecognised argument is refused, never ignored. Git bash rewrites /lan
rem into a path such as C:/Program Files/Git/lan, and ignoring that would
rem start a frontend the caller asked to open to the network on loopback only.
set "LAN="
if /I "%~1"=="/lan" set "LAN=1"
if /I "%~1"=="--lan" set "LAN=1"
if "%~1"=="" goto :args_ok
if defined LAN goto :args_ok
echo ERROR: unrecognised argument: %1
echo        usage: scripts\ccweb_startup.cmd [/lan ^| --lan]
echo        from git bash write --lan: bash rewrites /lan into a path
exit /b 2
:args_ok

rem This script lives in scripts\, so the repo root is one level up.
rem %%~fI resolves the "..\" to a real absolute path with no trailing slash.
for %%I in ("%~dp0..") do set "REPO=%%~fI"

rem  Put the ccwebdb conda environment in play and take ENVDIR and PGBIN
rem  from it, rather than from a guessed path. See ccweb_env.cmd.
rem  Everything started below inherits it: `start` passes this environment
rem  on to the consoles it opens.
call "%~dp0ccweb_env.cmd" || exit /b 2
call "%~dp0ccweb_settings.cmd" || exit /b 2
set "PGDATA=%CCWEB_DB_CLUSTER_DIR%"
set "RUNTIME=%REPO%\.runtime"
set "PIDFILE=%RUNTIME%\ccweb.pids"
call "%~dp0ccweb_logdir.cmd"
rem  The log writer: a plain script, run with the environment's Python.
set "LOGPIPE=%REPO%\backend\app\logpipe.py"
rem  Without this a piped Python holds its output back in a buffer, and
rem  the backend log would lag behind what the server has done.
set "PYTHONUNBUFFERED=1"
set "BHOST=%CCWEB_API_HOST%"
set "BPORT=%CCWEB_API_PORT%"
set "FPORT=%CCWEB_FRONTEND_PORT%"
set "FHOST=%CCWEB_FRONTEND_HOST%"
if defined LAN set "FHOST=0.0.0.0"
rem  Where to ask whether Vite answers: its own address, or loopback when
rem  it listens on every address.
set "FASK=%FHOST%"
if "%FASK%"=="0.0.0.0" set "FASK=127.0.0.1"

echo ============================================
echo   ccwebdb runtime startup
echo ============================================

rem --- preflight ------------------------------------------------------------
if not exist "%ENVDIR%\python.exe" (
    echo ERROR: conda environment not found: !ENVDIR!
    echo        see docs\environment-setup-windows.md
    exit /b 1
)
if not exist "%PGDATA%\PG_VERSION" (
    echo ERROR: no PostgreSQL cluster at !PGDATA!
    echo        see docs\environment-setup-windows.md
    exit /b 1
)
if not exist "%REPO%\frontend\node_modules\vite\bin\vite.js" (
    echo ERROR: frontend dependencies missing
    echo        run:  cd frontend ^&^& npm install
    exit /b 1
)
if not exist "%RUNTIME%" mkdir "%RUNTIME%"
if not exist "%LOGS%" mkdir "%LOGS%"

rem --- 1. PostgreSQL --------------------------------------------------------
rem  Started into a console of its own, like the backend and frontend below;
rem  ccweb_pgstart.cmd says why. Started from the caller's console instead,
rem  it would break when that console closes -- a tool's shell, say --
rem  while the backend and frontend kept running.
"%PGBIN%\pg_isready.exe" -h %CCWEB_DB_HOST% -p %CCWEB_DB_PORT% >nul 2>&1
if not errorlevel 1 (
    echo [1/3] postgresql   already running
) else (
    echo [1/3] postgresql   starting...
    call "%~dp0ccweb_pgstart.cmd" || exit /b 1
    call :waitpg 60
    "%PGBIN%\pg_isready.exe" -h %CCWEB_DB_HOST% -p %CCWEB_DB_PORT% >nul 2>&1
    if errorlevel 1 (
        echo       FAILED - see !LOGS!\postgres.log
        exit /b 1
    )
    echo       started
)

rem --- 2. FastAPI backend ---------------------------------------------------
call :portpid %BPORT% BPID
rem  A listener on the API's port is this backend only if it answers /health
rem  as the API does; anything else is another program that has the port,
rem  and the frontend would proxy /api to it.
if defined BPID (
    curl -s --max-time 2 "http://%BHOST%:%BPORT%/health" 2>nul | findstr /R /C:"status.:.ok" >nul
    if errorlevel 1 (
        echo [2/3] backend      FAILED - port %BPORT% is taken by another program ^(pid !BPID!^), not this API
        echo       free the port, or set CCWEB_API_PORT in .env to another one
        exit /b 1
    )
    echo [2/3] backend      already running on %BPORT% ^(pid !BPID!^)
) else (
    echo [2/3] backend      starting on %BPORT%...
    rem  The pipe is inside the quoted part, so the new console runs it.
    start "ccweb-backend" /MIN /D "%REPO%\backend" cmd /c ""%ENVDIR%\Scripts\uvicorn.exe" app.main:app --host %BHOST% --port %BPORT% 2>&1 | "%ENVDIR%\python.exe" "%LOGPIPE%" "%LOGS%\backend.log""
    rem  A held port is not an answer: a backend that binds and then hangs
    rem  holds 8000 exactly as a healthy one does.
    call :waiturl "http://%BHOST%:%BPORT%/health" 60
    if errorlevel 1 (
        echo       FAILED - /health did not answer - see !LOGS!\backend.log
        exit /b 1
    )
    call :portpid %BPORT% BPID
    if not defined BPID (
        echo       FAILED - see !LOGS!\backend.log
        exit /b 1
    )
    echo       pid !BPID!
)

rem --- 3. Vite frontend -----------------------------------------------------
call :portpid %FPORT% FPID
if defined FPID (
    echo [3/3] frontend     already running on %FPORT% ^(pid !FPID!^)
    if defined LAN (
        netstat -ano -p TCP | findstr /R /C:"0\.0\.0\.0:%FPORT%  *[0-9]" | findstr "LISTENING" >nul
        if errorlevel 1 (
            echo       WARNING - it answers this machine only, so /lan changed nothing.
            echo       Stop it with scripts\ccweb_shutdown.cmd /keepdb, then run this again.
        )
    )
) else (
    echo [3/3] frontend     starting on %FPORT%...
    start "ccweb-frontend" /MIN /D "%REPO%\frontend" cmd /c ""%ENVDIR%\node.exe" .\node_modules\vite\bin\vite.js --host %FHOST% --port %FPORT% 2>&1 | "%ENVDIR%\python.exe" "%LOGPIPE%" "%LOGS%\frontend.log""
    call :waiturl "http://%FASK%:%FPORT%/" 60
    if errorlevel 1 (
        echo       FAILED - Vite did not answer - see !LOGS!\frontend.log
        exit /b 1
    )
    call :portpid %FPORT% FPID
    if not defined FPID (
        echo       FAILED - see !LOGS!\frontend.log
        exit /b 1
    )
    echo       pid !FPID!
)

rem --- record PIDs ----------------------------------------------------------
> "%PIDFILE%" echo backend=!BPID!
>>"%PIDFILE%" echo frontend=!FPID!
>>"%PIDFILE%" echo started=%DATE% %TIME%

rem --- report ---------------------------------------------------------------
echo.
echo ============================================
echo   UI          http://%FASK%:%FPORT%
echo   API         http://%BHOST%:%BPORT%
echo   API docs    http://%BHOST%:%BPORT%/docs
echo   database    %CCWEB_DB_HOST%:%CCWEB_DB_PORT%/%CCWEB_DB_NAME%
echo.
echo   sign in     a manager account (FIRST_ADMIN_EMAIL in .env on a trial installation)
echo.
echo   logs        %LOGS%
echo   PIDs        .runtime\ccweb.pids
echo   stop with   scripts\ccweb_shutdown.cmd
if defined LAN call :lanreport
echo ============================================
exit /b 0

rem ---------------------------------------------------------------------------
rem  :lanreport  - the addresses other machines use, and the firewall rule
rem
rem  Every IPv4 address is listed, since a machine with more than one adapter
rem  has more than one. Adding the rule needs an administrator window, which
rem  this script does not assume, so it only says whether the rule exists.
rem ---------------------------------------------------------------------------
:lanreport
echo.
for /f "tokens=2 delims=:" %%A in ('ipconfig ^| findstr /C:"IPv4 Address"') do (
    for /f "tokens=1" %%B in ("%%A") do echo   network     http://%%B:%FPORT%/management
)
netsh advfirewall firewall show rule name="ccweb frontend LAN" >nul 2>&1
if errorlevel 1 (
    echo.
    echo   firewall    no rule yet; other machines are blocked until, in an
    echo               administrator window, you run:
    echo   netsh advfirewall firewall add rule name="ccweb frontend LAN" dir=in action=allow protocol=TCP localport=%FPORT% remoteip=localsubnet
)
goto :eof

rem ---------------------------------------------------------------------------
rem  :portpid <port> <varname>  - PID listening on <port>, empty if none
rem ---------------------------------------------------------------------------
:portpid
set "%~2="
for /f "tokens=5" %%A in ('netstat -ano -p TCP ^| findstr /R /C:":%~1  *[0-9]" ^| findstr "LISTENING"') do set "%~2=%%A"
goto :eof

rem ---------------------------------------------------------------------------
rem  :waiturl <url> <max tries>  - poll until it answers (exit 0), or give
rem  up (exit 1). A try that gets no answer takes up to two seconds, and a
rem  second passes before the next.
rem ---------------------------------------------------------------------------
:waiturl
set /a _tries=0
:waiturl_loop
curl -s -o nul --max-time 2 "%~1" >nul 2>&1
if not errorlevel 1 exit /b 0
set /a _tries+=1
if !_tries! GEQ %~2 exit /b 1
rem  ping, not timeout: timeout needs a real console and returns at once, with
rem  no pause, when stdin is redirected - as it is for any caller that is not a
rem  person at a keyboard - so the loop would give up almost instantly.
ping -n 2 127.0.0.1 >nul
goto waiturl_loop

rem ---------------------------------------------------------------------------
rem  :waitpg <max tries>  - poll until PostgreSQL accepts connections
rem
rem  pg_isready says "rejecting connections" while crash recovery replays WAL.
rem  That is progress, not failure, so this waits through it.
rem ---------------------------------------------------------------------------
:waitpg
set /a _tries=0
:waitpg_loop
"%PGBIN%\pg_isready.exe" -h %CCWEB_DB_HOST% -p %CCWEB_DB_PORT% >nul 2>&1
if not errorlevel 1 goto :eof
set /a _tries+=1
if %_tries% GEQ %~1 goto :eof
ping -n 2 127.0.0.1 >nul
goto waitpg_loop
