@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ---------------------------------------------------------------------------
rem  ccweb_shutdown.cmd - stop the ccwebdb development runtime.
rem
rem  Usage:  scripts\ccweb_shutdown.cmd            stop everything
rem          scripts\ccweb_shutdown.cmd /keepdb    stop the servers, leave PostgreSQL
rem
rem  Stops the servers by recorded PID, then sweeps ports 8000 and 5173 for
rem  survivors, then stops PostgreSQL with pg_ctl.
rem
rem  PostgreSQL is never killed: a forced stop leaves the cluster needing crash
rem  recovery. pg_ctl -m fast rolls back open transactions and checkpoints.
rem
rem  Pure cmd - no PowerShell.
rem ---------------------------------------------------------------------------

rem This script lives in scripts\, so the repo root is one level up.
rem %%~fI resolves the "..\" to a real absolute path with no trailing slash.
for %%I in ("%~dp0..") do set "REPO=%%~fI"

set "ENVDIR=%USERPROFILE%\miniforge3\envs\ccwebdb"
set "PGBIN=%ENVDIR%\Library\bin"
set "PGDATA=%REPO%\.pgdata"
set "RUNTIME=%REPO%\.runtime"
set "PIDFILE=%RUNTIME%\ccweb.pids"

set "KEEPDB="
if /I "%~1"=="/keepdb" set "KEEPDB=1"

rem The log directory must exist before anything redirects into it; otherwise
rem the redirect fails and the command attached to it never runs at all.
if not exist "%RUNTIME%" mkdir "%RUNTIME%"

echo ============================================
echo   ccwebdb runtime shutdown
echo ============================================

rem --- 1. stop by recorded PID ---------------------------------------------
if exist "%PIDFILE%" (
    for /f "usebackq tokens=1,2 delims==" %%K in ("%PIDFILE%") do (
        if /I "%%K"=="backend"  call :killpid %%L backend
        if /I "%%K"=="frontend" call :killpid %%L frontend
    )
) else (
    echo [1/3] no PID file - relying on the port sweep
)

rem --- 2. sweep the ports ---------------------------------------------------
echo [2/3] sweeping ports 8000 and 5173...
call :killport 8000
call :killport 5173

rem --- 3. PostgreSQL --------------------------------------------------------
if defined KEEPDB (
    echo [3/3] postgresql   left running ^(/keepdb^)
) else (
    "%PGBIN%\pg_isready.exe" -h localhost -p 5432 >nul 2>&1
    if errorlevel 1 (
        echo [3/3] postgresql   already stopped
    ) else (
        echo [3/3] postgresql   stopping ^(fast^)...
        "%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -m fast -w stop >"%RUNTIME%\pg_stop.log" 2>&1
        rem Trust pg_isready over the exit code: a failed redirect can mask it.
        "%PGBIN%\pg_isready.exe" -h localhost -p 5432 >nul 2>&1
        if errorlevel 1 (
            echo       stopped cleanly
        ) else (
            echo       FAILED - still accepting connections
            echo       see %RUNTIME%\pg_stop.log and %PGDATA%\server.log
        )
    )
)

if exist "%PIDFILE%" del /q "%PIDFILE%"

rem --- verify ---------------------------------------------------------------
echo.
echo verifying...
set "PROBLEM="
for %%P in (8000 5173) do (
    call :portpid %%P HOLDER
    if defined HOLDER (
        echo   port %%P  STILL HELD by pid !HOLDER!
        set "PROBLEM=1"
    ) else (
        echo   port %%P  free
    )
    set "HOLDER="
)
"%PGBIN%\pg_isready.exe" -h localhost -p 5432 >nul 2>&1
if errorlevel 1 (
    echo   postgres  stopped
) else (
    if defined KEEPDB ( echo   postgres  running ^(as requested^) ) else ( echo   postgres  STILL RUNNING & set "PROBLEM=1" )
)
echo ============================================
if defined PROBLEM exit /b 1
exit /b 0

rem ---------------------------------------------------------------------------
:killpid
rem  %1 = pid, %2 = label.  /T also takes child processes.
if "%~1"=="" goto :eof
tasklist /FI "PID eq %~1" 2>nul | find "%~1" >nul
if errorlevel 1 (
    echo [1/3] %~2      pid %~1 already gone
) else (
    echo [1/3] %~2      stopping pid %~1
    taskkill /PID %~1 /T /F >nul 2>&1
)
goto :eof

rem ---------------------------------------------------------------------------
:killport
call :portpid %~1 SURVIVOR
if defined SURVIVOR (
    echo       port %~1 held by pid !SURVIVOR! - stopping
    taskkill /PID !SURVIVOR! /T /F >nul 2>&1
    timeout /t 1 /nobreak >nul
) else (
    echo       port %~1 free
)
set "SURVIVOR="
goto :eof

rem ---------------------------------------------------------------------------
:portpid
set "%~2="
for /f "tokens=5" %%A in ('netstat -ano -p TCP ^| findstr /R /C:":%~1  *[0-9]" ^| findstr "LISTENING"') do set "%~2=%%A"
goto :eof
