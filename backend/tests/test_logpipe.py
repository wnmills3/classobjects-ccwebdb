"""The runtime's log writer: three files per type, none past the size limit."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from app import logpipe
from app.config import REPO_ROOT
from app.importers import cli
from app.logpipe import RollingWriter, numbered, pipe, rotate

SCRIPT = Path(logpipe.__file__)


def names(directory: Path) -> set[str]:
    return {p.name for p in directory.iterdir()}


def test_each_launch_starts_a_new_file_and_keeps_three(tmp_path: Path) -> None:
    log = tmp_path / "backend.log"
    for run in range(5):
        pipe([f"run {run}\n".encode()], RollingWriter(log, keep=3, max_bytes=1024))

    assert names(tmp_path) == {"backend.log", "backend.1.log", "backend.2.log"}
    assert log.read_text() == "run 4\n"
    assert numbered(log, 1).read_text() == "run 3\n"
    assert numbered(log, 2).read_text() == "run 2\n"


def test_a_file_rolls_over_before_it_passes_the_limit(tmp_path: Path) -> None:
    log = tmp_path / "frontend.log"
    line = b"x" * 99 + b"\n"  # 100 bytes
    pipe([line] * 25, RollingWriter(log, keep=3, max_bytes=1000))

    sizes = {p.name: p.stat().st_size for p in tmp_path.iterdir()}
    assert sizes == {
        "frontend.log": 500,
        "frontend.1.log": 1000,
        "frontend.2.log": 1000,
    }


def test_a_launch_that_wrote_nothing_does_not_push_a_log_out(
    tmp_path: Path,
) -> None:
    log = tmp_path / "backend.log"
    pipe([b"useful\n"], RollingWriter(log, keep=3, max_bytes=1024))
    pipe([], RollingWriter(log, keep=3, max_bytes=1024))
    pipe([], RollingWriter(log, keep=3, max_bytes=1024))

    assert numbered(log, 1).read_text() == "useful\n"
    assert log.read_bytes() == b""


def test_keeping_one_file_keeps_only_the_current_one(tmp_path: Path) -> None:
    log = tmp_path / "backend.log"
    log.write_text("old\n")
    rotate(log, keep=1)
    assert names(tmp_path) == set()


def test_the_script_can_only_rotate(tmp_path: Path) -> None:
    """For a file a program writes itself, like shutdown's pg_stop.log."""
    log = tmp_path / "logs" / "pg_stop.log"
    log.parent.mkdir()
    for stop in range(4):
        subprocess.run(
            [sys.executable, str(SCRIPT), str(log), "--rotate"],
            check=True,
            cwd=tmp_path,
        )
        log.write_text(f"stop {stop}\n")
    assert names(log.parent) == {"pg_stop.log", "pg_stop.1.log", "pg_stop.2.log"}
    assert numbered(log, 2).read_text() == "stop 1\n"


def test_the_script_pipes_stdin_with_limits_from_the_environment(
    tmp_path: Path,
) -> None:
    """Run as the startup script runs it: a plain script, fed by a pipe."""
    log = tmp_path / "logs" / "backend.log"
    env = {**os.environ, "CCWEB_LOG_KEEP": "2", "CCWEB_LOG_MAX_BYTES": "1024"}
    body = b"".join(f"line {n:04}\n".encode() for n in range(300))  # 3,000 bytes
    subprocess.run(
        [sys.executable, str(SCRIPT), str(log)],
        input=body,
        env=env,
        check=True,
        cwd=tmp_path,
    )
    files = sorted(names(log.parent))
    assert files == ["backend.1.log", "backend.log"]
    assert all((log.parent / f).stat().st_size <= 1024 for f in files)
    # The newest lines are in the current file.
    assert log.read_bytes().endswith(b"line 0299\n")


@pytest.mark.parametrize("value", ["zero", "0", "-3"])
def test_a_nonsense_setting_is_refused(
    monkeypatch: pytest.MonkeyPatch, value: str
) -> None:
    monkeypatch.setenv("CCWEB_LOG_KEEP", value)
    with pytest.raises(SystemExit):
        logpipe.keep_setting()


@pytest.mark.parametrize(
    ("setting", "expected"),
    [
        (None, REPO_ROOT / "logs" / "import"),
        ("", REPO_ROOT / "logs" / "import"),
        ("var/log", REPO_ROOT / "var" / "log" / "import"),
        (
            str(Path("/srv/ccweb-logs").resolve()),
            Path("/srv/ccweb-logs").resolve() / "import",
        ),
    ],
)
def test_import_review_files_follow_the_log_directory(
    monkeypatch: pytest.MonkeyPatch, setting: str | None, expected: Path
) -> None:
    """The importer finds the log directory as ccweb_logdir.cmd does."""
    if setting is None:
        monkeypatch.delenv("CCWEB_LOG_DIR", raising=False)
    else:
        monkeypatch.setenv("CCWEB_LOG_DIR", setting)
    assert cli.default_out_dir() == expected


def test_the_defaults_are_three_files_of_one_gigabyte(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("CCWEB_LOG_KEEP", raising=False)
    monkeypatch.delenv("CCWEB_LOG_MAX_BYTES", raising=False)
    assert logpipe.keep_setting() == 3
    assert logpipe.max_bytes_setting() == 1024**3
