@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ---------------------------------------------------------------------------
rem  ccweb_status.cmd - what is running, what is not, and how to start it.
rem
rem  Read-only: this script starts and stops nothing.
rem
rem  Each service is checked TWICE where it can be. A listening port only says
rem  something holds it; it does not say the thing answers. A backend that is
rem  wedged, still starting, or serving from a database it can no longer reach
rem  holds port 8000 exactly like a healthy one does, so the port check is
rem  paired with a request. "LISTENING but not answering" is a real state and
rem  gets its own word rather than being rounded up to RUNNING.
rem
rem  Exit codes, so a caller can branch on this:
rem    0  everything required is up
rem    1  something required is down or degraded
rem    2  the environment itself is not ready: ccwebdb cannot be activated,
rem       or the preflight section found something missing
rem
rem  Pure cmd - no PowerShell. netstat for PIDs, curl for readiness (curl.exe
rem  ships with Windows 10 1803 and later).
rem ---------------------------------------------------------------------------

for %%I in ("%~dp0..") do set "REPO=%%~fI"

set "PGDATA=%REPO%\.pgdata"
set "BPORT=8000"
set "FPORT=5173"
set "SPORT=9000"

set "DOWN="
set "BLOCKED="

echo ============================================
echo   ccwebdb runtime status
echo ============================================

rem --- 0. Environment -------------------------------------------------------
rem  Put ccwebdb in play the same way ccweb_startup.cmd does, and say whether
rem  the shell running this already had it. A failure is reported rather than
rem  fatal: "why can't I start?" is the question this script exists to answer,
rem  and the rest of the list is still worth seeing.
set "ENVOK="
set "PGBIN="
call "%~dp0ccweb_env.cmd" && set "ENVOK=1"
if not defined ENVOK (
    echo   Environment  conda ccwebdb    NOT AVAILABLE
    set "BLOCKED=1"
) else if /i "!CCWEB_ENV_STATE!"=="already active" (
    echo   Environment  conda ccwebdb    active in this shell
) else (
    echo   Environment  conda ccwebdb    activated for this check ^(not active in this shell^)
)

rem --- 1. PostgreSQL --------------------------------------------------------
rem  pg_isready asks the postmaster, rather than looking for a listening
rem  socket: a cluster still starting up answers "rejecting connections", and
rem  that is not the same as being up. Without the environment there is no
rem  pg_isready to ask, and a failed call looks exactly like "stopped" -- so
rem  that case says UNKNOWN instead of guessing.
set "PGUP="
if defined PGBIN (
    "%PGBIN%\pg_isready.exe" -h localhost -p 5432 >nul 2>&1
    if not errorlevel 1 set "PGUP=1"
)
if not defined PGBIN (
    echo   PostgreSQL   localhost:5432   UNKNOWN - no environment to ask with
    set "DOWN=!DOWN! postgresql"
) else if defined PGUP (
    echo   PostgreSQL   localhost:5432   RUNNING
) else (
    echo   PostgreSQL   localhost:5432   stopped
    set "DOWN=!DOWN! postgresql"
)

rem --- 2. Backend -----------------------------------------------------------
rem  /api/reference/item_status is public and reads the database, so a 200
rem  here means the backend is up AND talking to PostgreSQL. A port held with
rem  no answer is reported as its own state, not as running.
call :portpid %BPORT% BPID
set "BOK="
if defined BPID (
    curl -s -f -o nul --max-time 5 "http://127.0.0.1:%BPORT%/api/reference/item_status" && set "BOK=1"
)
if defined BOK (
    echo   Backend      127.0.0.1:%BPORT%   RUNNING   pid !BPID!
) else (
    if defined BPID (
        echo   Backend      127.0.0.1:%BPORT%   LISTENING but not answering   pid !BPID!
        set "DOWN=!DOWN! backend"
    ) else (
        echo   Backend      127.0.0.1:%BPORT%   stopped
        set "DOWN=!DOWN! backend"
    )
)

rem --- 3. Frontend ----------------------------------------------------------
call :portpid %FPORT% FPID
set "FOK="
if defined FPID (
    curl -s -f -o nul --max-time 5 "http://127.0.0.1:%FPORT%/management" && set "FOK=1"
)
if defined FOK (
    echo   Frontend     127.0.0.1:%FPORT%   RUNNING   pid !FPID!
) else (
    if defined FPID (
        echo   Frontend     127.0.0.1:%FPORT%   LISTENING but not answering   pid !FPID!
        set "DOWN=!DOWN! frontend"
    ) else (
        echo   Frontend     127.0.0.1:%FPORT%   stopped
        set "DOWN=!DOWN! frontend"
    )
)

rem --- 4. SonarQube (optional) ----------------------------------------------
rem  Not part of the runtime and never counted as "down": code quality is
rem  inspected on demand, and reporting it as missing would train the reader
rem  to ignore this whole list.
call :portpid %SPORT% SPID
if defined SPID (
    echo   SonarQube    127.0.0.1:%SPORT%   running   pid !SPID!   ^(optional^)
) else (
    echo   SonarQube    127.0.0.1:%SPORT%   stopped                ^(optional^)
)

rem --- preflight: things that would make a start fail ------------------------
rem  Checked only when something is actually down. A missing cluster matters
rem  when you are about to start PostgreSQL; it is noise when it is running.
if defined DOWN (
    if not exist "%PGDATA%\PG_VERSION" (
        echo.
        echo   BLOCKED: no PostgreSQL cluster at %PGDATA%
        echo            see docs\environment-setup.md
        set "BLOCKED=1"
    )
    if not exist "%REPO%\frontend\node_modules\vite\bin\vite.js" (
        echo.
        echo   BLOCKED: frontend dependencies missing
        echo            run:  cd frontend ^&^& npm install
        set "BLOCKED=1"
    )
)

rem --- what to do next ------------------------------------------------------
echo.
if not defined DOWN (
    echo   Everything is up.
    echo.
    echo     shop     http://127.0.0.1:%FPORT%/
    echo     console  http://127.0.0.1:%FPORT%/management
    echo     API docs http://127.0.0.1:%BPORT%/docs
    echo ============================================
    exit /b 0
)

echo   Not running:!DOWN!
echo.
if defined BLOCKED (
    echo   Fix the BLOCKED item^(s^) above first - startup will fail otherwise.
    echo ============================================
    exit /b 2
)

rem  One command brings up all three and leaves anything already listening
rem  alone, so it is the right answer whether one service is down or all of
rem  them. Invoked as .\ - NoDefaultCurrentDirectoryInExePath is set on this
rem  machine, so a bare name is "not recognized" even in this directory.
echo   To start:
echo.
echo       .\scripts\ccweb_startup.cmd
echo.
echo   ^(safe to re-run: whatever is already listening is left alone^)
echo ============================================
exit /b 1

rem ---------------------------------------------------------------------------
:portpid
rem  %1 = TCP port, %2 = variable to receive the listening PID.
rem  The findstr pattern anchors on ":<port> " so 5173 does not match 51730.
set "%~2="
for /f "tokens=5" %%A in ('netstat -ano -p TCP ^| findstr /R /C:":%~1  *[0-9]" ^| findstr "LISTENING"') do set "%~2=%%A"
goto :eof
