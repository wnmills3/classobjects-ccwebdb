#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_psql.sh - psql against the database the API uses.
#
#    ./scripts/ccweb_psql.sh                 an interactive session
#    ./scripts/ccweb_psql.sh -c "select 1"   any psql arguments
#
#  The bash twin of ccweb_psql.cmd. Host, port, database, user and password
#  default to .env's DATABASE_URL, read through ccweb_settings; PGHOST,
#  PGPORT, PGDATABASE, PGUSER and PGPASSWORD already set in the environment
#  win.
# ---------------------------------------------------------------------------
set -u
here="$(dirname "${BASH_SOURCE[0]}")"
. "$here/ccweb_env.sh" || exit 2
. "$here/ccweb_settings.sh" --include-password || exit 2

export PGHOST="${PGHOST:-$CCWEB_DB_HOST}"
export PGPORT="${PGPORT:-$CCWEB_DB_PORT}"
export PGDATABASE="${PGDATABASE:-$CCWEB_DB_NAME}"
export PGUSER="${PGUSER:-$CCWEB_DB_USER}"
export PGPASSWORD="${PGPASSWORD:-$CCWEB_DB_PASSWORD}"

if [ ! -x "$PGBIN/psql" ]; then
    echo "ERROR: psql not found at $PGBIN"
    echo "       see docs/environment-setup-linux.md"
    exit 1
fi

exec "$PGBIN/psql" "$@"
