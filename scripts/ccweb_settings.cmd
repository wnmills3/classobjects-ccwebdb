@echo off
rem ---------------------------------------------------------------------------
rem  ccweb_settings.cmd - put this repository's settings in the caller's scope.
rem
rem  Usage, from another script in this directory, after ccweb_env.cmd:
rem
rem      call "%~dp0ccweb_settings.cmd" || exit /b 2
rem      call "%~dp0ccweb_settings.cmd" --include-password || exit /b 2
rem
rem  Sets CCWEB_API_PORT, CCWEB_DB_CLUSTER_DIR and the rest, as
rem  backend\app\runtime_settings.py gives them: from .env, then the defaults
rem  there. A script never writes a port or a folder of its own. A value
rem  already in the environment wins over .env, as it does for the API.
rem  --include-password adds CCWEB_DB_PASSWORD and CCWEB_TEST_DB_PASSWORD.
rem
rem  No setlocal: setting the caller's variables is the point, so call it
rem  from inside the caller's own setlocal. Never from ccweb_env.cmd, which
rem  people also run in an interactive shell, where these would then outrank
rem  later edits to .env.
rem
rem  Exit 0 when the settings are set; 2, with Python's message naming the
rem  setting, when one is refused.
rem ---------------------------------------------------------------------------
if not defined ENVDIR (
    echo ERROR: ccweb_settings.cmd needs ccweb_env.cmd to have run first
    exit /b 2
)
set "CCWEB_SETTINGS_FILE=%TEMP%\ccweb_settings_%RANDOM%%RANDOM%.cmd"
pushd "%~dp0..\backend"
"%ENVDIR%\python.exe" -m app.runtime_settings --format cmd %1 >"%CCWEB_SETTINGS_FILE%"
set "CCWEB_SETTINGS_RC=%errorlevel%"
popd
if not "%CCWEB_SETTINGS_RC%"=="0" (
    del "%CCWEB_SETTINGS_FILE%" >nul 2>&1
    set "CCWEB_SETTINGS_FILE="
    set "CCWEB_SETTINGS_RC="
    echo ERROR: the settings above were refused - fix them in .env
    exit /b 2
)
call "%CCWEB_SETTINGS_FILE%"
del "%CCWEB_SETTINGS_FILE%" >nul 2>&1
set "CCWEB_SETTINGS_FILE="
set "CCWEB_SETTINGS_RC="
exit /b 0
