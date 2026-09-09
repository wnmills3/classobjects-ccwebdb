@echo off
rem ---------------------------------------------------------------------------
rem  Start the local SonarQube server (Community Build) and its PostgreSQL.
rem
rem    ccweb_sonar_start
rem
rem  Pure cmd - no PowerShell. Uses curl for readiness (curl.exe ships with
rem  Windows 10 1803 and later).
rem
rem  The database publishes no host port; SonarQube reaches it over the private
rem  podman network. SonarQube binds 127.0.0.1 only, so it is unreachable from
rem  the LAN whatever the firewall says.
rem
rem  Contract: pre-flights podman and port 9000, then exits non-zero unless
rem  /api/system/status is confirmed UP within the poll window.
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion

set "NET=sonar-net"
set "DBNAME=sonar-db"
set "SQNAME=sonarqube"
set "SQPORT=9000"
set "DBPASS=sonar"

podman info >nul 2>&1
if errorlevel 1 (
    echo ERROR: podman is not responding. Run: podman machine start
    exit /b 1
)

rem  Port must be free, unless it is already our own container holding it.
podman ps --format "{{.Names}}" | findstr /x "%SQNAME%" >nul 2>&1
if errorlevel 1 (
    netstat -ano -p TCP | findstr /R /C:":%SQPORT%  *[0-9]" | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        echo ERROR: port %SQPORT% is already in use by another process.
        exit /b 1
    )
)

podman network exists %NET% >nul 2>&1
if errorlevel 1 podman network create %NET% >nul

for %%V in (sonar-db-data sonarqube-data sonarqube-extensions sonarqube-logs) do (
    podman volume exists %%V >nul 2>&1
    if errorlevel 1 podman volume create %%V >nul
)

podman container exists %DBNAME% >nul 2>&1
if errorlevel 1 (
    echo [start] creating %DBNAME%
    podman run -d --name %DBNAME% --network %NET% ^
        -e POSTGRES_USER=sonar -e POSTGRES_PASSWORD=%DBPASS% -e POSTGRES_DB=sonar ^
        -v sonar-db-data:/var/lib/postgresql/data ^
        docker.io/library/postgres:16 >nul
) else (
    podman start %DBNAME% >nul
)

podman container exists %SQNAME% >nul 2>&1
if errorlevel 1 (
    echo [start] creating %SQNAME%
    podman run -d --name %SQNAME% --network %NET% ^
        -p 127.0.0.1:%SQPORT%:9000 ^
        -e SONAR_JDBC_URL=jdbc:postgresql://%DBNAME%:5432/sonar ^
        -e SONAR_JDBC_USERNAME=sonar ^
        -e SONAR_JDBC_PASSWORD=%DBPASS% ^
        -v sonarqube-data:/opt/sonarqube/data ^
        -v sonarqube-extensions:/opt/sonarqube/extensions ^
        -v sonarqube-logs:/opt/sonarqube/logs ^
        docker.io/library/sonarqube:community >nul
) else (
    podman start %SQNAME% >nul
)

echo [start] waiting for SonarQube to report UP - first boot takes 1-3 minutes
call :waitup "http://127.0.0.1:%SQPORT%/api/system/status" 300
if errorlevel 1 (
    echo ERROR: SonarQube did not reach status UP. Last 30 log lines:
    podman logs --tail 30 %SQNAME%
    exit /b 1
)

echo.
echo SonarQube is up:  http://localhost:%SQPORT%
echo First login is admin / admin - you will be forced to change it.
exit /b 0

rem ---------------------------------------------------------------------------
rem  :waitup <status url> <max seconds>  - poll until the body reports UP.
rem  Statuses are STARTING, UP, DOWN, RESTARTING and DB_MIGRATION_*; only UP
rem  contains the letters "UP", so a plain findstr is unambiguous here.
rem ---------------------------------------------------------------------------
:waitup
set "_tries=0"
:waitup_loop
curl -s --max-time 3 "%~1" 2>nul | findstr "UP" >nul 2>&1
if not errorlevel 1 exit /b 0
set /a _tries+=1
if !_tries! GEQ %~2 exit /b 1
rem  timeout.exe needs a real console and fails instantly - with no sleep at
rem  all - when stdin is redirected, which is exactly how an automated or
rem  scheduled caller runs this script. Do not "simplify" this back to
rem  timeout; ping's reply delay is a console-independent ~1 second sleep.
ping -n 2 127.0.0.1 >nul
goto waitup_loop
