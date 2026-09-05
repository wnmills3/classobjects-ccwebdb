@echo off
setlocal EnableExtensions
rem ---------------------------------------------------------------------------
rem  ccweb_claude.cmd - activate the ccwebdb conda environment and start Claude
rem                     Code in the repository root.
rem
rem  Usage:  scripts\ccweb_claude.cmd                  new session
rem          scripts\ccweb_claude.cmd ccweb            resume, searching "ccweb"
rem          scripts\ccweb_claude.cmd 01G7CtsF...      resume that session id
rem          scripts\ccweb_claude.cmd ccweb --effort high   extra flags pass through
rem          scripts\ccweb_claude.cmd /check           activate and report, do not start
rem
rem  claude's -r/--resume takes a session id, or any other value as a search
rem  term for the interactive picker - so a session name works.
rem
rem  Activating the environment matters: the status line reads
rem  CONDA_DEFAULT_ENV, and node, npm, psql and pg_ctl are only on PATH inside
rem  the environment.
rem ---------------------------------------------------------------------------

for %%I in ("%~dp0..") do set "REPO=%%~fI"
set "ENVNAME=ccwebdb"
set "CONDABAT=%USERPROFILE%\miniforge3\condabin\conda.bat"

set "CHECKONLY="
if /I "%~1"=="/check" set "CHECKONLY=1"

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
