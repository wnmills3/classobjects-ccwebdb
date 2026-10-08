"""The database as a workbook, and back (`app.workbook_backup`).

A backup the owner can open and correct, then rebuild a database from. The
round trip runs on two scratch databases of its own, with a schema made to
hold every hard case at once: a link to its own table pointing at a row
loaded after it, a link to another table, a computed column, an enum,
JSON with both SQL NULL and JSON's null, a decimal, a zone-aware
timestamp, a date, an empty string beside a NULL, and a value that looks
like a formula. Two vocabularies test `--unknown-for-missing`: `finish`
needs nothing but a code and a label, `coinage` a face value too.

The same two databases serve `app.backup.copy_rows` and the proof a backup
run makes (`app.backup_run.prove_workbook`), which need a source to copy and
a second database to restore into.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from app import backup, backup_run
from app import workbook_backup as wb
from openpyxl import Workbook, load_workbook
from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    Integer,
    Numeric,
    String,
    Table,
    create_engine,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.types import Enum, TypeEngine

from tests.conftest import TEST_URL, drop_database

SCHEMA = """
CREATE TYPE shade AS ENUM ('red', 'blue');
CREATE TABLE alembic_version (version_num varchar(32) PRIMARY KEY);
CREATE TABLE parent (
    id serial PRIMARY KEY,
    name text NOT NULL,
    parent_id integer REFERENCES parent(id),
    amount numeric(12, 2),
    doubled numeric(12, 2) GENERATED ALWAYS AS (amount * 2) STORED,
    at timestamptz,
    day date,
    flag boolean,
    data jsonb,
    color shade
);
CREATE TYPE provenance_source AS ENUM ('seeded', 'derived', 'manual');
CREATE TABLE finish (
    id serial PRIMARY KEY,
    code varchar(32) NOT NULL UNIQUE,
    label varchar(64) NOT NULL,
    sort_order integer NOT NULL,
    is_active boolean NOT NULL,
    source provenance_source NOT NULL,
    -- As grade.is_plus: an added Unknown row must get the default.
    featured boolean NOT NULL DEFAULT false
);
CREATE TABLE coinage (
    id serial PRIMARY KEY,
    code varchar(32) NOT NULL UNIQUE,
    label varchar(64) NOT NULL,
    sort_order integer NOT NULL,
    is_active boolean NOT NULL,
    source provenance_source NOT NULL,
    face numeric(8, 2) NOT NULL
);
CREATE TABLE child (
    id serial PRIMARY KEY,
    parent_id integer NOT NULL REFERENCES parent(id),
    note text,
    finish_id integer REFERENCES finish(id),
    coinage_id integer REFERENCES coinage(id)
);
"""

ROWS = """
INSERT INTO alembic_version VALUES ('rev_1');
INSERT INTO parent (id, name, parent_id, amount, at, day, flag, data, color) VALUES
  (1, 'root', NULL, 12.50, '2026-09-24 10:00:00+02', '2026-09-24', true,
   '{"a": [1, 2]}', 'red'),
  -- A child row pointing at a parent with a HIGHER id: loaded in one pass,
  -- its link would reference a row not yet there.
  (2, '', 3, NULL, NULL, NULL, false, NULL, NULL),
  (3, '=SUM(A1:A2)', 1, 0.10, '2026-01-01 00:00:00+00', NULL, NULL, '[]', 'blue'),
  -- JSON's own null, distinct from row 2's SQL NULL.
  (4, 'json null', 1, NULL, NULL, NULL, NULL, 'null', NULL);
SELECT setval('parent_id_seq', 4);
INSERT INTO finish (code, label, sort_order, is_active, source) VALUES
  ('matte', 'Matte', 10, true, 'seeded'), ('gloss', 'Gloss', 20, true, 'seeded');
INSERT INTO coinage (code, label, sort_order, is_active, source, face) VALUES
  ('cent', 'Cent', 10, true, 'seeded', 0.01);
INSERT INTO child (parent_id, note, finish_id, coinage_id) VALUES
  (1, NULL, 1, 1), (3, '', 2, NULL), (2, 'x', NULL, NULL);
"""


def _url(name: str) -> str:
    """The test server's address for the database of this name, password included."""
    return TEST_URL.set(database=name).render_as_string(hide_password=False)


def _admin() -> Engine:
    """A connection to the server's maintenance database, for CREATE/DROP."""
    return create_engine(
        TEST_URL.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )


@pytest.fixture(scope="module")
def source() -> Iterator[Engine]:
    """The filled source database, built once for the module.

    Every test only reads it -- exports it, compares against it, lists its
    tables -- so one copy serves them all.
    """
    admin = _admin()
    name = "ccwebdb_test_wb_source"
    with admin.connect() as conn:
        drop_database(conn, name)
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    engine = create_engine(_url(name))
    with engine.begin() as conn:
        conn.execute(text(SCHEMA))
        conn.execute(text(ROWS))
    try:
        yield engine
    finally:
        engine.dispose()
        with admin.connect() as conn:
            drop_database(conn, name)
        admin.dispose()


@pytest.fixture
def pair(
    source: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[Engine, str]]:
    """The filled source database and a fresh, empty target with its schema."""
    admin = _admin()
    name = "ccwebdb_test_wb_target"
    with admin.connect() as conn:
        drop_database(conn, name)
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    target = create_engine(_url(name))
    with target.begin() as conn:
        conn.execute(text(SCHEMA))
        conn.execute(text("INSERT INTO alembic_version VALUES ('rev_1')"))
        # As a migration might: a row the import must clear first.
        conn.execute(text("INSERT INTO parent (name) VALUES ('seeded by migration')"))
    # The live database is not the target: the source stands in for it.
    monkeypatch.setattr(wb.settings, "database_url", _url("ccwebdb_test_wb_source"))
    try:
        yield source, _url(name)
    finally:
        target.dispose()
        with admin.connect() as conn:
            drop_database(conn, name)
        admin.dispose()


def test_a_round_trip_rebuilds_the_database_exactly(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    counts = wb.export_workbook(source, path)
    assert counts == {"parent": 4, "finish": 2, "coinage": 1, "child": 3}

    loaded = wb.import_workbook(path, target_url)
    assert loaded.counts == counts
    assert loaded.substituted == []
    target = create_engine(target_url)
    try:
        assert wb.compare(source, target) == []
        with target.connect() as conn:
            # The empty string stayed empty, not NULL; the formula stayed text.
            assert (
                conn.execute(text("SELECT name FROM parent WHERE id = 2")).scalar()
                == ""
            )
            assert (
                conn.execute(text("SELECT note FROM child WHERE id = 2")).scalar() == ""
            )
            assert (
                conn.execute(text("SELECT note FROM child WHERE id = 1")).scalar()
                is None
            )
            # The migration's own row was cleared, and the computed column recomputed.
            assert conn.execute(text("SELECT count(*) FROM parent")).scalar() == 4
            # SQL NULL and JSON's null both survived as what they were.
            nulls = conn.execute(
                text("SELECT data IS NULL FROM parent WHERE id IN (2, 4) ORDER BY id")
            ).scalars()
            assert list(nulls) == [True, False]
            assert conn.execute(
                text("SELECT doubled FROM parent WHERE id = 1")
            ).scalar() == Decimal("25.00")
            # The sequence is past the loaded ids: a new row does not collide.
            new_id = conn.execute(
                text("INSERT INTO parent (name) VALUES ('new') RETURNING id")
            ).scalar()
            assert new_id == 5
    finally:
        target.dispose()


def test_the_workbook_is_readable_and_marks_what_it_cannot_hold(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    source, _ = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    book = load_workbook(path)
    assert book.sheetnames[:2] == [wb.ABOUT, wb.COLUMNS]
    # Foreign-key order: the referenced table's sheet comes first.
    assert book.sheetnames.index("parent") < book.sheetnames.index("child")
    sheet = book["parent"]
    header = [c.value for c in sheet[1]]
    assert "doubled (computed)" in header
    row = dict(zip(header, sheet[3], strict=True))  # id 2
    assert row["name"].value == wb.EMPTY_STRING
    formula = dict(zip(header, sheet[4], strict=True))["name"]
    assert formula.value == "=SUM(A1:A2)" and formula.data_type == "s"
    about = {
        r[0]: r[1] for r in book[wb.ABOUT].iter_rows(values_only=True) if r and r[0]
    }
    assert about["migration revision"] == "rev_1"
    assert about["format"] == wb.FORMAT


def test_an_edited_cell_arrives_and_nothing_else_moves(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    book = load_workbook(path)
    book["child"]["C2"] = "edited"  # child id 1's note
    book.save(path)
    wb.import_workbook(path, target_url)
    target = create_engine(target_url)
    try:
        assert [d.table for d in wb.compare(source, target)] == ["child"]
        with target.connect() as conn:
            assert (
                conn.execute(text("SELECT note FROM child WHERE id = 1")).scalar()
                == "edited"
            )
    finally:
        target.dispose()


def test_a_broken_link_is_refused_and_nothing_is_loaded(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    book = load_workbook(path)
    book["child"]["B2"] = 999  # no parent 999
    book.save(path)
    with pytest.raises(
        wb.WorkbookError,
        match=r"1 link\(s\) point at nothing: child id 1: parent_id 999 is no "
        r"parent\.id",
    ):
        wb.import_workbook(path, target_url)
    # An item-like table has no Unknown row: the flag changes nothing.
    with pytest.raises(wb.WorkbookError, match="point at nothing"):
        wb.import_workbook(path, target_url, unknown_for_missing=True)
    target = create_engine(target_url)
    try:
        with target.connect() as conn:
            # One transaction: the migration's row is still there, untouched.
            assert conn.execute(text("SELECT count(*) FROM parent")).scalar() == 1
    finally:
        target.dispose()


def test_a_mismatched_revision_or_a_missing_column_is_refused(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)

    target = create_engine(target_url)
    with target.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'rev_2'"))
    target.dispose()
    with pytest.raises(wb.WorkbookError, match="revision rev_1, the database at rev_2"):
        wb.import_workbook(path, target_url)

    target = create_engine(target_url)
    with target.begin() as conn:
        conn.execute(text("UPDATE alembic_version SET version_num = 'rev_1'"))
    target.dispose()
    book = load_workbook(path)
    book["child"].delete_cols(3)  # the note column
    book.save(path)
    with pytest.raises(wb.WorkbookError, match="child: missing column note"):
        wb.import_workbook(path, target_url)


def test_the_live_database_is_refused(
    pair: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    monkeypatch.setattr(wb.settings, "database_url", target_url)
    with pytest.raises(wb.WorkbookError, match="is the live database"):
        wb.import_workbook(path, target_url)


def test_the_live_database_is_refused_however_its_url_is_spelled(
    pair: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same server and database under another host spelling and no port.

    `127.0.0.1` with the default port left out names the server the live URL
    names as `localhost:5432`; comparing the URL strings would let it through.
    """
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    monkeypatch.setattr(wb.settings, "database_url", target_url)
    respelled = (
        make_url(target_url)
        .set(host="127.0.0.1", port=None)
        .render_as_string(hide_password=False)
    )
    assert respelled != target_url
    with pytest.raises(wb.WorkbookError, match="is the live database"):
        wb.import_workbook(path, respelled)
    target = create_engine(target_url)
    try:
        with target.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM parent")).scalar() == 1
    finally:
        target.dispose()


def _unreachable_live(target_url: str, *, database: str | None = None) -> str:
    """The target's server under `localhost`, as a role that does not exist.

    Connecting fails -- the role is refused before any database is opened --
    so the live database cannot be asked who it is, while the URL still names
    the target's host, port and (unless `database` says otherwise) database.
    """
    url = make_url(target_url)
    return url.set(
        host="localhost",
        port=url.port or 5432,
        username="ccwebdb_no_such_role",
        database=database or url.database,
    ).render_as_string(hide_password=False)


def test_an_unreachable_live_database_is_refused_by_its_url(
    pair: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The live server cannot answer, so its URL, normalised, is compared.

    The target is spelled `127.0.0.1` with the default port left out; the live
    URL says `localhost` and the port. Both name one host, port and database.
    """
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    live = _unreachable_live(target_url)
    monkeypatch.setattr(wb.settings, "database_url", live)
    port = make_url(live).port
    respelled = (
        make_url(target_url)
        .set(host="127.0.0.1", port=None if port == 5432 else port)
        .render_as_string(hide_password=False)
    )
    with pytest.raises(wb.WorkbookError, match="is the live database"):
        wb.import_workbook(path, respelled)
    target = create_engine(target_url)
    try:
        with target.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM parent")).scalar() == 1
    finally:
        target.dispose()


def test_an_unreachable_live_database_does_not_block_another(
    pair: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A restore still runs while the live database is down, into another one."""
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    live = _unreachable_live(target_url, database="ccwebdb_test_wb_elsewhere")
    monkeypatch.setattr(wb.settings, "database_url", live)
    wb.import_workbook(path, target_url)


def test_a_database_that_already_holds_items_is_refused(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    """A target with inventory rows is somebody's collection, not a new database."""
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    target = create_engine(target_url)
    try:
        with target.begin() as conn:
            conn.execute(text("CREATE TABLE inventory_item (id serial PRIMARY KEY)"))
            conn.execute(text("INSERT INTO inventory_item DEFAULT VALUES"))
        with pytest.raises(wb.WorkbookError, match="already holds 1 inventory item"):
            wb.import_workbook(path, target_url)
        with target.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM parent")).scalar() == 1
    finally:
        target.dispose()


def test_app_backup_finds_the_tables_no_model_describes(
    pair: tuple[Engine, str],
) -> None:
    """`app.backup` finds every table with no model, the migration table too.

    A copy of the models' tables alone would have no migration revision. The
    scratch database's tables have no models at all, so every one of them must be found.
    """
    source, _ = pair
    found = [table.name for table in backup.unmodelled_tables(source)]
    assert set(found) == {"alembic_version", "parent", "finish", "coinage", "child"}
    assert found.index("parent") < found.index("child")
    # And the copy's table list is the models' followed by these.
    names = [table.name for table in backup.all_tables(source)]
    assert names[-len(found) :] == found


def test_app_backup_copies_a_row_that_points_at_a_later_row_of_its_table(
    pair: tuple[Engine, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """`app.backup.copy_rows` run for real, a row at a time.

    Parent 2 points at parent 3. One row per round trip puts row 2 in the
    database before row 3 exists, as a table larger than one chunk does to
    any row that points forward.
    """
    source, target_url = pair
    monkeypatch.setattr(backup, "CHUNK", 1)
    target = create_engine(target_url)
    try:
        with target.begin() as conn:
            conn.execute(
                text("TRUNCATE parent, child, finish, coinage RESTART IDENTITY CASCADE")
            )
        tables = [
            table
            for table in backup.unmodelled_tables(source)
            if table.name != wb.VERSION_TABLE
        ]

        copied = dict(backup.copy_rows(source, target, tables))

        assert copied == {"parent": 4, "finish": 2, "coinage": 1, "child": 3}
        with source.connect() as a, target.connect() as b:
            for query in (
                "SELECT id, name, parent_id, amount, doubled, at, day, flag, "
                "color::text FROM parent ORDER BY id",
                "SELECT id, parent_id, note, finish_id, coinage_id "
                "FROM child ORDER BY id",
                "SELECT id, code, label, featured FROM finish ORDER BY id",
            ):
                assert b.execute(text(query)).all() == a.execute(text(query)).all()
            # SQL NULL (parent 2) and JSON's null (parent 4) each stayed what
            # it was: read back, both are Python's None.
            nulls = b.execute(
                text("SELECT data IS NULL FROM parent WHERE id IN (2, 4) ORDER BY id")
            ).scalars()
            assert list(nulls) == [True, False]
            # The sequence is past the copied ids: a new row does not collide.
            new_id = b.execute(
                text("INSERT INTO parent (name) VALUES ('new') RETURNING id")
            ).scalar()
            assert new_id == 5
    finally:
        target.dispose()


# -- what an export refuses, and what it reads ----------------------------------


@pytest.mark.parametrize(
    "value",
    [
        " ",
        "\u00a0",
        "first line\r\nsecond line",
        "one\rtwo",
        '""',
        "x" * 32768,
        "a\x0bb",
    ],
    ids=[
        "only a space",
        "only a no-break space",
        "a carriage return and line feed",
        "a bare carriage return",
        "two quotation marks",
        "longer than a cell holds",
        "a control character",
    ],
)
def test_a_text_a_cell_would_not_give_back_is_refused_and_named(
    pair: tuple[Engine, str], tmp_path: Path, value: str
) -> None:
    """An export that would read back as something else is no backup.

    A cell holds at most 32,767 characters and no carriage return; blank
    text reads back as NULL and `""` as the empty string. Each is refused
    with its table, row and column, and no workbook is left behind.
    """
    _, target_url = pair
    target = create_engine(target_url)
    path = tmp_path / "backup.xlsx"
    try:
        with target.begin() as conn:
            conn.execute(
                text("INSERT INTO parent (id, name) VALUES (50, :value)"),
                {"value": value},
            )
        with pytest.raises(wb.WorkbookError, match=r"parent id 50, name"):
            wb.export_workbook(target, path)
        assert not path.exists()
    finally:
        target.dispose()


def test_json_longer_than_a_cell_holds_is_refused_and_named(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    """JSON is a cell of text too; cut short it is not JSON at all."""
    _, target_url = pair
    target = create_engine(target_url)
    path = tmp_path / "backup.xlsx"
    try:
        with target.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO parent (id, name, data) VALUES "
                    "(51, 'long', jsonb_build_object('k', repeat('x', 40000)))"
                )
            )
        with pytest.raises(wb.WorkbookError, match=r"parent id 51, data"):
            wb.export_workbook(target, path)
        assert not path.exists()
    finally:
        target.dispose()


def test_an_empty_string_inside_json_is_not_mistaken_for_one_to_refuse(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    """JSON's `""` is its own text for an empty string, and reads back as one."""
    _, target_url = pair
    target = create_engine(target_url)
    scratch = tmp_path / "from_target.xlsx"
    try:
        with target.begin() as conn:
            conn.execute(
                text("INSERT INTO parent (id, name, data) VALUES (52, 'empty', '\"\"')")
            )
        counts = wb.export_workbook(target, scratch)
        assert counts["parent"] == 2
        sheet = load_workbook(scratch)["parent"]
        header = [cell.value for cell in sheet[1]]
        assert sheet.cell(row=3, column=header.index("data") + 1).value == '""'
    finally:
        target.dispose()


def test_an_export_reads_every_table_as_of_one_moment(
    pair: tuple[Engine, str], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A row saved while the export runs is in all of its sheets or in none.

    A child and the parent it points at are committed after the parent
    table has been read and before the child table is. Read table by table
    as each stands, the workbook would hold the child and not its parent,
    and no import would take it.
    """
    _, target_url = pair
    target = create_engine(target_url)
    other = create_engine(target_url)
    columns_of = wb.stored_columns

    def saving_meanwhile(table: Table) -> list[Column[Any]]:
        """The table's stored columns, after a save lands just ahead of `child`."""
        if table.name == "child":
            with other.begin() as conn:
                conn.execute(
                    text("INSERT INTO parent (id, name) VALUES (60, 'meanwhile')")
                )
                conn.execute(
                    text("INSERT INTO child (parent_id, note) VALUES (60, 'late')")
                )
        return columns_of(table)

    monkeypatch.setattr(wb, "stored_columns", saving_meanwhile)
    try:
        counts = wb.export_workbook(target, tmp_path / "backup.xlsx")
        with target.connect() as conn:
            # The save did happen: only the export's view of it is in question.
            assert conn.execute(text("SELECT count(*) FROM child")).scalar() == 1
    finally:
        other.dispose()
        target.dispose()

    assert (counts["parent"], counts["child"]) == (1, 0)


def test_a_sheet_for_a_table_the_database_does_not_have_is_refused(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    """Rows with nowhere to go are not dropped without a word."""
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    book = load_workbook(path)
    ghost = book.create_sheet("ghost")
    ghost.append(["id", "name"])
    ghost.append([1, "a row no table takes"])
    book.save(path)

    with pytest.raises(wb.WorkbookError, match="ghost"):
        wb.import_workbook(path, target_url)
    target = create_engine(target_url)
    try:
        with target.connect() as conn:
            # One transaction: the migration's row is still there, untouched.
            assert conn.execute(text("SELECT count(*) FROM parent")).scalar() == 1
    finally:
        target.dispose()


def test_a_table_only_the_other_database_has_is_a_difference(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    wb.import_workbook(path, target_url)
    target = create_engine(target_url)
    try:
        with target.begin() as conn:
            conn.execute(text("CREATE TABLE extra (id serial PRIMARY KEY)"))
            conn.execute(text("INSERT INTO extra DEFAULT VALUES"))
        assert wb.compare(source, target) == [wb.Difference("extra", -1, 1)]
    finally:
        target.dispose()


# -- the proof a backup run makes -----------------------------------------------


def _scratch_built_like_the_source(config: Config, _revision: str) -> None:
    """Stand in for the migrations: the scratch database gets the source's schema.

    The source's tables are this file's own, which no migration builds; the
    address is the one the proof handed the migrations.
    """
    url = config.get_main_option("sqlalchemy.url")
    assert url is not None
    scratch = create_engine(url)
    try:
        with scratch.begin() as conn:
            conn.execute(text(SCHEMA))
            conn.execute(text("INSERT INTO alembic_version VALUES ('rev_1')"))
    finally:
        scratch.dispose()


def _databases_named(name: str) -> int:
    """How many databases on the test server go by this name."""
    admin = _admin()
    try:
        with admin.connect() as conn:
            found = conn.execute(
                text("SELECT count(*) FROM pg_database WHERE datname = :name"),
                {"name": name},
            ).scalar_one()
    finally:
        admin.dispose()
    return int(found)


def test_the_proof_restores_the_workbook_and_compares_it_with_live(
    source: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`backup_run.prove_workbook` itself: restore, compare, drop.

    The source database stands in for live, as it does for every import
    here. Three workbooks: the one just exported, which is proved; one with
    a cell changed, which differs; and one with a link to nothing, which
    cannot be restored. The scratch database is gone after each.
    """
    monkeypatch.setattr(wb.settings, "database_url", _url("ccwebdb_test_wb_source"))
    monkeypatch.setattr(backup_run.command, "upgrade", _scratch_built_like_the_source)
    scratch = "ccwebdb_proof_wb_test"
    # One an interrupted run of this test left would refuse the first proof.
    admin = _admin()
    with admin.connect() as conn:
        drop_database(conn, scratch)
    admin.dispose()
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)

    assert backup_run.prove_workbook(source, path, stamp="wb_test") == []
    assert _databases_named(scratch) == 0

    _break(path, {"child": {"C2": "edited"}})  # child id 1's note
    assert backup_run.prove_workbook(source, path, stamp="wb_test") == [
        "the workbook differs from live in child: 3 rows live, 3 restored"
    ]
    assert _databases_named(scratch) == 0

    _break(path, {"child": {"B2": 999}})  # no parent 999
    (problem,) = backup_run.prove_workbook(source, path, stamp="wb_test")
    assert problem.startswith("the workbook could not be restored: 1 link(s) point")
    assert _databases_named(scratch) == 0


def test_remembered_widths_follow_their_column_by_name(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    """Widths are keyed by column name, so they land on the right letter.

    `note` is child's third column; its width must reach column C whatever
    position a width file lists it in, and a computed column is sized too.
    """
    source, _ = pair
    path = tmp_path / "sized.xlsx"
    widths = {"child": {"note": 40.5}, "parent": {"doubled (computed)": 13.0}}
    wb.export_workbook(source, path, widths)
    book = load_workbook(path)
    assert book["child"].column_dimensions["C"].width == 40.5
    header = [c.value for c in book["parent"][1]]
    letter = "ABCDEFGHIJ"[header.index("doubled (computed)")]
    assert book["parent"].column_dimensions[letter].width == 13.0
    assert wb.capture_widths(path) == widths


def test_remembering_replaces_a_sized_sheet_and_keeps_the_rest(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    source, _ = pair
    store = tmp_path / "widths.json"
    store.write_text(
        '{"child": {"note": 9.0}, "parent": {"name": 9.0, "amount": 9.0}}',
        encoding="utf-8",
    )
    path = tmp_path / "sized.xlsx"
    wb.export_workbook(source, path, {"parent": {"name": 33.0}})
    merged = wb.remember_widths(path, store)
    # parent was resized in the workbook: its entry is the workbook's now.
    assert merged == {"child": {"note": 9.0}, "parent": {"name": 33.0}}
    assert wb.load_widths(store) == merged


def test_a_width_excel_saved_for_a_run_of_columns_reaches_each(
    tmp_path: Path,
) -> None:
    """Excel may store one width for adjacent columns (min=1, max=3)."""
    book = Workbook()
    sheet = book.active
    assert sheet is not None
    sheet.title = "child"
    sheet.append(["id", "parent_id", "note"])
    run = sheet.column_dimensions["A"]
    run.min, run.max, run.width = 1, 3, 17.5
    path = tmp_path / "run.xlsx"
    book.save(path)
    assert wb.capture_widths(path) == {
        "child": {"id": 17.5, "note": 17.5, "parent_id": 17.5}
    }


def test_no_width_file_means_default_widths(tmp_path: Path) -> None:
    assert wb.load_widths(tmp_path / "absent.json") == {}


# -- links that point at nothing -------------------------------------------------


def _break(path: Path, cells: dict[str, dict[str, str | int]]) -> None:
    """Overwrite workbook cells: {sheet: {cell: value}}."""
    book = load_workbook(path)
    for sheet, values in cells.items():
        for cell, value in values.items():
            book[sheet][cell] = value
    book.save(path)


def test_every_broken_link_is_named_at_once(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    # child id 1's finish, child id 2's parent.
    _break(path, {"child": {"D2": 99, "B3": 998}})
    with pytest.raises(wb.WorkbookError) as refused:
        wb.import_workbook(path, target_url)
    message = str(refused.value)
    assert message.startswith("2 link(s) point at nothing")
    assert "child id 1: finish_id 99 is no finish.id" in message
    assert "child id 2: parent_id 998 is no parent.id" in message
    assert "--unknown-for-missing" in message


def test_a_missing_vocabulary_value_becomes_a_new_unknown_row(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    """With the flag, the link points at an Unknown row with id 0, reported."""
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    _break(path, {"child": {"D2": 99, "D3": 98}})
    loaded = wb.import_workbook(path, target_url, unknown_for_missing=True)
    assert loaded.substituted == [
        wb.Substitution("child", "id 1", "finish_id", 99, "finish", 0),
        wb.Substitution("child", "id 2", "finish_id", 98, "finish", 0),
    ]
    assert loaded.counts["finish"] == 3
    target = create_engine(target_url)
    try:
        with target.connect() as conn:
            unknown = conn.execute(
                text(
                    "SELECT code, label, sort_order, is_active, source::text, "
                    "featured FROM finish WHERE id = 0"
                )
            ).one()
            assert tuple(unknown) == (
                "unknown",
                "Unknown",
                21,
                True,
                "manual",
                False,
            )
            links = conn.execute(
                text("SELECT finish_id FROM child ORDER BY id")
            ).scalars()
            assert list(links) == [0, 0, None]
            # The sequence is untouched by id 0: the next real row is 3.
            new_id = conn.execute(
                text(
                    "INSERT INTO finish (code, label, sort_order, is_active, source) "
                    "VALUES ('satin', 'Satin', 30, true, 'manual') RETURNING id"
                )
            ).scalar()
            assert new_id == 3
    finally:
        target.dispose()


def test_an_unknown_row_already_in_the_sheet_is_reused(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    # finish id 2 becomes the Unknown row; child id 1 points at nothing.
    _break(path, {"finish": {"B3": "unknown"}, "child": {"D2": 99}})
    loaded = wb.import_workbook(path, target_url, unknown_for_missing=True)
    assert loaded.substituted == [
        wb.Substitution("child", "id 1", "finish_id", 99, "finish", 2)
    ]
    assert loaded.counts["finish"] == 2


def test_an_unknown_row_is_not_guessed_for_a_vocabulary_that_needs_more(
    pair: tuple[Engine, str], tmp_path: Path
) -> None:
    """`coinage` needs a face value: no Unknown row is made up for it."""
    source, target_url = pair
    path = tmp_path / "backup.xlsx"
    wb.export_workbook(source, path)
    _break(path, {"child": {"E2": 99}})
    with pytest.raises(
        wb.WorkbookError, match="coinage: an Unknown row needs face, which cannot"
    ):
        wb.import_workbook(path, target_url, unknown_for_missing=True)


# -- one cell -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "kind", "cell"),
    [
        (None, String(), None),
        ("", String(), wb.EMPTY_STRING),
        (Decimal("0.77344"), Numeric(12, 6), 0.77344),
        ({"a": 1}, JSONB(), '{"a": 1}'),
        (
            datetime(2026, 9, 24, 10, 0, tzinfo=timezone(timedelta(hours=2))),
            DateTime(timezone=True),
            "2026-09-24T10:00:00+02:00",
        ),
    ],
)
def test_a_value_is_written_as_a_cell(
    value: object, kind: TypeEngine[Any], cell: object
) -> None:
    assert wb.to_cell(value, kind) == cell


@pytest.mark.parametrize(
    ("cell", "kind", "value"),
    [
        (None, String(), None),
        ("  ", String(), None),
        (wb.EMPTY_STRING, String(), ""),
        (0.77344, Numeric(12, 6), Decimal("0.773440")),
        (12.5, Numeric(12, 2), Decimal("12.50")),
        (5.0, Integer(), 5),
        ("TRUE", Boolean(), True),
        ("no", Boolean(), False),
        (datetime(2026, 9, 24), Date(), date(2026, 9, 24)),
        (
            "2026-09-24T10:00:00+00:00",
            DateTime(timezone=True),
            datetime(2026, 9, 24, 10, tzinfo=UTC),
        ),
        ('{"a": [1]}', JSONB(), {"a": [1]}),
        # JSON's own null is a value, kept apart from SQL NULL (an empty cell).
        ("null", JSONB(), JSON.NULL),
        ("red", Enum("red", "blue"), "red"),
    ],
)
def test_a_cell_is_read_as_its_column_stores_it(
    cell: object, kind: TypeEngine[Any], value: object
) -> None:
    assert wb.from_cell(cell, kind, "t row 2, c") == value


def test_a_bad_cell_names_where_it_is() -> None:
    with pytest.raises(wb.WorkbookError, match="inventory_item row 7, piece_count"):
        wb.from_cell(2.5, Integer(), "inventory_item row 7, piece_count")
