"""Copying the database into another one.

The database is the system of record, so a backup that looks fine and is not
faithful is worse than none. These check the two things that make it faithful
and the one that makes it portable.
"""

from __future__ import annotations

import pytest
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
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in generated_columns(table)
    }

    assert found == {
        "inventory_item.sales_tax",
        "inventory_item.total_cost",
        "currency_detail.series_designation",
        "grade.grade_rank",
        "listing.is_active",
    }, "a generated column was added or removed; the backup skips whatever it finds"

    # And the derivation is genuinely reading the schema, not returning a
    # constant: a table with no computed column must come back empty.
    assert generated_columns(Base.metadata.tables["vendor"]) == []


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


class _FakeDialect:
    """Just enough dialect for `_resync_sequences` to branch on."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.identifier_preparer = self

    def quote(self, value: str) -> str:
        """Identifier quoting, as the real preparer would do it."""
        return f'"{value}"'


class _FakeBind:
    def __init__(self, dialect_name: str) -> None:
        self.dialect = _FakeDialect(dialect_name)


class _FakeSession:
    """Records what was executed and can be told to fail."""

    def __init__(self, dialect_name: str = "postgresql", fail: bool = False) -> None:
        self._bind = _FakeBind(dialect_name)
        self.fail = fail
        self.executed: list[str] = []
        self.committed = 0
        self.rolled_back = 0

    def get_bind(self) -> _FakeBind:
        """The engine this session is bound to."""
        return self._bind

    def execute(self, statement: object, params: object = None) -> object:
        """Run a statement, failing only on the `setval` if asked to.

        Only on `setval`, deliberately. Failing the first call instead makes
        the sequence *lookup* raise, which propagates whatever the code does
        about setval -- so the test would pass against the swallowing version
        and prove nothing. A mutation run caught exactly that.
        """
        sql = str(statement)
        self.executed.append(sql)
        if self.fail and "setval" in sql:
            raise RuntimeError("permission denied for sequence")
        return _FakeResult()

    def commit(self) -> None:
        """Count a commit."""
        self.committed += 1

    def rollback(self) -> None:
        """Count a rollback."""
        self.rolled_back += 1


class _FakeResult:
    def scalar(self) -> str:
        """The sequence name `pg_get_serial_sequence` would return."""
        return "public.inventory_item_id_seq"


def test_a_sequence_that_cannot_be_reset_is_an_error() -> None:
    """A failed resync must not be swallowed.

    This ran under a bare `except Exception: rollback()`, which produced
    precisely the failure its own docstring warns about -- a backup that
    reports success and collides on the first insert into it -- with nothing
    said. The restore is the moment the collection is recovered from, so a
    silent defect here is the most expensive kind in the codebase.
    """
    from app.backup import _resync_sequences

    session = _FakeSession(fail=True)
    table = Base.metadata.tables["inventory_item"]

    with pytest.raises(RuntimeError, match="permission denied"):
        _resync_sequences(session, table)  # type: ignore[arg-type]


def test_a_dialect_without_sequences_is_skipped_quietly() -> None:
    """The one case that really is not a failure stays quiet.

    Without this the fix above would turn every non-PostgreSQL target into an
    error, which is the overcorrection that makes people restore the bare
    `except`.
    """
    from app.backup import _resync_sequences

    session = _FakeSession(dialect_name="sqlite", fail=True)
    table = Base.metadata.tables["inventory_item"]

    _resync_sequences(session, table)  # type: ignore[arg-type]
    assert session.executed == []
    assert session.committed == 0


def test_a_successful_resync_commits_the_setval() -> None:
    """The happy path, so the two tests above cannot both pass on a no-op."""
    from app.backup import _resync_sequences

    session = _FakeSession()
    _resync_sequences(session, Base.metadata.tables["inventory_item"])  # type: ignore[arg-type]

    assert any("setval" in sql for sql in session.executed)
    assert session.committed == 1
    assert session.rolled_back == 0
