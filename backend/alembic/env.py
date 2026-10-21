"""Alembic environment.

The database URL and target metadata are taken from the application itself so
that migrations and the running app can never disagree about either.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings
from app.database import Base

# Importing the models module registers every table on Base.metadata, which is
# what autogenerate diffs against. Do not remove even though it looks unused.
from app import models  # noqa: F401

config = context.config

# Fall back to the application's configured database, but let a caller that
# already set `sqlalchemy.url` on the config (the tests do) keep theirs. Overwriting
# unconditionally would silently point every programmatic run at the
# development database. The config is a ConfigParser, which reads `%` as
# interpolation, so a URL-encoded password's `%` is doubled to arrive intact.
if not config.get_main_option("sqlalchemy.url", None):
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))

if config.config_file_name is not None:
    # Migrations also run inside other processes -- the backup's proof, the
    # tests -- whose own loggers must keep working afterwards; fileConfig's
    # default would silence every logger that exists when it runs.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


OFFLINE_REFUSAL = (
    "migrations run online only: the baseline and data migrations read the "
    "database, so there is no SQL script to emit without one (drop --sql)"
)


def run_migrations_online() -> None:
    """Connect and run migrations against a live database."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError(OFFLINE_REFUSAL)
run_migrations_online()
