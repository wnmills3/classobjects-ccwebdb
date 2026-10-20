#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_pgstart.sh - start the development cluster in a session of its own.
#
#  The bash twin of ccweb_pgstart.cmd, called by ccweb_startup.sh and
#  ccweb_claude.sh with PGBIN and ENVDIR (from ccweb_env.sh), PGDATA, LOGS
#  and CCWEB_DB_PORT exported. The server listens on that port, DATABASE_URL's,
#  whatever postgresql.conf says. Its output goes through the log writer into
#  postgres.log; setsid keeps it running after the calling shell exits.
#
#  Refuses when postmaster.pid exists and nothing answers: where the
#  cluster folder is shared between computers, the other one is probably
#  running it.
# ---------------------------------------------------------------------------
set -u
if [ -f "$PGDATA/postmaster.pid" ]; then
    echo "ERROR: $PGDATA/postmaster.pid exists, and PostgreSQL is not running here."
    echo "       If another computer shares this cluster folder, it is probably"
    echo "       running this database: stop it there first. If it is certainly"
    echo "       stopped everywhere, delete postmaster.pid and start again."
    exit 1
fi

mkdir -p "$LOGS"
logpipe="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/backend/app/logpipe.py"
export PYTHONUNBUFFERED=1
setsid nohup sh -c '"$1/pg_ctl" -D "$2" -o "-p $6 -c logging_collector=off" start 2>&1 | "$3/bin/python" "$4" "$5/postgres.log"' \
    sh "$PGBIN" "$PGDATA" "$ENVDIR" "$logpipe" "$LOGS" "$CCWEB_DB_PORT" >/dev/null 2>&1 &
exit 0
