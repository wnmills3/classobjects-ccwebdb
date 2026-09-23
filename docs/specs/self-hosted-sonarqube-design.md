# Self-hosted SonarQube Server for ccwebdb

SonarQube runs locally, so code and analysis data never leave the machine,
while the SonarQube MCP tools keep working. Day-to-day commands are in
`docs/runtime-operations.md`, section "SonarQube (local server)".

Out of scope: TLS, LAN or CI exposure, user management, auto-start on boot,
and Developer Edition features (branch analysis, PR decoration). Community
Build only.

## Decisions

- **Its own PostgreSQL, not embedded H2.** H2 is for evaluation only: it does
  not support upgrades and can lose data on a version change, which breaks the
  one promise that matters -- issue history persists.
- **Its own PostgreSQL, not the project's cluster** (`.pgdata/`). Reuse would
  couple SonarQube's lifecycle to the project database and put the cluster
  holding collection data within reach of an unrelated schema.
- **cmd scripts, not compose.** Follows the `scripts\ccweb_*.cmd` convention
  and needs no `podman-compose`.
- **SonarScanner, not `sonar analyze`.** `sonar analyze` is a change-set tool
  for the dev loop; populating a server project with issues, duplication,
  coverage and a quality gate needs SonarScanner CLI, run in a container so no
  Java install is needed.

## Architecture

Containers on a private podman network, `sonar-net`:

| Container | Image | Exposure |
|---|---|---|
| `sonar-db` | `postgres:16` | none; network-internal only |
| `sonarqube` | `sonarqube:community` | `127.0.0.1:9000` only |
| scanner | `sonarsource/sonar-scanner-cli` | ephemeral, one run per scan |
| MCP server | `sonarsource/sonarqube-mcp` | ephemeral, stdio to Claude Code |

The database publishes **no host port**, and SonarQube binds **`127.0.0.1`,
not `0.0.0.0`**, so it is unreachable from the LAN whatever the firewall says:
local-only is enforced at the network layer, not merely configured. The scanner
and the MCP server join `sonar-net` and address the server as
`http://sonarqube:9000`.

Named volumes: `sonar-db-data`, `sonarqube-data`, `sonarqube-extensions`,
`sonarqube-logs`.

## Scripts

| Script | Does |
|---|---|
| `ccweb_sonar_start.cmd` | Starts the podman machine if it is not responding (one attempt). Refuses if port 9000 is held by anything but its own container. Creates the network and volumes if absent, creates or starts both containers, and polls `/api/system/status` until `UP` (up to 300 tries, sleeping with `ping` between them; first boot takes 1-3 minutes); on timeout prints the last 30 log lines and exits non-zero. |
| `ccweb_sonar_stop.cmd` | Stops both containers, keeps the volumes, and exits non-zero unless `podman ps` confirms both are gone. |
| `ccweb_sonar_scan.cmd` | Fails fast if the server is not responding (it never starts it: an implicit start would make a stale dashboard look current) or `SONAR_TOKEN` is unset. Then runs the backend tests with coverage, the frontend tests with coverage, and the scanner; refuses to publish if any step fails. |
| `ccweb_sonar_mcp.cmd` | Launches the MCP server for Claude Code over stdio. Checks only `SONAR_TOKEN` (errors to stderr) and writes nothing else to stdout, which would corrupt the protocol stream. |

**Why a separate MCP launcher.** The CLI's own `sonar run mcp` starts its
container on the default bridge network with `SONARQUBE_URL=http://localhost:9000`
and no `--network` flag, so `localhost` is the container itself and it dies
with `Connection refused`. `ccweb_sonar_mcp.cmd` joins `sonar-net` instead.
`.mcp.json` (gitignored, hand-edited) points at it:

```json
{
  "mcpServers": {
    "sonarqube": {
      "command": "scripts\\ccweb_sonar_mcp.cmd"
    }
  }
}
```

**Do not run `sonar integrate claude`** on a machine where this works: it
overwrites `.mcp.json` with the `sonar run mcp` launcher and breaks MCP again.
If that happens, restore the file above.

## Configuration

`sonar-project.properties` at the repository root:

- `sonar.projectKey=classobjects-ccwebdb`
- `sonar.sources=backend/app,frontend/src` (migrations are excluded as
  generated noise)
- `sonar.tests=backend/tests,frontend/src`, with `sonar.test.inclusions`
  naming `*.test.js`, `*.test.jsx` and `src/test/**` -- frontend tests sit
  beside their components, and without the inclusion they count as uncovered
  source
- `sonar.python.coverage.reportPaths=coverage.xml`
- `sonar.javascript.lcov.reportPaths=frontend/coverage/lcov.info`
- `sonar.exclusions=frontend/dist/**,frontend/node_modules/**`

**Coverage paths must resolve inside the scanner's container.** The scan
script runs, from the repository root:

```
pytest -q --cov=backend/app --cov-report=xml:coverage.xml
```

with `relative_files = true` under `[tool.coverage.run]` in `pyproject.toml`.
All three are needed: coverage.py records absolute paths by default, and the
scanner reads `coverage.xml` inside a container where the repository is mounted
at a different absolute path. The mismatch does not error; it reports 0%
coverage, which reads as clean code rather than a broken pipeline. Running from
`backend\` (as `ccweb_check.cmd` does) records `app/...` paths that
`backend/app` cannot map, with the same silent result.

The frontend's `vitest run --coverage` writes `frontend/coverage/lcov.info`.

Ignored by git: `coverage.xml`, `.scannerwork/`, `frontend/coverage/`,
`.mcp.json`, `scripts/.sonar/`.

## Authentication

The scanner and MCP containers cannot read the host keychain, so both take
`SONAR_TOKEN` from the environment. Generate a token at
`http://localhost:9000/account/security`, then:

```
set "SONAR_TOKEN=squ_..."
```

SonarQube ships `admin` / `admin` and forces a password change at first login,
a manual browser step.

## Verifying it works

Check through a different channel than the configuration -- a written config
proves nothing about whether a container starts or a report is read:

- `/api/system/status` returns `UP`.
- A scan completes and the dashboard shows the project.
- **Coverage is non-zero.** 0% means the report was never mapped, the most
  common silent failure in this wiring.
- The quality gate is queryable.
- The `mcp__sonarqube__*` tools load after a Claude Code restart.
