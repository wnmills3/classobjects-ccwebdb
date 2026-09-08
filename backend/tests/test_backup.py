"""Copying the database into another one.

The database is the system of record, so a backup that looks fine and is not
faithful is worse than none. These check the two things that make it faithful
and the one that makes it portable.
"""

from __future__ import annotations

from app.backup import GENERATED, timestamped_name
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


def test_generated_columns_are_not_copied() -> None:
    """The destination recomputes them from its own expressions.

    Writing them would either be refused, or accepted and then disagree with
    the expression that is supposed to define them.
    """
    assert {"taxes", "total_cost"} == GENERATED
    columns = {c.name for c in Base.metadata.tables["inventory_item"].columns}
    assert columns > GENERATED, "the generated columns must still exist to skip"


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
