@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ---------------------------------------------------------------------------
rem  ccweb_pgadmin.cmd - browse the database in a web browser.
rem
rem  Starts pgAdmin 4 on http://127.0.0.1:5050 and opens it. The ccwebdb
rem  connection is already registered, so it is two clicks to the tables.
rem
rem  pgAdmin is NOT a project dependency. It lives in its own uv tool
rem  environment outside the repo, so it never appears in uv.lock and never
rem  reaches the conda env that UV_PROJECT_ENVIRONMENT points at. Install it
rem  with:
rem
rem      uv tool install --python 3.13 pgadmin4
rem
rem  The --python 3.13 is not optional. uv defaults to the newest interpreter
rem  it can see, which here is miniforge's 3.14; pywinpty publishes no cp314
rem  wheel, so uv falls back to building it from Rust source and fails on the
rem  missing MSVC linker.
rem
rem  Pure cmd - no PowerShell. curl.exe ships with Windows 10 1803 and later.
rem ---------------------------------------------------------------------------

rem This script lives in scripts\, so the repo root is one level up.
for %%I in ("%~dp0..") do set "REPO=%%~fI"

set "PGADMIN=%USERPROFILE%\.local\bin\pgadmin4.exe"
set "PGADMIN_CLI=%USERPROFILE%\.local\bin\pgadmin4-cli.exe"
set "PKG=%APPDATA%\uv\tools\pgadmin4\Lib\site-packages\pgadmin4"
set "PORT=5050"
set "URL=http://127.0.0.1:%PORT%/browser/"

rem --- preflight ------------------------------------------------------------
if not exist "%PGADMIN%" (
    echo ERROR: pgAdmin is not installed.
    echo.
    echo     uv tool install --python 3.13 pgadmin4
    echo.
    echo        Pin 3.13 - pywinpty has no cp314 wheel and uv would try to
    echo        compile it from Rust source.
    exit /b 1
)

rem --- config ---------------------------------------------------------------
rem  config_local.py turns off SERVER_MODE so there is no login screen, and
rem  binds to loopback. It lives inside the installed package, which a
rem  `uv tool upgrade pgadmin4` replaces wholesale - so restore it if it went
rem  missing rather than silently falling back to the multi-user defaults.
if not exist "%PKG%\config_local.py" (
    echo Restoring pgAdmin config_local.py ...
    copy /y "%REPO%\scripts\pgadmin_config_local.py" "%PKG%\config_local.py" >nul
    if errorlevel 1 (
        echo ERROR: could not write %PKG%\config_local.py
        exit /b 1
    )
    echo Re-registering the ccwebdb connection ...
    "%PGADMIN_CLI%" load-servers "%REPO%\scripts\pgadmin-servers.json" ^
        --user pgadmin4@pgadmin.org >nul
)

rem --- already running? -----------------------------------------------------
netstat -ano -p TCP | findstr /c:"127.0.0.1:%PORT%" | findstr /c:"LISTENING" >nul
if not errorlevel 1 (
    echo pgAdmin is already running.
    echo Opening %URL%
    start "" "%URL%"
    exit /b 0
)

rem --- start ----------------------------------------------------------------
echo Starting pgAdmin on %URL%
echo   (first run takes a few seconds while it builds its config database)
start "pgAdmin 4" /min "%PGADMIN%"

call :waiturl "%URL%" 60
if errorlevel 1 goto :notready
netstat -ano -p TCP | findstr /c:"127.0.0.1:%PORT%" | findstr /c:"LISTENING" >nul
if errorlevel 1 goto :notready

echo pgAdmin is up. Opening the browser.
start "" "%URL%"
echo.
echo   Connection:  ccwebdb ^(local^)  -  localhost:5432, user ccwebdb
echo   Password:    devpassword       -  tick "Save Password" to be asked once
echo.
echo   The friendly rows are in the VIEWS, not the tables:
echo     Databases ^> ccwebdb ^> Schemas ^> public ^> Views
echo     coin_inventory, currency_inventory, item_valuation, public_catalog
echo.
echo   Stop it by closing the "pgAdmin 4" window.
exit /b 0

:notready
echo.
echo ERROR: pgAdmin did not answer on %URL% within 60 seconds.
echo        Check its log: %APPDATA%\pgAdmin\pgadmin4.log
exit /b 1

rem ---------------------------------------------------------------------------
rem  :waiturl <url> <max seconds>  - poll until it answers, or give up
rem ---------------------------------------------------------------------------
:waiturl
set /a _tries=0
:waiturl_loop
curl -s -o nul --max-time 2 "%~1" >nul 2>&1
if not errorlevel 1 exit /b 0
set /a _tries+=1
if !_tries! GEQ %~2 exit /b 1
rem  One second. `timeout /t` is the obvious choice and is what
rem  ccweb_startup.cmd uses, but it aborts with "Input redirection is not
rem  supported" whenever stdin is not a console - which is any invocation from
rem  a script, a CI step or a tool. ping has no such restriction: -n 2 sends
rem  two packets one second apart, so it waits ~1s.
ping -n 2 127.0.0.1 >nul 2>&1
goto waiturl_loop
