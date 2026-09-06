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
set "CONDABAT=%USERPROFILE%\miniforge3\condabin\conda.bat"
set "PGBIN=%USERPROFILE%\miniforge3\envs\%ENVNAME%\Library\bin"
set "PGDATA=%REPO%\.pgdata"

set "CHECKONLY="
set "SKIPDB="
:parseargs
if /I "%~1"=="/check" ( set "CHECKONLY=1" & shift & goto parseargs )
if /I "%~1"=="/nodb"  ( set "SKIPDB=1"    & shift & goto parseargs )

rem --- preflight ------------------------------------------------------------
if not exist "%CONDABAT%" (
    echo ERROR: conda not found at %CONDABAT%
    echo        see docs\environment-setup.md
    exit /b 1
)

rem --- activate -------------------------------------------------------------
call "%CONDABAT%" activate %ENVNAME%
if errorlevel 1 (
    echo ERROR: could not activate the "%ENVNAME%" conda environment
    echo        create it with:  conda create -n %ENVNAME% python=3.13
    exit /b 1
)

if /I not "%CONDA_DEFAULT_ENV%"=="%ENVNAME%" (
    echo ERROR: expected CONDA_DEFAULT_ENV=%ENVNAME%, got "%CONDA_DEFAULT_ENV%"
    exit /b 1
)

cd /d "%REPO%"

echo environment  %CONDA_DEFAULT_ENV%
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
            "%PGBIN%\pg_ctl.exe" -D "%PGDATA%" -l "%PGDATA%\server.log" -w start >nul 2>&1
            "%PGBIN%\pg_isready.exe" -h localhost -p 5432 >nul 2>&1
            if errorlevel 1 (
                set "DBSTATE=FAILED TO START - see .pgdata\server.log"
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
where claude >nul 2>&1
if errorlevel 1 (
    echo ERROR: "claude" is not on PATH
    echo        expected in %%USERPROFILE%%\.local\bin
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
