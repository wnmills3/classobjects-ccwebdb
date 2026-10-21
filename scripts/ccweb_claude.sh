#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_claude.sh - start Claude Code in this repository, environment active.
#
#    ./scripts/ccweb_claude.sh                 a new session
#    ./scripts/ccweb_claude.sh SESSION...      resume that session
#    ./scripts/ccweb_claude.sh --check         report state, start nothing
#    ./scripts/ccweb_claude.sh --nodb          leave PostgreSQL alone
#
#  The bash twin of ccweb_claude.cmd: activates the conda environment, so
#  every command Claude Code runs finds the project's tools, and starts the
#  development cluster first when it is not running.
# ---------------------------------------------------------------------------
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$here/.." && pwd)"

CHECKONLY=""
SKIPDB=""
while [ $# -gt 0 ]; do
    case "$1" in
        --check) CHECKONLY=1; shift ;;
        --nodb) SKIPDB=1; shift ;;
        *) break ;;
    esac
done

. "$here/ccweb_env.sh" || exit 1
. "$here/ccweb_settings.sh" || exit 1
. "$here/ccweb_logdir.sh"
export PGDATA="$CCWEB_DB_CLUSTER_DIR" LOGS
cd "$REPO" || exit 1

pgready() { "$PGBIN/pg_isready" -h "$CCWEB_DB_HOST" -p "$CCWEB_DB_PORT" >/dev/null 2>&1; }

echo "environment  $CONDA_DEFAULT_ENV  ($CCWEB_ENV_STATE)"
echo "directory    $PWD"
echo "python       $(python --version 2>&1)"

DBSTATE="skipped"
if [ -z "$SKIPDB" ]; then
    if [ ! -f "$PGDATA/PG_VERSION" ]; then
        DBSTATE="no cluster at $PGDATA - see docs/environment-setup-linux.md"
    elif pgready; then
        DBSTATE="already running"
    elif [ -n "$CHECKONLY" ]; then
        if [ -f "$PGDATA/postmaster.pid" ]; then
            DBSTATE="not running here - postmaster.pid: another computer may be running it"
        else
            DBSTATE="not running - --check starts nothing"
        fi
    elif ! "$here/ccweb_pgstart.sh"; then
        DBSTATE="NOT STARTED - postmaster.pid: another computer may be running it"
    else
        tries=0
        until pgready || [ "$tries" -ge 60 ]; do tries=$((tries + 1)); sleep 1; done
        if pgready; then DBSTATE="started"; else DBSTATE="FAILED TO START - see $LOGS/postgres.log"; fi
    fi
fi
echo "postgres     $DBSTATE"

if ! command -v claude >/dev/null 2>&1 && [ -x "$HOME/.local/bin/claude" ]; then
    PATH="$HOME/.local/bin:$PATH"
fi
if ! command -v claude >/dev/null 2>&1; then
    echo "ERROR: \"claude\" is not on PATH"
    echo "       expected in $HOME/.local/bin"
    exit 1
fi

if [ -n "$CHECKONLY" ]; then
    echo "claude       $(claude --version 2>&1)"
    echo
    echo "--check only - not starting Claude Code"
    exit 0
fi

echo
if [ $# -eq 0 ]; then
    echo "starting a new session..."
    exec claude
fi
echo "resuming with \"$1\"..."
exec claude --resume "$@"
