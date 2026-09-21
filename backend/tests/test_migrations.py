"""Guard against the models and the Alembic migrations drifting apart."""

from __future__ import annotations

import ast
from collections.abc import Iterator
from decimal import Decimal
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
def migrated_url() -> Iterator[str]:
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
def round_trip_url() -> Iterator[str]:
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
def grade_migration_url() -> Iterator[str]:
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
def sales_venue_migration_url() -> Iterator[str]:
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


@pytest.fixture
def offer_claim_migration_url() -> Iterator[str]:
    """A throwaway database for migrating real listings, not an empty table."""
    url = TEST_URL
    name = f"{url.database}_migrations_claims"
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
def share_migration_url() -> Iterator[str]:
    """A throwaway database for backfilling real order lines, not an empty table."""
    url = TEST_URL
    name = f"{url.database}_migrations_shares"
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
        back: dict[str, str | None] = dict(
            conn.execute(
                text(
                    "SELECT i.grade_raw, g.code FROM inventory_item i "
                    "LEFT JOIN grade g ON g.id = i.grade_id"
                )
            )
            .tuples()
            .all()
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
        back: dict[int, bool] = dict(
            conn.execute(text("SELECT id, is_active FROM listing")).tuples().all()
        )
    engine.dispose()
    assert back == {listing_ids[True]: True, listing_ids[False]: False}


def test_the_offer_claim_migration_claims_existing_listings(
    offer_claim_migration_url: str,
) -> None:
    """Upgrade with listings already in place, then downgrade again.

    An empty database proves only that the DDL runs. Every listing that
    exists before this migration must come out the other side holding a
    claim whose state matches its status -- the one-active-offer invariant
    has to hold from the first moment, not only for rows written afterward.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", offer_claim_migration_url)
    upgrade(config, "d6a1f3b8c402")

    engine = create_engine(offer_claim_migration_url)
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
        store_id = conn.scalar(text("SELECT id FROM sales_venue WHERE code = 'store'"))

        def add_item() -> int:
            return conn.execute(
                text(
                    "INSERT INTO inventory_item (item_kind_id, storage_form_id, "
                    "authenticity_id, status_id, disposition_id, valuation_basis_id, "
                    "item_cost, shipping_cost, tax_rate, tax_includes_shipping, "
                    "source, created_at, updated_at) VALUES (:item_kind_id, "
                    ":storage_form_id, :authenticity_id, :status_id, "
                    ":disposition_id, :valuation_basis_id, 0, 0, 0, false, "
                    "'manual', now(), now()) RETURNING id"
                ),
                required,
            ).scalar_one()

        listing_ids = {}
        for status in ("active", "paused", "ended"):
            item_id = add_item()
            listing_ids[status] = conn.execute(
                text(
                    "INSERT INTO listing (inventory_item_id, price, currency_id, "
                    "sales_venue_id, format, status, listed_at, created_at, "
                    "updated_at) VALUES (:i, 10.00, :c, :s, 'fixed_price', "
                    ":status, now(), now(), now()) RETURNING id"
                ),
                {"i": item_id, "c": currency_id, "s": store_id, "status": status},
            ).scalar_one()

    upgrade(config, "head")
    with engine.connect() as conn:
        state_by_status: dict[str, str] = dict(
            conn.execute(
                text(
                    "SELECT l.status::text, c.state::text FROM offer_claim c "
                    "JOIN listing l ON l.id = c.listing_id"
                )
            )
            .tuples()
            .all()
        )
        assert state_by_status == {
            "active": "active",
            "paused": "paused",
            "ended": "released",
        }
        has_index = conn.scalar(text("SELECT to_regclass('uq_offer_claim_active')"))
        assert has_index is not None

    downgrade(config, "d6a1f3b8c402")
    with engine.connect() as conn:
        statuses: dict[int, str] = dict(
            conn.execute(text("SELECT id, status::text FROM listing")).tuples().all()
        )
        claim_table = conn.scalar(text("SELECT to_regclass('offer_claim')"))
    engine.dispose()
    assert statuses == {
        listing_ids["active"]: "active",
        listing_ids["paused"]: "paused",
        listing_ids["ended"]: "ended",
    }
    assert claim_table is None


def test_the_share_migration_backfills_existing_lines(
    share_migration_url: str,
) -> None:
    """Upgrade with order lines already in place, then check their shares.

    An empty database proves only that the `INSERT ... SELECT` runs. Every
    order line that existed before this migration must come out the other
    side with exactly one share, naming its listing's item and carrying
    that line's own transacted money -- `app.sale_state`'s order half now
    reaches an item only through this table, so a line the backfill
    missed, or gave the wrong item, the wrong amount, the wrong column
    (`listing.id`, two lines below `listing.inventory_item_id` in the SQL
    -- the `FROM` line sits between them, not nothing), or the wrong
    *source* for its money (`listing.price`, the asking price, instead of
    `sales_order_item.unit_price`, what the line actually transacted at)
    would silently lose the for-sale warning for that item on whatever
    database this migration runs against (never this project's own: live
    has zero `sales_order`/`sales_order_item` rows at `e7c3a5b19d84`).

    Four coincidences would each hide a real bug if left in place, so all
    four are broken on purpose. Two lines with different quantities *and*
    different prices, not one, so a transposed column fails at least one
    of them. Each line's `unit_price` set apart from its own listing's
    `price` -- and from the other line's `unit_price` too -- so a backfill
    that read the listing's asking price instead of the line's transacted
    one cannot produce the same number by coincidence; this is the
    divergence the migration's own docstring argues `revise_order` can
    create, and a test that priced every line at its listing's price would
    never exercise it. An unordered decoy listing inserted before the real
    ones, so `listing.id` and `sales_order_item.id` do not start from the
    same number (a join on the wrong column would otherwise match every
    row by coincidence, since both sequences begin at 1 in a fresh
    database). And the listings created in an order that never lets a
    listing's own `id` equal the `inventory_item_id` it points to (an
    item-then-listing lockstep -- insert an item, then its listing,
    repeated -- would give every listing the same id as its own item,
    which would make `l.id` and `l.inventory_item_id` byte-identical in
    every result row and hide the `l.id`-for-`l.inventory_item_id` copy-
    paste bug entirely). Checked per line rather than by count or sum, the
    same reason the sales-venue migration test above checks each listing's
    row rather than an aggregate.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", share_migration_url)
    upgrade(config, "e7c3a5b19d84")

    engine = create_engine(share_migration_url)
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
        store_id = conn.scalar(text("SELECT id FROM sales_venue WHERE code = 'store'"))

        # There is no row shape at this revision the backfill would need to
        # skip: `listing.inventory_item_id` is already `NOT NULL` here, so
        # every line's listing already names an item -- confirmed against
        # the schema itself, not assumed the way the migration's own
        # docstring states it.
        nullable = conn.scalar(
            text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_name = 'listing' AND column_name = 'inventory_item_id'"
            )
        )
        assert nullable == "NO"

        def add_item() -> int:
            return conn.execute(
                text(
                    "INSERT INTO inventory_item (item_kind_id, storage_form_id, "
                    "authenticity_id, status_id, disposition_id, valuation_basis_id, "
                    "item_cost, shipping_cost, tax_rate, tax_includes_shipping, "
                    "source, created_at, updated_at) VALUES (:item_kind_id, "
                    ":storage_form_id, :authenticity_id, :status_id, "
                    ":disposition_id, :valuation_basis_id, 0, 0, 0, false, "
                    "'manual', now(), now()) RETURNING id"
                ),
                required,
            ).scalar_one()

        def add_listing(item_id: int, price: str) -> int:
            return conn.execute(
                text(
                    "INSERT INTO listing (inventory_item_id, price, currency_id, "
                    "sales_venue_id, format, status, listed_at, created_at, "
                    "updated_at) VALUES (:i, :p, :c, :s, 'fixed_price', 'ended', "
                    "now(), now(), now()) RETURNING id"
                ),
                {"i": item_id, "p": price, "c": currency_id, "s": store_id},
            ).scalar_one()

        # Every item is inserted before any listing, and the listings are
        # then created in a shuffled order (second's, then the decoy's,
        # then first's) rather than each item's listing following it
        # immediately. Interleaving item-then-listing-then-item-then-listing
        # would give every listing the same id as its own item (both
        # sequences advancing together, 1/1, 2/2, 3/3), which would make
        # `l.id` and `l.inventory_item_id` equal in every row this test
        # reads and hide the `l.id`-for-`l.inventory_item_id` bug entirely.
        # Derived ids, so a future edit here can be checked against this
        # comment rather than re-derived from scratch:
        #   item:    decoy=1, first=2, second=3
        #   listing: (for second)=1, (decoy's)=2, (for first)=3
        # No listing id equals its own item's id: 1's item is 3, 2's item
        # is 1, 3's item is 2.
        decoy_item_id = add_item()  # item id 1
        item_ids = {"first": add_item(), "second": add_item()}  # item ids 2, 3
        listing_ids = {
            "second": add_listing(item_ids["second"], "45.00"),  # listing id 1
        }
        # An unordered decoy listing, inserted between the two real ones so
        # `listing.id` and `sales_order_item.id` do not start from the same
        # number below either -- both sequences begin at 1 in a fresh
        # database, and a join on the wrong column (`listing.id =
        # sales_order_item.id` instead of `listing.id =
        # sales_order_item.listing_id`) would otherwise match every row by
        # coincidence. Never ordered, so the backfill must not give it a
        # share either.
        add_listing(decoy_item_id, "1.00")  # listing id 2
        listing_ids["first"] = add_listing(item_ids["first"], "100.00")  # listing id 3
        customer_id = conn.execute(
            text(
                "INSERT INTO customer (display_name, created_at, updated_at) "
                "VALUES ('A buyer', now(), now()) RETURNING id"
            )
        ).scalar_one()
        order_id = conn.execute(
            text(
                "INSERT INTO sales_order (customer_id, sales_venue_id, "
                "sales_order_status_id, placed_at, created_at, updated_at) "
                "VALUES (:c, :v, :s, now(), now(), now()) RETURNING id"
            ),
            {
                "c": customer_id,
                "v": store_id,
                "s": add("sales_order_status", "placed"),
            },
        ).scalar_one()
        # Each line's unit_price differs from its own listing's asking
        # price (100.00, 45.00, set on the listings above) and from the
        # other line's unit_price, not just from its own quantity's
        # partner -- so a backfill that priced a share from `listing.price`
        # instead of `sales_order_item.unit_price` cannot land on the same
        # number as the correct one, or on the same wrong number a missing
        # `* quantity` would produce, by coincidence:
        #   correct:        120.00 * 2 = 240.00,  30.00 * 3 =  90.00
        #   wrong source:   100.00 * 2 = 200.00,  45.00 * 3 = 135.00
        #   missing * qty:  120.00      = 120.00, 30.00      =  30.00
        # All six numbers are distinct.
        lines = {
            "first": (listing_ids["first"], 2, "120.00"),  # 240.00
            "second": (listing_ids["second"], 3, "30.00"),  # 90.00
        }
        line_ids = {
            name: conn.execute(
                text(
                    "INSERT INTO sales_order_item (sales_order_id, listing_id, "
                    "quantity, unit_price) VALUES (:o, :l, :q, :p) RETURNING id"
                ),
                {"o": order_id, "l": listing_id, "q": qty, "p": price},
            ).scalar_one()
            for name, (listing_id, qty, price) in lines.items()
        }

    upgrade(config, "head")
    with engine.connect() as conn:
        shares = {
            row.sales_order_item_id: (
                row.inventory_item_id,
                row.amount,
                row.fee_amount,
            )
            for row in conn.execute(
                text(
                    "SELECT sales_order_item_id, inventory_item_id, amount, "
                    "fee_amount FROM sales_order_item_share"
                )
            )
        }
    engine.dispose()
    assert shares == {
        line_ids["first"]: (item_ids["first"], Decimal("240.00"), Decimal("0.00")),
        line_ids["second"]: (item_ids["second"], Decimal("90.00"), Decimal("0.00")),
    }


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


def test_the_fee_kind_migration_seeds_the_vocabulary(migrated_url: str) -> None:
    """`c6908f789bf8`'s INSERT actually runs, on a database built only by it.

    `test_fee_kinds_are_seeded` (test_sales_fees_schema.py) runs against the
    `create_all`-built test database, so it passes because of conftest's own
    `_FEE_KINDS` seeding -- which mirrors `ensure_store_venue` for the same
    reason -- and would stay green even if this migration's INSERT were
    deleted entirely. Only a database built purely by running the migrations
    can catch that, which is what `migrated_url` is for.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", migrated_url)
    upgrade(config, "head")

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


#: Every CHECK constraint the models declare on a table the migrations build,
#: with the table it belongs to. `compare_metadata` (the diff
#: `test_migrations_match_models` runs) does not compare CHECK constraints at
#: all, so without this list a migration could omit one and both of this
#: file's other tests would stay green.
_EXPECTED_CHECKS = {
    ("listing", "ck_listing_item_xor_lot"),
    ("listing", "ck_listing_lot_quantity_one"),
    ("listing", "ck_listing_price_non_negative"),
    ("listing", "ck_listing_quantity_non_negative"),
    ("sales_order_fee", "ck_sales_order_fee_non_negative"),
    ("sales_order_item_share", "ck_sales_order_item_share_non_negative"),
}


def test_the_migration_carries_every_check_constraint(migrated_url: str) -> None:
    """A CHECK in the models but not in a migration is invisible to the diff.

    `compare_metadata` does not compare CHECK constraints, and the tests that
    do query them run against the `create_all`-built `db` fixture -- which is
    built from the models, so it would find a model's constraint whether the
    migration wrote one or not. Only a purely migrated database can tell.
    """
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", migrated_url)
    upgrade(config, "head")

    engine = create_engine(migrated_url)
    try:
        with engine.connect() as connection:
            found = set(
                connection.execute(
                    text(
                        "SELECT rel.relname, con.conname FROM pg_constraint con "
                        "JOIN pg_class rel ON rel.oid = con.conrelid "
                        "WHERE con.contype = 'c'"
                    )
                ).all()
            )
    finally:
        engine.dispose()

    missing = sorted(_EXPECTED_CHECKS - found)
    assert missing == [], (
        f"constraint(s) {missing} are declared on the models but no migration "
        "creates them, and compare_metadata cannot see the difference"
    )


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
