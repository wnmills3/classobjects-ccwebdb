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
from app.runtime_settings import REPO_ROOT, RuntimeSettings

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


@windows_only
@pytest.mark.parametrize(
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


#: A key in .env.example, written out or commented out with its default.
_EXAMPLE_KEY = re.compile(r"^#? ?([A-Z][A-Z0-9_]+)=", re.MULTILINE)


def test_env_example_names_every_setting_and_no_other() -> None:
    """Every key the code reads is documented; nothing documented is unread."""
    text = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = set(_EXAMPLE_KEY.findall(text))
    read = {name.upper() for name in Settings.model_fields}
    assert sorted(read - documented) == [], "read by the code, not in .env.example"
    assert sorted(documented - read) == [], "in .env.example, read by nothing"
