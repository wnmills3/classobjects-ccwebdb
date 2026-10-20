#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_status.sh - what is running, what is not, and how to start it.
#
#  The bash twin of ccweb_status.cmd: read-only, the same checks and
#  wording. A listening port is paired with a request, so a backend that
#  holds its port without answering is "LISTENING but not answering", not
#  RUNNING.
#
#  Exit codes:
#    0  everything required is up
#    1  something required is down or degraded
#    2  the environment itself is not ready, or the preflight found
#       something missing
# ---------------------------------------------------------------------------
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$here/.." && pwd)"

# The pid listening on TCP port $1: empty if nothing listens, "?" when
# something does but belongs to another user, whose pid ss cannot show.
portpid() {
    local listing
    listing="$(ss -Hltnp "sport = :$1" 2>/dev/null)"
    [ -n "$listing" ] || return 0
    local pid
    pid="$(printf '%s\n' "$listing" | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -n 1)"
    printf '%s\n' "${pid:-?}"
}

DOWN=""
BLOCKED=""

echo "============================================"
echo "  ccwebdb runtime status"
echo "============================================"

# --- 0. Environment ---------------------------------------------------------
# Named as ccweb_env.sh reads it, so a failure names the one it looked
# for, not the default.
ENVNAME="$(bash "$here/ccweb_env.sh" --read-dotenv)"
ENVNAME="${ENVNAME#CCWEB_CONDA_ENV=}"
ENVNAME="${ENVNAME:-ccwebdb}"
ENVOK=""
PGBIN=""
if . "$here/ccweb_env.sh"; then ENVOK=1; fi
if [ -z "$ENVOK" ]; then
    echo "  Environment  conda $ENVNAME    NOT AVAILABLE"
    echo "  BLOCKED: the $ENVNAME conda environment could not be put in play"
    echo "           see docs/environment-setup-linux.md"
    BLOCKED=1
elif [ "$CCWEB_ENV_STATE" = "already active" ]; then
    echo "  Environment  conda $ENVNAME    active in this shell"
else
    echo "  Environment  conda $ENVNAME    activated for this check (not active in this shell)"
fi

# Every address and folder below is a setting, read by the environment's
# Python. Without it each check says UNKNOWN rather than guessing.
SETOK=""
if [ -n "$ENVOK" ]; then
    if . "$here/ccweb_settings.sh"; then
        SETOK=1
    else
        echo "  BLOCKED: a setting in .env was refused - see the message above"
        BLOCKED=1
    fi
fi
WHY="no environment to ask with"
[ -n "$ENVOK" ] && WHY="a setting in .env was refused"

# --- 1. PostgreSQL ----------------------------------------------------------
if [ -z "$SETOK" ]; then
    echo "  PostgreSQL   UNKNOWN - $WHY"
    DOWN="$DOWN postgresql"
elif "$PGBIN/pg_isready" -h "$CCWEB_DB_HOST" -p "$CCWEB_DB_PORT" >/dev/null 2>&1; then
    echo "  PostgreSQL   $CCWEB_DB_HOST:$CCWEB_DB_PORT   RUNNING"
else
    echo "  PostgreSQL   $CCWEB_DB_HOST:$CCWEB_DB_PORT   stopped"
    DOWN="$DOWN postgresql"
fi

# --- 2. Backend and 3. Frontend ---------------------------------------------
# $1 label, $2 host, $3 port, $4 path that must answer, $5 name for DOWN
check_server() {
    local pid
    pid="$(portpid "$3")"
    if [ -n "$pid" ] && curl -s -f -o /dev/null --max-time 5 "http://$2:$3$4"; then
        echo "  $1$2:$3   RUNNING   pid $pid"
    elif [ -n "$pid" ]; then
        echo "  $1$2:$3   LISTENING but not answering   pid $pid"
        DOWN="$DOWN $5"
    else
        echo "  $1$2:$3   stopped"
        DOWN="$DOWN $5"
    fi
}
if [ -z "$SETOK" ]; then
    echo "  Backend      UNKNOWN - $WHY"
    echo "  Frontend     UNKNOWN - $WHY"
    DOWN="$DOWN backend frontend"
else
    check_server "Backend      " "$CCWEB_API_HOST" "$CCWEB_API_PORT" /api/reference/item_status backend
    check_server "Frontend     " "$CCWEB_FRONTEND_HOST" "$CCWEB_FRONTEND_PORT" /management frontend
fi

# --- 4. SonarQube (optional) ------------------------------------------------
if [ -z "$SETOK" ]; then
    echo "  SonarQube    UNKNOWN - $WHY   (optional)"
else
    SPID="$(portpid "$CCWEB_SONAR_PORT")"
    if [ -n "$SPID" ]; then
        echo "  SonarQube    127.0.0.1:$CCWEB_SONAR_PORT   running   pid $SPID   (optional)"
    else
        echo "  SonarQube    127.0.0.1:$CCWEB_SONAR_PORT   stopped                (optional)"
    fi
fi

# --- preflight: things that would make a start fail --------------------------
if [ -n "$DOWN" ]; then
    if [ -n "$SETOK" ] && [ ! -f "$CCWEB_DB_CLUSTER_DIR/PG_VERSION" ]; then
        echo
        echo "  BLOCKED: no PostgreSQL cluster at $CCWEB_DB_CLUSTER_DIR"
        echo "           see docs/environment-setup-linux.md"
        BLOCKED=1
    fi
    if [ ! -f "$REPO/frontend/node_modules/vite/bin/vite.js" ]; then
        echo
        echo "  BLOCKED: frontend dependencies missing"
        echo "           run:  cd frontend && npm install"
        BLOCKED=1
    fi
fi

# --- what to do next --------------------------------------------------------
echo
if [ -z "$DOWN" ]; then
    echo "  Everything is up."
    echo
    echo "    shop     http://$CCWEB_FRONTEND_HOST:$CCWEB_FRONTEND_PORT/"
    echo "    console  http://$CCWEB_FRONTEND_HOST:$CCWEB_FRONTEND_PORT/management"
    echo "    API docs http://$CCWEB_API_HOST:$CCWEB_API_PORT/docs"
    echo "============================================"
    exit 0
fi

echo "  Not running:$DOWN"
echo
if [ -n "$BLOCKED" ]; then
    echo "  Fix the BLOCKED item(s) above first - startup will fail otherwise."
    echo "============================================"
    exit 2
fi

echo "  To start:"
echo
echo "      ./scripts/ccweb_startup.sh"
echo
echo "  (safe to re-run: whatever is already listening is left alone)"
echo "============================================"
exit 1
