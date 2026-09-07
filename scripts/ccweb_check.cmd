@echo off
rem ---------------------------------------------------------------------------
rem  Run every quality gate, in the order that fails fastest.
rem
rem    ccweb_check          format check, lint, types, tests, frontend
rem    ccweb_check fix      reformat and auto-fix first, then check
rem
rem  Exit code is non-zero if anything failed, so CI can call this directly.
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion

set "REPO=%~dp0.."
set "ENVDIR=%USERPROFILE%\miniforge3\envs\ccwebdb"
set "PY=%ENVDIR%\python.exe"
set "NODE=%ENVDIR%\node.exe"
set "FAILED="

if not exist "%PY%" (
    echo ERROR: conda environment not found: %ENVDIR%
    echo        see docs\environment-setup.md
    exit /b 1
)

pushd "%REPO%"

if /i "%~1"=="fix" (
    echo [fix] ruff format
    "%PY%" -m ruff format .
    echo [fix] ruff check --fix
    "%PY%" -m ruff check . --fix
    if exist "frontend\node_modules\prettier" (
        echo [fix] prettier
        "%NODE%" frontend\node_modules\prettier\bin\prettier.cjs --write frontend --log-level warn
    )
    echo.
)

echo === python format ===
"%PY%" -m ruff format --check .
if errorlevel 1 set "FAILED=!FAILED! format"

echo === python lint ===
"%PY%" -m ruff check .
if errorlevel 1 set "FAILED=!FAILED! lint"

echo === python types ===
rem Not yet a gate: mypy still reports findings in generic SQLAlchemy code.
rem The count is expected to fall, never rise -- see docs\code-quality.md.
"%PY%" -m mypy
if errorlevel 1 echo     (type findings are reported, not enforced -- see docs\code-quality.md)

echo === tests ===
pushd backend
"%PY%" -m pytest -q
if errorlevel 1 set "FAILED=!FAILED! tests"
popd

if exist "frontend\node_modules\eslint" (
    echo === frontend lint ===
    "%NODE%" frontend\node_modules\eslint\bin\eslint.js frontend
    if errorlevel 1 set "FAILED=!FAILED! eslint"

    echo === frontend format ===
    "%NODE%" frontend\node_modules\prettier\bin\prettier.cjs --check frontend --log-level warn
    if errorlevel 1 set "FAILED=!FAILED! prettier"
)

popd

echo.
if defined FAILED (
    echo FAILED:!FAILED!
    exit /b 1
)
echo All checks passed.
exit /b 0
