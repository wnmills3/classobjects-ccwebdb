#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  ccweb_sonar_scan.sh - run both test suites with coverage, then publish an
#  analysis to the self-hosted SonarQube.
#
#  The bash twin of ccweb_sonar_scan.cmd. Needs SONAR_TOKEN in the
#  environment and SonarQube up (ccweb_sonar_start.sh). Refuses to publish
#  if either suite fails, so a dashboard never reflects untested code.
# ---------------------------------------------------------------------------
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$here/.." && pwd)"
. "$here/ccweb_env.sh" || exit 2
. "$here/ccweb_settings.sh" || exit 2
SQPORT="$CCWEB_SONAR_PORT"

# Fail fast rather than starting the server implicitly: an implicit start
# would hide a stopped server and make a stale dashboard look current.
if ! curl -s -f -o /dev/null --max-time 3 "http://127.0.0.1:$SQPORT/api/system/status"; then
    echo "ERROR: SonarQube is not responding on port $SQPORT."
    echo "       Run ./scripts/ccweb_sonar_start.sh first."
    exit 1
fi
if [ -z "${SONAR_TOKEN:-}" ]; then
    echo "ERROR: SONAR_TOKEN is not set. Generate one at"
    echo "       http://localhost:$SQPORT/account/security"
    echo "       then:  read -rs SONAR_TOKEN && export SONAR_TOKEN"
    exit 1
fi

cd "$REPO" || exit 2
PY="$ENVDIR/bin/python"
NODE="$ENVDIR/bin/node"

echo "=== tests with coverage ==="
. "$here/ccweb_testdb.sh" use || exit 1
if ! "$PY" -m pytest --cov=backend/app --cov-report=xml:coverage.xml; then
    echo "ERROR: tests failed - refusing to publish an analysis."
    exit 1
fi

echo "=== frontend tests with coverage ==="
if ! (cd frontend && "$NODE" node_modules/vitest/vitest.mjs run --coverage); then
    echo "ERROR: frontend tests failed - refusing to publish an analysis."
    exit 1
fi

echo "=== scanner ==="
if ! podman run --rm --network sonar-net \
    -e SONAR_HOST_URL=http://sonarqube:9000 \
    -e SONAR_TOKEN \
    -v "$REPO:/usr/src" \
    docker.io/sonarsource/sonar-scanner-cli; then
    echo "ERROR: scanner failed."
    exit 1
fi

echo
echo "Published: http://localhost:$SQPORT/dashboard?id=classobjects-ccwebdb"
exit 0
