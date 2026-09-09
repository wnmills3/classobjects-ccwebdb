@echo off
rem ---------------------------------------------------------------------------
rem  Publish an analysis of this repository to the local SonarQube server.
rem
rem    ccweb_sonar_scan
rem
rem  Requires SONAR_TOKEN in the environment: the scanner runs in its own
rem  container and cannot read the host keychain.
rem
rem  Contract: pre-flights the server and SONAR_TOKEN, then exits non-zero
rem  (and refuses to publish) if the test suite fails or the scanner fails.
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion

for %%I in ("%~dp0..") do set "REPO=%%~fI"
set "SQPORT=9000"

rem  Fail fast rather than starting the server implicitly. An implicit start
rem  would hide a stopped server and make a stale dashboard look current.
curl -s -f -o nul --max-time 3 "http://127.0.0.1:%SQPORT%/api/system/status"
if errorlevel 1 (
    echo ERROR: SonarQube is not responding on port %SQPORT%.
    echo        Run scripts\ccweb_sonar_start.cmd first.
    exit /b 1
)

if "%SONAR_TOKEN%"=="" (
    echo ERROR: SONAR_TOKEN is not set. Generate one at
    echo        http://localhost:%SQPORT%/account/security
    echo        then:  set "SONAR_TOKEN=squ_..."
    exit /b 1
)

pushd "%REPO%"

set "ENVDIR=%USERPROFILE%\miniforge3\envs\ccwebdb"
set "PY=%ENVDIR%\python.exe"

if not exist "%PY%" (
    echo ERROR: conda environment not found: %ENVDIR%
    echo        see docs\environment-setup.md
    popd
    exit /b 1
)

echo === tests with coverage ===
rem  Non-zero coverage needs all three of: relative_files = true under
rem  [tool.coverage.run] in pyproject.toml, running from the repository root
rem  (this "pushd" above), and --cov=backend/app. Running from backend, the
rem  way ccweb_check.cmd does, records paths like app/db.py, which sonar.sources
rem  (backend/app) cannot map - a silent 0% that looks like clean code.
"%PY%" -m pytest -q --cov=backend/app --cov-report=xml:coverage.xml
if errorlevel 1 (
    echo ERROR: tests failed - refusing to publish an analysis.
    popd
    exit /b 1
)

echo === scanner ===
podman run --rm --network sonar-net ^
    -e SONAR_HOST_URL=http://sonarqube:9000 ^
    -e SONAR_TOKEN=%SONAR_TOKEN% ^
    -v "%REPO%:/usr/src" ^
    docker.io/sonarsource/sonar-scanner-cli
if errorlevel 1 (
    echo ERROR: scanner failed.
    popd
    exit /b 1
)

popd
echo.
echo Published: http://localhost:%SQPORT%/dashboard?id=classobjects-ccwebdb
exit /b 0
