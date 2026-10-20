@echo off
rem ---------------------------------------------------------------------------
rem  Open psql against the development database.
rem
rem  Every argument is passed straight through to psql, so the usual flags work:
rem
rem    .\scripts\ccweb_psql.cmd                              interactive shell
rem    .\scripts\ccweb_psql.cmd -c "select * from metal;"    run one statement
rem    .\scripts\ccweb_psql.cmd -f query.sql                 run a file
rem    .\scripts\ccweb_psql.cmd -c "\d inventory_item"       describe a table
rem
rem  Defaults (localhost:5432, ccwebdb/ccwebdb, devpassword) match
rem  backend\app\config.py's DATABASE_URL. Override any of them by setting
rem  the standard PG* variables before calling, e.g. set PGDATABASE=ccwebdb_test
rem ---------------------------------------------------------------------------
setlocal

rem  Put the ccwebdb conda environment in play and take ENVDIR and PGBIN
rem  from it, rather than from a guessed path. See ccweb_env.cmd.
call "%~dp0ccweb_env.cmd" || exit /b 2
rem  The database the API uses: DATABASE_URL's, from .env.
call "%~dp0ccweb_settings.cmd" --include-password || exit /b 2

if not defined PGHOST     set "PGHOST=%CCWEB_DB_HOST%"
if not defined PGPORT     set "PGPORT=%CCWEB_DB_PORT%"
if not defined PGDATABASE set "PGDATABASE=%CCWEB_DB_NAME%"
if not defined PGUSER     set "PGUSER=%CCWEB_DB_USER%"
if not defined PGPASSWORD set "PGPASSWORD=%CCWEB_DB_PASSWORD%"

if not exist "%PGBIN%\psql.exe" (
    echo ERROR: psql not found at %PGBIN%
    echo        see docs\environment-setup.md
    exit /b 1
)

"%PGBIN%\psql.exe" %*
exit /b %errorlevel%
