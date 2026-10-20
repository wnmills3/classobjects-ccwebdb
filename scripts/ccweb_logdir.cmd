@echo off
rem ---------------------------------------------------------------------------
rem  ccweb_logdir.cmd - set LOGS, the directory the runtime writes its logs to.
rem
rem  CCWEB_LOG_DIR names it (default logs, in the repository); ccweb_settings
rem  has already made it absolute, taking a relative path from the repo root,
rem  not from wherever the caller happens to stand, so startup and shutdown
rem  always agree.
rem
rem  Called by ccweb_startup.cmd, ccweb_shutdown.cmd, ccweb_claude.cmd,
rem  ccweb_backup.cmd and ccweb_testdb.cmd after ccweb_settings.cmd. No
rem  setlocal: setting the caller's LOGS is the point.
rem ---------------------------------------------------------------------------
set "LOGS=%CCWEB_LOG_DIR%"
exit /b 0
