"""Guard against the models and the Alembic migrations drifting apart."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.migration import MigrationContext
from app.database import Base
from app.models.views import create_views
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url  # noqa: F401

from tests.conftest import TEST_URL

BACKEND_DIR = Path(__file__).resolve().parents[1]
ALEMBIC_VERSIONS = BACKEND_DIR / "alembic" / "versions"


@pytest.fixture
def migrated_url() -> str:
    """A throwaway database built purely by running the migrations."""
    url = TEST_URL
    name = f"{url.database}_migrations"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    # hide_password=False is essential: str(url) would render it as "***".
    target = url.set(database=name).render_as_string(hide_password=False)
    try:
        yield target
    finally:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid() "
                    # Client sessions only. An autovacuum worker may be running
                    # on this database, and signalling one needs the
                    # pg_signal_autovacuum_worker role -- which the application
                    # user does not have, so the attempt raises and the teardown
                    # fails intermittently. Workers exit when the database is
                    # dropped, so there is nothing to terminate here anyway.
                    "AND backend_type = 'client backend'"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


@pytest.fixture
def round_trip_url() -> str:
    """A throwaway database of its own.

    So this test cannot fight `migrated_url` for one database while both
    fixtures are torn down at different times.
    """
    url = TEST_URL
    name = f"{url.database}_migrations_roundtrip"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")

    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    # hide_password=False is essential: str(url) would render it as "***".
    target = url.set(database=name).render_as_string(hide_password=False)
    try:
        yield target
    finally:
        with admin.connect() as conn:
            conn.execute(
                text(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = :name AND pid <> pg_backend_pid() "
                    "AND backend_type = 'client backend'"
                ),
                {"name": name},
            )
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        admin.dispose()


def test_migrations_round_trip(round_trip_url: str) -> None:
    """`alembic downgrade base` must complete after `alembic upgrade head`.

    The point of this test is that the downgrade *completes* -- not that it
    reproduces any particular schema, just that every `downgrade()` in the
    chain runs without PostgreSQL refusing an operation. Four bugs of the same
    shape (a downgrade doing something in the wrong order for what exists in
    the database at the moment it runs) went unseen for a long time because no
    test ever ran a full downgrade; this is that test.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", round_trip_url)

    upgrade(config, "head")
    downgrade(config, "base")


def test_migrations_match_models(migrated_url: str) -> None:
    """`alembic upgrade head` must produce exactly what the models describe.

    If this fails, a model changed without a matching migration -- generate one
    with `alembic revision --autogenerate`.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", migrated_url)
    upgrade(config, "head")

    engine = create_engine(migrated_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(
                connection,
                opts={"compare_type": True, "compare_server_default": True},
            )
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()

    # alembic_version is created by Alembic itself and is not in our metadata.
    meaningful = [
        entry
        for entry in diff
        if not (
            isinstance(entry, tuple)
            and len(entry) > 1
            and getattr(entry[1], "name", None) == "alembic_version"
        )
    ]
    assert meaningful == [], f"models and migrations disagree: {meaningful}"


def _create_views_kwargs(file_stem: str, function_name: str) -> dict[str, bool]:
    """The literal keyword arguments a migration's `create_views(...)` call passes.

    Read from the source with `ast` rather than hand-copied into a table:
    a hand-copied flag set only tests that `create_views` behaves as the copy
    says it should, not that the migration actually passes that copy. That gap
    is exactly how this project's soft-delete flag went missing three times in
    one task, in three different call sites, past three separate hand audits.
    Parsing beats a signature-shaped regex for the same reason it does
    everywhere else in this codebase: a call site can wrap arguments across
    lines in ways a regex has to special-case and a parser does not.
    """
    tree = ast.parse((ALEMBIC_VERSIONS / f"{file_stem}.py").read_text())
    function = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == function_name
    )
    call = next(
        node
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "create_views"
    )
    return {
        keyword.arg: ast.literal_eval(keyword.value)
        for keyword in call.keywords
        if keyword.arg is not None
    }


#: Every `create_views(...)` call in a migration, with the file it lives in,
#: the function it is called from, and the columns that must NOT appear in
#: what it produces.
#:
#: A view rebuilt at some revision must strip every feature added after it --
#: and a *downgrade* is the easy one to get wrong, because it runs after every
#: later downgrade has already removed its column. Adding a `create_views`
#: call to a migration means adding a row here.
HISTORICAL_VIEW_CALLS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "b78d71343405_rename_cost_columns_and_add_series_designation",
        "upgrade",
        ("deleted_at",),
    ),
    (
        "b78d71343405_rename_cost_columns_and_add_series_designation",
        "downgrade",
        (
            "deleted_at",
            "item_cost",
            "shipping_cost",
            "sales_tax",
            "piece_count",
            "source_title",
        ),
    ),
    (
        "fdf22151d022_lot_lineage_parent_item_and_split_marker",
        "upgrade",
        (
            "deleted_at",
            "item_cost",
            "shipping_cost",
            "sales_tax",
            "piece_count",
            "source_title",
        ),
    ),
    (
        "ffe36996607c_add_soft_delete",
        "downgrade",
        ("deleted_at",),
    ),
)


@pytest.mark.parametrize(
    ("file_stem", "function_name", "forbidden"), HISTORICAL_VIEW_CALLS
)
def test_a_historical_view_names_no_column_added_after_its_revision(
    file_stem: str, function_name: str, forbidden: tuple[str, ...]
) -> None:
    """A view created by an earlier revision must not name a later column.

    `alembic upgrade head` on an empty database fails partway otherwise, and so
    does any downgrade past the revision that added the column. This reads the
    real keyword arguments out of the migration file rather than assuming a
    call site passes what it is supposed to -- see `_create_views_kwargs`.
    """
    kwargs = _create_views_kwargs(file_stem, function_name)
    for statement in create_views(**kwargs):
        for column in forbidden:
            assert column not in statement, (
                f"{file_stem}.{function_name} would create a view naming "
                f"{column!r}, which does not exist at its revision"
            )
