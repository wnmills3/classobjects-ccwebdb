"""Application settings, loaded from environment variables and the repo-root .env."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Any, Self
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator
from pydantic_settings import SettingsConfigDict
from sqlalchemy.engine import URL, make_url

from .runtime_settings import REPO_ROOT, RuntimeFields

#: The shortest signing key accepted: 32 characters, half the 64 hex
#: characters `secrets.token_hex(32)` makes.
MIN_SECRET_LENGTH = 32

#: Signing keys this repository has published, as a default or an example.
#: Long enough to pass the length check, and known to everyone who reads it.
PUBLISHED_SECRETS = frozenset(
    {
        "dev-only-insecure-secret-change-me",
        "replace-me-with-a-random-64-char-hex-string",
    }
)

_MAKE_A_SECRET = (
    'Put a new one in .env: python -c "import secrets; print(secrets.token_hex(32))"'
)

#: Database passwords this repository publishes, for the development cluster.
PUBLISHED_DATABASE_PASSWORDS = frozenset({"devpassword"})

#: Hosts that are this machine.
_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _is_local(url: URL) -> bool:
    """Whether a database URL names this machine: a loopback host or a socket.

    No host at all is the local socket, and so is a host that is a folder --
    in the URL's authority or as libpq's `host` query parameter.
    """
    query_host = url.query.get("host")
    if isinstance(query_host, tuple):
        query_host = query_host[0] if query_host else None
    host = url.host or query_host or ""
    return not host or host in _LOCAL_HOSTS or host.startswith("/")


def _dev_server_origins(data: dict[str, Any]) -> list[str]:
    """The dev server's two addresses, at the port already read."""
    port = data.get("ccweb_frontend_port")
    if port is None:
        # The port itself was refused; that error is the one to report.
        return []
    return [f"http://localhost:{port}", f"http://127.0.0.1:{port}"]


class Settings(RuntimeFields):
    """Everything configurable, read from the environment and the repo .env.

    The fields the scripts read too, the database's among them, are
    `RuntimeFields`'.
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        # A misspelt name in .env is an error, not a setting silently left at
        # its default -- JWT_SECRET spelt wrong would otherwise run unsigned
        # by any secret at all.
        extra="forbid",
        # A refusal must not print what it refused: the value is a secret.
        hide_input_in_errors=True,
    )

    # --- auth ---------------------------------------------------------------
    # JWT_SECRET in .env; no default. Anyone who knows it can sign a manager's
    # token, so a missing, short or published value refuses to start
    # (`_refuse_a_weak_secret`).
    jwt_secret: str = Field(default="", repr=False)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    # --- app ----------------------------------------------------------------
    api_prefix: str = "/api"
    # Unset: the two addresses the dev server answers on, at its port.
    cors_origins: list[str] = Field(default_factory=_dev_server_origins)

    # The IANA zone "today" and every local date is in (America/New_York).
    # Empty: the host's own zone. Stored times are UTC either way; this
    # decides only which calendar day a moment falls on, in reports and in
    # backup file names, so a server running on UTC can still keep its
    # owner's days. See app/clock.py.
    time_zone: str = ""

    # Where database dumps, workbook exports and media copies go when a
    # command is given no folder: beside the checkout, never inside it.
    ccweb_archive_dir: Path = REPO_ROOT.parent / "ccwebdb-backups"

    # DigiCert's seal for the certificate the shop is served under, read by
    # the frontend build (VITE_ keys are the only ones Vite passes to the
    # page). Empty: the shop shows no seal.
    vite_digicert_seal_id: str = ""

    # The shop's name and its owner's, read by the frontend build like the
    # seal. Empty: the frontend's own defaults -- "Coin Shop", and no owner
    # named.
    vite_shop_name: str = ""
    vite_shop_owner: str = ""

    # Whether the public shop is open (SHOP_OPEN). Closed, the catalog
    # answers signed-in callers only, registration is refused, and the
    # shop's pages show a coming-soon page to anyone signed out. The
    # console and every signed-in account are unaffected.
    shop_open: bool = True

    # Seeded administrator, created by `python -m app.seed`. No default
    # password: `seed` refuses to create the account without a strong one.
    first_admin_email: str = "admin@example.com"
    first_admin_password: str = Field(default="", repr=False)

    # --- image storage ------------------------------------------------------
    # The folder the bytes live in, MEDIA_ROOT, is a `RuntimeFields` field:
    # the service templates name it too.

    # Longest edge, in pixels, of each generated rendition. Originals are never
    # served -- public requests are answered only from these.
    thumbnail_max_px: int = 320
    web_max_px: int = 1600

    # Refused before anything is decoded. A "decompression bomb" is a small
    # file that expands to gigabytes of pixels, so both limits are needed.
    max_upload_bytes: int = 25 * 1024 * 1024
    max_image_pixels: int = 50_000_000

    # --- photograph library -------------------------------------------------
    # Where `python -m app.photo_import` looks for files to import. A
    # directory rather than an upload, because the photographs are already on
    # the machine and 673 of them through a file picker is a long sitting.
    # Git-ignored: a coin collection's photographs are not source code.
    photo_library_root: Path = REPO_ROOT / "photos"

    # --- sales tax on acquisitions ---------------------------------------------
    # Read when the API starts, then copied onto each item as it is created. Tax
    # paid is a historical fact, so changing these governs purchases recorded
    # afterwards and rewrites none recorded before. A fraction, not a
    # percentage -- 0.0635 is 6.35% -- bounded so that 6.35 typed for 6.35% is
    # refused at startup instead of multiplying every new cost by 7.35.
    # Unset: no tax, until the owner names their own jurisdiction's rate.
    sales_tax_rate: Decimal = Field(default=Decimal("0"), ge=0, le=1)
    sales_tax_includes_shipping: bool = True

    @field_validator("jwt_secret")
    @classmethod
    def _refuse_a_weak_secret(cls, value: str) -> str:
        """Refuse a signing key a stranger could know or guess."""
        if not value:
            raise ValueError(f"JWT_SECRET is not set. {_MAKE_A_SECRET}")
        if value in PUBLISHED_SECRETS:
            raise ValueError(f"JWT_SECRET is a published example. {_MAKE_A_SECRET}")
        if len(value) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"JWT_SECRET is shorter than {MIN_SECRET_LENGTH} characters. "
                f"{_MAKE_A_SECRET}"
            )
        return value

    @field_validator("time_zone")
    @classmethod
    def _a_known_zone(cls, value: str) -> str:
        """Refuse a zone name the zone database does not know."""
        if value:
            try:
                ZoneInfo(value)
            except (ValueError, ZoneInfoNotFoundError):
                raise ValueError(f"{value!r} is not a known time zone") from None
        return value

    @field_validator("ccweb_archive_dir")
    @classmethod
    def _from_the_repository(cls, value: Path) -> Path:
        """A relative folder is the repository's, as the scripts' folders are."""
        return value if value.is_absolute() else REPO_ROOT / value

    @model_validator(mode="after")
    def _refuse_the_published_database_password(self) -> Self:
        """Refuse the development password on a database another machine serves.

        The password is in this repository, so on a server reachable from
        elsewhere it is no password at all. On this machine's own cluster it
        guards nothing a local user could not reach anyway.
        """
        url = make_url(self.database_url)
        if url.password in PUBLISHED_DATABASE_PASSWORDS and not _is_local(url):
            raise ValueError(
                "DATABASE_URL uses the published development password on a "
                "database that is not on this machine. Set a real password for "
                "the database's role, and put it in DATABASE_URL in .env."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    """The settings singleton. Cached so the .env is read once per process."""
    return Settings()


settings = get_settings()
