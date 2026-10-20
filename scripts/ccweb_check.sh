#!/usr/bin/env bash
# ---------------------------------------------------------------------------
#  Run every quality gate, in the order that fails fastest.
#
#    ./scripts/ccweb_check.sh        format check, lint, types, tests, frontend
#    ./scripts/ccweb_check.sh fix    reformat and auto-fix first, then check
#
#  The bash twin of ccweb_check.cmd; the reasons for each gate are written
#  there. Exit code is non-zero if anything failed.
# ---------------------------------------------------------------------------
set -u
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$here/.." && pwd)"
. "$here/ccweb_env.sh" || exit 2
PY="$ENVDIR/bin/python"
NODE="$ENVDIR/bin/node"
FAILED=""

if [ ! -x "$PY" ]; then
    echo "ERROR: conda environment not found: $ENVDIR"
    echo "       see docs/environment-setup-linux.md"
    exit 1
fi

cd "$REPO" || exit 2

if [ "${1:-}" = "fix" ]; then
    echo "[fix] ruff format"
    "$PY" -m ruff format .
    echo "[fix] ruff check --fix"
    "$PY" -m ruff check . --fix
    if [ -d frontend/node_modules/prettier ]; then
        echo "[fix] prettier"
        "$NODE" frontend/node_modules/prettier/bin/prettier.cjs --write frontend \
            --ignore-path .gitignore --ignore-path frontend/.prettierignore --log-level warn
    fi
    echo
fi

echo "=== python format ==="
"$PY" -m ruff format --check . || FAILED="$FAILED format"

echo "=== python lint ==="
"$PY" -m ruff check . || FAILED="$FAILED lint"

echo "=== mutation scaffolding guard ==="
# A deliberate mutation is marked `if False:`, `if True:` or MUTATION; none
# may be left in backend/app. See ccweb_check.cmd.
hits="$(grep -rn --include='*.py' -e 'if False:' -e 'if True:' -e 'MUTATION' backend/app || true)"
if [ -n "$hits" ]; then
    printf '  left in place: %s\n' "$hits"
    echo "  FAILED: mutation-test scaffolding was left in backend/app -- see the line(s) above"
    FAILED="$FAILED mutation-scaffold"
fi

echo "=== python types ==="
"$PY" -m mypy || FAILED="$FAILED types"

echo "=== tests ==="
# Where this computer has a test cluster of its own, the tests run there
# (ccweb_testdb.sh); elsewhere TEST_DATABASE_URL is left as it was.
. "$here/ccweb_testdb.sh" use || FAILED="$FAILED testdb"
(cd backend && "$PY" -m pytest) || FAILED="$FAILED tests"

if [ -d frontend/node_modules/eslint ]; then
    echo "=== frontend lint ==="
    "$NODE" frontend/node_modules/eslint/bin/eslint.js --max-warnings 0 frontend || FAILED="$FAILED eslint"

    echo "=== frontend format ==="
    "$NODE" frontend/node_modules/prettier/bin/prettier.cjs --check frontend \
        --ignore-path .gitignore --ignore-path frontend/.prettierignore --log-level warn \
        || FAILED="$FAILED prettier"

    echo "=== frontend tests ==="
    (cd frontend && "$NODE" node_modules/vitest/vitest.mjs run) || FAILED="$FAILED vitest"

    echo "=== frontend bundle isolation ==="
    # A failed build must not leave an older build's graph for the check
    # to misread: dist is cleared first.
    if (cd frontend && rm -rf dist && "$NODE" node_modules/vite/bin/vite.js build); then
        (cd frontend && "$NODE" scripts/check-bundle-isolation.mjs) || FAILED="$FAILED isolation"
    else
        FAILED="$FAILED build"
    fi
else
    # An unrun check is not a passed one.
    echo "=== frontend === SKIPPED: frontend/node_modules is missing"
    echo "    run: cd frontend && npm install"
    FAILED="$FAILED frontend-not-installed"
fi

echo
if [ -n "$FAILED" ]; then
    echo "FAILED:$FAILED"
    exit 1
fi
echo "All checks passed."
exit 0
