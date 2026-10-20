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
_CONTAINER_PORTS = re.compile(r"sonarqube:9000|%SQPORT%:9000|%DBNAME%:5432")

#: More of the scripts, each with what it alone used to write out.
_SETTLED_TOO = {
    "ccweb_backup": ("OneDrive", "20:00"),
    "ccweb_pgadmin": ("5050",),
    "ccweb_sonar_start": (),
    "ccweb_sonar_scan": (),
}


@pytest.mark.parametrize("name", sorted(_SETTLED_TOO))
def test_the_other_scripts_name_no_literal_either(name: str) -> None:
    """Backup folder and time, pgAdmin's and SonarQube's ports: settings too."""
    forbidden = _LITERALS + _SETTLED_TOO[name]
    found = [
        f"{name}.cmd:{number}: {literal}"
        for number, line in _code_lines(SCRIPTS / f"{name}.cmd")
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


@pytest.mark.parametrize("name", _SETTLED)
def test_scripts_name_no_port_or_cluster_literal(name: str) -> None:
    """A port or folder written into a script is a second copy of a setting."""
    found = [
        f"{name}.cmd:{number}: {literal}"
        for number, line in _code_lines(SCRIPTS / f"{name}.cmd")
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
    values = {**assignments(RuntimeSettings()), "CCWEB_USER": "someone"}
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
