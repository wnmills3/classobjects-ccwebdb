@echo off
rem ---------------------------------------------------------------------------
rem  Stop the local SonarQube server and its database.
rem
rem  Volumes are deliberately kept, so issue history, settings and the token
rem  survive a stop. To discard everything, remove the volumes by hand:
rem      podman volume rm sonar-db-data sonarqube-data sonarqube-extensions sonarqube-logs
rem ---------------------------------------------------------------------------
setlocal

podman stop sonarqube >nul 2>&1
podman stop sonar-db  >nul 2>&1

echo SonarQube stopped. Volumes kept.
exit /b 0
