@echo off
rem ---------------------------------------------------------------------------
rem  Stop the local SonarQube server and its database.
rem
rem  Volumes are deliberately kept, so issue history, settings and the token
rem  survive a stop. To discard everything, remove the volumes by hand:
rem      podman volume rm sonar-db-data sonarqube-data sonarqube-extensions sonarqube-logs
rem
rem  Exits non-zero if either container is still listed by podman afterwards -
rem  success is verified, not assumed (docs/runtime-operations.md, "Success is
rem  verified, not assumed").
rem
rem  Contract: exits non-zero unless podman confirms both containers are gone.
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion

podman stop sonarqube >nul 2>&1
podman stop sonar-db  >nul 2>&1

rem --- verify -----------------------------------------------------------
rem  A silent podman failure (machine unreachable, daemon down) must not
rem  read as success: a piped "for /f" only ever sees findstr's exit code,
rem  so check podman ps on its own first, before trusting an empty result.
podman ps --format "{{.Names}}" >nul 2>&1
if errorlevel 1 (
    echo ERROR: podman is not responding - cannot verify SonarQube stopped.
    exit /b 1
)

set "PROBLEM="
for /f "delims=" %%N in ('podman ps --format "{{.Names}}" 2^>nul') do (
    if /I "%%N"=="sonarqube" set "PROBLEM=1"
    if /I "%%N"=="sonar-db"  set "PROBLEM=1"
)

if defined PROBLEM (
    echo ERROR: SonarQube did not stop - still listed by podman ps:
    podman ps --format "{{.Names}} {{.Status}}" 2>nul | findstr /I "sonarqube sonar-db"
    exit /b 1
)

echo SonarQube stopped. Volumes kept.
exit /b 0
