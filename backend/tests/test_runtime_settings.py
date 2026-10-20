"""The values scripts and the development server take from `.env`.

Each test reads settings with no `.env` file and none of their variables in
the environment, so what it sees is the defaults -- which must be exactly
the literals the scripts used before they asked for settings.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from app.config import Settings
from app.runtime_settings import (
    REPO_ROOT,
    RuntimeSettings,
    UnknownPlaceholder,
    UnsafeValue,
    assignments,
    format_cmd,
    format_sh,
    main,
    render,
)


@pytest.fixture
def bare(monkeypatch: pytest.MonkeyPatch) -> pytest.MonkeyPatch:
    """No setting from the environment: only defaults and what a test sets."""
    for name in RuntimeSettings.model_fields:
        monkeypatch.delenv(name.upper(), raising=False)
    return monkeypatch


def defaults() -> RuntimeSettings:
    """Settings as an installation with no `.env` reads them."""
    return RuntimeSettings(_env_file=None)


def test_with_no_env_file_the_values_are_todays_literals(
    bare: pytest.MonkeyPatch,
) -> None:
    """The scripts' old literals, every one, and nothing else."""
    backup = (
        Path.home() / "OneDrive" / "coins_backup"
        if sys.platform == "win32"
        else Path.home() / "ccwebdb-backup"
    )
    assert assignments(defaults()) == {
        "CCWEB_REPO": str(REPO_ROOT),
        "CCWEB_ENV_DIR": sys.prefix,
        "CCWEB_API_HOST": "127.0.0.1",
        "CCWEB_API_PORT": "8000",
        "CCWEB_FRONTEND_HOST": "127.0.0.1",
        "CCWEB_FRONTEND_PORT": "5173",
        "CCWEB_DB_CLUSTER_DIR": str(REPO_ROOT / ".pgdata"),
        "CCWEB_DB_HOST": "localhost",
        "CCWEB_DB_PORT": "5432",
        "CCWEB_DB_NAME": "ccwebdb",
        "CCWEB_DB_USER": "ccwebdb",
        "CCWEB_TEST_DB_CLUSTER_DIR": str(REPO_ROOT / ".pgdata.test"),
        "CCWEB_TEST_DB_PORT": "5433",
        "CCWEB_TEST_DB_USER": "ccwebdb",
        "CCWEB_TEST_DB_URL": (
            "postgresql+psycopg://ccwebdb:devpassword@localhost:5433/ccwebdb_test"
        ),
        "CCWEB_LOG_DIR": str(REPO_ROOT / "logs"),
        "CCWEB_LOG_KEEP": "3",
        "CCWEB_LOG_MAX_BYTES": "1073741824",
        "CCWEB_BACKUP_DIR": str(backup),
        "CCWEB_BACKUP_TIME": "20:00",
        "CCWEB_PGADMIN_PORT": "5050",
        "CCWEB_SONAR_PORT": "9000",
        "CCWEB_SITE_DOMAIN": "",
        "CCWEB_TLS_CERT": "",
        "CCWEB_TLS_KEY": "",
        "CCWEB_SITE_ROOT": str(REPO_ROOT / "frontend" / "dist"),
    }


def test_the_password_is_given_only_when_asked(bare: pytest.MonkeyPatch) -> None:
    """Printing the settings must not print the database's password."""
    bare.setenv("DATABASE_URL", "postgresql+psycopg://u:s3cret-pw@db:5432/x")
    plain = assignments(defaults())
    assert "CCWEB_DB_PASSWORD" not in plain
    assert not [name for name, value in plain.items() if "s3cret-pw" in value]
    asked = assignments(defaults(), include_password=True)
    assert asked["CCWEB_DB_PASSWORD"] == "s3cret-pw"


def test_the_test_clusters_role_comes_from_its_url(bare: pytest.MonkeyPatch) -> None:
    """ccweb_testdb creates the role its URL signs in as, not a second copy."""
    bare.setenv(
        "TEST_DATABASE_URL", "postgresql+psycopg://tester:t3st-pw@localhost:5433/x_test"
    )
    plain = assignments(defaults())
    assert plain["CCWEB_TEST_DB_USER"] == "tester"
    assert "CCWEB_TEST_DB_PASSWORD" not in plain
    asked = assignments(defaults(), include_password=True)
    assert asked["CCWEB_TEST_DB_PASSWORD"] == "t3st-pw"


@pytest.mark.parametrize(
    "name", ["CCWEB_BACKUP_DIR", "CCWEB_LOG_DIR", "CCWEB_API_PORT", "TEST_DATABASE_URL"]
)
def test_a_setting_left_empty_means_its_default(
    bare: pytest.MonkeyPatch, tmp_path: Path, name: str
) -> None:
    """`CCWEB_BACKUP_DIR=` uncommented must not mean the current folder.

    An empty path is ".", so the backup would land in the repository.
    """
    env = tmp_path / ".env"
    env.write_bytes(f"{name}=\n".encode())
    assert assignments(RuntimeSettings(_env_file=env)) == assignments(defaults())


def test_a_relative_folder_is_taken_from_the_repository_root(
    bare: pytest.MonkeyPatch,
) -> None:
    """Wherever the caller stands, `logs` means the repository's."""
    bare.chdir(REPO_ROOT / "backend")
    bare.setenv("CCWEB_LOG_DIR", "elsewhere")
    assert assignments(defaults())["CCWEB_LOG_DIR"] == str(REPO_ROOT / "elsewhere")


def _bash() -> str | None:
    """A bash that is not WSL's launcher, or None.

    On Windows `bash` on PATH may be System32's, which starts a WSL
    distribution; only Git for Windows' own bash is used there.
    """
    if sys.platform != "win32":
        return shutil.which("bash")
    git = shutil.which("git")
    if git is None:
        return None
    # git.exe sits in Git\cmd or Git\<toolchain>\bin depending on PATH.
    for folder in Path(git).resolve().parents:
        candidate = folder / "bin" / "bash.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def test_the_sh_format_survives_every_awkward_character(
    bare: pytest.MonkeyPatch,
) -> None:
    """A folder name holding quotes, `$`, `%` or `!` comes back exactly."""
    bash = _bash()
    if bash is None:
        pytest.skip("no bash here other than WSL's launcher")
    awkward = "a b'c\"d%e!f$g\\h"
    bare.setenv("CCWEB_BACKUP_DIR", awkward)
    text = format_sh(assignments(defaults()))
    shown = subprocess.run(
        [bash, "-c", 'eval "$1"; printf %s "$CCWEB_BACKUP_DIR"', "bash", text],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    # Relative to the repository root, as every relative folder is.
    assert shown == str(REPO_ROOT / awkward)


@pytest.mark.parametrize("char", ['"', "%", "!", "\n"])
def test_the_cmd_format_refuses_what_cmd_would_mangle(char: str) -> None:
    """Refuse `%` and `!`, which cmd expands, and `"`, which ends the value."""
    with pytest.raises(UnsafeValue, match="CCWEB_BACKUP_DIR"):
        format_cmd({"CCWEB_API_PORT": "8000", "CCWEB_BACKUP_DIR": f"D:{char}x"})


def test_the_cmd_format_is_one_set_per_value() -> None:
    """Quoted whole, so a trailing space or `&` stays inside the value."""
    text = format_cmd({"CCWEB_A": "x y&z", "CCWEB_B": "2"})
    assert text.splitlines() == ['set "CCWEB_A=x y&z"', 'set "CCWEB_B=2"']


def test_a_bad_value_exits_2_naming_the_key_not_the_value(
    bare: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A script must stop, not run on with an empty port."""
    bare.setenv("CCWEB_API_PORT", "80a")
    assert main(["--format", "sh"]) == 2
    captured = capsys.readouterr()
    assert "CCWEB_API_PORT" in captured.err
    assert "80a" not in captured.err
    assert captured.out == ""


def test_a_value_cmd_cannot_hold_exits_2_naming_the_key(
    bare: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Refused by name, so the reader knows which line of `.env` to fix."""
    bare.setenv("CCWEB_BACKUP_DIR", "D:\\100%")
    assert main(["--format", "cmd"]) == 2
    assert "CCWEB_BACKUP_DIR" in capsys.readouterr().err


def test_the_test_cluster_may_not_share_the_collections_port(
    bare: pytest.MonkeyPatch,
) -> None:
    """Test databases are built and dropped: never in the collection's cluster."""
    bare.setenv("CCWEB_TEST_DB_PORT", "5432")
    with pytest.raises(ValueError, match="CCWEB_TEST_DB_PORT"):
        defaults()


def test_a_backup_time_must_be_a_time_of_day(bare: pytest.MonkeyPatch) -> None:
    """Handed to a scheduler, which would read 25:00 its own way or not at all."""
    bare.setenv("CCWEB_BACKUP_TIME", "25:00")
    with pytest.raises(ValueError, match="ccweb_backup_time"):
        defaults()


def test_runtime_settings_needs_no_signing_key(bare: pytest.MonkeyPatch) -> None:
    """`ccweb_status` must work on a machine whose `.env` has no JWT_SECRET."""
    bare.delenv("JWT_SECRET", raising=False)
    assert assignments(defaults())["CCWEB_API_PORT"] == "8000"


def test_runtime_settings_ignores_the_applications_own_keys(
    bare: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The scripts read the same `.env` the API does, every key included."""
    env = tmp_path / ".env"
    env.write_bytes(b"SHOP_OPEN=false\nCCWEB_API_PORT=8100\n")
    assert assignments(RuntimeSettings(_env_file=env))["CCWEB_API_PORT"] == "8100"


def test_settings_still_refuses_a_misspelt_key(tmp_path: Path) -> None:
    """A misspelt script setting stops the API, as a misspelt JWT_SECRET does."""
    env = tmp_path / ".env"
    env.write_bytes(b"CCWEB_API_PROT=8100\n")
    with pytest.raises(ValueError, match="ccweb_api_prot"):
        Settings(_env_file=env, jwt_secret="k" * 32)


def test_every_runtime_field_is_a_settings_field() -> None:
    """So every key a script reads may be written in `.env`."""
    assert RuntimeSettings.model_fields.keys() <= Settings.model_fields.keys()


def test_render_fills_both_placeholder_forms() -> None:
    """`@NAME@` for files of our own, `{$NAME}` for Caddy's own syntax."""
    values = {"CCWEB_DB_PORT": "5432", "CCWEB_SITE_DOMAIN": "example.com"}
    assert render("port @CCWEB_DB_PORT@ at {$CCWEB_SITE_DOMAIN}", values) == (
        "port 5432 at example.com"
    )


def test_render_leaves_other_braces_and_at_signs_alone() -> None:
    """A JSON body, a mail address and Caddy's {path} are not placeholders."""
    text = '{"to": "me@example.com"} try_files {path} /index.html'
    assert render(text, {"CCWEB_DB_PORT": "5432"}) == text


def test_an_unknown_placeholder_is_refused_by_name() -> None:
    """A misspelt placeholder must not reach a service's configuration empty."""
    with pytest.raises(UnknownPlaceholder, match="CCWEB_DB_PROT"):
        render("port @CCWEB_DB_PROT@", {"CCWEB_DB_PORT": "5432"})


def test_render_prints_a_template_filled_from_settings(
    bare: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--render FILE` is how a script or a person fills a template."""
    template = tmp_path / "t.json"
    template.write_bytes(b'{"Port": @CCWEB_DB_PORT@, "User": "@CCWEB_DB_USER@"}\n')
    assert main(["--render", str(template)]) == 0
    assert capsys.readouterr().out == '{"Port": 5432, "User": "ccwebdb"}\n'


def test_render_names_an_unknown_placeholder_and_exits_2(
    bare: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Exit 2 with the placeholder's name; nothing half-filled on stdout."""
    template = tmp_path / "t.conf"
    template.write_bytes(b"x=@CCWEB_NOPE@\n")
    assert main(["--render", str(template)]) == 2
    captured = capsys.readouterr()
    assert "CCWEB_NOPE" in captured.err
    assert captured.out == ""
