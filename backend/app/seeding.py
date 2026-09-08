"""Load and export reference data.

Reference data lives in versioned JSON under ``backend/data/reference/``,
never hardcoded in Python. Two reasons:

1. **A new installation should inherit it.** Most of these tables -- grades,
   mints, Federal Reserve districts, coinage composition -- are public facts or
   settled vocabulary, not one collection's opinions. Shipping them as data
   means a fresh install starts useful instead of empty.

2. **What we learn flows back out.** Classifiers discovered while importing a
   real collection are written with ``source='derived'``. ``export`` can then
   dump the vocabulary back to JSON, and the ``--source`` filter is what keeps
   one collection's guesses out of the shared catalogue.

Foreign keys are written as the *code* of the referenced row rather than its
id, because ids are per-installation and codes are not. Resolution is generic:
a key ``metal`` is matched to the column ``metal_id`` and looked up by code in
whatever table that column points at.

Usage::

    python -m app.seeding load                  # idempotent, safe to re-run
    python -m app.seeding export --out data/reference/exported
    python -m app.seeding export --source seeded derived
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Iterable, Iterator, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import (
    REFERENCE_MODELS,
    Composition,
    ProvenanceSource,
    ReferenceMixin,
    Series,
    SeriesAlias,
)

#: backend/data/reference
DATA_DIR = Path(__file__).resolve().parents[1] / "data" / "reference"

#: Tables the seeder understands, in dependency order: a table may only
#: reference tables that appear before it.
SEEDABLE: tuple[type[ReferenceMixin], ...] = (*REFERENCE_MODELS, Composition)

#: Composition has no `code`, so its identity is the span it describes.
NATURAL_KEYS: dict[str, tuple[str, ...]] = {
    "composition": ("denomination_id", "country_id", "year_from", "metal_id"),
}

#: Columns the loader must not try to write.
_SKIP_PREFIX = "_"


class SeedError(RuntimeError):
    """A seed file refers to something that does not exist."""


def _model_by_table() -> dict[str, type[ReferenceMixin]]:
    return {model.__tablename__: model for model in SEEDABLE}


def natural_key(model: type[ReferenceMixin]) -> tuple[str, ...]:
    """The columns identifying a row for re-loading -- `code`, or a span."""
    return NATURAL_KEYS.get(model.__tablename__, ("code",))


def iter_seed_files(data_dir: Path = DATA_DIR) -> Iterator[Path]:
    """Every seed file, in a stable order so loads are reproducible."""
    yield from sorted(data_dir.glob("*.json"))


def load_seed_data(data_dir: Path = DATA_DIR) -> dict[str, list[dict[str, Any]]]:
    """Merge every seed file into one mapping of table name to rows.

    Two files may contribute to the same table; rows are concatenated in file
    order. Keys beginning with an underscore are commentary and are dropped.
    """
    merged: dict[str, list[dict[str, Any]]] = {}
    for path in iter_seed_files(data_dir):
        payload = json.loads(path.read_text(encoding="utf-8"))
        for table, rows in payload.items():
            if table.startswith(_SKIP_PREFIX):
                continue
            if not isinstance(rows, list):
                raise SeedError(f"{path.name}: {table!r} must be a list of rows")
            merged.setdefault(table, []).extend(rows)
    return merged


def _foreign_key_targets(model: type[ReferenceMixin]) -> dict[str, tuple[str, str]]:
    """Map a bare data key to the column it fills and the table it points at.

    ``{"metal": ("metal_id", "metal")}`` -- so a seed row saying
    ``"metal": "silver"`` resolves silver's id in the ``metal`` table.
    """
    targets: dict[str, tuple[str, str]] = {}
    for column in model.__table__.columns:
        if not column.foreign_keys or not column.name.endswith("_id"):
            continue
        bare = column.name[: -len("_id")]
        target_table = next(iter(column.foreign_keys)).column.table.name
        targets[bare] = (column.name, target_table)
    return targets


def _resolve_row(
    row: dict[str, Any],
    model: type[ReferenceMixin],
    code_index: dict[str, dict[str, int]],
    where: str,
) -> dict[str, Any]:
    """Turn a seed row into column values, resolving code references to ids."""
    fk_targets = _foreign_key_targets(model)
    columns = {c.name for c in model.__table__.columns}
    values: dict[str, Any] = {}

    for key, value in row.items():
        if key.startswith(_SKIP_PREFIX):
            continue
        if key in fk_targets:
            column_name, target_table = fk_targets[key]
            if value is None:
                values[column_name] = None
                continue
            index = code_index.get(target_table, {})
            if value not in index:
                raise SeedError(
                    f"{where}: {key}={value!r} does not exist in {target_table}"
                )
            values[column_name] = index[value]
        elif key in columns:
            values[key] = value
        else:
            raise SeedError(f"{where}: unknown column {key!r} on {model.__tablename__}")
    return values


def _build_code_index(session: Session) -> dict[str, dict[str, int]]:
    """Current code -> id for every table that has a code column."""
    index: dict[str, dict[str, int]] = {}
    for model in SEEDABLE:
        if "code" not in {c.name for c in model.__table__.columns}:
            continue
        rows = session.execute(select(model.code, model.id)).all()
        index[model.__tablename__] = dict(rows)
    return index


def seed_all(
    session: Session,
    data_dir: Path = DATA_DIR,
    *,
    only: Sequence[str] | None = None,
) -> dict[str, Counter]:
    """Load every seed file into the database. Idempotent.

    Existing rows are matched on their natural key and updated in place, so a
    label can be reworded in a seed file and re-loaded without creating a
    duplicate or disturbing rows that reference it by id.

    Rows a person has since edited are **not** overwritten: anything whose
    ``source`` is ``manual`` is left alone, because a hand correction outranks
    a shipped default.
    """
    data = load_seed_data(data_dir)
    stats: dict[str, Counter] = {}

    for model in SEEDABLE:
        table = model.__tablename__
        if only and table not in only:
            continue
        rows = data.get(table)
        if not rows:
            continue

        counter: Counter = Counter()
        keys = natural_key(model)
        # Rebuilt per table so a table can reference one seeded earlier in the
        # same run.
        code_index = _build_code_index(session)

        for position, row in enumerate(rows, start=1):
            where = f"{table}[{position}]"
            values = _resolve_row(row, model, code_index, where)
            missing = [k for k in keys if k not in values]
            if missing:
                raise SeedError(f"{where}: missing natural key {missing}")

            lookup = {k: values[k] for k in keys}
            existing = session.execute(
                select(model).filter_by(**lookup)
            ).scalar_one_or_none()

            if existing is None:
                values.setdefault("source", ProvenanceSource.seeded)
                session.add(model(**values))
                counter["created"] += 1
                continue

            if existing.source == ProvenanceSource.manual:
                counter["skipped_manual"] += 1
                continue

            changed = False
            for column, value in values.items():
                if _differs(getattr(existing, column), value):
                    setattr(existing, column, value)
                    changed = True
            counter["updated" if changed else "unchanged"] += 1

        session.flush()
        stats[table] = counter

    if not only or "series_alias" in only:
        stats["series_alias"] = _seed_series_aliases(session, data)

    session.commit()
    return stats


def _seed_series_aliases(
    session: Session, data: dict[str, list[dict[str, Any]]]
) -> Counter:
    """Load the colloquial names, which the generic loader cannot carry.

    `series_alias` has no `code` and no `label`, so it is not a classifier in
    the sense the rest of this module means. It is a lookup that exists purely
    so a search for "Mercury" finds a Winged Liberty Head Dime.
    """
    counter: Counter = Counter()
    rows = data.get("series_alias") or []
    if not rows:
        return counter

    series_ids = dict(session.execute(select(Series.code, Series.id)).all())
    existing = {
        (series_id, alias)
        for series_id, alias in session.execute(
            select(SeriesAlias.series_id, SeriesAlias.alias)
        ).all()
    }

    for position, row in enumerate(rows, start=1):
        code = row.get("series")
        alias = row.get("alias")
        if not code or not alias:
            raise SeedError(f"series_alias[{position}]: needs 'series' and 'alias'")
        series_id = series_ids.get(code)
        if series_id is None:
            raise SeedError(f"series_alias[{position}]: unknown series {code!r}")
        if (series_id, alias) in existing:
            counter["unchanged"] += 1
            continue
        session.add(SeriesAlias(series_id=series_id, alias=alias))
        existing.add((series_id, alias))
        counter["created"] += 1

    session.flush()
    return counter


def _differs(current: object, incoming: object) -> bool:
    """Compare tolerantly across the JSON/database type boundary.

    Numerics arrive as strings so that no value passes through a float on its
    way into the database, which means ``"0.9000"`` and ``Decimal("0.9000")``
    must compare equal.
    """
    if isinstance(current, Decimal) and isinstance(incoming, str):
        return current != Decimal(incoming)
    if isinstance(current, ProvenanceSource) and isinstance(incoming, str):
        return current.value != incoming
    return current != incoming


def export_reference_data(
    session: Session,
    out_dir: Path,
    *,
    sources: Iterable[str] = ("seeded",),
    include_inactive: bool = False,
) -> dict[str, int]:
    """Write reference tables back out as seed files.

    This is the path that lets one installation's curation benefit the next.
    ``sources`` is the safety valve: exporting only ``seeded`` reproduces the
    shipped catalogue, while adding ``derived`` includes classifiers learned
    from real data. ``manual`` rows are one operator's private decisions and
    are excluded unless asked for explicitly.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    wanted = {ProvenanceSource(s) for s in sources}
    written: dict[str, int] = {}

    for model in SEEDABLE:
        table = model.__tablename__
        fk_targets = _foreign_key_targets(model)
        by_column = {
            column: (bare, target) for bare, (column, target) in fk_targets.items()
        }

        stmt = select(model)
        if not include_inactive and hasattr(model, "is_active"):
            stmt = stmt.filter_by(is_active=True)
        records = session.execute(stmt).scalars().all()

        rows: list[dict[str, Any]] = []
        for record in records:
            if record.source not in wanted:
                continue
            rows.append(_record_to_row(record, model, by_column, session))

        if not rows:
            continue

        payload = {
            "_comment": [
                f"Exported from {table}.",
                f"Sources included: {', '.join(sorted(s.value for s in wanted))}.",
            ],
            table: rows,
        }
        path = out_dir / f"{table}.json"
        path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
        written[table] = len(rows)

    return written


def _record_to_row(
    record: object,
    model: type[ReferenceMixin],
    by_column: dict[str, tuple[str, str]],
    session: Session,
) -> dict[str, Any]:
    """One database row as a portable seed row, with ids turned back to codes."""
    models = _model_by_table()
    row: dict[str, Any] = {}

    for column in model.__table__.columns:
        name = column.name
        if name == "id":
            continue
        value = getattr(record, name)
        if value is None:
            continue

        if name in by_column:
            bare, target_table = by_column[name]
            target_model = models.get(target_table)
            if target_model is not None:
                code = session.get(target_model, value)
                if code is not None:
                    row[bare] = code.code
                    continue
            row[name] = value
            continue

        if isinstance(value, Decimal):
            row[name] = str(value)
        elif isinstance(value, ProvenanceSource) or (
            hasattr(value, "value") and not isinstance(value, (int, str, bool))
        ):
            row[name] = value.value
        else:
            row[name] = value

    # created_at/updated_at describe this installation, not the vocabulary.
    for transient in ("created_at", "updated_at"):
        row.pop(transient, None)
    return row


def _main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    load = sub.add_parser("load", help="load seed files into the database")
    load.add_argument("--data-dir", type=Path, default=DATA_DIR)
    load.add_argument("--only", nargs="*", help="limit to these tables")

    export = sub.add_parser("export", help="dump reference tables to seed files")
    export.add_argument("--out", type=Path, required=True)
    export.add_argument(
        "--source",
        nargs="*",
        default=["seeded"],
        choices=[s.value for s in ProvenanceSource],
    )
    export.add_argument("--include-inactive", action="store_true")

    args = parser.parse_args(argv)

    with SessionLocal() as session:
        if args.command == "load":
            stats = seed_all(session, args.data_dir, only=args.only)
            total = Counter()
            for table, counter in sorted(stats.items()):
                total.update(counter)
                summary = ", ".join(f"{k} {v}" for k, v in sorted(counter.items()))
                print(f"  {table:<24} {summary}")
            print(f"\n{dict(total)}")
        else:
            written = export_reference_data(
                session,
                args.out,
                sources=args.source,
                include_inactive=args.include_inactive,
            )
            for table, count in sorted(written.items()):
                print(f"  {table:<24} {count} rows")
            print(f"\nwrote {len(written)} files to {args.out}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
