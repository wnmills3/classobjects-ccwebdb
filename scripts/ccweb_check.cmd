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
rem  Put the ccwebdb conda environment in play and take ENVDIR and PGBIN
rem  from it, rather than from a guessed path. See ccweb_env.cmd.
call "%~dp0ccweb_env.cmd" || exit /b 2
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

echo === mutation scaffolding guard ===
rem  An ordered mutation test disables a guard on purpose -- an `if False:`
rem  in place of a shop-guard predicate is the shape this findstr catches
rem  directly. A mutation that leaves no such literal, such as deleting an
rem  `AdminUser` dependency from an endpoint, is invisible to this stage on
rem  its own -- there is nothing here to grep. The rule that makes that
rem  mutation catchable too is a convention, not code: leave a MUTATION
rem  comment at the site of ANY deliberate mutation that would otherwise
rem  leave no trace, and this stage catches the marker even though it
rem  cannot see the mutation itself. A mutation left with no `if False:`,
rem  `if True:`, or MUTATION marker is simply not detectable here -- this
rem  stage enforces the marker convention, not the absence of bugs.
rem  MUTATION is matched case-sensitively (no /I): the convention is to
rem  spell it in caps, and `if False:`/`if True:` have no lowercase form in
rem  Python, so nothing is lost. This is a literal-text check, not a parser:
rem  `if 0:`, `if not True:`, or a `False` with unusual spacing all evade it.
rem  `backend\app` currently contains none of these three, so any match here
rem  is new and real -- there is no backlog to grandfather.
set "MUTATION_HIT="
for /f "delims=" %%L in ('findstr /S /N /C:"if False:" /C:"if True:" /C:"MUTATION" "%REPO%\backend\app\*.py" 2^>nul') do (
    echo   left in place: %%L
    set "MUTATION_HIT=1"
)
if defined MUTATION_HIT (
    echo   FAILED: mutation-test scaffolding was left in backend\app -- see the line^(s^) above
    set "FAILED=!FAILED! mutation-scaffold"
)

echo === python types ===
rem Fails the build like the rest: with no findings standing, a single new
rem one is the signal.
"%PY%" -m mypy
if errorlevel 1 set "FAILED=!FAILED! types"

echo === tests ===
pushd backend
"%PY%" -m pytest -q
if errorlevel 1 set "FAILED=!FAILED! tests"
popd

if exist "frontend\node_modules\eslint" (
    echo === frontend lint ===
    "%NODE%" frontend\node_modules\eslint\bin\eslint.js --max-warnings 0 frontend
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
    rem  Every frontend gate lives in the block above, so a missing
    rem  node_modules runs none of them -- including the bundle-isolation
    rem  check. It fails the run: an unrun check is not a passed one.
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
