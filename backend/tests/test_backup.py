"""Copying the database into another one.

The database is the system of record, so a backup that looks fine and is not
faithful is worse than none. These check the two things that make it faithful
and the one that makes it portable. The copy itself, `copy_rows`, is run
between two scratch databases in `tests/test_workbook_backup.py`; a whole
copy, `run`, is made here from the test database.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from app import backup
from app.backup import generated_columns, timestamped_name
from app.models import Base
from app.models.views import ALL_VIEWS
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url

from tests.conftest import TEST_URL, drop_database


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

    A generated column the backup tried to insert would be refused by
    PostgreSQL and abort the copy partway, leaving later tables empty while
    `--list` still showed a plausible size.

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
        "friedberg_number.is_star",
        "friedberg_number.is_mule",
    }, "a generated column was added or removed; the backup skips whatever it finds"

    # And the derivation is genuinely reading the schema, not returning a
    # constant: a table with no computed column must come back empty.
    assert generated_columns(Base.metadata.tables["vendor"]) == []


def test_the_backup_url_keeps_its_password() -> None:
    """`str(url)` masks the password as ***, which cannot authenticate.

    The failure is quiet and confusing: a URL that looks correct and is
    refused. conftest.py documents the same trap for the test database.
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


@pytest.mark.parametrize(
    "name",
    ['bak"; DROP DATABASE ccwebdb; --', "bak-1", "", "a" * 64, "bak copy"],
)
def test_a_database_name_that_would_need_quoting_is_refused(
    name: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """`--name` reaches `CREATE DATABASE`, so only a plain identifier passes.

    Refused before any connection is made: the server is never asked.
    """

    def no_server(url: str) -> Engine:
        raise AssertionError("a refused name must not reach the server")

    monkeypatch.setattr(backup, "admin_engine", no_server)

    with pytest.raises(ValueError, match="not a usable database name"):
        backup.create_database("postgresql+psycopg://u:p@localhost/ccwebdb", name)

    with pytest.raises(SystemExit) as exited:
        backup.main(["--name", name])
    assert exited.value.code == 2
    assert "not a usable database name" in capsys.readouterr().err


def test_a_plain_database_name_is_accepted() -> None:
    """The refusal above is not a refusal of everything."""
    assert backup.check_database_name("ccwebdb_bak_20261021_101500") == (
        "ccwebdb_bak_20261021_101500"
    )
    assert backup.check_database_name("a" * 63) == "a" * 63


class _FakeDialect:
    """Just enough dialect for `_resync_sequences` to branch on."""

    def __init__(self, name: str) -> None:
        """A dialect of this name that is its own identifier preparer."""
        self.name = name
        self.identifier_preparer = self

    def quote(self, value: str) -> str:
        """Identifier quoting, as the real preparer would do it."""
        return f'"{value}"'


class _FakeBind:
    def __init__(self, dialect_name: str) -> None:
        """A bind whose dialect has this name."""
        self.dialect = _FakeDialect(dialect_name)


class _FakeSession:
    """Records what was executed and can be told to fail."""

    def __init__(self, dialect_name: str = "postgresql", fail: bool = False) -> None:
        """A session on the named dialect, nothing yet executed or committed."""
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
        about setval -- so the test would pass against a version that
        swallowed the failure, and prove nothing.
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

    Swallowed, it would be a backup that reports success and collides on the
    first insert into it, with nothing said. The restore is the moment the
    collection is recovered from, so a silent defect here is the most
    expensive kind in the codebase.
    """
    from app.backup import _resync_sequences

    session = _FakeSession(fail=True)
    table = Base.metadata.tables["inventory_item"]

    with pytest.raises(RuntimeError, match="permission denied"):
        _resync_sequences(session, table)  # type: ignore[arg-type]


def test_a_dialect_without_sequences_is_skipped_quietly() -> None:
    """The one case that really is not a failure stays quiet.

    Without this every non-PostgreSQL target would be an error, which is the
    overcorrection that invites a bare `except`.
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


@pytest.fixture
def copy_url(engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    """An empty database to copy into, with the test database as the source.

    `app.backup.run` copies whatever `settings.database_url` names; here that
    is the test session's own database, never the application's.
    """
    monkeypatch.setattr(
        backup.settings,
        "database_url",
        engine.url.render_as_string(hide_password=False),
    )
    name = "ccwebdb_test_bak_copy"
    admin = create_engine(
        TEST_URL.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    with admin.connect() as conn:
        drop_database(conn, name)
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield TEST_URL.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            drop_database(conn, name)
        admin.dispose()


def test_a_copy_holds_what_the_application_reads_beside_the_tables(
    copy_url: str,
) -> None:
    """The views, `grade_display()` and `levenshtein()` are not in the models.

    The inventory search composes grades with `grade_display()` and the
    near-duplicate serial check calls `levenshtein()`: a copy without them
    holds every row and still cannot serve the console.
    """
    backup.run(copy_url)

    copy = create_engine(copy_url)
    try:
        with copy.connect() as conn:
            views = set(
                conn.execute(
                    text("SELECT viewname FROM pg_views WHERE schemaname = 'public'")
                ).scalars()
            )
            functions = conn.execute(
                text("SELECT count(*) FROM pg_proc WHERE proname = 'grade_display'")
            ).scalar_one()
            distance = conn.execute(text("SELECT levenshtein('abc', 'abd')")).scalar()
    finally:
        copy.dispose()

    assert views >= set(ALL_VIEWS)
    assert functions == 1
    assert distance == 1
