"""Guard against the models and the Alembic migrations drifting apart.

The first revision is a baseline: `backend/alembic/baseline.sql`, the schema
as it stood when the history was squashed. These tests build a database by
running the migrations alone and hold it against the models, the views and
the SQL functions the application defines -- the test suite's own database
is built from those, so a difference would pass every other test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.command import current, upgrade
from alembic.config import Config
from alembic.migration import MigrationContext
from app.config import settings
from app.database import Base
from app.models.views import ALL_VIEWS
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from tests.conftest import TEST_URL, drop_database

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _upgrade(url: str) -> None:
    """Run every migration against `url`."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    upgrade(config, "head")


def test_a_configured_url_with_a_percent_sign_reaches_alembic_intact(
    engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`env.py` falls back to the app's URL, which may hold a `%`.

    Alembic's config is a `ConfigParser`, which reads `%` as interpolation;
    a URL-encoded password (`p%40ss`) must arrive as written. `current` only
    reads `alembic_version`, on the suite's own database.
    """
    url = engine.url.update_query_dict({"application_name": "ccweb%test"})
    rendered = url.render_as_string(hide_password=False)
    assert "%" in rendered
    monkeypatch.setattr(settings, "database_url", rendered)
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    assert config.get_main_option("sqlalchemy.url", None) is None
    current(config)
    assert config.get_main_option("sqlalchemy.url") == rendered


@pytest.fixture(scope="module")
def migrated_url() -> Iterator[str]:
    """A throwaway database built purely by running the migrations.

    Built and upgraded once for the module: every test here only reads it.
    `_normalised` creates temporary views, but inside a transaction it rolls
    back, so nothing a test does is seen by the next.
    """
    url = TEST_URL
    name = f"{url.database}_migrations"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")

    with admin.connect() as conn:
        drop_database(conn, name)
        conn.execute(text(f'CREATE DATABASE "{name}"'))

    # hide_password=False is essential: str(url) would render it as "***".
    target = url.set(database=name).render_as_string(hide_password=False)
    try:
        _upgrade(target)
        yield target
    finally:
        with admin.connect() as conn:
            drop_database(conn, name)
        admin.dispose()


def test_migrations_match_models(migrated_url: str) -> None:
    """`alembic upgrade head` must produce exactly what the models describe.

    If this fails, a model changed without a matching migration -- generate one
    with `alembic revision --autogenerate`.
    """
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


def test_the_baseline_seeds_the_fee_kinds(migrated_url: str) -> None:
    """The baseline's rows actually load, on a database built only by it.

    `test_fee_kinds_are_seeded` (test_sales_fees_schema.py) runs against the
    `create_all`-built test database, so it passes because of conftest's own
    `_FEE_KINDS` seeding -- which mirrors `ensure_store_venue` for the same
    reason -- and would stay green even if the baseline's INSERTs were
    deleted entirely. Only a database built purely by running the migrations
    can catch that, which is what `migrated_url` is for.
    """
    engine = create_engine(migrated_url)
    try:
        with engine.connect() as connection:
            codes = set(
                connection.execute(
                    text("SELECT code FROM sales_fee_kind WHERE is_active")
                ).scalars()
            )
    finally:
        engine.dispose()

    assert codes == {
        "commission",
        "processing",
        "listing",
        "shipping_label",
        "promotion",
        "other",
    }


#: A hand-written restatement of a *subset* of the models' CHECK constraints
#: -- not generated from the models, and not a claim of completeness. (The
#: models declare 32 constraints named `ck_*`; this list has 6.) Two things
#: this independence buys, verified directly against this repo's installed
#: Alembic (1.19.1), not assumed:
#:
#: - `compare_metadata` (the diff `test_migrations_match_models` runs) *does*
#:   detect an added or removed named CHECK -- `_compare_check_constraints`
#:   is a registered comparator -- so if this list only restated "does the
#:   model have one", it would add nothing over that existing test.
#: - But `compare_metadata` never compares a CHECK's *expression*:
#:   `DefaultImpl.compare_check_constraint` (`alembic/ddl/impl.py`) returns
#:   `ComparisonResult.Equal()` unconditionally, no dialect overrides it, and
#:   `_ck_constraint_sig._sig` is just `(name,)`. A migration that creates
#:   `ck_listing_lot_quantity_one` with the wrong predicate is invisible to
#:   the drift test. `test_the_migration_carries_every_check_constraint`
#:   below asserts `pg_get_constraintdef` against the exact text expected,
#:   which closes that gap.
#: - And because this list is written independently of both the model and the
#:   migration, deleting a CHECK from *both* leaves `compare_metadata` looking
#:   at two sides that still agree (neither has it) -- silent -- while this
#:   test still fails, because its expectation lives in neither place.
_CHECKS_THE_SELLING_WORK_ADDED: dict[tuple[str, str], str] = {
    ("listing", "ck_listing_item_xor_lot"): (
        "CHECK (((inventory_item_id IS NULL) <> (sales_lot_id IS NULL)))"
    ),
    ("listing", "ck_listing_lot_quantity_one"): (
        "CHECK (((sales_lot_id IS NULL) OR (quantity_available <= 1)))"
    ),
    ("listing", "ck_listing_price_non_negative"): ("CHECK ((price >= (0)::numeric))"),
    ("listing", "ck_listing_quantity_non_negative"): (
        "CHECK ((quantity_available >= 0))"
    ),
    ("sales_order_fee", "ck_sales_order_fee_non_negative"): (
        "CHECK ((amount >= (0)::numeric))"
    ),
    ("sales_order_item_share", "ck_sales_order_item_share_non_negative"): (
        "CHECK ((amount >= (0)::numeric))"
    ),
}

#: `pg_indexes.indexdef` for `uq_sales_lot_item_open`, as PostgreSQL actually
#: stores and renders it. `compare_metadata` has no handling at all for
#: `postgresql_where` (`alembic/ddl/postgresql.py`), and `_ix_constraint_sig`
#: only ever hashes `(is_unique,) + column_names` -- a migration whose partial
#: index predicate drifted from the model (`released_at IS NULL` becoming,
#: say, `released_at IS NOT NULL`) would be invisible to every other test in
#: this file, `create_all`-built `db` included.
_SALES_LOT_ITEM_OPEN_INDEXDEF = (
    "CREATE UNIQUE INDEX uq_sales_lot_item_open ON public.sales_lot_item "
    "USING btree (inventory_item_id) WHERE (released_at IS NULL)"
)


def test_the_migration_carries_every_check_constraint(migrated_url: str) -> None:
    """Each hand-picked CHECK exists in the migration, with the right predicate.

    Presence alone is not enough -- see `_CHECKS_THE_SELLING_WORK_ADDED`'s own
    docstring for why `compare_metadata` cannot be trusted for either the
    expression or the "deleted from both sides" case. While built against the
    same `migrated_url` database, this also asserts `uq_sales_lot_item_open`'s
    partial-index predicate, which no comparator here checks at all.
    """
    engine = create_engine(migrated_url)
    try:
        with engine.connect() as connection:
            found = {
                (table, name): definition
                for table, name, definition in connection.execute(
                    text(
                        "SELECT rel.relname, con.conname, "
                        "pg_get_constraintdef(con.oid) FROM pg_constraint con "
                        "JOIN pg_class rel ON rel.oid = con.conrelid "
                        "WHERE con.contype = 'c'"
                    )
                ).all()
            }
            # One index, looked up by its exact name, so a scalar rather than
            # a mapping: `dict(rows)` cannot be typed (a `Row` is iterable but
            # not declared as a pair, so mypy infers `Never`) and the dict
            # comprehension that can be typed is a ruff `C416`. `None` here
            # means the migration created no such index at all, which the
            # assertion below reports as the drift it is.
            index_def = connection.execute(
                text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE indexname = 'uq_sales_lot_item_open'"
                )
            ).scalar_one_or_none()
    finally:
        engine.dispose()

    missing = sorted(key for key in _CHECKS_THE_SELLING_WORK_ADDED if key not in found)
    assert missing == [], (
        f"constraint(s) {missing} are declared here but no migration creates "
        "them, and compare_metadata cannot see the difference"
    )

    wrong_predicate = {
        key: found[key]
        for key, expected in _CHECKS_THE_SELLING_WORK_ADDED.items()
        if found[key] != expected
    }
    assert wrong_predicate == {}, (
        f"constraint(s) exist with the wrong expression: {wrong_predicate} -- "
        "compare_metadata never compares a CHECK's predicate, only its name"
    )

    assert index_def == _SALES_LOT_ITEM_OPEN_INDEXDEF, (
        "uq_sales_lot_item_open's partial-index predicate has drifted from "
        "what the model declares -- compare_metadata does not check this"
    )


#: The database objects outside `Base.metadata` whose definitions matter:
#: the four views, and `grade_display()`, which three of them call.
_DEFINITIONS = (
    "SELECT 'view ' || c.relname, pg_get_viewdef(c.oid) FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace "
    "WHERE n.nspname = 'public' AND c.relkind = 'v' "
    "UNION ALL "
    "SELECT 'function ' || p.proname, pg_get_functiondef(p.oid) FROM pg_proc p "
    "JOIN pg_namespace n ON n.oid = p.pronamespace "
    "WHERE n.nspname = 'public' AND p.prokind = 'f' "
    "AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.objid = p.oid "
    "AND d.deptype = 'e')"
)


def _definitions(url: str) -> dict[str, str]:
    """Each view's and function's definition, as PostgreSQL renders it."""
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            return {
                str(name): str(definition)
                for name, definition in connection.execute(text(_DEFINITIONS)).all()
            }
    finally:
        engine.dispose()


def _normalised(url: str, found: dict[str, str]) -> dict[str, str]:
    """`found` with each view re-created once and rendered again.

    PostgreSQL stores `x IN ('a', 'b')` on a varchar as
    `(ARRAY['a'::varchar, ...])::text[]`, but the text it prints for that
    reads back as `ARRAY[('a'::varchar)::text, ...]` -- the same predicate,
    rendered differently. The baseline was made from printed text and the
    test database from `views.py`, so both sides pass through one round trip
    here, inside a transaction that is rolled back.
    """
    engine = create_engine(url)
    try:
        with engine.connect() as connection:
            out = {}
            for name, definition in found.items():
                if name.startswith("view "):
                    connection.execute(
                        text(f"CREATE TEMP VIEW _normalised AS {definition}")
                    )
                    definition = connection.execute(
                        text("SELECT pg_get_viewdef('_normalised'::regclass)")
                    ).scalar_one()
                    connection.execute(text("DROP VIEW _normalised"))
                out[name] = definition
            connection.rollback()
            return out
    finally:
        engine.dispose()


def test_the_baseline_views_and_functions_are_the_apps(
    migrated_url: str, engine: Engine
) -> None:
    """The baseline's views and `grade_display()` equal what the app defines.

    The test database is built from `app.models.views` and
    `app.grades.GRADE_DISPLAY_SQL`; the real one from the baseline's frozen
    SQL. `compare_metadata` sees neither views nor functions, so without this
    a view edited in `views.py` with no migration would pass every test and
    never reach the real database.
    """
    # `engine` is the session's test database, built from the app's own
    # definitions by conftest. Both sides are normalized in the migrated
    # database, whose tables are the same.
    built = _normalised(migrated_url, _definitions(migrated_url))
    expected = _normalised(
        migrated_url,
        _definitions(engine.url.render_as_string(hide_password=False)),
    )

    assert {f"view {name}" for name in ALL_VIEWS} <= set(built)
    differing = sorted(
        name
        for name in built.keys() | expected.keys()
        if built.get(name) != expected.get(name)
    )
    assert differing == [], (
        f"the migrations and the app define these differently: {differing}"
    )
