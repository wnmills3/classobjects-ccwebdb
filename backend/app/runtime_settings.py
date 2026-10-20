"""The values the scripts and the development server need, from the repo .env.

Scripts in both shells ask this module for their ports, hosts and folders
instead of writing them out, so a value lives in one place:

    python -m app.runtime_settings --format cmd     set "NAME=value" lines
    python -m app.runtime_settings --format sh      NAME='value' lines, exported
    python -m app.runtime_settings --format json

Run from `backend/`. `RuntimeSettings` reads the same `.env` as the API but
ignores the application's own keys and validates nothing secret, so a
machine with no `JWT_SECRET` can still ask what port its backend uses. The
API's `Settings` inherits `RuntimeFields`, so every key a script reads is a
key `.env` may hold, and a misspelt one still stops the API.

This module must not import `app.config`: that module builds the API's
settings when imported, which refuses to run without a signing key.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from pydantic import (
    Field,
    ValidationError,
    ValidationInfo,
    field_validator,
    model_validator,
)
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url

from .logpipe import DEFAULT_KEEP, DEFAULT_MAX_BYTES

# backend/app/runtime_settings.py -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]

#: The development server's hosts and ports, read by Vite as well.
_DEV_SERVER = json.loads(
    (REPO_ROOT / "frontend" / "dev-server.json").read_text(encoding="utf-8")
)

#: Hosts on which a test cluster and the collection's would be one machine.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

#: PostgreSQL's own port when a URL names none.
_PG_DEFAULT_PORT = 5432

_TIME_OF_DAY = re.compile(r"([01]\d|2[0-3]):[0-5]\d", re.ASCII)

#: What cmd would read as something other than part of the value: `%` and
#: `!` start expansions, `"` ends the quoted assignment, a line break ends
#: the command.
_CMD_UNSAFE = frozenset('"%!\r\n')

#: A `'` inside a single-quoted sh string: close, escaped quote, reopen.
_SH_QUOTE = "'\\''"


def _default_backup_dir() -> Path:
    """The routine backup's folder: OneDrive on Windows, the home elsewhere."""
    if sys.platform == "win32":
        return Path.home() / "OneDrive" / "coins_backup"
    return Path.home() / "ccwebdb-backup"


class RuntimeFields(BaseSettings):
    """Settings both the scripts and the API read. Names are their .env keys."""

    # --- database -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://ccwebdb:devpassword@localhost:5432/ccwebdb",
        repr=False,
    )
    # The cluster this machine starts, and the one tests build databases in.
    ccweb_db_cluster_dir: Path = Path(".pgdata")
    ccweb_test_db_cluster_dir: Path = Path(".pgdata.test")
    ccweb_test_db_port: int = Field(default=5433, ge=1, le=65535)
    # Empty: the test cluster's own, on CCWEB_TEST_DB_PORT.
    test_database_url: str | None = Field(default=None, repr=False)

    # --- the conda environment ----------------------------------------------
    # Read by ccweb_env before Python can run, so the scripts hold their
    # defaults; declared here so .env may name them.
    ccweb_conda_env: str | None = None
    ccweb_conda_root: str | None = None

    # --- development server -------------------------------------------------
    ccweb_api_host: str = _DEV_SERVER["apiHost"]
    ccweb_api_port: int = Field(default=_DEV_SERVER["apiPort"], ge=1, le=65535)
    ccweb_frontend_host: str = _DEV_SERVER["frontendHost"]
    ccweb_frontend_port: int = Field(
        default=_DEV_SERVER["frontendPort"], ge=1, le=65535
    )
    ccweb_pgadmin_port: int = Field(default=5050, ge=1, le=65535)
    ccweb_sonar_port: int = Field(default=9000, ge=1, le=65535)

    # --- logs ----------------------------------------------------------------
    ccweb_log_dir: Path = Path("logs")
    ccweb_log_keep: int = Field(default=DEFAULT_KEEP, ge=1)
    ccweb_log_max_bytes: int = Field(default=DEFAULT_MAX_BYTES, ge=1024)

    # --- routine backup -----------------------------------------------------
    ccweb_backup_dir: Path = Field(default_factory=_default_backup_dir)
    # Local time of day, HH:MM, at which the scheduled backup runs.
    ccweb_backup_time: str = "20:00"

    # --- public site (deploy/Caddyfile) -------------------------------------
    ccweb_site_domain: str = ""
    ccweb_tls_cert: str = ""
    ccweb_tls_key: str = ""
    # The built frontend Caddy serves; a copy where the web server's account
    # can read it, when the repository is somewhere it cannot.
    ccweb_site_root: Path = Path("frontend/dist")

    @field_validator("*", mode="before")
    @classmethod
    def _empty_means_default(cls, value: object, info: ValidationInfo) -> object:
        """Read `KEY=` as unset for anything but text.

        An empty folder is ".", wherever the process stands -- a backup
        written into the repository -- and an empty number is an error
        about nothing. For text, empty is a value (TIME_ZONE=: the host's).
        Inherited by the API's Settings, so it covers its fields too.
        """
        if (
            value == ""
            and info.field_name is not None
            and cls.model_fields[info.field_name].annotation is not str
        ):
            raise PydanticUseDefault
        return value

    @field_validator("ccweb_backup_time")
    @classmethod
    def _a_time_of_day(cls, value: str) -> str:
        """Refuse what a scheduler would misread: HH:MM, 00:00 to 23:59."""
        if not _TIME_OF_DAY.fullmatch(value):
            raise ValueError("must be a time of day, HH:MM from 00:00 to 23:59")
        return value

    @model_validator(mode="after")
    def _resolve_and_check(self) -> RuntimeFields:
        """Take relative folders from the repository root; keep tests apart.

        Only these fields: the API's own folders keep the meaning they had.
        """
        for name in RuntimeFields.model_fields:
            value = getattr(self, name)
            if isinstance(value, Path) and not value.is_absolute():
                setattr(self, name, REPO_ROOT / value)
        url = make_url(self.database_url)
        if (url.host or "localhost") in _LOCAL_HOSTS and (
            url.port or _PG_DEFAULT_PORT
        ) == self.ccweb_test_db_port:
            raise ValueError(
                "CCWEB_TEST_DB_PORT is DATABASE_URL's own port: the tests would "
                "build and drop their databases in the collection's cluster"
            )
        return self


class RuntimeSettings(RuntimeFields):
    """`RuntimeFields` from the repo .env, ignoring the application's keys."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
        hide_input_in_errors=True,
    )


class UnsafeValue(ValueError):
    """A value the requested shell format cannot carry intact."""

    def __init__(self, name: str) -> None:
        """Name the setting, never its value: it may be a secret."""
        super().__init__(
            f'{name} holds a character cmd cannot carry: " % ! or a line break'
        )
        self.name = name


def assignments(s: RuntimeFields, *, include_password: bool = False) -> dict[str, str]:
    """Every value a script uses, by the variable name it uses."""
    url = make_url(s.database_url)
    values = {
        "CCWEB_REPO": str(REPO_ROOT),
        "CCWEB_ENV_DIR": sys.prefix,
        "CCWEB_API_HOST": s.ccweb_api_host,
        "CCWEB_API_PORT": str(s.ccweb_api_port),
        "CCWEB_FRONTEND_HOST": s.ccweb_frontend_host,
        "CCWEB_FRONTEND_PORT": str(s.ccweb_frontend_port),
        "CCWEB_DB_CLUSTER_DIR": str(s.ccweb_db_cluster_dir),
        "CCWEB_DB_HOST": url.host or "localhost",
        "CCWEB_DB_PORT": str(url.port or _PG_DEFAULT_PORT),
        "CCWEB_DB_NAME": url.database or "",
        "CCWEB_DB_USER": url.username or "",
        "CCWEB_TEST_DB_CLUSTER_DIR": str(s.ccweb_test_db_cluster_dir),
        "CCWEB_TEST_DB_PORT": str(s.ccweb_test_db_port),
        "CCWEB_TEST_DB_URL": s.test_database_url
        or (
            "postgresql+psycopg://ccwebdb:devpassword@localhost:"
            f"{s.ccweb_test_db_port}/ccwebdb_test"
        ),
        "CCWEB_LOG_DIR": str(s.ccweb_log_dir),
        "CCWEB_LOG_KEEP": str(s.ccweb_log_keep),
        "CCWEB_LOG_MAX_BYTES": str(s.ccweb_log_max_bytes),
        "CCWEB_BACKUP_DIR": str(s.ccweb_backup_dir),
        "CCWEB_BACKUP_TIME": s.ccweb_backup_time,
        "CCWEB_PGADMIN_PORT": str(s.ccweb_pgadmin_port),
        "CCWEB_SONAR_PORT": str(s.ccweb_sonar_port),
        "CCWEB_SITE_DOMAIN": s.ccweb_site_domain,
        "CCWEB_TLS_CERT": s.ccweb_tls_cert,
        "CCWEB_TLS_KEY": s.ccweb_tls_key,
        "CCWEB_SITE_ROOT": str(s.ccweb_site_root),
    }
    if include_password:
        values["CCWEB_DB_PASSWORD"] = url.password or ""
    return values


def format_cmd(values: dict[str, str]) -> str:
    """`set "NAME=value"` lines, for a .cmd to `call`."""
    for name, value in values.items():
        if _CMD_UNSAFE.intersection(value):
            raise UnsafeValue(name)
    return "".join(f'set "{name}={value}"\n' for name, value in values.items())


def format_sh(values: dict[str, str]) -> str:
    """`NAME='value'` lines and one `export`, for a bash script to `eval`."""
    lines = [
        f"{name}='{value.replace(chr(39), _SH_QUOTE)}'"
        for name, value in values.items()
    ]
    lines.append("export " + " ".join(values))
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    """Print the settings for a shell; exit 2, naming the key, on a bad one."""
    parser = argparse.ArgumentParser(prog="python -m app.runtime_settings")
    parser.add_argument(
        "--format",
        choices=["cmd", "sh", "json"],
        default="cmd" if sys.platform == "win32" else "sh",
    )
    parser.add_argument(
        "--include-password",
        action="store_true",
        help="add CCWEB_DB_PASSWORD, for a script to read into a variable",
    )
    args = parser.parse_args(argv)
    try:
        values = assignments(RuntimeSettings(), include_password=args.include_password)
        if args.format == "cmd":
            text = format_cmd(values)
        elif args.format == "sh":
            text = format_sh(values)
        else:
            text = json.dumps(values, indent=2) + "\n"
    except ValidationError as refused:
        for error in refused.errors():
            where = ".".join(str(part) for part in error["loc"]).upper()
            print(
                f"runtime_settings: {where or 'settings'}: {error['msg']}",
                file=sys.stderr,
            )
        return 2
    except UnsafeValue as refused:
        print(f"runtime_settings: {refused}", file=sys.stderr)
        return 2
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
