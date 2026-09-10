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
rem A gate since the backlog reached zero. It was reported-only while a
rem standing count of findings made a new one invisible; with none left, a
rem single new finding is the signal, so it fails the build like the rest.
"%PY%" -m mypy
if errorlevel 1 set "FAILED=!FAILED! types"

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

    echo === frontend tests ===
    rem  Run from frontend so vitest picks up vite.config.js; node is not
    rem  on PATH, so the script is invoked directly rather than via npm.
    pushd frontend
    "%NODE%" node_modules\vitest\vitest.mjs run
    if errorlevel 1 set "FAILED=!FAILED! vitest"
    popd

    echo === frontend bundle isolation ===
    rem  Builds both entries and asserts neither reaches the other's tree.
    rem  The eslint boundary rules check the source; this checks the artefact,
    rem  so a build-configuration mistake cannot pass unnoticed.
    pushd frontend
    rem  Clear dist first: a failed build can leave a previous good
    rem  build's dist\.vite\bundle-graph.json on disk, and the isolation
    rem  check has no way to tell that graph is stale rather than
    rem  current. Removing it here means a failed build leaves nothing
    rem  for the check to misread, on this run or a later manual one.
    if exist dist rmdir /s /q dist
    "%NODE%" node_modules\vite\bin\vite.js build
    if errorlevel 1 (
        set "FAILED=!FAILED! build"
    ) else (
        "%NODE%" scripts\check-bundle-isolation.mjs
        if errorlevel 1 set "FAILED=!FAILED! isolation"
    )
    popd
) else (
    rem  Silence here used to read as success: every frontend gate lives in
    rem  the block above, so a missing node_modules printed "All checks
    rem  passed" having run none of them -- including the bundle-isolation
    rem  check. An unrun check is not a passed one.
    echo === frontend === SKIPPED: frontend\node_modules is missing
    echo     run: cd frontend ^&^& npm install
    set "FAILED=!FAILED! frontend-not-installed"
)

popd

echo.
if defined FAILED (
    echo FAILED:!FAILED!
    exit /b 1
)
echo All checks passed.
exit /b 0
