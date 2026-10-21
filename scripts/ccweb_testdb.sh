#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_testdb.sh - a PostgreSQL cluster of this computer's own, for tests.
#
#    ./scripts/ccweb_testdb.sh          start it, creating it the first time
#    ./scripts/ccweb_testdb.sh stop     stop it
#    ./scripts/ccweb_testdb.sh status   say whether it exists and is running
#    . scripts/ccweb_testdb.sh use      (sourced) start it if it exists, and
#                                       set TEST_DATABASE_URL in the caller,
#                                       and nothing else
#
#  The bash twin of ccweb_testdb.cmd. If another computer shares the
#  collection's cluster folder, only one computer runs it at a time, so the
#  other has no PostgreSQL to test against. This one
#  (CCWEB_TEST_DB_CLUSTER_DIR, default .pgdata.test, on CCWEB_TEST_DB_PORT,
#  default 5433) holds nothing but the databases a test session builds and
#  drops, so the tests never build theirs inside the collection's cluster.
#  It is never shared and never holds the collection, so its fixed
#  development password protects nothing. "use" changes nothing where
#  TEST_DATABASE_URL is already set or the cluster does not exist.
# ---------------------------------------------------------------------------

_ccweb_testdb_sourced=""
(return 0 2>/dev/null) && _ccweb_testdb_sourced=1

_ccweb_testdb_main() {
    local here mode
    here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
    mode="${1:-start}"
    if [ "$mode" = "use" ] && [ -z "$_ccweb_testdb_sourced" ]; then
        echo "ERROR: 'use' sets a variable in your shell, so source it:"
        echo "       . scripts/ccweb_testdb.sh use"
        return 2
    fi
    if [ "$mode" != "use" ] && [ -n "$_ccweb_testdb_sourced" ]; then
        echo "ERROR: run it, do not source it:  ./scripts/ccweb_testdb.sh $mode"
        return 2
    fi
    case "$mode" in start|use|stop|status) ;; *)
        echo "usage: ccweb_testdb.sh [start | stop | status]"
        return 2 ;;
    esac
    [ "$mode" = "use" ] && [ -n "${TEST_DATABASE_URL:-}" ] && return 0

    . "$here/ccweb_env.sh" || return 2
    . "$here/ccweb_settings.sh" --include-password || return 2
    local data="$CCWEB_TEST_DB_CLUSTER_DIR" port="$CCWEB_TEST_DB_PORT"
    local host="$CCWEB_TEST_DB_HOST"
    # The superuser's password is the application role's: one password for
    # a cluster that holds nothing but test databases.
    export PGPASSWORD="$CCWEB_TEST_DB_PASSWORD"
    [ "$mode" = "use" ] && [ ! -f "$data/PG_VERSION" ] && return 0
    . "$here/ccweb_logdir.sh"
    mkdir -p "$LOGS"

    ready() { "$PGBIN/pg_isready" -h "$host" -p "$port" -U postgres -d postgres >/dev/null 2>&1; }

    case "$mode" in
        stop)
            if ! ready; then echo "Test cluster is not running."; return 0; fi
            "$PGBIN/pg_ctl" -D "$data" stop -m fast
            return $?
            ;;
        status)
            if [ ! -f "$data/PG_VERSION" ]; then
                echo "Test cluster   not created  (./scripts/ccweb_testdb.sh creates it)"
            elif ready; then
                echo "Test cluster   $host:$port   RUNNING"
            else
                echo "Test cluster   $host:$port   stopped"
            fi
            return 0
            ;;
    esac

    # --- start, and use ------------------------------------------------------
    if ! ready; then
        local created=""
        if [ ! -f "$data/PG_VERSION" ]; then
            echo "Creating the test cluster in $data"
            local pwfile
            pwfile="$(mktemp)"
            printf '%s\n' "$PGPASSWORD" > "$pwfile"
            if ! "$PGBIN/initdb" -D "$data" -U postgres --pwfile="$pwfile" \
                --auth-host=scram-sha-256 --auth-local=scram-sha-256 --encoding=UTF8 \
                > "$LOGS/initdb-test.log" 2>&1; then
                rm -f "$pwfile"
                echo "ERROR: initdb failed - see $LOGS/initdb-test.log"
                return 1
            fi
            rm -f "$pwfile"
            created=1
        fi
        # Its own session, so it outlives this shell; its output through the
        # log writer, as the development cluster's is.
        # Not fd 3 either: under "use" that is the caller's pipe, held open
        # for as long as the server runs.
        setsid nohup sh -c '"$1/pg_ctl" -D "$2" -o "-p $3 -c logging_collector=off" start 2>&1 | "$4/bin/python" "$5/backend/app/logpipe.py" "$6/postgres-test.log"' \
            sh "$PGBIN" "$data" "$port" "$ENVDIR" "$CCWEB_REPO" "$LOGS" >/dev/null 2>&1 3>&- &
        local tries=0
        until ready; do
            tries=$((tries + 1))
            if [ "$tries" -ge 30 ]; then
                echo "ERROR: the test cluster did not start on port $port - see $LOGS/postgres-test.log"
                return 1
            fi
            sleep 1
        done
        if [ -n "$created" ]; then
            # The application role, as in the collection's cluster: allowed to
            # create databases, not a superuser.
            if ! "$PGBIN/psql" -h "$host" -p "$port" -U postgres -d postgres -q -v ON_ERROR_STOP=1 \
                -c "CREATE ROLE $CCWEB_TEST_DB_USER WITH LOGIN PASSWORD '$PGPASSWORD' CREATEDB;"; then
                echo "ERROR: could not create the $CCWEB_TEST_DB_USER role in the test cluster"
                return 1
            fi
        fi
    fi

    if [ "$mode" = "use" ]; then
        printf '%s\n' "$CCWEB_TEST_DB_URL" >&3
        return 0
    fi
    echo "Test cluster RUNNING on $host:$port ($data)"
    echo "ccweb_check.sh uses it by itself. For pytest on its own, first run:"
    echo "    export TEST_DATABASE_URL='$CCWEB_TEST_DB_URL'"
    return 0
}

if [ -z "$_ccweb_testdb_sourced" ]; then
    set -u
    _ccweb_testdb_main "$@"
    exit $?
fi
# Sourced, the work runs in a subshell: the settings, PGPASSWORD and LOGS it
# sets stay there, so only the URL reaches the caller's shell, as from the
# cmd twin. Its messages go to stderr; the URL comes back on fd 3.
_ccweb_testdb_url="$(_ccweb_testdb_main "$@" 3>&1 1>&2)"
_ccweb_testdb_rc=$?
[ -n "$_ccweb_testdb_url" ] && export TEST_DATABASE_URL="$_ccweb_testdb_url"
unset -f _ccweb_testdb_main
unset _ccweb_testdb_sourced _ccweb_testdb_url
# The file's status is its last command's: the function's, without a
# top-level return that would read as ending the script that sourced this.
(exit "$_ccweb_testdb_rc")
