#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_startup.sh - bring up the ccwebdb development runtime.
#
#    1. PostgreSQL   (pg_ctl, cluster in CCWEB_DB_CLUSTER_DIR)
#    2. FastAPI      CCWEB_API_HOST:CCWEB_API_PORT
#    3. Vite         CCWEB_FRONTEND_HOST:CCWEB_FRONTEND_PORT
#
#  Usage:  ./scripts/ccweb_startup.sh         Vite answers this machine only
#          ./scripts/ccweb_startup.sh --lan   Vite answers the local network too
#
#  The bash twin of ccweb_startup.cmd. Each server runs in a session of its
#  own (setsid), so it outlives this shell, and its process group is written
#  to .runtime/ccweb.pids for ccweb_shutdown.sh to stop as a whole. Logs go
#  to CCWEB_LOG_DIR through the log writer. Safe to re-run: anything already
#  listening is left alone. A listener on the API's port counts as the API
#  only if /health answers {"status":"ok"}; otherwise the port is reported
#  as taken and the exit code is 1. Vite's dev server is for a home network,
#  never the internet.
# ---------------------------------------------------------------------------
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$here/.." && pwd)"

LAN=""
case "${1:-}" in
    "") ;;
    --lan) LAN=1 ;;
    *)
        echo "ERROR: unrecognised argument: $1"
        echo "       usage: ./scripts/ccweb_startup.sh [--lan]"
        exit 2 ;;
esac

. "$here/ccweb_env.sh" || exit 2
. "$here/ccweb_settings.sh" || exit 2
. "$here/ccweb_logdir.sh"
export PGDATA="$CCWEB_DB_CLUSTER_DIR" LOGS
RUNTIME="$REPO/.runtime"
PIDFILE="$RUNTIME/ccweb.pids"
LOGPIPE="$REPO/backend/app/logpipe.py"
export PYTHONUNBUFFERED=1
BHOST="$CCWEB_API_HOST"
BPORT="$CCWEB_API_PORT"
FPORT="$CCWEB_FRONTEND_PORT"
FHOST="$CCWEB_FRONTEND_HOST"
[ -n "$LAN" ] && FHOST="0.0.0.0"
# Where to ask whether Vite answers: its own address, or loopback when it
# listens on every address.
FASK="$FHOST"
[ "$FASK" = "0.0.0.0" ] && FASK="127.0.0.1"

# The pid listening on TCP port $1, empty if none ("?" if another user's).
portpid() {
    local listing pid
    listing="$(ss -Hltnp "sport = :$1" 2>/dev/null)"
    [ -n "$listing" ] || return 0
    pid="$(printf '%s\n' "$listing" | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n 1)"
    printf '%s\n' "${pid:-?}"
}
# Poll $1 until it answers (0) or $2 tries pass (1).
waiturl() {
    local tries=0
    until curl -s -o /dev/null --max-time 2 "$1"; do
        tries=$((tries + 1))
        [ "$tries" -ge "$2" ] && return 1
        sleep 1
    done
}
pgready() { "$PGBIN/pg_isready" -h "$CCWEB_DB_HOST" -p "$CCWEB_DB_PORT" >/dev/null 2>&1; }

echo "============================================"
echo "  ccwebdb runtime startup"
echo "============================================"

# --- preflight --------------------------------------------------------------
if [ ! -x "$ENVDIR/bin/python" ]; then
    echo "ERROR: conda environment not found: $ENVDIR"
    echo "       see docs/environment-setup-linux.md"
    exit 1
fi
if [ ! -f "$PGDATA/PG_VERSION" ]; then
    echo "ERROR: no PostgreSQL cluster at $PGDATA"
    echo "       see docs/environment-setup-linux.md"
    exit 1
fi
if [ ! -f "$REPO/frontend/node_modules/vite/bin/vite.js" ]; then
    echo "ERROR: frontend dependencies missing"
    echo "       run:  cd frontend && npm install"
    exit 1
fi
mkdir -p "$RUNTIME" "$LOGS"

# --- 1. PostgreSQL ----------------------------------------------------------
if pgready; then
    echo "[1/3] postgresql   already running"
else
    echo "[1/3] postgresql   starting..."
    "$here/ccweb_pgstart.sh" || exit 1
    tries=0
    until pgready || [ "$tries" -ge 60 ]; do tries=$((tries + 1)); sleep 1; done
    if ! pgready; then
        echo "      FAILED - see $LOGS/postgres.log"
        exit 1
    fi
    echo "      started"
fi

# --- 2. FastAPI backend -----------------------------------------------------
BPID="$(portpid "$BPORT")"
BGROUP=""
# A listener on the API's port is this backend only if it answers /health
# as the API does; anything else is another program that has the port, and
# the frontend would proxy /api to it.
if [ -n "$BPID" ]; then
    if ! curl -s --max-time 2 "http://$BHOST:$BPORT/health" 2>/dev/null | grep -q 'status.:.ok'; then
        echo "[2/3] backend      FAILED - port $BPORT is taken by another program (pid $BPID), not this API"
        echo "      free the port, or set CCWEB_API_PORT in .env to another one"
        exit 1
    fi
    echo "[2/3] backend      already running on $BPORT (pid $BPID)"
else
    echo "[2/3] backend      starting on $BPORT..."
    # $! must be setsid's own pid, which is the new process group's id:
    # so the cd is its own command, not part of what is backgrounded.
    (cd "$REPO/backend" || exit 1
     setsid nohup sh -c \
        '"$1/bin/python" -m uvicorn app.main:app --host "$2" --port "$3" 2>&1 | "$1/bin/python" "$4" "$5/backend.log"' \
        sh "$ENVDIR" "$BHOST" "$BPORT" "$LOGPIPE" "$LOGS" >/dev/null 2>&1 &
        echo $! > "$RUNTIME/backend.group")
    BGROUP="$(cat "$RUNTIME/backend.group")"; rm -f "$RUNTIME/backend.group"
    # A held port is not an answer: a backend that binds and then hangs holds
    # its port exactly as a healthy one does.
    if ! waiturl "http://$BHOST:$BPORT/health" 60; then
        echo "      FAILED - /health did not answer - see $LOGS/backend.log"
        exit 1
    fi
    BPID="$(portpid "$BPORT")"
    if [ -z "$BPID" ]; then
        echo "      FAILED - see $LOGS/backend.log"
        exit 1
    fi
    echo "      pid $BPID"
fi

# --- 3. Vite frontend -------------------------------------------------------
FPID="$(portpid "$FPORT")"
FGROUP=""
if [ -n "$FPID" ]; then
    echo "[3/3] frontend     already running on $FPORT (pid $FPID)"
    if [ -n "$LAN" ] && ! ss -Hltn "sport = :$FPORT" | grep -q "0\.0\.0\.0:$FPORT\|\*:$FPORT"; then
        echo "      WARNING - it answers this machine only, so --lan changed nothing."
        echo "      Stop it with ./scripts/ccweb_shutdown.sh --keepdb, then run this again."
    fi
else
    echo "[3/3] frontend     starting on $FPORT..."
    (cd "$REPO/frontend" || exit 1
     setsid nohup sh -c \
        '"$1/bin/node" ./node_modules/vite/bin/vite.js --host "$2" --port "$3" 2>&1 | "$1/bin/python" "$4" "$5/frontend.log"' \
        sh "$ENVDIR" "$FHOST" "$FPORT" "$LOGPIPE" "$LOGS" >/dev/null 2>&1 &
        echo $! > "$RUNTIME/frontend.group")
    FGROUP="$(cat "$RUNTIME/frontend.group")"; rm -f "$RUNTIME/frontend.group"
    if ! waiturl "http://$FASK:$FPORT/" 60; then
        echo "      FAILED - Vite did not answer - see $LOGS/frontend.log"
        exit 1
    fi
    FPID="$(portpid "$FPORT")"
    if [ -z "$FPID" ]; then
        echo "      FAILED - see $LOGS/frontend.log"
        exit 1
    fi
    echo "      pid $FPID"
fi

# --- record process groups --------------------------------------------------
# The group, where this run started the server: shutdown stops the whole
# group, the log writer with it. Left alone where it was already running.
{
    echo "backend=${BGROUP:-}"
    echo "frontend=${FGROUP:-}"
    echo "started=$(date -Is)"
} > "$PIDFILE"

# --- report -----------------------------------------------------------------
echo
echo "============================================"
echo "  UI          http://$FASK:$FPORT"
echo "  API         http://$BHOST:$BPORT"
echo "  API docs    http://$BHOST:$BPORT/docs"
echo "  database    $CCWEB_DB_HOST:$CCWEB_DB_PORT/$CCWEB_DB_NAME"
echo
echo "  sign in     a manager account (FIRST_ADMIN_EMAIL in .env on a trial installation)"
echo
echo "  logs        $LOGS"
echo "  PIDs        .runtime/ccweb.pids"
echo "  stop with   ./scripts/ccweb_shutdown.sh"
if [ -n "$LAN" ]; then
    echo
    for address in $(hostname -I 2>/dev/null); do
        case "$address" in *:*) ;; *) echo "  network     http://$address:$FPORT/management" ;; esac
    done
    if command -v ufw >/dev/null 2>&1; then
        echo
        echo "  firewall    if ufw is active, other machines are blocked until you run,"
        echo "              with this network's address range (192.168.1.0/24, say)"
        echo "              in place of YOUR_SUBNET:"
        echo "  sudo ufw allow from YOUR_SUBNET to any port $FPORT proto tcp"
    fi
fi
echo "============================================"
exit 0
