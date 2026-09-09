"""Copying the database into another one.

The database is the system of record, so a backup that looks fine and is not
faithful is worse than none. These check the two things that make it faithful
and the one that makes it portable.
"""

from __future__ import annotations

from app.backup import generated_columns, timestamped_name
from app.models import Base
from sqlalchemy.engine import make_url


def test_tables_copy_in_foreign_key_order() -> None:
    """A row must never arrive before the row it references.

    `sorted_tables` is the topological order. Copying alphabetically would
    fail on the first foreign key -- `coin_detail` before `inventory_item`.
    """
    order = [t.name for t in Base.metadata.sorted_tables]
    assert order.index("inventory_item") < order.index("coin_detail")
    assert order.index("inventory_item") < order.index("item_certification")
    assert order.index("purchase_order") < order.index("inventory_item")
    assert order.index("vendor") < order.index("purchase_order")
    assert order.index("series") < order.index("inventory_item")


def test_generated_columns_are_read_from_the_models() -> None:
    """Every column the schema computes, found without anyone listing it.

    The previous version of this test asserted a hardcoded set against a copy
    of itself, so it could not fail. It did not: `series_designation` was added
    as a generated column, the set was not updated, the backup tried to insert
    it, PostgreSQL refused, and the copy aborted partway -- leaving five tables
    empty in every backup taken afterwards, while `--list` still showed a
    plausible 21 MB.

    So this walks the metadata the same way the backup does and asserts the
    answer covers every table, which is what makes it fail when the next
    generated column appears.
    """
    found = {
        f"{table.name}.{name}"
        for table in Base.metadata.sorted_tables
        for name in generated_columns(table)
    }

    assert found == {
        "inventory_item.sales_tax",
        "inventory_item.total_cost",
        "currency_detail.series_designation",
    }, "a generated column was added or removed; the backup skips whatever it finds"

    # And the derivation is genuinely reading the schema, not returning a
    # constant: a table with no computed column must come back empty.
    assert generated_columns(Base.metadata.tables["vendor"]) == set()


def test_the_backup_url_keeps_its_password() -> None:
    """`str(url)` masks the password as ***, which cannot authenticate.

    The failure is quiet and confusing: a URL that looks correct and is
    refused. conftest.py documents the same trap for the test database, and
    this module hit it too.
    """
    url = make_url("postgresql+psycopg://user:secret@localhost:5432/ccwebdb")
    masked = str(url.set(database="ccwebdb_bak_1"))
    kept = url.set(database="ccwebdb_bak_1").render_as_string(hide_password=False)

    assert "***" in masked
    assert "secret" not in masked
    assert "secret" in kept
    assert kept.endswith("/ccwebdb_bak_1")


def test_backup_names_sort_chronologically() -> None:
    """So `--list` shows the newest first without parsing dates."""
    name = timestamped_name()
    assert name.startswith("ccwebdb_bak_")
    stamp = name.removeprefix("ccwebdb_bak_")
    assert len(stamp) == len("20260908_123538")
    assert stamp.replace("_", "").isdigit(), "no day names -- they do not sort"
