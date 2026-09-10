@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ---------------------------------------------------------------------------
rem  ccweb_startup.cmd - bring up the ccwebdb development runtime.
rem
rem    1. PostgreSQL   localhost:5432   (pg_ctl, cluster in .pgdata)
rem    2. FastAPI      127.0.0.1:8000
rem    3. Vite         127.0.0.1:5173
rem
rem  PIDs are written to .runtime\ccweb.pids for ccweb_shutdown.cmd.
rem  Safe to re-run: anything already listening is left alone.
rem
rem  Pure cmd - no PowerShell. Uses netstat for PIDs and curl for readiness
rem  (curl.exe ships with Windows 10 1803 and later).
rem ---------------------------------------------------------------------------

rem This script lives in scripts\, so the repo root is one level up.
rem %%~fI resolves the "..\" to a real absolute path with no trailing slash.
for %%I in ("%~dp0..") do set "REPO=%%~fI"

set "ENVDIR=%USERPROFILE%\miniforge3\envs\ccwebdb"
set "PGBIN=%ENVDIR%\Library\bin"
set "PGDATA=%REPO%\.pgdata"
set "RUNTIME=%REPO%\.runtime"
set "PIDFILE=%RUNTIME%\ccweb.pids"
set "BPORT=8000"
set "FPORT=5173"

echo ============================================
echo   ccwebdb runtime startup
echo ============================================

rem --- preflight ------------------------------------------------------------
if not exist "%ENVDIR%\python.exe" (
    echo ERROR: conda environment not found: %ENVDIR%
    echo        see docs\environment-setup.md
    exit /b 1
)
if not exist "%PGDATA%\PG_VERSION" (
    echo ERROR: no PostgreSQL cluster at %PGDATA%
    echo        see docs\environment-setup.md
    exit /b 1
)
if not exist "%REPO%\frontend\node_modules\vite\bin\vite.js" (
    echo ERROR: frontend dependencies missing
    echo        run:  cd frontend ^&^& npm install
    exit /b 1
)
if not exist "%RUNTIME%" mkdir "%RUNTIME%"

rem --- 1. PostgreSQL --------------------------------------------------------
rem  Started through `start`, into a console of its own, like the backend and
rem  frontend below. The postmaster spawns a process for every connection and
rem  background task, each inheriting its console. Started from a shell that
rem  later goes away - Claude Code's, or a window someone closes - it keeps
rem  running while every process it spawns afterwards dies with 0xC0000142.
rem  On 2026-09-10 this script started all three from Claude Code's shell; the
rem  backend and frontend survived that session ending, and PostgreSQL did not.
"%PGBIN%\pg_isready.exe" -h localhost -p 5432 >nul 2>&1
if not errorlevel 1 (
    echo [1/3] postgresql   already running
) else (
    echo [1/3] postgresql   starting...
    start "ccweb-postgres" /MIN cmd /c ""%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -l "%PGDATA%\server.log" start >"%RUNTIME%\pg_start.log" 2>&1"
    call :waitpg 60
    "%PGBIN%\pg_isready.exe" -h localhost -p 5432 >nul 2>&1
    if errorlevel 1 (
        echo       FAILED - see %RUNTIME%\pg_start.log and %PGDATA%\server.log
        exit /b 1
    )
    echo       started
)

rem --- 2. FastAPI backend ---------------------------------------------------
call :portpid %BPORT% BPID
if defined BPID (
    echo [2/3] backend      already running on %BPORT% ^(pid !BPID!^)
) else (
    echo [2/3] backend      starting on %BPORT%...
    start "ccweb-backend" /MIN /D "%REPO%\backend" cmd /c ""%ENVDIR%\Scripts\uvicorn.exe" app.main:app --host 127.0.0.1 --port %BPORT% >"%RUNTIME%\backend.log" 2>&1"
    call :waiturl "http://127.0.0.1:%BPORT%/health" 60
    call :portpid %BPORT% BPID
    if not defined BPID (
        echo       FAILED - see %RUNTIME%\backend.log
        exit /b 1
    )
    echo       pid !BPID!
)

rem --- 3. Vite frontend -----------------------------------------------------
call :portpid %FPORT% FPID
if defined FPID (
    echo [3/3] frontend     already running on %FPORT% ^(pid !FPID!^)
) else (
    echo [3/3] frontend     starting on %FPORT%...
    start "ccweb-frontend" /MIN /D "%REPO%\frontend" cmd /c ""%ENVDIR%\node.exe" .\node_modules\vite\bin\vite.js --host 127.0.0.1 --port %FPORT% >"%RUNTIME%\frontend.log" 2>&1"
    call :waiturl "http://127.0.0.1:%FPORT%/" 60
    call :portpid %FPORT% FPID
    if not defined FPID (
        echo       FAILED - see %RUNTIME%\frontend.log
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
echo   UI          http://127.0.0.1:%FPORT%
echo   API         http://127.0.0.1:%BPORT%
echo   API docs    http://127.0.0.1:%BPORT%/docs
echo   database    localhost:5432/ccwebdb
echo.
echo   sign in     admin@example.com / adminpassword
echo.
echo   logs        .runtime\backend.log  .runtime\frontend.log
echo   PIDs        .runtime\ccweb.pids
echo   stop with   scripts\ccweb_shutdown.cmd
echo ============================================
exit /b 0

rem ---------------------------------------------------------------------------
rem  :portpid <port> <varname>  - PID listening on <port>, empty if none
rem ---------------------------------------------------------------------------
:portpid
set "%~2="
for /f "tokens=5" %%A in ('netstat -ano -p TCP ^| findstr /R /C:":%~1  *[0-9]" ^| findstr "LISTENING"') do set "%~2=%%A"
goto :eof

rem ---------------------------------------------------------------------------
rem  :waiturl <url> <max seconds>  - poll until it answers, or give up
rem ---------------------------------------------------------------------------
:waiturl
set /a _tries=0
:waiturl_loop
curl -s -o nul --max-time 2 "%~1" >nul 2>&1
if not errorlevel 1 goto :eof
set /a _tries+=1
if !_tries! GEQ %~2 goto :eof
rem  ping, not timeout: timeout needs a real console and returns at once, with
rem  no pause, when stdin is redirected - as it is for any caller that is not a
rem  person at a keyboard - so the loop would give up almost instantly.
ping -n 2 127.0.0.1 >nul
goto waiturl_loop

rem ---------------------------------------------------------------------------
rem  :waitpg <max seconds>  - poll until PostgreSQL accepts connections
rem
rem  pg_isready says "rejecting connections" while crash recovery replays WAL.
rem  That is progress, not failure, so this waits through it.
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
