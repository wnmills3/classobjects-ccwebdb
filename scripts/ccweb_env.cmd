@echo off
rem ---------------------------------------------------------------------------
rem  ccweb_env.cmd - put the ccwebdb conda environment in play for the caller.
rem
rem  Usage, from another script in this directory:
rem
rem      call "%~dp0ccweb_env.cmd" || exit /b 2
rem
rem  Deliberately has NO setlocal. Activation has to survive the return to the
rem  caller, which is the whole point; the caller's own setlocal is what keeps
rem  it from leaking into the interactive shell that ran the caller.
rem
rem  Why activate at all, when every script already names its programs by full
rem  path: the full path only fixes WHICH python runs, not what environment it
rem  runs in. Packages are installed by uv, pip or conda into whatever
rem  environment is in play at the time -- uv follows UV_PROJECT_ENVIRONMENT,
rem  conda follows CONDA_PREFIX -- and processes started from here inherit the
rem  same variables. Starting them with ccwebdb active keeps the installer and
rem  the runtime pointed at one environment, instead of trusting that they
rem  happen to agree.
rem
rem  Sets, in the caller's scope:
rem    ENVDIR           the ccwebdb environment (from CONDA_PREFIX, not a guess)
rem    PGBIN            PostgreSQL's binaries inside it
rem    CCWEB_ENV_STATE  "already active" or "activated"
rem  and makes sure USERPROFILE -- plus APPDATA and LOCALAPPDATA when missing --
rem  names a real folder, so callers keep using %USERPROFILE% as they always
rem  have. There is deliberately no second name for the home directory: two
rem  variables for one folder can disagree, and USERPROFILE is the one every
rem  other program already reads.
rem
rem  Note: conda's own _conda_activate.bat (conda 26.5.3) runs PowerShell to
rem  make a GUID for a temp file name. That is upstream, not this project, and
rem  activation still succeeds without it -- the GUID is simply empty.
rem
rem  Exit 0 when ccwebdb is active, 1 when it could not be put in play.
rem ---------------------------------------------------------------------------

rem --- home directory -------------------------------------------------------
rem  USERPROFILE is used as-is when it names a folder that exists. When it is
rem  unset, or points somewhere that is not there, it is REPLACED -- not
rem  shadowed by a second variable -- from HOMEDRIVE+HOMEPATH, and failing that
rem  from %SystemDrive%\Users\%USERNAME%. Each candidate has to exist: a
rem  USERNAME with no matching profile folder would otherwise give a confident,
rem  wrong answer.
rem
rem  It has to be SET, not merely known. conda is a Python program and finds
rem  ~/.condarc through Path.expanduser(), which reads USERPROFILE; without it
rem  `conda activate` dies with "Could not determine home directory" before
rem  doing anything. So does uv (its cache), npm, git, and anything else started
rem  from here that looks for a home. Only the caller's scope is changed.
if defined USERPROFILE if exist "%USERPROFILE%\" goto :ccweb_home_ok
set "USERPROFILE="
if defined HOMEDRIVE if defined HOMEPATH if exist "%HOMEDRIVE%%HOMEPATH%\" set "USERPROFILE=%HOMEDRIVE%%HOMEPATH%"
if not defined USERPROFILE if defined USERNAME if exist "%SystemDrive%\Users\%USERNAME%\" set "USERPROFILE=%SystemDrive%\Users\%USERNAME%"
if not defined USERPROFILE (
    echo ERROR: cannot tell where the home directory is
    echo        none of USERPROFILE, HOMEDRIVE+HOMEPATH or %%SystemDrive%%\Users\USERNAME
    echo        names a folder that exists
    exit /b 1
)
:ccweb_home_ok
rem  The profile load that sets USERPROFILE also sets these, so a shell missing
rem  one is likely missing the others -- and uv and npm keep their caches under
rem  them. pgAdmin's uv tool install is found through APPDATA too.
if not defined APPDATA if exist "%USERPROFILE%\AppData\Roaming\" set "APPDATA=%USERPROFILE%\AppData\Roaming"
if not defined LOCALAPPDATA if exist "%USERPROFILE%\AppData\Local\" set "LOCALAPPDATA=%USERPROFILE%\AppData\Local"

rem --- already in play? -----------------------------------------------------
rem  Checked against the environment's own python, not just the name: a stale
rem  CONDA_DEFAULT_ENV left over from a deleted environment should not pass.
set "CCWEB_ENV_STATE="
if /i "%CONDA_DEFAULT_ENV%"=="ccwebdb" if defined CONDA_PREFIX if exist "%CONDA_PREFIX%\python.exe" set "CCWEB_ENV_STATE=already active"
if defined CCWEB_ENV_STATE goto :ccweb_env_ready

rem --- find conda -----------------------------------------------------------
rem  CONDA_EXE is set by `conda init` and names conda.exe in <root>\Scripts, so
rem  condabin sits two levels up. %%~f normalises the "..\.." away.
set "CCWEB_CONDABAT="
if defined CONDA_EXE for %%I in ("%CONDA_EXE%\..\..\condabin\conda.bat") do if exist "%%~fI" set "CCWEB_CONDABAT=%%~fI"
if not defined CCWEB_CONDABAT for /f "delims=" %%I in ('where conda.bat 2^>nul') do if not defined CCWEB_CONDABAT set "CCWEB_CONDABAT=%%I"
if not defined CCWEB_CONDABAT if exist "%USERPROFILE%\miniforge3\condabin\conda.bat" set "CCWEB_CONDABAT=%USERPROFILE%\miniforge3\condabin\conda.bat"
if not defined CCWEB_CONDABAT (
    echo ERROR: conda not found
    echo        looked at CONDA_EXE, conda.bat on PATH, and
    echo        %USERPROFILE%\miniforge3\condabin\conda.bat
    echo        see docs\environment-setup.md
    exit /b 1
)

rem --- activate -------------------------------------------------------------
rem  Kept on its own line, outside any parenthesised block: the check below
rem  must be parsed after activation has run, or it reads the old value.
call "%CCWEB_CONDABAT%" activate ccwebdb
if /i not "%CONDA_DEFAULT_ENV%"=="ccwebdb" (
    echo ERROR: could not activate the ccwebdb conda environment
    echo        CONDA_DEFAULT_ENV is "%CONDA_DEFAULT_ENV%"
    echo        create it with:  conda create -n ccwebdb python=3.13
    exit /b 1
)
set "CCWEB_ENV_STATE=activated"

:ccweb_env_ready
set "ENVDIR=%CONDA_PREFIX%"
set "PGBIN=%ENVDIR%\Library\bin"
exit /b 0
