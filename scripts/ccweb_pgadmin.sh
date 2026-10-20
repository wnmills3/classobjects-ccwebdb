#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_pgadmin.sh - start pgAdmin on this machine and open it.
#
#  The bash twin of ccweb_pgadmin.cmd. pgAdmin is a uv tool
#  (uv tool install --python 3.13 pgadmin4), run in desktop mode on
#  127.0.0.1:CCWEB_PGADMIN_PORT. When its config_local.py has gone missing
#  -- a `uv tool upgrade` replaces the package -- the repository's copy is
#  put back and the connection to the database DATABASE_URL names is
#  registered again from scripts/pgadmin-servers.json.
# ---------------------------------------------------------------------------
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$here/.." && pwd)"
. "$here/ccweb_env.sh" || exit 2
. "$here/ccweb_settings.sh" || exit 2

PGADMIN="$HOME/.local/bin/pgadmin4"
PGADMIN_CLI="$HOME/.local/bin/pgadmin4-cli"
PORT="$CCWEB_PGADMIN_PORT"
URL="http://127.0.0.1:$PORT/browser/"
export CCWEB_PGADMIN_PORT

listening() { ss -Hltn "sport = :$PORT" 2>/dev/null | grep -q .; }
open_url() {
    if command -v wslview >/dev/null 2>&1; then wslview "$1"
    elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$1" >/dev/null 2>&1
    else echo "Open $1 in a browser."
    fi
}

if [ ! -x "$PGADMIN" ]; then
    echo "ERROR: pgAdmin is not installed."
    echo
    echo "    uv tool install --python 3.13 pgadmin4"
    exit 1
fi

# The installed package, wherever uv keeps its tools on this machine.
shopt -s nullglob
packages=("$(uv tool dir)"/pgadmin4/lib/python*/site-packages/pgadmin4)
shopt -u nullglob
PKG="${packages[0]:-}"
if [ -z "$PKG" ]; then
    echo "ERROR: pgAdmin's package was not found under $(uv tool dir)/pgadmin4"
    exit 1
fi

if [ ! -f "$PKG/config_local.py" ]; then
    echo "Restoring pgAdmin config_local.py ..."
    if ! cp "$REPO/scripts/pgadmin_config_local.py" "$PKG/config_local.py"; then
        echo "ERROR: could not write $PKG/config_local.py"
        exit 1
    fi
    echo "Re-registering the ccwebdb connection ..."
    servers="$(mktemp --suffix=.json)"
    (cd "$REPO/backend" && "$ENVDIR/bin/python" -m app.runtime_settings \
        --render "$REPO/scripts/pgadmin-servers.json") > "$servers"
    if ! "$PGADMIN_CLI" load-servers "$servers" --user pgadmin4@pgadmin.org >/dev/null; then
        echo "WARNING: the ccwebdb connection was not registered. pgAdmin"
        echo "         still starts; add the server there by hand -"
        echo "         $CCWEB_DB_HOST:$CCWEB_DB_PORT, database $CCWEB_DB_NAME, user $CCWEB_DB_USER."
    fi
    rm -f "$servers"
fi

if listening; then
    echo "pgAdmin is already running."
    echo "Opening $URL"
    open_url "$URL"
    exit 0
fi

echo "Starting pgAdmin on $URL"
echo "  (first run takes a few seconds while it builds its config database)"
setsid nohup "$PGADMIN" >/dev/null 2>&1 &

tries=0
until curl -s -o /dev/null --max-time 2 "$URL"; do
    tries=$((tries + 1))
    if [ "$tries" -ge 60 ]; then
        echo
        echo "ERROR: pgAdmin did not answer on $URL in 60 tries (one to three minutes)."
        echo "       Check its log: $HOME/.pgadmin/pgadmin4.log"
        exit 1
    fi
    sleep 1
done
listening || { echo "ERROR: pgAdmin answered but is not listening on $PORT"; exit 1; }

echo "pgAdmin is up. Opening the browser."
open_url "$URL"
echo
echo "  Connection:  ccwebdb (local)  -  $CCWEB_DB_HOST:$CCWEB_DB_PORT, user $CCWEB_DB_USER"
echo "  Password:    the one in DATABASE_URL (.env) - tick \"Save Password\" to be asked once"
echo
echo "  The friendly rows are in the VIEWS, not the tables:"
echo "    Databases > $CCWEB_DB_NAME > Schemas > public > Views"
echo "    coin_inventory, currency_inventory, item_valuation, public_catalog"
echo
echo "  Stop it with: pkill -f pgadmin4"
exit 0
