@echo off
rem ---------------------------------------------------------------------------
rem  One whole backup of the collection, proved before anything older goes.
rem
rem    .\scripts\ccweb_backup.cmd                    back up now, into the backup folder
rem    .\scripts\ccweb_backup.cmd /schedule [HH:MM]  run it every day at that time (20:00)
rem    .\scripts\ccweb_backup.cmd /unschedule        stop running it every day
rem    .\scripts\ccweb_backup.cmd /scheduled         show the daily task, if there is one
rem
rem  The backup itself is `python -m app.backup_run` (docs\system-
rem  administration.md, Backing up and restoring): it exports the database to
rem  a workbook, copies the photographs, restores the workbook into a scratch
rem  database and compares that with live, checks the photographs, and only
rem  then prunes obsolete photograph files and removes the older workbook.
rem  When any of that fails nothing is removed and the exit code is 1.
rem
rem  The folder is %USERPROFILE%\OneDrive\coins_backup; set CCWEB_BACKUP_DIR
rem  to keep it somewhere else -- as a user environment variable, or the
rem  daily task, which starts in a session of its own, does not see it.
rem  Everything the backup prints goes to backup.log in the logs folder, each
rem  run under a line with its date and time; a run that could not put the
rem  environment in play says so there too, because a scheduled run has no
rem  console for anyone to read.
rem
rem  PostgreSQL has to be running: a backup of a stopped database is a failed
rem  run, logged as one. A scheduled run the machine was off or asleep for is
rem  not made up later; the next day's runs as usual.
rem ---------------------------------------------------------------------------
setlocal EnableExtensions

for %%I in ("%~dp0..") do set "REPO=%%~fI"
set "TASK=ccwebdb backup"

if /i "%~1"=="/schedule" goto :schedule
if /i "%~1"=="/unschedule" goto :unschedule
if /i "%~1"=="/scheduled" goto :scheduled
if not "%~1"=="" (
    echo ERROR: unknown argument %1
    echo        .\scripts\ccweb_backup.cmd [/schedule [HH:MM] ^| /unschedule ^| /scheduled]
    exit /b 2
)

rem  The log's line for this run is written before anything that can fail,
rem  so a run that gets no further still leaves its date and the reason.
call "%~dp0ccweb_logdir.cmd"
if not exist "%LOGS%\" mkdir "%LOGS%"
echo ==== %DATE% %TIME% ==== >> "%LOGS%\backup.log"

rem  Put the ccwebdb conda environment in play. See ccweb_env.cmd.
call "%~dp0ccweb_env.cmd"
if errorlevel 1 (
    echo ERROR: the ccwebdb environment could not be put in play - no backup was made >> "%LOGS%\backup.log"
    echo backup FAILED - the ccwebdb environment could not be put in play; see %LOGS%\backup.log
    exit /b 2
)

if not defined CCWEB_BACKUP_DIR set "CCWEB_BACKUP_DIR=%USERPROFILE%\OneDrive\coins_backup"

pushd "%REPO%\backend"
"%ENVDIR%\python.exe" -m app.backup_run "%CCWEB_BACKUP_DIR%" >> "%LOGS%\backup.log" 2>&1
set "RC=%errorlevel%"
popd

if "%RC%"=="0" (
    echo backup proved, in %CCWEB_BACKUP_DIR%
) else (
    echo backup FAILED ^(exit %RC%^) - nothing was removed; see %LOGS%\backup.log
)
exit /b %RC%

:schedule
set "AT=%~2"
if "%AT%"=="" set "AT=20:00"
schtasks /Create /TN "%TASK%" /TR "\"%REPO%\scripts\ccweb_backup.cmd\"" /SC DAILY /ST %AT% /F
if errorlevel 1 (
    echo ERROR: the daily backup could not be scheduled
    exit /b 1
)
echo the backup runs every day at %AT%; .\scripts\ccweb_backup.cmd /unschedule stops it
exit /b 0

:unschedule
schtasks /Delete /TN "%TASK%" /F
exit /b %errorlevel%

:scheduled
schtasks /Query /TN "%TASK%" /FO LIST
exit /b %errorlevel%
