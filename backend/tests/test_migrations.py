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


@pytest.fixture
def grade_migration_url() -> str:
    """A throwaway database for migrating real grades, not an empty table."""
    url = TEST_URL
    name = f"{url.database}_migrations_grades"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
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


@pytest.fixture
def sales_venue_migration_url() -> str:
    """A throwaway database for migrating real listings, not an empty table."""
    url = TEST_URL
    name = f"{url.database}_migrations_venues"
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
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


#: Old grade code -> (grade code, strike type) after the split.
GRADES_BEFORE_AND_AFTER = {
    "MS65": ("65", "business"),
    "PR69+": ("69+", "proof"),
    "BU": ("60", "business"),
    "BU+": ("63", "business"),
    "GEM_BU": ("65", "business"),
    "PROOF": ("63", "proof"),
    "AU": ("55", "business"),
    "AU+": ("55+", "business"),
    "N_UNC": ("N60", None),
    "N64": ("N64", None),
    "CIRC": ("CIRC", None),
    # Not a grade the split knows: left where it is.
    "MS64PL": ("MS64PL", None),
}


def test_the_grade_migration_moves_real_items(grade_migration_url: str) -> None:
    """Upgrade with grades and items in place, then downgrade again.

    An empty database proves only that the DDL runs. The first attempt on a
    copy of the live collection failed where this one would have: the old
    `grade.is_proof` is NOT NULL with no default, and the migration inserted
    number grades before dropping it.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", grade_migration_url)
    upgrade(config, "d9a2e47b1c05")

    engine = create_engine(grade_migration_url)
    reference = (
        "INSERT INTO {table} (code, label, sort_order, is_active, source) "
        "VALUES (:code, :code, 0, true, 'seeded') RETURNING id"
    )
    scales = {"MS65": "sheldon", "PR69+": "sheldon", "N_UNC": "note"}
    scales |= {"N64": "note", "N60": "note", "MS64PL": "sheldon"}
    numbers = {"MS65": 65, "PR69+": 69, "N64": 64, "N60": 60}
    with engine.begin() as conn:

        def add(table: str, code: str) -> int:
            return conn.execute(
                text(reference.format(table=table)), {"code": code}
            ).scalar_one()

        required = {
            column: add(table, "x")
            for column, table in {
                "item_kind_id": "item_kind",
                "storage_form_id": "storage_form",
                "authenticity_id": "authenticity",
                "status_id": "item_status",
                "disposition_id": "disposition",
                "valuation_basis_id": "valuation_basis",
            }.items()
        }
        scale_ids = {code: add("grade_scale", code) for code in ("sheldon", "note")}
        for code in [*GRADES_BEFORE_AND_AFTER, "N60"]:
            conn.execute(
                text(
                    "INSERT INTO grade (code, label, grade_scale_id, numeric_value, "
                    "is_proof, sort_order, is_active, source) VALUES (:c, :c, :s, "
                    ":n, :p, 0, true, 'seeded')"
                ),
                {
                    "c": code,
                    "s": scale_ids.get(scales.get(code, "")),
                    "n": numbers.get(code),
                    "p": code.startswith("PR"),
                },
            )
        for code in GRADES_BEFORE_AND_AFTER:
            conn.execute(
                text(
                    "INSERT INTO inventory_item (item_kind_id, storage_form_id, "
                    "authenticity_id, status_id, disposition_id, valuation_basis_id, "
                    "item_cost, shipping_cost, tax_rate, tax_includes_shipping, "
                    "source, created_at, updated_at, grade_raw, grade_id) VALUES "
                    "(:item_kind_id, :storage_form_id, :authenticity_id, :status_id, "
                    ":disposition_id, :valuation_basis_id, 0, 0, 0, false, 'manual', "
                    "now(), now(), :raw, (SELECT id FROM grade WHERE code = :raw))"
                ),
                {**required, "raw": code},
            )

    upgrade(config, "e4b8c1d27f63")
    moved_sql = (
        "SELECT i.grade_raw, g.code, stk.code, "
        "grade_display(stk.prefix, stk.suffix, g.numeric_value, g.is_plus, "
        "g.label, gs.code = 'sheldon') FROM inventory_item i "
        "LEFT JOIN grade g ON g.id = i.grade_id "
        "LEFT JOIN grade_scale gs ON gs.id = g.grade_scale_id "
        "LEFT JOIN strike_type stk ON stk.id = i.strike_type_id"
    )
    with engine.connect() as conn:
        moved = {
            raw: (grade, strike)
            for raw, grade, strike, _ in conn.execute(text(moved_sql))
        }
        shown = {raw: display for raw, _, _, display in conn.execute(text(moved_sql))}
        left = set(conn.scalars(text("SELECT code FROM grade")))
    assert moved == GRADES_BEFORE_AND_AFTER
    assert shown["PR69+"] == "PR69+"
    assert shown["BU+"] == "MS63"
    # The adjectival rows went with their items; the unknown one stayed.
    assert not left & {"MS65", "BU", "GEM_BU", "PROOF", "N_UNC"}
    assert "MS64PL" in left

    downgrade(config, "d9a2e47b1c05")
    with engine.connect() as conn:
        back = dict(
            conn.execute(
                text(
                    "SELECT i.grade_raw, g.code FROM inventory_item i "
                    "LEFT JOIN grade g ON g.id = i.grade_id"
                )
            ).all()
        )
    engine.dispose()
    assert back["MS65"] == "MS65"
    assert back["PR69+"] == "PR69+"
    # An adjectival grade comes back as the number it became.
    assert back["BU"] == "MS60"
    assert back["N64"] == "N64"


def test_the_sales_venue_migration_moves_real_rows(
    sales_venue_migration_url: str,
) -> None:
    """Upgrade and downgrade with listings and an order in place.

    `test_migrations_round_trip` runs on an empty database, so it proves only
    that the DDL executes: `UPDATE listing SET sales_venue_id`, `UPDATE listing
    SET status = 'ended' WHERE NOT is_active` and the downgrade's
    `is_active_plain` copy never touch a row there. Nor does the behavioural
    generated-column test (`test_sales_venues.test_is_active_follows_status`)
    reach this migration: that runs against the `create_all` database, so it
    checks the *model's* Computed expression. A typo in the migration's own
    expression would pass every other gate and appear first on live data.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", sales_venue_migration_url)
    upgrade(config, "c2d7a9e5f614")

    engine = create_engine(sales_venue_migration_url)
    reference = (
        "INSERT INTO {table} (code, label, sort_order, is_active, source) "
        "VALUES (:code, :code, 0, true, 'seeded') RETURNING id"
    )
    with engine.begin() as conn:

        def add(table: str, code: str) -> int:
            return conn.execute(
                text(reference.format(table=table)), {"code": code}
            ).scalar_one()

        item_columns = {
            "item_kind_id": "item_kind",
            "storage_form_id": "storage_form",
            "authenticity_id": "authenticity",
            "status_id": "item_status",
            "disposition_id": "disposition",
            "valuation_basis_id": "valuation_basis",
        }
        required = {column: add(table, "x") for column, table in item_columns.items()}
        currency_id = conn.execute(
            text(
                "INSERT INTO currency (code, label, sort_order, is_active, source, "
                "symbol, minor_units) VALUES ('USD', 'US dollar', 0, true, 'seeded', "
                "'$', 2) RETURNING id"
            )
        ).scalar_one()
        item_id = conn.execute(
            text(
                "INSERT INTO inventory_item (item_kind_id, storage_form_id, "
                "authenticity_id, status_id, disposition_id, valuation_basis_id, "
                "item_cost, shipping_cost, tax_rate, tax_includes_shipping, "
                "source, created_at, updated_at) VALUES (:item_kind_id, "
                ":storage_form_id, :authenticity_id, :status_id, :disposition_id, "
                ":valuation_basis_id, 0, 0, 0, false, 'manual', now(), now()) "
                "RETURNING id"
            ),
            required,
        ).scalar_one()
        listing_ids = {
            active: conn.execute(
                text(
                    "INSERT INTO listing (inventory_item_id, price, currency_id, "
                    "is_active, listed_at, created_at, updated_at) VALUES (:i, 10.00, "
                    ":c, :a, now(), now(), now()) RETURNING id"
                ),
                {"i": item_id, "c": currency_id, "a": active},
            ).scalar_one()
            for active in (True, False)
        }
        customer_id = conn.execute(
            text(
                "INSERT INTO customer (display_name, created_at, updated_at) "
                "VALUES ('A buyer', now(), now()) RETURNING id"
            )
        ).scalar_one()
        order_id = conn.execute(
            text(
                "INSERT INTO sales_order (customer_id, sales_order_status_id, "
                "placed_at, created_at, updated_at) VALUES (:c, :s, now(), now(), "
                "now()) RETURNING id"
            ),
            {"c": customer_id, "s": add("sales_order_status", "placed")},
        ).scalar_one()

    upgrade(config, "head")
    with engine.connect() as conn:
        store_id = conn.scalar(text("SELECT id FROM sales_venue WHERE code = 'store'"))
        moved = {
            row.id: (row.status, row.is_active, row.sales_venue_id)
            for row in conn.execute(
                text("SELECT id, status, is_active, sales_venue_id FROM listing")
            )
        }
        order_venue = conn.scalar(
            text("SELECT sales_venue_id FROM sales_order WHERE id = :o"),
            {"o": order_id},
        )
    # Per row, not in aggregate: a backfill that set every listing to the same
    # status would satisfy a count and fail here.
    assert moved[listing_ids[True]] == ("active", True, store_id)
    assert moved[listing_ids[False]] == ("ended", False, store_id)
    assert order_venue == store_id

    # The migration can only ever produce 'active' and 'ended', and several
    # wrong expressions ("status <> 'ended'") agree with the right one on just
    # those two. `paused` is the third status and the one that tells them
    # apart, so the generated column is asked about it here -- against the
    # migration's own DDL, which is the only place this can be checked.
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE listing SET status = 'paused' WHERE id = :i"),
            {"i": listing_ids[True]},
        )
        paused = conn.scalar(
            text("SELECT is_active FROM listing WHERE id = :i"),
            {"i": listing_ids[True]},
        )
        conn.execute(
            text("UPDATE listing SET status = 'active' WHERE id = :i"),
            {"i": listing_ids[True]},
        )
    assert paused is False

    downgrade(config, "c2d7a9e5f614")
    with engine.connect() as conn:
        back = dict(conn.execute(text("SELECT id, is_active FROM listing")).all())
    engine.dispose()
    assert back == {listing_ids[True]: True, listing_ids[False]: False}


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
        ("deleted_at", "strike_type", "grade_display", "sales_venue"),
    ),
    (
        "c847d0c63f84_replace_inventory_item_error_columns_",
        "upgrade",
        ("strike_type", "grade_display", "sales_venue"),
    ),
    (
        "ffe36996607c_add_soft_delete",
        "upgrade",
        ("strike_type", "grade_display", "sales_venue"),
    ),
    (
        "e4b8c1d27f63_strike_type_and_number_grades",
        "downgrade",
        ("strike_type", "grade_display", "grade_rank", "sales_venue"),
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
            "strike_type",
            "grade_display",
            "sales_venue",
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
            "strike_type",
            "grade_display",
            "sales_venue",
        ),
    ),
    (
        "ffe36996607c_add_soft_delete",
        "downgrade",
        ("deleted_at", "strike_type", "grade_display", "sales_venue"),
    ),
    (
        "e4b8c1d27f63_strike_type_and_number_grades",
        "upgrade",
        ("sales_venue",),
    ),
    (
        "d6a1f3b8c402_sales_venues",
        "downgrade",
        ("sales_venue", "l.format"),
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
