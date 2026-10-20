#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_sonar_start.sh - start the self-hosted SonarQube and wait until UP.
#
#  The bash twin of ccweb_sonar_start.cmd: the network sonar-net, four
#  volumes, a PostgreSQL 16 container (sonar-db) and SonarQube Community
#  (sonarqube) published on 127.0.0.1:CCWEB_SONAR_PORT, created on the first
#  run and started on later ones. Podman runs natively on Linux, so there
#  is no podman machine to start. docs/specs/self-hosted-sonarqube-design.md
#  says why each piece is there.
# ---------------------------------------------------------------------------
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NET="sonar-net"
DBNAME="sonar-db"
SQNAME="sonarqube"
. "$here/ccweb_env.sh" || exit 2
. "$here/ccweb_settings.sh" || exit 2
SQPORT="$CCWEB_SONAR_PORT"
DBPASS="sonar"

if ! podman info >/dev/null 2>&1; then
    echo "ERROR: podman is not responding."
    echo "       Check:  podman info"
    exit 1
fi

if [ -z "$(podman ps --filter "name=^$SQNAME\$" --format '{{.Names}}')" ] \
    && ss -Hltn "sport = :$SQPORT" 2>/dev/null | grep -q .; then
    echo "ERROR: port $SQPORT is already in use by another process."
    exit 1
fi

podman network exists "$NET" >/dev/null 2>&1 || podman network create "$NET" >/dev/null
for volume in sonar-db-data sonarqube-data sonarqube-extensions sonarqube-logs; do
    podman volume exists "$volume" >/dev/null 2>&1 || podman volume create "$volume" >/dev/null
done

if podman container exists "$DBNAME" >/dev/null 2>&1; then
    podman start "$DBNAME" >/dev/null
else
    echo "[start] creating $DBNAME"
    podman run -d --name "$DBNAME" --network "$NET" \
        -e POSTGRES_USER=sonar -e POSTGRES_PASSWORD="$DBPASS" -e POSTGRES_DB=sonar \
        -v sonar-db-data:/var/lib/postgresql/data \
        docker.io/library/postgres:16 >/dev/null
fi

if podman container exists "$SQNAME" >/dev/null 2>&1; then
    podman start "$SQNAME" >/dev/null
else
    echo "[start] creating $SQNAME"
    podman run -d --name "$SQNAME" --network "$NET" \
        -p "127.0.0.1:$SQPORT:9000" \
        -e "SONAR_JDBC_URL=jdbc:postgresql://$DBNAME:5432/sonar" \
        -e SONAR_JDBC_USERNAME=sonar \
        -e SONAR_JDBC_PASSWORD="$DBPASS" \
        -v sonarqube-data:/opt/sonarqube/data \
        -v sonarqube-extensions:/opt/sonarqube/extensions \
        -v sonarqube-logs:/opt/sonarqube/logs \
        docker.io/library/sonarqube:community >/dev/null
fi

echo "[start] waiting for SonarQube to report UP - first boot takes 1-3 minutes"
tries=0
until curl -s --max-time 3 "http://127.0.0.1:$SQPORT/api/system/status" | grep -q '"status":"UP"'; do
    tries=$((tries + 1))
    if [ "$tries" -ge 300 ]; then
        echo "ERROR: SonarQube did not reach status UP. Last 30 log lines:"
        podman logs --tail 30 "$SQNAME"
        exit 1
    fi
    sleep 1
done

echo
echo "SonarQube is up:  http://127.0.0.1:$SQPORT"
echo "First login is admin / admin - you will be forced to change it."
exit 0
