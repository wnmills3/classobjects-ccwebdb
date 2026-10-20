# shellcheck shell=bash
# ---------------------------------------------------------------------------
#  ccweb_logdir.sh - set LOGS, the directory the runtime writes its logs to.
#
#  The bash twin of ccweb_logdir.cmd. CCWEB_LOG_DIR names it (default logs,
#  in the repository); ccweb_settings.sh has already made it absolute, so
#  startup and shutdown always agree. Sourced after ccweb_settings.sh.
# ---------------------------------------------------------------------------
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    echo "ERROR: ccweb_logdir.sh is sourced by the other scripts, not run" >&2
    exit 2
fi
export LOGS="$CCWEB_LOG_DIR"
