#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_shutdown.sh - stop the ccwebdb development runtime.
#
#  Usage:  ./scripts/ccweb_shutdown.sh            stop everything
#          ./scripts/ccweb_shutdown.sh --keepdb   stop the servers, leave PostgreSQL
#
#  The bash twin of ccweb_shutdown.cmd. Stops the process groups
#  ccweb_startup.sh recorded (TERM, then KILL after ten seconds), sweeps the
#  API and frontend ports for survivors, then stops PostgreSQL with pg_ctl.
#  The sweep stops a port's listener only when it is the program startup
#  runs there (uvicorn, vite); any other is named, left alone, and makes the
#  exit code 1. Without the settings the recorded groups are still stopped; the port
#  sweep and the database are left, since their addresses are not known.
# ---------------------------------------------------------------------------
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$here/.." && pwd)"

KEEPDB=""
case "${1:-}" in
    "") ;;
    --keepdb) KEEPDB=1 ;;
    *)
        echo "ERROR: unrecognised argument: $1"
        echo "       usage: ./scripts/ccweb_shutdown.sh [--keepdb]"
        exit 2 ;;
esac

PGBIN=""
if ! . "$here/ccweb_env.sh"; then
    echo "WARNING: ccwebdb environment unavailable - PostgreSQL cannot be"
    echo "         stopped from here; the servers still will be."
    PGBIN=""
fi
SETOK=""
if [ -n "$PGBIN" ] && . "$here/ccweb_settings.sh"; then SETOK=1; fi
RUNTIME="$REPO/.runtime"
PIDFILE="$RUNTIME/ccweb.pids"
LOGS=""
if [ -n "$SETOK" ]; then
    PGDATA="$CCWEB_DB_CLUSTER_DIR"
    . "$here/ccweb_logdir.sh"
    mkdir -p "$LOGS"
fi
mkdir -p "$RUNTIME"

portpid() {
    ss -Hltnp "sport = :$1" 2>/dev/null | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n 1
}
# Stop process group $1 if it is still the one startup made: its commands
# must include $3 (uvicorn, vite).
killgroup() {
    local group="$1" label="$2" marker="$3" waited=0
    [ -n "$group" ] || return 0
    if ! pgrep -g "$group" -f "$marker" >/dev/null 2>&1; then
        echo "[1/3] $label      group $group is not a running $marker - left alone"
        return 0
    fi
    echo "[1/3] $label      stopping group $group"
    kill -TERM -- "-$group" 2>/dev/null
    while kill -0 -- "-$group" 2>/dev/null && [ "$waited" -lt 10 ]; do
        sleep 1; waited=$((waited + 1))
    done
    kill -KILL -- "-$group" 2>/dev/null
    return 0
}
# Stop port $1's listener only if its command line includes $2, the program
# startup runs there (uvicorn, vite). Any other is some other program that
# happens to have the port: it is named and left alone, and the verify step
# reports the port still held.
killport() {
    local survivor
    survivor="$(portpid "$1")"
    if [ -z "$survivor" ]; then
        echo "      port $1 free"
        return 0
    fi
    if ! ps -o args= -p "$survivor" 2>/dev/null | grep -q -- "$2"; then
        echo "      WARNING: port $1 held by pid $survivor, which is not $2 - left alone"
        return 0
    fi
    echo "      port $1 held by pid $survivor - stopping"
    kill -TERM "$survivor" 2>/dev/null
    sleep 1
    kill -0 "$survivor" 2>/dev/null && kill -KILL "$survivor" 2>/dev/null
    return 0
}
pgready() { "$PGBIN/pg_isready" -h "$CCWEB_DB_HOST" -p "$CCWEB_DB_PORT" >/dev/null 2>&1; }

echo "============================================"
echo "  ccwebdb runtime shutdown"
echo "============================================"

# --- 1. stop by recorded process group ---------------------------------------
if [ -f "$PIDFILE" ]; then
    while IFS='=' read -r key value; do
        case "$key" in
            backend) killgroup "$value" backend uvicorn ;;
            frontend) killgroup "$value" frontend vite ;;
        esac
    done < "$PIDFILE"
else
    echo "[1/3] no PID file - relying on the port sweep"
fi

# --- 2. sweep the ports -------------------------------------------------------
if [ -n "$SETOK" ]; then
    echo "[2/3] sweeping ports $CCWEB_API_PORT and $CCWEB_FRONTEND_PORT..."
    killport "$CCWEB_API_PORT" uvicorn
    killport "$CCWEB_FRONTEND_PORT" vite
else
    echo "[2/3] port sweep   SKIPPED - without the settings the ports are not known"
fi

# --- 3. PostgreSQL ------------------------------------------------------------
PGSKIP=""
if [ -z "$PGBIN" ]; then
    echo "[3/3] postgresql   SKIPPED - no ccwebdb environment to stop it with"
    PGSKIP=1
elif [ -z "$SETOK" ]; then
    echo "[3/3] postgresql   SKIPPED - a setting in .env was refused"
    PGSKIP=1
elif [ -n "$KEEPDB" ]; then
    echo "[3/3] postgresql   left running (--keepdb)"
elif ! pgready; then
    echo "[3/3] postgresql   already stopped"
else
    echo "[3/3] postgresql   stopping (fast)..."
    "$ENVDIR/bin/python" "$REPO/backend/app/logpipe.py" "$LOGS/pg_stop.log" --rotate
    "$PGBIN/pg_ctl" -D "$PGDATA" -m fast -w stop > "$LOGS/pg_stop.log" 2>&1
    if pgready; then
        echo "      FAILED - still accepting connections"
        echo "      see $LOGS/pg_stop.log and $LOGS/postgres.log"
    else
        echo "      stopped cleanly"
    fi
fi

rm -f "$PIDFILE"

# --- verify -------------------------------------------------------------------
echo
echo "verifying..."
PROBLEM=""
if [ -z "$SETOK" ]; then
    echo "  ports     NOT CHECKED - without the settings the ports are not known"
    PROBLEM=1
else
    for port in "$CCWEB_API_PORT" "$CCWEB_FRONTEND_PORT"; do
        holder="$(portpid "$port")"
        if [ -n "$holder" ] || ss -Hltn "sport = :$port" 2>/dev/null | grep -q .; then
            echo "  port $port  STILL HELD by pid ${holder:-?}"
            PROBLEM=1
        else
            echo "  port $port  free"
        fi
    done
fi
if [ -n "$PGSKIP" ]; then
    echo "  postgres  NOT CHECKED - no ccwebdb environment or settings"
    PROBLEM=1
elif ! pgready; then
    echo "  postgres  stopped"
elif [ -n "$KEEPDB" ]; then
    echo "  postgres  running (as requested)"
else
    echo "  postgres  STILL RUNNING"
    PROBLEM=1
fi
echo "============================================"
[ -n "$PROBLEM" ] && exit 1
exit 0
