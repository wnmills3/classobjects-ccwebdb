@echo off
setlocal EnableExtensions EnableDelayedExpansion
rem ---------------------------------------------------------------------------
rem  ccweb_testdb.cmd - a PostgreSQL cluster of this computer's own, for tests.
rem
rem    .\scripts\ccweb_testdb.cmd          start it, creating it the first time
rem    .\scripts\ccweb_testdb.cmd stop     stop it
rem    .\scripts\ccweb_testdb.cmd status   say whether it exists and is running
rem
rem  If another computer shares the collection's cluster folder, for example
rem  through OneDrive, only one computer runs it at a time, so the other has
rem  no PostgreSQL to test against. This one (CCWEB_TEST_DB_CLUSTER_DIR,
rem  default .pgdata.test, on CCWEB_TEST_DB_PORT, default 5433) holds nothing
rem  but the databases a test session builds and drops, so the tests never
rem  build theirs inside the collection's cluster. It is never shared and
rem  never holds the collection, so its fixed development password protects
rem  nothing.
rem
rem  ccweb_check.cmd and ccweb_sonar_scan.cmd run
rem
rem      call "%~dp0ccweb_testdb.cmd" use
rem
rem  which, once .pgdata.test exists, starts it if needed and sets
rem  TEST_DATABASE_URL in the caller, so the tests run here and never build
rem  their databases inside the shared cluster. Where .pgdata.test does not
rem  exist, or TEST_DATABASE_URL is already set, "use" changes nothing.
rem ---------------------------------------------------------------------------

for %%I in ("%~dp0..") do set "REPO=%%~fI"

set "MODE=%~1"
if "%MODE%"=="" set "MODE=start"
if /i "%MODE%"=="use" if defined TEST_DATABASE_URL exit /b 0

call "%~dp0ccweb_env.cmd" || exit /b 2
call "%~dp0ccweb_settings.cmd" --include-password || exit /b 2
set "TESTDATA=%CCWEB_TEST_DB_CLUSTER_DIR%"
set "TESTHOST=%CCWEB_TEST_DB_HOST%"
set "TESTPORT=%CCWEB_TEST_DB_PORT%"
set "TESTURL=%CCWEB_TEST_DB_URL%"
set "TESTUSER=%CCWEB_TEST_DB_USER%"
rem  The superuser's password is the application role's: one password
rem  for a cluster that holds nothing but test databases.
set "PGPASSWORD=%CCWEB_TEST_DB_PASSWORD%"
if /i "%MODE%"=="use" if not exist "%TESTDATA%\PG_VERSION" exit /b 0
call "%~dp0ccweb_logdir.cmd"
if not exist "%LOGS%" mkdir "%LOGS%"

if /i "%MODE%"=="start" goto start
if /i "%MODE%"=="use" goto start
if /i "%MODE%"=="stop" goto stop
if /i "%MODE%"=="status" goto status
echo usage: ccweb_testdb.cmd [start ^| stop ^| status]
exit /b 2

:start
"%PGBIN%\pg_isready.exe" -h %TESTHOST% -p %TESTPORT% -U postgres -d postgres >nul 2>&1
if not errorlevel 1 goto ready

set "CREATED="
if exist "%TESTDATA%\PG_VERSION" goto launch
echo Creating the test cluster in %TESTDATA%
set "PWFILE=%TEMP%\ccweb_testdb_pw.txt"
rem  No space before the redirection: it would end up in the password.
>"%PWFILE%" echo %PGPASSWORD%
"%PGBIN%\initdb.exe" -D "%TESTDATA%" -U postgres --pwfile="%PWFILE%" --auth-host=scram-sha-256 --auth-local=scram-sha-256 --encoding=UTF8 >"%LOGS%\initdb-test.log" 2>&1
set "RC=!errorlevel!"
del "%PWFILE%"
if not "%RC%"=="0" (
    echo ERROR: initdb failed - see %LOGS%\initdb-test.log
    exit /b 1
)
set "CREATED=1"

:launch
rem  A console of its own, for the reason given in ccweb_pgstart.cmd. A crash
rem  can leave postmaster.pid behind; unlike the shared cluster's, no other
rem  computer can be running this one, so PostgreSQL is left to clear it.
start "ccweb-postgres-test" /MIN cmd /c ""%PGBIN%\pg_ctl.exe" -D "%TESTDATA%" -o "-p %TESTPORT% -c logging_collector=off" start 2>&1 | "%ENVDIR%\python.exe" "%REPO%\backend\app\logpipe.py" "%LOGS%\postgres-test.log""
set /a TRIES=0
:wait
ping -n 2 127.0.0.1 >nul
"%PGBIN%\pg_isready.exe" -h %TESTHOST% -p %TESTPORT% -U postgres -d postgres >nul 2>&1
if not errorlevel 1 goto started
set /a TRIES+=1
if !TRIES! lss 30 goto wait
echo ERROR: the test cluster did not start on port %TESTPORT% - see %LOGS%\postgres-test.log
exit /b 1

:started
rem  The application role, as in the shared cluster: allowed to create
rem  databases, not a superuser, so the tests meet the same permissions.
if not defined CREATED goto ready
"%PGBIN%\psql.exe" -h %TESTHOST% -p %TESTPORT% -U postgres -d postgres -q -v ON_ERROR_STOP=1 -c "CREATE ROLE %TESTUSER% WITH LOGIN PASSWORD '%PGPASSWORD%' CREATEDB;"
if errorlevel 1 (
    echo ERROR: could not create the %TESTUSER% role in the test cluster
    exit /b 1
)

:ready
if /i not "%MODE%"=="use" goto report
endlocal & set "TEST_DATABASE_URL=%TESTURL%"
exit /b 0

:report
echo Test cluster RUNNING on %TESTHOST%:%TESTPORT% (%TESTDATA%)
echo ccweb_check.cmd uses it by itself. For pytest on its own, first run:
echo     set "TEST_DATABASE_URL=%TESTURL%"
exit /b 0

:stop
"%PGBIN%\pg_isready.exe" -h %TESTHOST% -p %TESTPORT% -U postgres -d postgres >nul 2>&1
if errorlevel 1 (
    echo Test cluster is not running.
    exit /b 0
)
"%PGBIN%\pg_ctl.exe" -D "%TESTDATA%" stop -m fast
exit /b %errorlevel%

:status
if not exist "%TESTDATA%\PG_VERSION" (
    echo Test cluster   not created  ^(.\scripts\ccweb_testdb.cmd creates it^)
    exit /b 0
)
"%PGBIN%\pg_isready.exe" -h %TESTHOST% -p %TESTPORT% -U postgres -d postgres >nul 2>&1
if errorlevel 1 (
    echo Test cluster   %TESTHOST%:%TESTPORT%   stopped
) else (
    echo Test cluster   %TESTHOST%:%TESTPORT%   RUNNING
)
exit /b 0
