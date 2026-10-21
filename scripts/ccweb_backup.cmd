@echo off
rem ---------------------------------------------------------------------------
rem  One whole backup of the collection, proved before anything older goes.
rem
rem    .\scripts\ccweb_backup.cmd                    back up now, into the backup folder
rem    .\scripts\ccweb_backup.cmd /schedule [HH:MM]  run it every day at that time (20:00),
rem                                                  signed in or not (administrator cmd)
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
rem  The folder is CCWEB_BACKUP_DIR, read from .env through ccweb_settings
rem  (unset: ccwebdb-backup in your home folder). Set it in .env, which the
rem  daily task reads too; a variable set only in this window it never sees.
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

rem  The log folder and the backup folder are settings, which the
rem  environment's Python reads; without it there is no backup.log to
rem  write to, and the failure is said here only. A scheduled run's
rem  result is also in the task's own Last Result.
call "%~dp0ccweb_env.cmd"
if errorlevel 1 (
    echo backup FAILED - the ccwebdb environment could not be put in play; no backup was made
    exit /b 2
)
call "%~dp0ccweb_settings.cmd"
if errorlevel 1 (
    echo backup FAILED - a setting in .env was refused; no backup was made
    exit /b 2
)
call "%~dp0ccweb_logdir.cmd"
if not exist "%LOGS%\" mkdir "%LOGS%"
echo ==== %DATE% %TIME% ==== >> "%LOGS%\backup.log"

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
rem  A time given here is checked as the setting is, as in the bash twin.
if not "%~2"=="" set "CCWEB_BACKUP_TIME=%~2"
call "%~dp0ccweb_env.cmd" || exit /b 2
call "%~dp0ccweb_settings.cmd" || exit /b 2
set "AT=%CCWEB_BACKUP_TIME%"
rem  /NP: the task runs as this user whether or not anyone is signed in,
rem  and no password is stored -- in exchange it reaches only this machine,
rem  which is all a backup needs. Windows allows that only from an
rem  administrator cmd. No /RU: the task is the current user's either way,
rem  and with /RU schtasks asks for a password despite /NP.
schtasks /Create /TN "%TASK%" /TR "\"%REPO%\scripts\ccweb_backup.cmd\"" /SC DAILY /ST %AT% /NP /F
if errorlevel 1 (
    echo ERROR: the daily backup could not be scheduled - run this from an administrator cmd
    exit /b 1
)
if not exist "%CCWEB_BACKUP_DIR%" mkdir "%CCWEB_BACKUP_DIR%"
rem  Signed out, OneDrive is not running to fetch a file kept only online,
rem  so a backup folder inside OneDrive is pinned to stay on this device; it
rem  syncs at the next sign-in. Any other folder is left as it is.
set "PIN="
for %%O in ("%OneDrive%" "%OneDriveConsumer%" "%OneDriveCommercial%") do call :in_onedrive "%%~O"
if defined PIN (
    attrib +P -U "%CCWEB_BACKUP_DIR%"
    attrib +P -U /S /D "%CCWEB_BACKUP_DIR%\*"
)
echo the backup runs every day at %AT%, signed in or not; .\scripts\ccweb_backup.cmd /unschedule stops it
exit /b 0

:in_onedrive
rem  Sets PIN when CCWEB_BACKUP_DIR is the folder %1 or one inside it. cmd's
rem  substitution and if /i both ignore case, as Windows paths do.
if "%~1"=="" exit /b 0
set "ROOT=%~1"
call set "REST=%%CCWEB_BACKUP_DIR:%ROOT%=%%"
if /i not "%ROOT%%REST%"=="%CCWEB_BACKUP_DIR%" exit /b 0
if not defined REST (
    set "PIN=1"
    exit /b 0
)
if "%REST:~0,1%"=="\" set "PIN=1"
exit /b 0

:unschedule
schtasks /Delete /TN "%TASK%" /F
exit /b %errorlevel%

:scheduled
schtasks /Query /TN "%TASK%" /FO LIST
exit /b %errorlevel%
