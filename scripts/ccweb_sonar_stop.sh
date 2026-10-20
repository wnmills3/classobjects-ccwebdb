#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_sonar_stop.sh - stop SonarQube and its database; keep the volumes.
#
#  The bash twin of ccweb_sonar_stop.cmd.
# ---------------------------------------------------------------------------
set -u
podman stop sonarqube >/dev/null 2>&1
podman stop sonar-db >/dev/null 2>&1

if ! running="$(podman ps --format '{{.Names}}' 2>/dev/null)"; then
    echo "ERROR: podman is not responding - cannot verify SonarQube stopped."
    exit 1
fi
if printf '%s\n' "$running" | grep -qx -e sonarqube -e sonar-db; then
    echo "ERROR: SonarQube did not stop - still listed by podman ps:"
    podman ps --format '{{.Names}} {{.Status}}' 2>/dev/null | grep -i -e sonarqube -e sonar-db
    exit 1
fi
echo "SonarQube stopped. Volumes kept."
exit 0
