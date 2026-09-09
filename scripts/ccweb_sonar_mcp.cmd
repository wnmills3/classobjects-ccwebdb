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
rem
rem  Contract: pre-flights only SONAR_TOKEN (exits non-zero to stderr if
rem  unset), then execs the MCP server; writes nothing else to stdout.
rem ---------------------------------------------------------------------------
setlocal

for %%I in ("%~dp0..") do set "REPO=%%~fI"

if "%SONAR_TOKEN%"=="" (
    echo ERROR: SONAR_TOKEN is not set. 1>&2
    exit /b 1
)

rem  Any pre-flight added here - e.g. copying the server/token checks from
rem  ccweb_sonar_scan.cmd - must redirect its ERROR/echo lines with 1>&2. That
rem  script's "echo ERROR:" lines go to stdout, which is fine there but would
rem  corrupt the MCP protocol stream if copied in unchanged.
podman run --init --rm -i ^
    --network sonar-net ^
    -e SONARQUBE_TOKEN=%SONAR_TOKEN% ^
    -e SONARQUBE_URL=http://sonarqube:9000 ^
    -e SONARQUBE_PROJECT_KEY=classobjects-ccwebdb ^
    -v "%REPO%:/app/mcp-workspace:ro" ^
    docker.io/sonarsource/sonarqube-mcp
