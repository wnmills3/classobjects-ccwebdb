@echo off
rem ---------------------------------------------------------------------------
rem  Open psql against the development database.
rem
rem  Every argument is passed straight through to psql, so the usual flags work:
rem
rem    ccweb_psql                          interactive shell
rem    ccweb_psql -c "select * from metal;"    run one statement
rem    ccweb_psql -f query.sql                 run a file
rem    ccweb_psql -c "\d inventory_item"       describe a table
rem
rem  Connection settings match app\config.py. Override any of them by setting
rem  the standard PG* variables before calling, e.g. set PGDATABASE=ccwebdb_test
rem ---------------------------------------------------------------------------
setlocal

set "PGBIN=%USERPROFILE%\miniforge3\envs\ccwebdb\Library\bin"

if not defined PGHOST     set "PGHOST=localhost"
if not defined PGPORT     set "PGPORT=5432"
if not defined PGDATABASE set "PGDATABASE=ccwebdb"
if not defined PGUSER     set "PGUSER=ccwebdb"
if not defined PGPASSWORD set "PGPASSWORD=devpassword"

if not exist "%PGBIN%\psql.exe" (
    echo ERROR: psql not found at %PGBIN%
    echo        see docs\environment-setup.md
    exit /b 1
)

"%PGBIN%\psql.exe" %*
exit /b %errorlevel%
