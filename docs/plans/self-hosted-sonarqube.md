# Self-hosted SonarQube Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run SonarQube locally so no code or analysis data reaches the EU cloud, while keeping the SonarQube MCP tools and secret-scanning hooks working.

**Architecture:** Three containers on a private podman network `sonar-net`: a PostgreSQL 16 database that publishes no host port, a SonarQube Community Build server bound to `127.0.0.1:9000` only, and an ephemeral SonarScanner CLI run once per analysis. Lifecycle is driven by `scripts/ccweb_sonar_*.cmd` following the existing `ccweb_*.cmd` convention.

**Tech Stack:** podman 6.1.1 (WSL2), `postgres:16`, `sonarqube:community`, `sonarsource/sonar-scanner-cli`, sonarqube-cli 1.7.0, pytest + pytest-cov, cmd/batch.

**Spec:** `docs/specs/self-hosted-sonarqube-design.md`

## Global Constraints

- **cmd/batch only. No PowerShell.** No `.ps1`, and no `powershell -Command` from inside a `.cmd`.
- **`NoDefaultCurrentDirectoryInExePath=1`** is set on this machine: always invoke scripts as `.\scripts\name.cmd` or with a full path, never `cmd /c name.cmd`.
- **Never commit to `main`.** All work lands on `feat/self-hosted-sonarqube`, which already exists and holds the spec.
- **No dates in filenames**; docs live flat in `docs/specs/` and `docs/plans/`.
- `.gitattributes` already forces `*.cmd` to CRLF on checkout. Do not add per-file line-ending handling.
- Python is the conda env at `%USERPROFILE%\miniforge3\envs\ccwebdb` (`python.exe`, `node.exe` both inside it).
- `UV_PROJECT_ENVIRONMENT` points `uv` at that conda env, so `uv add` installs there. Do not create a `.venv`.
- SonarQube binds **`127.0.0.1` only**; the database publishes **no host port**. Local-only is enforced at the network layer, not by firewall configuration.
- Community Build only. No branch analysis, no PR decoration, no TLS, no auto-start.
- `scripts\ccweb_check.cmd` must still pass at the end of every task that touches Python or frontend files.

---

### Task 1: Server lifecycle scripts

Stands up the network, volumes and both containers, and tears them down without destroying data. Start and stop ship together because a reviewer cannot sensibly accept one without the other.

**Files:**
- Create: `scripts/ccweb_sonar_start.cmd`
- Create: `scripts/ccweb_sonar_stop.cmd`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: a running server at `http://127.0.0.1:9000` whose `/api/system/status` returns a body containing `UP`; podman objects named `sonar-net`, `sonar-db`, `sonarqube`, and volumes `sonar-db-data`, `sonarqube-data`, `sonarqube-extensions`, `sonarqube-logs`. Task 2 and Task 3 depend on these exact names.

- [ ] **Step 1: Confirm the server is not already running (the check must fail first)**

Run:

```
curl -s -f -o nul --max-time 3 "http://127.0.0.1:9000/api/system/status"
echo exit=%errorlevel%
```

Expected: non-zero exit, because nothing is listening yet. If this succeeds, something already owns port 9000; stop it before continuing.

- [ ] **Step 2: Write `scripts/ccweb_sonar_start.cmd`**

```bat
@echo off
rem ---------------------------------------------------------------------------
rem  Start the local SonarQube server (Community Build) and its PostgreSQL.
rem
rem    ccweb_sonar_start
rem
rem  Pure cmd - no PowerShell. Uses curl for readiness (curl.exe ships with
rem  Windows 10 1803 and later).
rem
rem  The database publishes no host port; SonarQube reaches it over the private
rem  podman network. SonarQube binds 127.0.0.1 only, so it is unreachable from
rem  the LAN whatever the firewall says.
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion

set "NET=sonar-net"
set "DBNAME=sonar-db"
set "SQNAME=sonarqube"
set "SQPORT=9000"
set "DBPASS=sonar"

podman info >nul 2>&1
if errorlevel 1 (
    echo ERROR: podman is not responding. Run: podman machine start
    exit /b 1
)

rem  Port must be free, unless it is already our own container holding it.
podman ps --format "{{.Names}}" | findstr /x "%SQNAME%" >nul 2>&1
if errorlevel 1 (
    netstat -ano -p TCP | findstr /R /C:":%SQPORT%  *[0-9]" | findstr "LISTENING" >nul 2>&1
    if not errorlevel 1 (
        echo ERROR: port %SQPORT% is already in use by another process.
        exit /b 1
    )
)

podman network exists %NET% >nul 2>&1
if errorlevel 1 podman network create %NET% >nul

for %%V in (sonar-db-data sonarqube-data sonarqube-extensions sonarqube-logs) do (
    podman volume exists %%V >nul 2>&1
    if errorlevel 1 podman volume create %%V >nul
)

podman container exists %DBNAME% >nul 2>&1
if errorlevel 1 (
    echo [start] creating %DBNAME%
    podman run -d --name %DBNAME% --network %NET% ^
        -e POSTGRES_USER=sonar -e POSTGRES_PASSWORD=%DBPASS% -e POSTGRES_DB=sonar ^
        -v sonar-db-data:/var/lib/postgresql/data ^
        docker.io/library/postgres:16 >nul
) else (
    podman start %DBNAME% >nul
)

podman container exists %SQNAME% >nul 2>&1
if errorlevel 1 (
    echo [start] creating %SQNAME%
    podman run -d --name %SQNAME% --network %NET% ^
        -p 127.0.0.1:%SQPORT%:9000 ^
        -e SONAR_JDBC_URL=jdbc:postgresql://%DBNAME%:5432/sonar ^
        -e SONAR_JDBC_USERNAME=sonar ^
        -e SONAR_JDBC_PASSWORD=%DBPASS% ^
        -v sonarqube-data:/opt/sonarqube/data ^
        -v sonarqube-extensions:/opt/sonarqube/extensions ^
        -v sonarqube-logs:/opt/sonarqube/logs ^
        docker.io/library/sonarqube:community >nul
) else (
    podman start %SQNAME% >nul
)

echo [start] waiting for SonarQube to report UP - first boot takes 1-3 minutes
call :waitup "http://127.0.0.1:%SQPORT%/api/system/status" 300
if errorlevel 1 (
    echo ERROR: SonarQube did not reach status UP. Last 30 log lines:
    podman logs --tail 30 %SQNAME%
    exit /b 1
)

echo.
echo SonarQube is up:  http://localhost:%SQPORT%
echo First login is admin / admin - you will be forced to change it.
exit /b 0

rem ---------------------------------------------------------------------------
rem  :waitup <status url> <max seconds>  - poll until the body reports UP.
rem  Statuses are STARTING, UP, DOWN, RESTARTING and DB_MIGRATION_*; only UP
rem  contains the letters "UP", so a plain findstr is unambiguous here.
rem ---------------------------------------------------------------------------
:waitup
set "_tries=0"
:waitup_loop
curl -s --max-time 3 "%~1" 2>nul | findstr "UP" >nul 2>&1
if not errorlevel 1 exit /b 0
set /a _tries+=1
if !_tries! GEQ %~2 exit /b 1
timeout /t 1 /nobreak >nul
goto waitup_loop
```

- [ ] **Step 3: Write `scripts/ccweb_sonar_stop.cmd`**

```bat
@echo off
rem ---------------------------------------------------------------------------
rem  Stop the local SonarQube server and its database.
rem
rem  Volumes are deliberately kept, so issue history, settings and the token
rem  survive a stop. To discard everything, remove the volumes by hand:
rem      podman volume rm sonar-db-data sonarqube-data sonarqube-extensions sonarqube-logs
rem ---------------------------------------------------------------------------
setlocal

podman stop sonarqube >nul 2>&1
podman stop sonar-db  >nul 2>&1

echo SonarQube stopped. Volumes kept.
exit /b 0
```

- [ ] **Step 4: Run the start script and verify it reaches UP**

Run: `.\scripts\ccweb_sonar_start.cmd`

Expected: pulls `postgres:16` and `sonarqube:community` on first run, then prints `SonarQube is up:  http://localhost:9000` and exits 0. First boot may take 1-3 minutes while Elasticsearch starts.

- [ ] **Step 5: Verify status through a second channel**

Run:

```
curl -s "http://127.0.0.1:9000/api/system/status"
podman ps --format "{{.Names}} {{.Status}}"
```

Expected: JSON containing `"status":"UP"`, and both `sonarqube` and `sonar-db` listed as `Up`. Trusting only the script's own success message would prove nothing beyond self-consistency.

- [ ] **Step 6: Verify the database is NOT reachable from the host**

Run:

```
podman port sonar-db
```

Expected: no port mappings printed. The database must be reachable only over `sonar-net`.

- [ ] **Step 7: Verify stop keeps the data**

Run:

```
.\scripts\ccweb_sonar_stop.cmd
podman volume ls --format "{{.Name}}" | findstr sonarqube-data
```

Expected: `sonarqube-data` is still listed. Then restart with `.\scripts\ccweb_sonar_start.cmd` and confirm it reaches UP again, this time in seconds rather than minutes.

- [ ] **Step 8: Commit**

```bash
git add scripts/ccweb_sonar_start.cmd scripts/ccweb_sonar_stop.cmd
git commit -m "Add start and stop scripts for a local SonarQube server"
```

---

### Task 2: Authentication cutover

The spec's flagged risk, deliberately resolved before anything is built on top of it: `sonar auth login` is designed around the SonarQube Cloud browser flow and may not complete against a self-hosted server. Finding this out now costs one task; finding it out last invalidates the scan and integrate work.

**Files:**
- None. This task changes the machine's stored credential, and if the fallback is needed, the environment variables Task 3 already reads.

**Interfaces:**
- Consumes: the running server from Task 1 at `http://127.0.0.1:9000`.
- Produces: either a stored credential pointing at `http://localhost:9000`, **or** a `SONAR_TOKEN` value. Task 3 requires `SONAR_TOKEN` in the environment either way, because the scanner runs in its own container and cannot read the host keychain.

- [ ] **Step 1: Complete the forced first-login password change**

Open `http://localhost:9000` and log in as `admin` / `admin`. SonarQube forces a password change on first login; set one and keep it. This is a manual browser step and cannot be scripted.

- [ ] **Step 2: Generate a user token**

In the browser: **My Account -> Security -> Generate Tokens**, type **User Token**. Copy the value (it starts with `squ_`); it is shown once and never again.

Set it for the current shell:

```
set "SONAR_TOKEN=squ_paste_the_value_here"
```

- [ ] **Step 3: Verify the token works, independently of the CLI**

Run:

```
curl -s -u %SONAR_TOKEN%: "http://127.0.0.1:9000/api/authentication/validate"
```

Expected: `{"valid":true}`. Proving the token before involving the CLI means a later CLI failure cannot be misread as a bad token.

- [ ] **Step 4: Attempt the CLI browser login (this is the risk being tested)**

Run **in a real terminal, not through the agent**. The CLI states plainly that agents cannot authenticate themselves, and its arrow-key TUI hangs when its output is captured:

```
sonar auth logout
sonar auth login -s http://localhost:9000
```

- [ ] **Step 5: Record which path applies**

Run: `sonar auth status`

- **If it reports `Connected` to `http://localhost:9000`:** the browser flow works against a self-hosted server. Continue.
- **If login failed, or it still shows `sonarcloud.io`:** the fallback applies. Set both variables persistently:

```
setx SONAR_TOKEN "squ_paste_the_value_here"
setx SONAR_HOST_URL "http://localhost:9000"
```

Open a new terminal afterwards; `setx` does not affect the current one. Note in Task 5 that MCP will rely on these variables rather than the keychain.

- [ ] **Step 6: Confirm nothing still points at the EU cloud**

Run: `sonar auth status`

Expected: the output must **not** contain `https://sonarcloud.io`. If it does, the cutover has not happened and Task 3 would publish to the cloud. Stop and resolve this before continuing.

- [ ] **Step 7: No commit**

This task changes machine credentials only. There is nothing to commit; do not create an empty commit.

---

### Task 3: Project configuration and first analysis

**Files:**
- Create: `sonar-project.properties`
- Create: `scripts/ccweb_sonar_scan.cmd`
- Modify: `.gitignore` (append at the end)

**Interfaces:**
- Consumes: the running server from Task 1, `SONAR_TOKEN` from Task 2, and the network name `sonar-net`.
- Produces: project key `classobjects-ccwebdb` on the server, and `scripts/ccweb_sonar_scan.cmd`, which Task 4 extends with coverage. `sonar.projectKey` is what lets Task 5 run integrate with no `--project` flag.

- [ ] **Step 1: Confirm the project does not exist yet**

Run:

```
curl -s -u %SONAR_TOKEN%: "http://127.0.0.1:9000/api/projects/search?projects=classobjects-ccwebdb"
```

Expected: `"components":[]`, an empty list. This is the failing state this task fixes.

- [ ] **Step 2: Write `sonar-project.properties`**

```properties
# Analysis settings for the local SonarQube server. The scanner reads this file
# from the repository root; `sonar integrate claude` also reads sonar.projectKey
# from it, which is why no --project flag is needed anywhere.
sonar.projectKey=classobjects-ccwebdb
sonar.projectName=classobjects-ccwebdb

# Only real source is analysed. backend/alembic is intentionally absent:
# migrations are generated and add noise without adding signal.
sonar.sources=backend/app,frontend/src
sonar.tests=backend/tests

sonar.python.version=3.13
sonar.python.coverage.reportPaths=coverage.xml

sonar.exclusions=frontend/dist/**,frontend/node_modules/**
sonar.scm.provider=git
```

- [ ] **Step 3: Append to `.gitignore`**

Add at the end of the file, after the existing `.claude/` and `.mcp.json` block:

```gitignore

# SonarQube analysis output. The scanner's working directory and the coverage
# report are both regenerated on every run.
coverage.xml
.scannerwork/
```

- [ ] **Step 4: Write `scripts/ccweb_sonar_scan.cmd`**

Coverage is added in Task 4; this version publishes issues only.

```bat
@echo off
rem ---------------------------------------------------------------------------
rem  Publish an analysis of this repository to the local SonarQube server.
rem
rem    ccweb_sonar_scan
rem
rem  Requires SONAR_TOKEN in the environment: the scanner runs in its own
rem  container and cannot read the host keychain.
rem ---------------------------------------------------------------------------
setlocal enabledelayedexpansion

set "REPO=%~dp0.."
set "SQPORT=9000"

rem  Fail fast rather than starting the server implicitly. An implicit start
rem  would hide a stopped server and make a stale dashboard look current.
curl -s -f -o nul --max-time 3 "http://127.0.0.1:%SQPORT%/api/system/status"
if errorlevel 1 (
    echo ERROR: SonarQube is not responding on port %SQPORT%.
    echo        Run scripts\ccweb_sonar_start.cmd first.
    exit /b 1
)

if "%SONAR_TOKEN%"=="" (
    echo ERROR: SONAR_TOKEN is not set. Generate one at
    echo        http://localhost:%SQPORT%/account/security
    echo        then:  set "SONAR_TOKEN=squ_..."
    exit /b 1
)

pushd "%REPO%"

echo === scanner ===
podman run --rm --network sonar-net ^
    -e SONAR_HOST_URL=http://sonarqube:9000 ^
    -e SONAR_TOKEN=%SONAR_TOKEN% ^
    -v "%REPO%:/usr/src:z" ^
    docker.io/sonarsource/sonar-scanner-cli
if errorlevel 1 (
    echo ERROR: scanner failed.
    popd
    exit /b 1
)

popd
echo.
echo Published: http://localhost:%SQPORT%/dashboard?id=classobjects-ccwebdb
exit /b 0
```

- [ ] **Step 5: Run the scan**

Run: `.\scripts\ccweb_sonar_scan.cmd`

Expected: pulls `sonar-scanner-cli` on first run, prints `EXECUTION SUCCESS`, exits 0.

The scanner reaches the server as `http://sonarqube:9000`, the container name on `sonar-net` — not `localhost`, which inside the scanner container would refer to the scanner itself.

- [ ] **Step 6: Verify the project now exists, with issues**

Run:

```
curl -s -u %SONAR_TOKEN%: "http://127.0.0.1:9000/api/projects/search?projects=classobjects-ccwebdb"
curl -s -u %SONAR_TOKEN%: "http://127.0.0.1:9000/api/issues/search?componentKeys=classobjects-ccwebdb&ps=1"
```

Expected: the first returns a component with key `classobjects-ccwebdb`; the second returns a `"total"` field without an error. Confirm visually at the dashboard URL the script printed.

- [ ] **Step 7: Verify scan output is not tracked**

Run: `git status --porcelain`

Expected: shows `sonar-project.properties`, `scripts/ccweb_sonar_scan.cmd` and `.gitignore` — and **not** `coverage.xml` or `.scannerwork/`.

- [ ] **Step 8: Commit**

```bash
git add sonar-project.properties scripts/ccweb_sonar_scan.cmd .gitignore
git commit -m "Analyse the project against the local SonarQube server"
```

---

### Task 4: Coverage wiring

**Files:**
- Modify: `pyproject.toml` (the `[dependency-groups]` `dev` list)
- Modify: `scripts/ccweb_sonar_scan.cmd` (add a test-with-coverage step before the scanner)

**Interfaces:**
- Consumes: `scripts/ccweb_sonar_scan.cmd` from Task 3 and `sonar.python.coverage.reportPaths=coverage.xml` already set in `sonar-project.properties`.
- Produces: `coverage.xml` at the repository root, with paths SonarQube can map onto `sonar.sources`.

- [ ] **Step 1: Confirm coverage is currently zero on the server**

Run:

```
curl -s -u %SONAR_TOKEN%: "http://127.0.0.1:9000/api/measures/component?component=classobjects-ccwebdb&metricKeys=coverage"
```

Expected: either no `coverage` measure at all, or a value of `0.0`. This is the failing state this task fixes.

- [ ] **Step 2: Add pytest-cov**

Run: `uv add --group dev "pytest-cov>=7.0.0"`

`UV_PROJECT_ENVIRONMENT` points at the conda env, so this installs there and updates both `pyproject.toml` and `uv.lock`. Confirm the dev group now lists `pytest-cov` between `pytest` and `ruff`.

- [ ] **Step 3: Determine which working directory the report must come from**

This step decides whether coverage will map at all. `scripts\ccweb_check.cmd` runs pytest from inside `backend`, but a report produced there records paths like `app/db.py`, while `sonar.sources` is `backend/app` — SonarQube would match nothing and report 0%.

Try from the repository root:

```
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m pytest -q --cov=backend/app --cov-report=xml:coverage.xml
```

Expected: the suite passes and `coverage.xml` appears at the repository root.

If the suite fails from the root because tests assume `backend` as the working directory, use this fallback instead, which writes the report one level up:

```
pushd backend
"%USERPROFILE%\miniforge3\envs\ccwebdb\python.exe" -m pytest -q --cov=app --cov-report=xml:..\coverage.xml
popd
```

Whichever runs green is the command that goes into the script in Step 4. Record which one you used.

- [ ] **Step 4: Add the coverage step to `scripts/ccweb_sonar_scan.cmd`**

Insert immediately after `pushd "%REPO%"` and before `echo === scanner ===`, using the command that worked in Step 3 (the repository-root form is shown here):

```bat
set "ENVDIR=%USERPROFILE%\miniforge3\envs\ccwebdb"
set "PY=%ENVDIR%\python.exe"

if not exist "%PY%" (
    echo ERROR: conda environment not found: %ENVDIR%
    echo        see docs\environment-setup.md
    popd
    exit /b 1
)

echo === tests with coverage ===
"%PY%" -m pytest -q --cov=backend/app --cov-report=xml:coverage.xml
if errorlevel 1 (
    echo ERROR: tests failed - refusing to publish an analysis.
    popd
    exit /b 1
)
```

Publishing from a failing suite would attach misleading coverage to a broken build, so the script stops instead.

- [ ] **Step 5: Re-run the scan**

Run: `.\scripts\ccweb_sonar_scan.cmd`

Expected: tests run, then `EXECUTION SUCCESS`.

- [ ] **Step 6: Verify coverage is non-zero — the real gate**

Run:

```
curl -s -u %SONAR_TOKEN%: "http://127.0.0.1:9000/api/measures/component?component=classobjects-ccwebdb&metricKeys=coverage,lines_to_cover,uncovered_lines"
```

Expected: `coverage` has a value **greater than 0**, and `lines_to_cover` is non-zero.

A `0.0` here does **not** mean the tests cover nothing. It almost always means the report's file paths did not match `sonar.sources`. If it happens, open `coverage.xml`, read the `<source>` element and one `filename` attribute, and check that together they resolve to `backend/app/...` relative to the repository root. Switch to the other command form from Step 3 and re-run.

- [ ] **Step 7: Confirm the existing gates still pass**

Run: `.\scripts\ccweb_check.cmd`

Expected: `All checks passed.` Adding a dependency must not disturb ruff, mypy, pytest, eslint or prettier.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock scripts/ccweb_sonar_scan.cmd
git commit -m "Report test coverage to the local SonarQube server"
```

---

### Task 5: Agent integration against the local server

**Files:**
- Regenerated by the CLI, all gitignored: `.mcp.json`, `.claude/settings.json`, `.claude/hooks/sonar-secrets/**`
- Modify: `docs/runtime-operations.md`

**Interfaces:**
- Consumes: the credential or `SONAR_TOKEN` / `SONAR_HOST_URL` from Task 2, and `sonar.projectKey` from Task 3.
- Produces: `mcp__sonarqube__*` tools bound to the local server.

- [ ] **Step 1: Inspect the current wiring**

Run: `type .mcp.json`

Expected: it runs `sonar run mcp`. The server it talks to follows the stored credential, so this file itself does not change — but re-running integrate refreshes the hooks and the project binding.

- [ ] **Step 2: Re-run integrate**

Run: `sonar integrate claude --non-interactive`

No `--project` flag: the CLI reads `sonar.projectKey` from `sonar-project.properties`, created in Task 3. This is confirmed in `sonar integrate claude --help`.

Expected: the "No project key provided" warning seen against the cloud is now **absent**.

- [ ] **Step 3: Verify the MCP server starts**

Start `sonar run mcp` in a background shell, then in another run:

```
podman ps --format "{{.Image}} {{.Status}}"
```

Expected: `sonarsource/sonarqube-mcp` listed as `Up`. Stop it afterwards; Claude Code owns this process normally.

- [ ] **Step 4: Restart Claude Code and approve the MCP prompt**

Restart the session and approve the project's `.mcp.json` when prompted.

If it was previously rejected the prompt will not reappear on its own. Clear the stored rejection first:

```
claude mcp reset-project-choices
```

Check the state at any time with `claude mcp get sonarqube`.

- [ ] **Step 5: Verify the tools loaded and read local data**

In the restarted session, confirm `mcp__sonarqube__*` tools are present, then ask for the quality gate status of `classobjects-ccwebdb`.

Expected: a real answer sourced from `localhost:9000` — not an empty result, and not a SonarCloud response.

- [ ] **Step 6: Confirm no configuration leaked into git**

Run: `git status --porcelain`

Expected: clean apart from the documentation change. `.claude/` and `.mcp.json` are already ignored.

- [ ] **Step 7: Document the workflow**

Add a section to `docs/runtime-operations.md` covering: start with `.\scripts\ccweb_sonar_start.cmd`, scan with `.\scripts\ccweb_sonar_scan.cmd`, stop with `.\scripts\ccweb_sonar_stop.cmd`; that `SONAR_TOKEN` must be set in the environment; and that the podman volumes hold all history, so a stop is safe but `podman volume rm` is not.

- [ ] **Step 8: Commit**

```bash
git add docs/runtime-operations.md
git commit -m "Document running the local SonarQube server"
```

---

### Task 6: Make the MCP server reachable (amendment)

Added after Task 5 revealed a defect in this plan. Tasks 1-5 assumed that
`sonar integrate claude` plus a session restart would produce working
`mcp__sonarqube__*` tools. It does not.

`sonar run mcp` launches its container with **no `--network` flag**, so the
container lands on the default bridge while `SONARQUBE_URL` is the stored
`http://localhost:9000` — which inside that container's own network namespace is
the container itself. It dies at startup with `Connection refused` and, running
under `--rm`, removes itself before it can even be seen in `podman ps -a`.
`sonar run mcp --help` exposes only `--debug`, `--read-only`, `--toolsets` and
`--project`: no networking control exists.

`scripts/ccweb_sonar_scan.cmd` already solves the identical problem by joining
`sonar-net` and addressing the server by container name. The MCP path needs the
same treatment. This has been verified working before writing this task: the
image started cleanly on `sonar-net` and reported `All tools loaded: 29 tools`.

**Files:**
- Create: `scripts/ccweb_sonar_mcp.cmd`
- Modify: `.mcp.json` (gitignored — changed on disk, never committed)
- Modify: `docs/runtime-operations.md`

**Interfaces:**
- Consumes: the `sonar-net` network and `sonarqube` container from Task 1;
  `SONAR_TOKEN` from the environment; project key from Task 3.
- Produces: an MCP launch command that actually connects.

- [ ] **Step 1: Confirm the CLI's own launcher fails**

Run `sonar run mcp` and observe it exit with `Connection refused` to
`http://localhost:9000`. This is the failing state being fixed. Do not spend
long here — it fails within seconds.

- [ ] **Step 2: Write `scripts/ccweb_sonar_mcp.cmd`**

```bat
@echo off
rem ---------------------------------------------------------------------------
rem  Launch the SonarQube MCP server so that it can actually reach the local
rem  SonarQube.
rem
rem  `sonar run mcp` starts the container on the default bridge network with
rem  SONARQUBE_URL=http://localhost:9000. Inside that container, localhost is
rem  the container itself, so it dies at startup with "Connection refused".
rem  The CLI exposes no --network flag. Joining sonar-net and addressing the
rem  server by container name is exactly what ccweb_sonar_scan.cmd already does.
rem
rem  Claude Code runs this over stdio; do not echo anything to stdout here, or
rem  it will corrupt the protocol stream.
rem ---------------------------------------------------------------------------
setlocal

for %%I in ("%~dp0..") do set "REPO=%%~fI"

if "%SONAR_TOKEN%"=="" (
    echo ERROR: SONAR_TOKEN is not set. 1>&2
    exit /b 1
)

podman run --init --rm -i ^
    --network sonar-net ^
    -e SONARQUBE_TOKEN=%SONAR_TOKEN% ^
    -e SONARQUBE_URL=http://sonarqube:9000 ^
    -e SONARQUBE_PROJECT_KEY=classobjects-ccwebdb ^
    -v "%REPO%:/app/mcp-workspace:ro" ^
    docker.io/sonarsource/sonarqube-mcp
```

Diagnostics go to stderr (`1>&2`) because stdout is the MCP protocol stream.

- [ ] **Step 3: Point `.mcp.json` at it**

`.mcp.json` is gitignored and must not be committed. Write:

```json
{
  "mcpServers": {
    "sonarqube": {
      "command": "scripts\\ccweb_sonar_mcp.cmd"
    }
  }
}
```

- [ ] **Step 4: Verify the server starts and stays up**

Run `.\scripts\ccweb_sonar_mcp.cmd` with stdin from `nul` and confirm the log
reports `SonarQube MCP Server Started`, `URL: http://sonarqube:9000`, and a
non-zero tool count. Expected: `All tools loaded: 29 tools`.

- [ ] **Step 5: Document the trade-off**

Add to the `## SonarQube (local server)` section of `docs/runtime-operations.md`:
that `.mcp.json` is hand-managed and points at `ccweb_sonar_mcp.cmd`; **that
re-running `sonar integrate claude` overwrites `.mcp.json` and reintroduces the
broken `sonar run mcp` invocation**; and that the fix is to point it back.

- [ ] **Step 6: Commit**

Commit `scripts/ccweb_sonar_mcp.cmd` and `docs/runtime-operations.md`. Confirm
`.mcp.json` does not appear in `git status`.

---

### Amendment: what Task 4 Step 6 actually found

Task 4 Step 6 above tells the reader, on a `0.0` coverage result, to "switch to
the other command form from Step 3 and re-run" — i.e. swap between running
pytest from the repository root and running it from `backend`. That was never
the fix. **This section records the decisive discovery that Step 6 is missing:**
the actual remedy was adding

```toml
[tool.coverage.run]
relative_files = true
```

to `pyproject.toml`, keeping the repository-root form
(`--cov=backend/app --cov-report=xml:coverage.xml`) throughout. coverage.py
records **absolute** host paths by default; the scanner container mounts the
repository at a different absolute path, so those recorded paths do not exist
inside the container and match nothing under `sonar.sources`. Switching which
directory pytest runs from does not change that coverage.py still writes
absolute paths — it only changes which absolute paths are wrong.
`relative_files = true` makes the paths portable between host and container
instead. See `docs/specs/self-hosted-sonarqube-design.md`, "As built", for the
full explanation.

Three further divergences between this plan and what shipped were never
written down anywhere until now:

- **`:waitup` no longer sleeps with `timeout`.** Step 2's script body used
  `timeout /t 1 /nobreak >nul` inside the polling loop. The shipped
  `scripts/ccweb_sonar_start.cmd` uses `ping -n 2 127.0.0.1 >nul` instead:
  `timeout.exe` needs a real console and fails instantly, sleeping not at all,
  when stdin is redirected — exactly how an automated or scheduled caller runs
  this script. Ping's reply delay is a console-independent ~1 second sleep.
- **Repository-root resolution is canonicalising, not string concatenation.**
  Step 2 and Step 4's script bodies used `set "REPO=%~dp0.."`, a literal
  `...\scripts\..` path. The shipped scripts across all four use
  `for %%I in ("%~dp0..") do set "REPO=%%~fI"`, which resolves `%%~fI` to a
  real absolute path with no trailing `..` segment and no trailing slash —
  needed wherever `%REPO%` is bind-mounted into a container by path.
- **The scanner's bind mount dropped the `:z` SELinux suffix.** Task 3 Step 4's
  script body mounted `-v "%REPO%:/usr/src:z"`. The shipped
  `scripts/ccweb_sonar_scan.cmd` mounts `-v "%REPO%:/usr/src"`, with no `:z`.
  `:z` relabels the mount for SELinux label sharing between containers, which
  the WSL2 podman machine on this Windows host does not use; it was inert here
  and was dropped rather than carried forward as dead configuration.

## Done when

- `.\scripts\ccweb_sonar_start.cmd` brings the server to `UP`, and `_stop.cmd` preserves the volumes.
- `sonar auth status` does not mention `sonarcloud.io`.
- The dashboard shows project `classobjects-ccwebdb` with issues **and coverage above 0**.
- `mcp__sonarqube__*` tools answer from the local server.
- `.\scripts\ccweb_check.cmd` prints `All checks passed.`
- `git status` is clean; no analysis output or agent configuration is tracked.
