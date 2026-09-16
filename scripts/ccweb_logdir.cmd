@echo off
rem ---------------------------------------------------------------------------
rem  ccweb_logdir.cmd - set LOGS, the directory the runtime writes its logs to.
rem
rem  CCWEB_LOG_DIR names it and defaults to .\logs. A relative path is taken
rem  from the repo root, not from wherever the caller happens to stand, so
rem  startup and shutdown always agree. LOGS is always absolute, with no
rem  trailing backslash.
rem
rem  Called by ccweb_startup.cmd and ccweb_shutdown.cmd once REPO is set. No
rem  setlocal: setting the caller's LOGS is the point.
rem ---------------------------------------------------------------------------
if not defined CCWEB_LOG_DIR set "CCWEB_LOG_DIR=.\logs"
pushd "%REPO%"
for %%I in ("%CCWEB_LOG_DIR%") do set "LOGS=%%~fI"
popd
if "%LOGS:~-1%"=="\" if not "%LOGS:~-2,1%"==":" set "LOGS=%LOGS:~0,-1%"
exit /b 0
