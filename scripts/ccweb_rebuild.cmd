@echo off
rem ---------------------------------------------------------------------------
rem  Build a replacement database ALONGSIDE the live one. Nothing here touches
rem  `ccwebdb`: the DATABASE_URL override is confined to this script's own
rem  environment, so a mistake costs a scratch database and nothing else. The
rem  swap is a separate, deliberate step -- see docs/workflow-import-and-cleanup.md.
rem
rem  Order matters. `app.seed` creates five DEMO items with listings; run before
rem  the import they take CC-000002..CC-000006 and every real item is silently
rem  offset by five. Migrate, load reference data, import, derive, then seed.
rem
rem  `app.series_match`, `app.series_classify`, `app.classifier_defaults` and
rem  `app.serial_patterns` are NOT part of the import pipeline. Skipped,
rem  their work is simply absent from the result -- 3,273 series matches and
rem  498 serial designations -- with nothing to say so.
rem ---------------------------------------------------------------------------
setlocal
set "REPO=%~dp0.."
rem  Put the ccwebdb conda environment in play and take ENVDIR and PGBIN
rem  from it, rather than from a guessed path. See ccweb_env.cmd.
call "%~dp0ccweb_env.cmd" || exit /b 2
set "PY=%ENVDIR%\python.exe"
set "TARGET=ccwebdb_rebuild"
if "%~1" neq "" set "BOOK=%~1"
if not defined BOOK set "BOOK=%USERPROFILE%\OneDrive\wnm3_coins.xlsx"

set "PGHOST=localhost"
set "PGPORT=5432"
set "PGUSER=ccwebdb"
set "PGPASSWORD=devpassword"
set "DATABASE_URL=postgresql+psycopg://ccwebdb:devpassword@localhost:5432/%TARGET%"

if not exist "%BOOK%" (
    echo ERROR: workbook not found: %BOOK%
    exit /b 1
)

echo === dropping any previous %TARGET% ===
"%PGBIN%\dropdb.exe" --if-exists %TARGET%
if errorlevel 1 goto :failed

echo === creating %TARGET% ===
"%PGBIN%\createdb.exe" %TARGET%
if errorlevel 1 goto :failed

cd /d "%REPO%\backend"

echo === alembic upgrade head ===
"%PY%" -m alembic upgrade head
if errorlevel 1 goto :failed

echo === reference data ===
"%PY%" -m app.seeding load
if errorlevel 1 goto :failed

echo === importing %BOOK% ===
"%PY%" -m app.importers.cli --file "%BOOK%" --commit --quiet
if errorlevel 1 goto :failed

echo === series match ===
"%PY%" -m app.series_match --commit
if errorlevel 1 goto :failed

rem  After series_match: text first, then the facts for what text left.
echo === series classify ===
"%PY%" -m app.series_classify --commit
if errorlevel 1 goto :failed

rem  Then what the facts decide: a note's class, seal, signatures and
rem  Reserve Bank, a coin's composition.
echo === classifier defaults ===
"%PY%" -m app.classifier_defaults --commit
if errorlevel 1 goto :failed

echo === serial patterns ===
"%PY%" -m app.serial_patterns --commit
if errorlevel 1 goto :failed

echo === demo seed (last, so it cannot offset real item codes) ===
"%PY%" -m app.seed
if errorlevel 1 goto :failed

echo.
echo Built %TARGET%. The live database was not touched.
echo Next: compare it against live, then swap deliberately.
exit /b 0

:failed
echo.
echo REBUILD FAILED - %TARGET% is incomplete. The live database was not touched.
exit /b 1
