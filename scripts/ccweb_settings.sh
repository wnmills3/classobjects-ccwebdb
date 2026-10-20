# shellcheck shell=bash
# ---------------------------------------------------------------------------
#  ccweb_settings.sh - put this repository's settings in the caller's shell.
#
#  Usage, from another script in this directory, after ccweb_env.sh:
#
#      . "$(dirname "${BASH_SOURCE[0]}")/ccweb_settings.sh" || exit 2
#      . "$(dirname "${BASH_SOURCE[0]}")/ccweb_settings.sh" --include-password || exit 2
#
#  The bash twin of ccweb_settings.cmd: sets CCWEB_API_PORT,
#  CCWEB_DB_CLUSTER_DIR and the rest, exported, as
#  backend/app/runtime_settings.py gives them -- from .env, then the
#  defaults there. A value already in the environment wins over .env.
#  --include-password adds CCWEB_DB_PASSWORD and CCWEB_TEST_DB_PASSWORD.
#
#  Sourced from a script, never from ccweb_env.sh: in an interactive shell
#  these would then outrank later edits to .env.
#
#  Returns 0 when the settings are set; 2, with Python's message naming the
#  setting, when one is refused.
# ---------------------------------------------------------------------------
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "ERROR: ccweb_settings.sh is sourced by the other scripts, not run" >&2
    exit 2
fi
if [ -z "${ENVDIR:-}" ]; then
    echo "ERROR: ccweb_settings.sh needs ccweb_env.sh to have run first"
    return 2
fi
# Sourced without arguments, a file sees its caller's "$@" (--lan, --keepdb,
# a session name), so nothing here is passed on but the one option meant for
# it, and only when it comes first.
_ccweb_password=""
[ "${1:-}" = "--include-password" ] && _ccweb_password="--include-password"
if ! _ccweb_settings="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" \
    && "$ENVDIR/bin/python" -m app.runtime_settings --format sh $_ccweb_password)"; then
    unset _ccweb_settings _ccweb_password
    echo "ERROR: the settings above were refused - fix them in .env"
    return 2
fi
eval "$_ccweb_settings"
unset _ccweb_settings _ccweb_password
