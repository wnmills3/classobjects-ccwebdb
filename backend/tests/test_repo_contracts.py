"""Agreements between files that no single module's tests can see.

Each is a place two files must say the same thing -- a key the code reads
and the example that documents it, a script and its twin in the other
shell -- where nothing else would notice them drifting apart.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from subprocess import CompletedProcess

import pytest
from app.config import Settings
from app.runtime_settings import REPO_ROOT, RuntimeSettings, assignments, render

SCRIPTS = REPO_ROOT / "scripts"

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="runs cmd.exe")

#: The values the scripts once wrote out, which now come from settings.
_LITERALS = ("5432", "5433", "8000", "5173", "9000", ".pgdata", "devpassword")

#: Scripts whose every value comes from settings.
_SETTLED = (
    "ccweb_env",
    "ccweb_settings",
    "ccweb_logdir",
    "ccweb_status",
    "ccweb_startup",
    "ccweb_shutdown",
    "ccweb_pgstart",
    "ccweb_claude",
    "ccweb_psql",
    "ccweb_testdb",
    "ccweb_check",
)


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """A script's lines that are not comments, with their numbers."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return [
        (number, line)
        for number, line in enumerate(lines, 1)
        if not re.match(r"\s*(rem\b|::|#)", line, re.IGNORECASE)
    ]


#: Ports inside a container are the image's, not this machine's: SonarQube
#: listens on 9000 and its database on 5432 whatever the host maps them to.
_CONTAINER_PORTS = re.compile(
    r"sonarqube:9000|(%SQPORT%|\$SQPORT):9000|(%DBNAME%|\$DBNAME):5432"
)

#: More of the scripts, each with what it alone used to write out.
_SETTLED_TOO = {
    "ccweb_backup": ("OneDrive", "20:00"),
    "ccweb_pgadmin": ("5050",),
    "ccweb_sonar_start": (),
    "ccweb_sonar_scan": (),
}


@pytest.mark.parametrize("suffix", [".cmd", ".sh"])
@pytest.mark.parametrize("name", sorted(_SETTLED_TOO))
def test_the_other_scripts_name_no_literal_either(name: str, suffix: str) -> None:
    """Backup folder and time, pgAdmin's and SonarQube's ports: settings too."""
    forbidden = _LITERALS + _SETTLED_TOO[name]
    found = [
        f"{name}{suffix}:{number}: {literal}"
        for number, line in _code_lines(SCRIPTS / f"{name}{suffix}")
        for literal in forbidden
        if literal in _CONTAINER_PORTS.sub("", line)
    ]
    assert found == []


def test_the_pgadmin_connection_is_a_template_of_settings() -> None:
    """Its host, port, database and user are the API's, filled in at run time."""
    text = (SCRIPTS / "pgadmin-servers.json").read_text(encoding="utf-8")
    for placeholder in ("@CCWEB_DB_HOST@", "@CCWEB_DB_PORT@", "@CCWEB_DB_NAME@"):
        assert placeholder in text
    assert not [literal for literal in ("5432", "localhost") if literal in text]


@pytest.mark.parametrize("suffix", [".cmd", ".sh"])
@pytest.mark.parametrize("name", _SETTLED)
def test_scripts_name_no_port_or_cluster_literal(name: str, suffix: str) -> None:
    """A port or folder written into a script is a second copy of a setting."""
    found = [
        f"{name}{suffix}:{number}: {literal}"
        for number, line in _code_lines(SCRIPTS / f"{name}{suffix}")
        for literal in _LITERALS
        if literal in line
    ]
    assert found == []


#: .env files shaped as people write them, Windows editors included.
_DOTENVS = pytest.mark.parametrize(
    "dotenv",
    [
        b"CCWEB_CONDA_ENV=alpha\r\nSHOP_OPEN=true\r\n",
        b"SHOP_OPEN=true\nCCWEB_CONDA_ENV=beta\n",
        b'CCWEB_CONDA_ENV="gamma"\n',
        b"CCWEB_CONDA_ENV='zeta'\n",
        b"# CCWEB_CONDA_ENV=nope\nCCWEB_CONDA_ENV=delta\n",
        b"\xef\xbb\xbfSHOP_OPEN=true\r\nCCWEB_CONDA_ENV=epsilon\r\n",
        b"SHOP_OPEN=true\n",
    ],
    ids=[
        "crlf",
        "later-line",
        "double-quoted",
        "single-quoted",
        "comment",
        "bom",
        "absent",
    ],
)


def _bash() -> str | None:
    """A bash that is not WSL's launcher, or None (see test_runtime_settings)."""
    if sys.platform != "win32":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    for folder in Path(git).resolve().parents:
        candidate = folder / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


@_DOTENVS
def test_the_bash_twin_reads_the_conda_env_name_like_pydantic(
    tmp_path: Path, dotenv: bytes
) -> None:
    """ccweb_env.sh reads .env before Python can run, as ccweb_env.cmd does."""
    bash = _bash()
    if bash is None:
        pytest.skip("no bash here other than WSL's launcher")
    shutil.copytree(SCRIPTS, tmp_path / "scripts")
    env_file = tmp_path / ".env"
    env_file.write_bytes(dotenv)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCWEB_")}
    shown = subprocess.run(
        [bash, (tmp_path / "scripts" / "ccweb_env.sh").as_posix(), "--read-dotenv"],
        capture_output=True,
        text=True,
        env=environment,
        check=True,
    ).stdout.strip()
    pydantic = RuntimeSettings(_env_file=env_file).ccweb_conda_env or "ccwebdb"
    assert shown == f"CCWEB_CONDA_ENV={pydantic}"


@pytest.mark.skipif(
    sys.platform == "win32", reason="needs the environment's bin/python"
)
@pytest.mark.parametrize("arguments", ["--lan", "--keepdb", "--schedule 06:30", "use"])
def test_settings_ignore_the_arguments_of_the_script_that_sources_them(
    arguments: str,
) -> None:
    """`. file` hands the sourced file its caller's "$@": none may reach Python."""
    bash = _bash()
    if bash is None:
        pytest.skip("no bash")
    script = (
        f"set -- {arguments}; ENVDIR={sys.prefix}; "
        f". {(SCRIPTS / 'ccweb_settings.sh').as_posix()} && echo port=$CCWEB_API_PORT"
    )
    shown = subprocess.run(
        [bash, "-c", script], capture_output=True, text=True, check=False
    )
    assert "unrecognized arguments" not in shown.stderr
    assert re.search(r"^port=\d+$", shown.stdout, re.MULTILINE), shown.stderr


#: Stand-ins for what ccweb_testdb.sh calls, so its sourced path runs here:
#: a cluster that answers on the second ask, a server that outlives the
#: script, and settings holding a password the caller must never receive.
_TESTDB_STUBS = {
    "scripts/ccweb_env.sh": 'export ENVDIR="$ROOT/env" PGBIN="$ROOT/stubs"\n',
    "scripts/ccweb_settings.sh": (
        'export CCWEB_TEST_DB_CLUSTER_DIR="$ROOT/cluster" CCWEB_TEST_DB_PORT=5999\n'
        "export CCWEB_TEST_DB_PASSWORD=sekrit CCWEB_TEST_DB_USER=tester\n"
        "export CCWEB_TEST_DB_URL=postgresql://tester@localhost:5999/x_test\n"
        'export CCWEB_REPO="$ROOT" CCWEB_LOG_DIR="$ROOT/logs"\n'
    ),
    "stubs/pg_isready": (
        '[ -f "$ROOT/asked" ] && exit 0\ntouch "$ROOT/asked"\nexit 1\n'
    ),
    "stubs/pg_ctl": "sleep 8\n",
    "stubs/setsid": 'exec "$@"\n',
    "env/bin/python": "cat >/dev/null\n",
    "cluster/PG_VERSION": "18\n",
}


def test_sourcing_testdb_use_hands_the_caller_only_the_url(tmp_path: Path) -> None:
    """`. ccweb_testdb.sh use` sets TEST_DATABASE_URL and nothing else.

    It runs in people's own shells: no setting may be left behind to outrank
    a later .env edit, and no password for the next psql to sign in with.
    """
    bash = _bash()
    if bash is None:
        pytest.skip("no bash here other than WSL's launcher")
    for relative, text in _TESTDB_STUBS.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode())
        path.chmod(0o755)
    for name in ("ccweb_testdb.sh", "ccweb_logdir.sh"):
        shutil.copy(SCRIPTS / name, tmp_path / "scripts" / name)
    environment = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("CCWEB_", "PG", "TEST_DATABASE_URL", "LOGS"))
    }
    script = (
        'cd "$1" && export ROOT="$PWD" PATH="$PWD/stubs:$PATH"; '
        ". scripts/ccweb_testdb.sh use; echo rc=$?; env"
    )
    shown = subprocess.run(
        [bash, "-c", script, "bash", tmp_path.as_posix()],
        capture_output=True,
        text=True,
        env=environment,
        timeout=6,
        check=True,
    ).stdout
    assert "rc=0" in shown.splitlines()
    assert "TEST_DATABASE_URL=postgresql://tester@localhost:5999/x_test" in shown
    leaked = [
        line
        for line in shown.splitlines()
        if line.startswith(("CCWEB_", "PGPASSWORD=", "LOGS=")) or "sekrit" in line
    ]
    assert leaked == []


def test_the_conda_default_is_the_same_in_both_env_scripts() -> None:
    """The one default written in two places, because no Python runs yet."""
    cmd = (SCRIPTS / "ccweb_env.cmd").read_text(encoding="utf-8")
    sh = (SCRIPTS / "ccweb_env.sh").read_text(encoding="utf-8")
    in_cmd = re.findall(r'set "_CCWEB_ENVNAME=(\w+)"', cmd)
    in_sh = re.findall(r'_ccweb_envname="\$\{_ccweb_envname:-(\w+)\}"', sh)
    assert in_cmd == in_sh == ["ccwebdb"]


#: Scripts with no twin in the other shell, and why.
_ONE_SHELL_ONLY: dict[str, str] = {}


def test_every_script_has_a_twin_in_the_other_shell() -> None:
    """A step a Windows reader can script, a Linux reader can too."""
    cmd = {p.stem for p in SCRIPTS.glob("*.cmd")}
    sh = {p.stem for p in SCRIPTS.glob("*.sh")}
    assert sorted(cmd - sh - _ONE_SHELL_ONLY.keys()) == [], "no .sh twin"
    assert sorted(sh - cmd - _ONE_SHELL_ONLY.keys()) == [], "no .cmd twin"


#: Every template, and the placeholders `render` must leave none of.
_TEMPLATES = sorted(
    [
        *(p for p in (REPO_ROOT / "deploy").rglob("*") if p.is_file()),
        SCRIPTS / "pgadmin-servers.json",
    ]
)
_LEFT_OVER = re.compile(r"@[A-Z][A-Z0-9_]*@|\{\$[A-Z][A-Z0-9_]*\}")


@pytest.mark.parametrize("template", _TEMPLATES, ids=lambda p: p.name)
def test_every_template_renders_completely(template: Path) -> None:
    """Every placeholder a template names is a setting: none survive render."""
    values = {
        **assignments(RuntimeSettings()),
        # A development machine has no site; render refuses those empty.
        "CCWEB_SITE_DOMAIN": "example.com",
        "CCWEB_TLS_CERT": "/certs/fullchain.pem",
        "CCWEB_TLS_KEY": "/certs/example.com.key",
        "CCWEB_USER": "someone",
    }
    rendered = render(template.read_text(encoding="utf-8"), values)
    assert _LEFT_OVER.findall(rendered) == []


def _documented_caddyfile() -> str:
    """The Caddyfile the Windows deployment document shows for its example."""
    doc = (REPO_ROOT / "docs" / "deployment-windows.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```\n(classobjects\.com \{.*?)```", doc, re.DOTALL)
    assert len(blocks) == 1, "the example Caddyfile is no longer one block"
    return blocks[0]


def test_the_rendered_caddyfile_is_the_documented_one() -> None:
    """The template, filled with the example's values, is the file it shows."""
    template = (REPO_ROOT / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    values = {
        "CCWEB_SITE_DOMAIN": "classobjects.com",
        "CCWEB_TLS_CERT": "C:\\caddy\\certs\\fullchain.pem",
        "CCWEB_TLS_KEY": "C:\\caddy\\certs\\classobjects.com.key",
        "CCWEB_SITE_ROOT": (
            "C:\\Users\\wnmil\\dev\\classobjects-ccwebdb\\frontend\\dist"
        ),
        "CCWEB_API_HOST": "127.0.0.1",
        "CCWEB_API_PORT": "8000",
    }
    rendered = render(template, values)
    body = "\n".join(
        line for line in rendered.splitlines() if not line.startswith("#")
    ).strip()
    assert body == _documented_caddyfile().strip()


def test_shell_scripts_are_lf_and_executable() -> None:
    """A CR breaks every line for bash, and git must record the +x bit."""
    listing = subprocess.run(
        ["git", "ls-files", "-s", "--", "scripts/*.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    modes = {line.split()[3]: line.split()[0] for line in listing}
    on_disk = {f"scripts/{p.name}" for p in SCRIPTS.glob("*.sh")}
    assert sorted(on_disk - modes.keys()) == [], "not committed"
    assert sorted(name for name, mode in modes.items() if mode != "100755") == []
    assert [p.name for p in SCRIPTS.glob("*.sh") if b"\r" in p.read_bytes()] == []


@windows_only
@_DOTENVS
def test_conda_env_name_read_like_pydantic(tmp_path: Path, dotenv: bytes) -> None:
    """ccweb_env reads .env before Python can run; it must read it the same way."""
    shutil.copytree(SCRIPTS, tmp_path / "scripts")
    env_file = tmp_path / ".env"
    env_file.write_bytes(dotenv)
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCWEB_")}
    shown = subprocess.run(
        [
            "cmd",
            "/d",
            "/c",
            str(tmp_path / "scripts" / "ccweb_env.cmd"),
            "--read-dotenv",
        ],
        capture_output=True,
        text=True,
        env=environment,
        check=True,
    ).stdout.strip()
    pydantic = RuntimeSettings(_env_file=env_file).ccweb_conda_env or "ccwebdb"
    assert shown == f"CCWEB_CONDA_ENV={pydantic}"


def _run_cmd_probe(tmp_path: Path, body: str, **extra: str) -> CompletedProcess[str]:
    """Run `body` as a .cmd file, without this shell's CCWEB_ settings."""
    probe = tmp_path / "probe.cmd"
    probe.write_bytes(("@echo off\r\n" + body.replace("\n", "\r\n")).encode())
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCWEB_")}
    return subprocess.run(
        ["cmd", "/d", "/c", str(probe)],
        capture_output=True,
        text=True,
        env={**environment, **extra},
        check=False,
    )


@windows_only
def test_ccweb_settings_cmd_writes_no_file(tmp_path: Path) -> None:
    """The password must not pass through a file, even a temporary one."""
    nowhere = tmp_path / "nowhere"
    shown = _run_cmd_probe(
        tmp_path,
        f'call "{SCRIPTS}\\ccweb_env.cmd" >nul || exit /b 9\n'
        f'set "TEMP={nowhere}"\n'
        f'set "TMP={nowhere}"\n'
        f'call "{SCRIPTS}\\ccweb_settings.cmd" --include-password || exit /b 3\n'
        "echo port=%CCWEB_API_PORT%\n"
        "if defined CCWEB_DB_PASSWORD echo password=given\n",
    )
    assert shown.returncode == 0, shown.stdout + shown.stderr
    assert re.search(r"^port=\d+$", shown.stdout, re.MULTILINE)
    assert "password=given" in shown.stdout.splitlines()
    assert not nowhere.exists()


@windows_only
def test_ccweb_settings_cmd_exits_2_and_sets_nothing_on_a_refusal(
    tmp_path: Path,
) -> None:
    """A refused .env stops the caller; no half of the settings is left set."""
    shown = _run_cmd_probe(
        tmp_path,
        f'call "{SCRIPTS}\\ccweb_env.cmd" >nul || exit /b 9\n'
        f'call "{SCRIPTS}\\ccweb_settings.cmd"\n'
        "echo rc=%errorlevel% repo=[%CCWEB_REPO%] port=[%CCWEB_API_PORT%]\n",
        CCWEB_API_PORT="80a",
    )
    assert "rc=2 repo=[] port=[80a]" in shown.stdout.splitlines()
    assert "CCWEB_API_PORT" in shown.stderr


#: The loopback addresses a script may write itself, and why each is not a
#: setting: cmd's one-second sleep pings the loopback; a backend bound to
#: every address is asked for on the loopback; pgAdmin runs with no sign-in
#: and SonarQube holds the source, so both listen on the loopback by design
#: -- an address .env could move would expose them.
_LOOPBACK_ALLOWED = re.compile(
    r"ping -n \d+ 127\.0\.0\.1"
    r"|0\.0\.0\.0.*127\.0\.0\.1"
)
_LOOPBACK_BY_DESIGN = {"ccweb_pgadmin", "ccweb_sonar_scan", "ccweb_sonar_start"}


@pytest.mark.parametrize("suffix", [".cmd", ".sh"])
def test_scripts_write_no_host_of_their_own(suffix: str) -> None:
    """Every other host comes from the settings, as ports and folders do."""
    found = [
        f"{path.name}:{number}: {line.strip()}"
        for path in sorted(SCRIPTS.glob(f"*{suffix}"))
        for number, line in _code_lines(path)
        if re.search(r"127\.0\.0\.1|localhost", line)
        and not _LOOPBACK_ALLOWED.search(line)
        and not (path.stem in _LOOPBACK_BY_DESIGN and "localhost" not in line)
        and not (path.stem == "ccweb_status" and "SonarQube" in line)
    ]
    assert found == []


@pytest.mark.parametrize("suffix", [".cmd", ".sh"])
def test_the_development_cluster_starts_on_the_settings_port(suffix: str) -> None:
    """pg_ctl is told DATABASE_URL's port, not left to postgresql.conf's."""
    text = (SCRIPTS / f"ccweb_pgstart{suffix}").read_text(encoding="utf-8")
    assert re.search(r"pg_ctl[^\n]*-o \"-p (%CCWEB_DB_PORT%|\$\d)", text), text


@windows_only
def test_status_cmd_names_the_conda_environment_it_looked_for(
    tmp_path: Path,
) -> None:
    """CCWEB_CONDA_ENV may name another environment; status must say which."""
    shown = _run_cmd_probe(
        tmp_path,
        f'call "{SCRIPTS}\\ccweb_status.cmd"\n',
        CCWEB_CONDA_ENV="nosuch_env_q",
    )
    assert re.search(r"conda nosuch_env_q\s+NOT AVAILABLE", shown.stdout), shown.stdout
    assert "conda ccwebdb" not in shown.stdout


def test_status_sh_names_the_conda_environment_it_looked_for() -> None:
    """The bash twin says the same."""
    bash = _bash()
    if bash is None:
        pytest.skip("no bash here other than WSL's launcher")
    environment = {k: v for k, v in os.environ.items() if not k.startswith("CCWEB_")}
    shown = subprocess.run(
        [bash, (SCRIPTS / "ccweb_status.sh").as_posix()],
        capture_output=True,
        text=True,
        env={**environment, "CCWEB_CONDA_ENV": "nosuch_env_q"},
        timeout=60,
        check=False,
    )
    assert re.search(r"conda nosuch_env_q\s+NOT AVAILABLE", shown.stdout), shown.stdout
    assert "conda ccwebdb" not in shown.stdout


#: A Markdown link's target: `](target)` or `](target#anchor)`.
_LINK = re.compile(r"\]\(([^)\s#]+)(?:#[^)]*)?\)")


def _tracked(pattern: str) -> list[Path]:
    """Tracked files matching a git pathspec."""
    listing = subprocess.run(
        ["git", "ls-files", "--", pattern],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [REPO_ROOT / line for line in listing]


#: Inline code and fenced blocks, where `](x)` is text, not a link.
_CODE = re.compile(r"```.*?```|`[^`\n]*`", re.DOTALL)


def test_every_relative_markdown_link_resolves() -> None:
    """A renamed or removed document must take its links with it."""
    broken = [
        f"{doc.relative_to(REPO_ROOT).as_posix()}: {target}"
        for doc in _tracked("*.md")
        for target in _LINK.findall(_CODE.sub("", doc.read_text(encoding="utf-8")))
        if "://" not in target
        and not target.startswith("mailto:")
        and not (doc.parent / target).exists()
    ]
    assert broken == []


#: A document a script or source file names, as `docs\x.md` or `docs/x.md`.
_DOC_NAMED = re.compile(r"docs[\\/]([\w./-]+\.md)")


def test_every_document_a_script_or_source_names_exists() -> None:
    """A document a script tells its reader to see must be there to see."""
    sources = [*_tracked("scripts/*"), *_tracked("backend/app/*.py")]
    missing = sorted(
        f"{path.relative_to(REPO_ROOT).as_posix()}: docs/{name}"
        for path in sources
        for name in _DOC_NAMED.findall(path.read_text(encoding="utf-8"))
        if not (REPO_ROOT / "docs" / name).exists()
    )
    assert missing == []


#: A key in .env.example, written out or commented out with its default.
_EXAMPLE_KEY = re.compile(r"^#? ?([A-Z][A-Z0-9_]+)=", re.MULTILINE)


def test_env_example_names_every_setting_and_no_other() -> None:
    """Every key the code reads is documented; nothing documented is unread."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(_EXAMPLE_KEY.findall(text))
    read = {name.upper() for name in Settings.model_fields}
    assert sorted(read - documented) == [], "read by the code, not in .env.example"
    assert sorted(documented - read) == [], "in .env.example, read by nothing"
