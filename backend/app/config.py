"""Application settings, loaded from environment variables and the repo-root .env."""

from decimal import Decimal
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/config.py -> backend/app -> backend -> repo root
REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Everything configurable, read from the environment and the repo .env."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- database -----------------------------------------------------------
    database_url: str = Field(
        default="postgresql+psycopg://ccwebdb:devpassword@localhost:5432/ccwebdb",
        repr=False,
    )

    # --- auth ---------------------------------------------------------------
    # Overridden via JWT_SECRET in .env. The default is intentionally obvious
    # so that an unconfigured deployment is easy to spot.
    jwt_secret: str = Field(default="dev-only-insecure-secret-change-me", repr=False)
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 14

    # --- app ----------------------------------------------------------------
    api_prefix: str = "/api"
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # Seeded administrator, created by `python -m app.seed`.
    first_admin_email: str = "admin@example.com"
    first_admin_password: str = Field(default="adminpassword", repr=False)

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

    # --- sales tax on acquisitions ---------------------------------------------
    # Read when the API starts, then copied onto each item as it is created. Tax
    # paid is a historical fact, so changing these governs purchases recorded
    # afterwards and rewrites none recorded before. A fraction, not a
    # percentage -- 0.0635 is 6.35% -- bounded so that 6.35 typed for 6.35% is
    # refused at startup instead of multiplying every new cost by 7.35.
    sales_tax_rate: Decimal = Field(default=Decimal("0.0635"), ge=0, le=1)
    sales_tax_includes_shipping: bool = True


@lru_cache
def get_settings() -> Settings:
    """The settings singleton. Cached so the .env is read once per process."""
    return Settings()


settings = get_settings()
