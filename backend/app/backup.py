"""Copy the whole database into another one, schema and all.

The database is the system of record, so this is a fallback copy of it.
`pg_dump` remains the fastest way to take a file off
the machine, but this exists because it is **portable**: the schema comes from
the SQLAlchemy models rather than from PostgreSQL, and the destination is a
URL. Pointing it at another engine is a change of URL, not of code.

    python -m app.backup                          copy to a timestamped database
    python -m app.backup --to <url>               copy anywhere SQLAlchemy reaches
    python -m app.backup --list                   what copies exist
    python -m app.backup --verify <name>          check a copy against the source

**What portability does and does not buy.** Every row moves, in foreign-key
order, and `Base.metadata.create_all` builds the schema on any dialect
SQLAlchemy supports. Three things do not survive a move off PostgreSQL
unchanged, and the report says so rather than leaving it to be discovered:

- `JSONB` columns (`inventory_item.attributes`, the change log's values) become the
  target's JSON type, or text where it has none.
- Generated columns are recomputed by the target from their expressions rather
  than copied, so a dialect without them needs the arithmetic doing elsewhere.
  Which columns those are is read from the models, never listed here.
- Partial indexes and enum types degrade to whatever the dialect offers.

None of that loses data. It changes how the target enforces it.

**Every table, not only the modeled ones.** `alembic_version` is
Alembic's own, with no model; a copy driven by the models alone left it
out, and the copy could not be migrated. Such tables are read from the
source database itself (`unmodelled_tables`), created in the copy and
copied after the rest.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    Column,
    MetaData,
    Table,
    create_engine,
    func,
    insert,
    inspect,
    select,
    text,
)
from sqlalchemy.engine import Connection, Dialect, Engine, make_url
from sqlalchemy.orm import Session

from .config import settings
from .models import Base

#: Rows copied per round trip. Large enough to be quick, small enough that a
#: failure does not sit on one enormous uncommitted transaction.
CHUNK = 1000


def generated_columns(table: Table) -> list[Column[Any]]:
    """The columns this table computes for itself, in the table's order.

    Asked of the table rather than listed here: a hardcoded list goes stale
    the moment a generated column is added, and the copy then tries to
    insert it, is refused, and aborts partway.

    The destination recomputes these from their own expressions, so writing
    them would either be refused or accepted and then disagree with the
    expression that is supposed to define them.
    """
    return [c for c in table.columns if c.computed is not None]


def stored_columns(table: Table) -> list[Column[Any]]:
    """Every column but the generated ones, in the table's order: what is written.

    What `copy_rows` copies and `app.workbook_backup` exports and imports as
    editable cells; `generated_columns` is the rest.
    """
    return [c for c in table.columns if c.computed is None]


def unmodelled_tables(source: Engine) -> list[Table]:
    """The source's tables that no model describes, in foreign-key order.

    Reflected from the database itself, so a table added by a migration and
    never given a model is copied too rather than silently dropped.
    """
    reflected = MetaData()
    reflected.reflect(source)
    return [t for t in reflected.sorted_tables if t.name not in Base.metadata.tables]


def all_tables(source: Engine) -> list[Table]:
    """The modeled tables in foreign-key order, then the unmodelled ones."""
    return [*Base.metadata.sorted_tables, *unmodelled_tables(source)]


def timestamped_name(prefix: str = "ccwebdb_bak") -> str:
    """A backup name that sorts chronologically."""
    return f"{prefix}_{datetime.now(UTC):%Y%m%d_%H%M%S}"


def admin_engine(url_str: str) -> Engine:
    """A connection to `postgres`, for CREATE DATABASE which cannot be in one."""
    url = make_url(url_str).set(database="postgres")
    return create_engine(url, isolation_level="AUTOCOMMIT")


def create_database(source_url: str, name: str) -> str:
    """Make an empty database beside the source, and return its URL."""
    with admin_engine(source_url).connect() as conn:
        exists = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :n"), {"n": name}
        ).scalar()
        if exists:
            raise RuntimeError(f"{name} already exists")
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    return make_url(source_url).set(database=name).render_as_string(hide_password=False)


def copy_rows(
    source: Engine, target: Engine, tables: list[Table] | None = None
) -> Iterator[tuple[str, int]]:
    """Every table, in foreign-key order, yielding what was written.

    `sorted_tables` is the topological order, so a row never arrives before
    the row it references. Copying alphabetically would fail on the first
    foreign key. The unmodelled tables come last: they reference modeled
    ones, never the reverse.
    """
    with Session(source) as read, Session(target) as write:
        for table in tables if tables is not None else all_tables(source):
            columns = stored_columns(table)
            names = [c.name for c in columns]
            total = 0
            offset = 0
            while True:
                rows = read.execute(
                    select(*columns)
                    .order_by(*table.primary_key.columns)
                    .limit(CHUNK)
                    .offset(offset)
                ).all()
                if not rows:
                    break
                write.execute(
                    insert(table), [dict(zip(names, row, strict=True)) for row in rows]
                )
                total += len(rows)
                offset += CHUNK
            write.commit()
            _resync_sequences(write, table)
            yield table.name, total


def _resync_sequences(session: Session, table: Table) -> None:
    """Point the table's id sequence past the ids just copied, and commit.

    Commits only when a sequence was set: a dialect without sequences, or a
    table whose id is not backed by one, has nothing to commit.
    """
    if resync_sequence(session, session.get_bind().dialect, table):
        session.commit()


def resync_sequence(
    executor: Connection | Session, dialect: Dialect, table: Table
) -> bool:
    """Point the table's id sequence past its highest id; whether one was set.

    Without this a copy or an import accepts the existing rows and then
    collides on the first insert, which would make a restored database look
    fine until someone used it. `app.backup` and `app.workbook_backup` both
    call it after loading a table.

    **A failure here is raised, not swallowed** -- swallowing it would leave
    exactly that state, silently. The two cases that are genuinely not
    failures are checked for instead: a dialect with no sequences, and a
    table whose id is not backed by one.
    """
    name = getattr(table, "name", None)
    primary = list(table.primary_key.columns)
    if name is None or len(primary) != 1 or primary[0].name != "id":
        return False
    if dialect.name != "postgresql":
        return False

    # NULL when the column has no owned sequence -- an id the application
    # assigns rather than the database. Asked separately so that case can be
    # told from a sequence that exists and could not be set.
    sequence = executor.execute(
        text("SELECT pg_get_serial_sequence(:t, 'id')"), {"t": name}
    ).scalar()
    if sequence is None:
        return False

    # The table name is interpolated because an identifier cannot be a bind
    # parameter. It comes from SQLAlchemy's own metadata, never from input,
    # and is quoted by the dialect's preparer so a reserved or mixed-case
    # name survives.
    quoted = dialect.identifier_preparer.quote(name)
    executor.execute(
        text(
            "SELECT setval(:s, coalesce((SELECT max(id) FROM " + quoted + "), 1), true)"
        ),
        {"s": sequence},
    )
    return True


#: Reported as the copy's row count for a table the copy does not have. A
#: real count is never negative, so it cannot be mistaken for one, and it
#: sorts below every genuine shortfall.
MISSING = -1


def compare(source: Engine, target: Engine) -> list[tuple[str, int, int]]:
    """Row counts per table on both sides. Any mismatch is a failed backup.

    A table absent from the copy counts `MISSING` rather than raising. A copy
    taken before a migration added a table is the ordinary case -- it is what
    an old backup *is* -- and answering "cannot verify" with a traceback from
    the tool you reach for when you are already worried about a backup is the
    wrong moment to be unhelpful.
    """
    out: list[tuple[str, int, int]] = []
    inspector = inspect(target)
    present = set(inspector.get_table_names())
    with Session(source) as a, Session(target) as b:
        for table in all_tables(source):
            left = a.execute(select(func.count()).select_from(table)).scalar() or 0
            if table.name not in present:
                out.append((table.name, left, MISSING))
                continue
            right = b.execute(select(func.count()).select_from(table)).scalar() or 0
            out.append((table.name, left, right))
    return out


def run(target_url: str | None, *, name: str | None = None) -> tuple[str, int]:
    """Copy the live database into `target_url`, creating it if it is local."""
    source_url = settings.database_url
    if target_url is None:
        target_url = create_database(source_url, name or timestamped_name())

    source = create_engine(source_url)
    target = create_engine(target_url)
    Base.metadata.create_all(target)
    extra = unmodelled_tables(source)
    if extra:
        # Their own reflected definitions; the modeled tables they point at
        # exist now, so their foreign keys resolve.
        extra[0].metadata.create_all(target, tables=extra)

    written = 0
    for table_name, count in copy_rows(
        source, target, [*Base.metadata.sorted_tables, *extra]
    ):
        if count:
            print(f"  {table_name:<28}{count:>8,}")
        written += count
    return target_url, written


def main(argv: list[str] | None = None) -> int:
    """Back up, list, or verify."""
    parser = argparse.ArgumentParser(prog="backup", description=__doc__)
    parser.add_argument("--to", help="destination URL; default is a local copy")
    parser.add_argument("--name", help="name for the local copy")
    parser.add_argument("--list", action="store_true", help="list local copies")
    parser.add_argument("--verify", help="compare a copy against the source")
    args = parser.parse_args(argv)

    if args.list:
        with admin_engine(settings.database_url).connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT datname, pg_size_pretty(pg_database_size(datname)) "
                    "FROM pg_database WHERE datname LIKE 'ccwebdb_bak%' "
                    "ORDER BY datname DESC"
                )
            ).all()
        for datname, size in rows:
            print(f"  {datname:<32}{size}")
        if not rows:
            print("  no backups yet")
        return 0

    if args.verify:
        # render_as_string(False) keeps the password. `str(url)` masks it as
        # ***, producing a URL that looks right and cannot authenticate --
        # the same trap conftest.py documents for the test database.
        url = (
            make_url(settings.database_url)
            .set(database=args.verify)
            .render_as_string(hide_password=False)
        )
        differences = [
            row
            for row in compare(create_engine(settings.database_url), create_engine(url))
            if row[1] != row[2]
        ]
        if differences:
            absent = [row for row in differences if row[2] == MISSING]
            if absent:
                print(
                    f"OLDER SCHEMA -- {args.verify} has no "
                    f"{', '.join(t for t, _, _ in absent)}."
                )
                print("It predates a migration, so it cannot be compared table")
                print("for table. It is not a usable backup of the collection now.")
            print("MISMATCH -- this backup is not a faithful copy:")
            for table, left, right in differences:
                shown = "absent" if right == MISSING else f"{right:>8,}"
                print(f"  {table:<28}source {left:>8,}   copy {shown:>8}")
            return 1
        print(f"{args.verify} matches the source on every table")
        return 0

    print("copying...")
    url, written = run(args.to, name=args.name)
    print(f"\n{written:,} rows -> {make_url(url).database}")
    print("\nverify with:  python -m app.backup --verify <name>")
    return 0


if __name__ == "__main__":
    sys.exit(main())
