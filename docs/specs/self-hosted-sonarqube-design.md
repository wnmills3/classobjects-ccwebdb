# Self-hosted SonarQube Server for ccwebdb

**Date:** 2026-09-09
**Status:** Approved, not yet implemented

## Goal

Run SonarQube locally so code and analysis data never reach the EU cloud, while
keeping the SonarQube MCP tools and secret-scanning hooks working.

The current connection is SonarQube Cloud **EU**. This is not an inference: the
CLI's own help states `-s` accepts "SonarQube Cloud EU (https://sonarcloud.io),
or SonarQube Cloud US (https://sonarqube.us). Defaults to SonarQube Cloud EU."

## Non-goals (YAGNI)

No TLS, no LAN or CI exposure, no user management, no auto-start on boot, no
frontend tests, and no Developer Edition features (branch analysis, PR
decoration). Community Build only.

## Decisions and why

**Own PostgreSQL rather than embedded H2.** SonarQube ships H2 for *evaluation
only*; it does not support upgrades on H2 and data can be lost on a version
change. The requirement is that history persists, and H2 is precisely the
option that quietly breaks that promise later.

**Own PostgreSQL rather than reusing the project's cluster** (`.pgdata/`, started
by `ccweb_startup.cmd`). Reuse would couple SonarQube's lifecycle to the project
database and put the cluster holding collection data in reach of an unrelated
schema. Isolation is worth one extra container.

**cmd scripts, not compose.** `scripts/` already uses `ccweb_*.cmd`. Following
that convention avoids introducing a second orchestration format and needs no
`podman-compose` dependency.

**Analysis needs SonarScanner, not `sonar analyze`.** `sonar analyze` is a
change-set tool (`--file`, `--staged`, `--base`) for the dev loop. Populating a
server project with issues, duplication and a quality gate requires SonarScanner
CLI. Run containerized, so no Java install is needed.

## Architecture

Three containers on a private podman network `sonar-net`:

| Container  | Image                            | Exposure                     |
| ---------- | -------------------------------- | ---------------------------- |
| `sonar-db` | `postgres:16`                    | none; network-internal only  |
| `sonarqube`| `sonarqube:community`            | `127.0.0.1:9000` only        |
| scanner    | `sonarsource/sonar-scanner-cli`  | ephemeral, one run per scan  |

Two deliberate choices: the database publishes **no host port**, and SonarQube
binds **`127.0.0.1`, not `0.0.0.0`**, so it is unreachable from the LAN even if
the Windows firewall were permissive. Local-only is enforced at the network
layer, not merely configured.

The scanner joins `sonar-net` and reaches the server as `http://sonarqube:9000`.

Named volumes: `sonar-db-data`, `sonarqube-data`, `sonarqube-extensions`,
`sonarqube-logs`.

Host prerequisites are already satisfied and were verified, not assumed:
`vm.max_map_count` is 1048576 (Elasticsearch needs >= 262144), ~31 GB RAM is
visible inside the WSL2 VM, and 955 GB disk is free. Note that
`podman machine inspect` reports `Memory: 2048`, but the WSL2 backend ignores
that field -- the value measured inside the VM is what counts.

## Repository artifacts

Committed:

- `scripts/ccweb_sonar_start.cmd` -- create network/volumes if absent, start both
  containers, poll `/api/system/status` until `UP`
- `scripts/ccweb_sonar_stop.cmd` -- stop containers, keep volumes
- `scripts/ccweb_sonar_scan.cmd` -- run tests with coverage, then the scanner.
  Fails fast with a clear message if the server is unreachable rather than
  silently starting it: an implicit start would hide a stopped server
- `sonar-project.properties` -- project key, sources, coverage paths
- `pyproject.toml` -- add `pytest-cov` to dev dependencies

Ignored (added to `.gitignore`): `coverage.xml`, `.scannerwork/`.

## Coverage wiring

`pyproject.toml` sets `pythonpath = ["backend"]`, so the importable package is
`app`:

```
pytest --cov=app --cov-report=xml:coverage.xml
```

`sonar-project.properties`:

```
sonar.projectKey=classobjects-ccwebdb
sonar.sources=backend/app,frontend/src
sonar.tests=backend/tests
sonar.python.coverage.reportPaths=coverage.xml
```

The frontend is analysed for issues but reports no coverage: it has no test
script. That should read as an honest 0%, not be hidden behind an exclusion.

## Auth cutover

1. `sonar auth logout`
2. `sonar auth login -s http://localhost:9000` -- interactive, run by the user;
   the CLI states plainly that agents cannot authenticate themselves
3. `sonar integrate claude --non-interactive` -- no `--project` flag needed:
   the CLI reads `sonar.projectKey` from `sonar-project.properties` at the
   repository root, which this design already creates (confirmed in
   `sonar integrate claude --help`)
4. Restart Claude Code and approve the `.mcp.json` prompt

**Known risk, to be tested first.** `sonar auth login` is built around the Cloud
browser flow and may not complete against a self-hosted server. The documented
fallback is environment variables (`SONAR_TOKEN`, `SONAR_HOST_URL`) with a token
generated in the SonarQube UI. This is verified **before** the scripts are built,
because it can change the auth section of the implementation.

The CLI stores **one** credential, so this replaces the SonarCloud connection.
The `wnmills3` org is left intact and simply unused; nothing is deleted.

## Failure modes to handle

- First boot takes 1-3 minutes while Elasticsearch starts; the start script polls
  for readiness rather than assuming it
- SonarQube ships `admin/admin` and forces a password change at first login --
  a manual browser step
- Port 9000 may already be in use; the start script checks before starting
- The scanner cannot reach the server if it is not on `sonar-net`

## Verification

- `/api/system/status` returns `UP`
- Scanner run completes and the project appears on the server
- **Coverage is non-zero.** A 0% result means the report was never parsed, which
  is the most common silent failure in this wiring
- Quality gate is queryable
- `mcp__sonarqube__*` tools load after a session restart

Verification is through a different channel than configuration: a written config
file proves nothing about whether a container can start or a report can be read.
