#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_backup.sh - the routine backup, and its daily schedule.
#
#    ./scripts/ccweb_backup.sh                      back up now
#    ./scripts/ccweb_backup.sh --schedule [HH:MM]   every day (CCWEB_BACKUP_TIME)
#    ./scripts/ccweb_backup.sh --unschedule         stop that
#    ./scripts/ccweb_backup.sh --scheduled          show the schedule
#
#  The bash twin of ccweb_backup.cmd. A backup exports the collection into
#  CCWEB_BACKUP_DIR, proves the workbook by restoring it into a scratch
#  database, checks and prunes the media copy, and removes the older
#  workbook only once the new one is proved (python -m app.backup_run).
#  Its log is backup.log in CCWEB_LOG_DIR.
#
#  The folder is CCWEB_BACKUP_DIR, read from .env through ccweb_settings
#  (unset: ccwebdb-backup in your home folder). Set it in .env, which the
#  daily timer reads too; a variable set only in this shell it never sees.
#  The cmd twin pins a folder inside OneDrive to stay on the device; Linux
#  has no OneDrive client, so there is nothing to pin here.
#
#  --schedule installs deploy/linux/ccwebdb-backup.service and .timer as
#  systemd user units, filled from the settings, and enables lingering so
#  the timer runs whether or not you are signed in -- the counterpart of
#  the Windows task's "Interactive/Background". Without a systemd user
#  manager it prints the crontab line to add instead.
# ---------------------------------------------------------------------------
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$here/.." && pwd)"
UNITS="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

have_user_systemd() { systemctl --user show-environment >/dev/null 2>&1; }

case "${1:-}" in
    "") ;;
    --schedule)
        [ -n "${2:-}" ] && export CCWEB_BACKUP_TIME="$2"
        . "$here/ccweb_env.sh" || exit 2
        . "$here/ccweb_settings.sh" || exit 2
        if ! have_user_systemd; then
            echo "ERROR: no systemd user manager here, so no timer can be installed."
            echo "       Add this line with 'crontab -e' instead:"
            echo "       ${CCWEB_BACKUP_TIME#*:} ${CCWEB_BACKUP_TIME%%:*} * * * $REPO/scripts/ccweb_backup.sh"
            exit 1
        fi
        mkdir -p "$UNITS" "$CCWEB_BACKUP_DIR"
        for unit in ccwebdb-backup.service ccwebdb-backup.timer; do
            (cd "$REPO/backend" && "$ENVDIR/bin/python" -m app.runtime_settings \
                --render "$REPO/deploy/linux/$unit") > "$UNITS/$unit" || exit 2
        done
        systemctl --user daemon-reload
        if ! systemctl --user enable --now ccwebdb-backup.timer >/dev/null 2>&1; then
            echo "ERROR: the daily backup could not be scheduled - see: systemctl --user status ccwebdb-backup.timer"
            exit 1
        fi
        loginctl enable-linger "$USER" 2>/dev/null \
            || echo "WARNING: lingering is not enabled, so it runs only while you are signed in"
        echo "the backup runs every day at $CCWEB_BACKUP_TIME, signed in or not; ./scripts/ccweb_backup.sh --unschedule stops it"
        exit 0 ;;
    --unschedule)
        systemctl --user disable --now ccwebdb-backup.timer
        rc=$?
        rm -f "$UNITS/ccwebdb-backup.timer" "$UNITS/ccwebdb-backup.service"
        systemctl --user daemon-reload 2>/dev/null
        exit "$rc" ;;
    --scheduled)
        systemctl --user list-timers --all --no-pager ccwebdb-backup.timer
        # 1 when there is none, as the cmd twin's schtasks query gives: not
        # systemd's own codes (4 for a unit that does not exist).
        systemctl --user is-enabled ccwebdb-backup.timer >/dev/null 2>&1 || exit 1
        exit 0 ;;
    *)
        echo "ERROR: unknown argument $1"
        echo "       ./scripts/ccweb_backup.sh [--schedule [HH:MM] | --unschedule | --scheduled]"
        exit 2 ;;
esac

# --- back up now --------------------------------------------------------------
# The log and backup folders are settings, which the environment's Python
# reads; without it there is no backup.log to write to, and the failure is
# said here only (and, for a scheduled run, in its journal).
if ! . "$here/ccweb_env.sh"; then
    echo "backup FAILED - the ccwebdb environment could not be put in play; no backup was made"
    exit 2
fi
if ! . "$here/ccweb_settings.sh"; then
    echo "backup FAILED - a setting in .env was refused; no backup was made"
    exit 2
fi
. "$here/ccweb_logdir.sh"
mkdir -p "$LOGS"
echo "==== $(date '+%Y-%m-%d %H:%M:%S') ====" >> "$LOGS/backup.log"

(cd "$REPO/backend" && "$ENVDIR/bin/python" -m app.backup_run "$CCWEB_BACKUP_DIR") \
    >> "$LOGS/backup.log" 2>&1
RC=$?
if [ "$RC" -eq 0 ]; then
    echo "backup proved, in $CCWEB_BACKUP_DIR"
else
    echo "backup FAILED (exit $RC) - nothing was removed; see $LOGS/backup.log"
fi
exit "$RC"
