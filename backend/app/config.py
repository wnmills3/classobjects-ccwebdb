"""Application settings, loaded from environment variables and the repo-root .env."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import SettingsConfigDict

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
    cors_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"]
    )

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
    # Bytes never live in the database: a collection's photographs run to
    # gigabytes. This is the local backend's root; an S3-compatible backend
    # swaps in behind the same interface without touching callers.
    media_root: Path = REPO_ROOT / "media"

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
    sales_tax_rate: Decimal = Field(default=Decimal("0.0635"), ge=0, le=1)
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


@lru_cache
def get_settings() -> Settings:
    """The settings singleton. Cached so the .env is read once per process."""
    return Settings()


settings = get_settings()
