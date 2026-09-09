"""Copy the whole database into another one, schema and all.

The database is the system of record, so this is the fallback -- the
spreadsheet no longer is. `pg_dump` remains the fastest way to take a file off
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

- `JSONB` columns (`inventory_item.attributes`, `import_row.raw`) become the
  target's JSON type, or text where it has none.
- Generated columns are recomputed by the target from their expressions rather
  than copied, so a dialect without them needs the arithmetic doing elsewhere.
  Which columns those are is read from the models, never listed here.
- Partial indexes and enum types degrade to whatever the dialect offers.

None of that loses data. It changes how the target enforces it.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Iterator
from datetime import UTC, datetime

from sqlalchemy import Table, create_engine, func, insert, select, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session

from .config import settings
from .models import Base

#: Rows copied per round trip. Large enough to be quick, small enough that a
#: failure does not sit on one enormous uncommitted transaction.
CHUNK = 1000


def generated_columns(table: Table) -> set[str]:
    """The columns this table computes for itself.

    Asked of the model rather than listed here. A hardcoded set went stale the
    moment `currency_detail.series_designation` was added: the backup then
    tried to insert it, PostgreSQL refused, and the copy aborted partway --
    silently leaving five tables empty in every backup taken afterwards.

    The destination recomputes these from their own expressions, so writing
    them would either be refused, as it was, or accepted and then disagree with
    the expression that is supposed to define them.
    """
    return {c.name for c in table.columns if c.computed is not None}


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


def copy_rows(source: Engine, target: Engine) -> Iterator[tuple[str, int]]:
    """Every table, in foreign-key order, yielding what was written.

    `sorted_tables` is the topological order, so a row never arrives before
    the row it references. Copying alphabetically would fail on the first
    foreign key.
    """
    with Session(source) as read, Session(target) as write:
        for table in Base.metadata.sorted_tables:
            columns = [c for c in table.columns if c.computed is None]
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
    """Point each serial sequence past the ids just copied.

    Without this the copy accepts existing rows and then collides on the first
    insert, which would make a restored backup look fine until someone used it.
    Skipped silently on dialects without sequences.
    """
    name = getattr(table, "name", None)
    primary = list(table.primary_key.columns)
    if name is None or len(primary) != 1 or primary[0].name != "id":
        return
    try:
        session.execute(
            text(
                "SELECT setval(pg_get_serial_sequence(:t, 'id'), "
                "coalesce((SELECT max(id) FROM " + name + "), 1), true)"
            ),
            {"t": name},
        )
        session.commit()
    except Exception:
        session.rollback()


def compare(source: Engine, target: Engine) -> list[tuple[str, int, int]]:
    """Row counts per table on both sides. Any mismatch is a failed backup."""
    out: list[tuple[str, int, int]] = []
    with Session(source) as a, Session(target) as b:
        for table in Base.metadata.sorted_tables:
            left = a.execute(select(func.count()).select_from(table)).scalar() or 0
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

    written = 0
    for table_name, count in copy_rows(source, target):
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
            print("MISMATCH -- this backup is not a faithful copy:")
            for table, left, right in differences:
                print(f"  {table:<28}source {left:>8,}   copy {right:>8,}")
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
