"""The migration that moves each purchase's seller link into the seller table.

Built on a throwaway database taken to the revision before it, given the
links, then upgraded -- the path the live database takes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests.conftest import TEST_URL, drop_database

BACKEND_DIR = Path(__file__).resolve().parents[1]
BEFORE = "b7d2f4a91c36"


def _to(url: str, revision: str) -> None:
    """Migrate the database at `url`, and no other, up to the revision."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    upgrade(config, revision)


@pytest.fixture
def before_url() -> Iterator[str]:
    """A database migrated up to the revision before the seller table."""
    name = f"{TEST_URL.database}_sellers"
    admin = create_engine(
        TEST_URL.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        drop_database(conn, name)
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    url = TEST_URL.set(database=name).render_as_string(hide_password=False)
    try:
        _to(url, BEFORE)
        yield url
    finally:
        with admin.connect() as conn:
            drop_database(conn, name)
        admin.dispose()


def test_each_seller_link_becomes_a_seller(before_url: str) -> None:
    engine = create_engine(before_url)
    with engine.begin() as conn:
        # Decoy vendors first, so no purchase's id coincides with a seller's.
        for name in ("decoy-a", "decoy-b", "decoy-c", "ebay.com"):
            conn.execute(
                text(
                    "insert into vendor (name, created_at, updated_at) "
                    "values (:n, now(), now())"
                ),
                {"n": name},
            )
        ebay = conn.execute(
            text("select id from vendor where name = 'ebay.com'")
        ).scalar()
        links = {
            "P-1": "https://www.ebay.com/usr/deswin3834",
            "P-2": "https://www.ebay.com/usr/deswin3834",
            "P-3": "https://www.ebay.com/usr/coind0g",
            "P-4": "https://www.ebay.com/str/coinshopstore",
            "P-5": None,
        }
        for number, link in links.items():
            conn.execute(
                text(
                    "insert into purchase_order "
                    "(vendor_id, order_number, seller_url, created_at, updated_at) "
                    "values (:v, :n, :u, now(), now())"
                ),
                {"v": ebay, "n": number, "u": link},
            )

    _to(before_url, "head")

    with engine.connect() as conn:
        sellers = conn.execute(
            text("select name, store_url from seller order by name")
        ).all()
        assert [tuple(s) for s in sellers] == [
            ("coind0g", "https://www.ebay.com/usr/coind0g"),
            ("coinshopstore", "https://www.ebay.com/str/coinshopstore"),
            ("deswin3834", "https://www.ebay.com/usr/deswin3834"),
        ]
        rows = (
            conn.execute(
                text(
                    "select po.order_number, s.name from purchase_order po "
                    "left join seller s on s.id = po.seller_id"
                )
            )
            .tuples()
            .all()
        )
        named: dict[str, str | None] = dict(rows)
        assert named == {
            "P-1": "deswin3834",
            "P-2": "deswin3834",
            "P-3": "coind0g",
            "P-4": "coinshopstore",
            "P-5": None,
        }
        columns = {c["name"] for c in inspect(conn).get_columns("purchase_order")}
        assert "seller_url" not in columns
    engine.dispose()
