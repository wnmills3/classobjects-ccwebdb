"""Tests for reference-data loading and export.

The export path exists so that one installation's curation can benefit the
next: a fresh install should inherit grades, mints, districts and coinage
composition rather than rebuilding them. That only works if codes -- not ids --
are what cross the boundary, and if one collection's private guesses cannot
leak into a catalog meant to be shared.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from app.models import Grade, Metal, ProvenanceSource
from app.seeding import (
    SEEDABLE,
    SeedError,
    _seed_note_issues,
    _seed_reference_aliases,
    _seed_series_aliases,
    _seed_series_year_ranges,
    export_reference_data,
    load_seed_data,
    seed_all,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session


def test_seed_files_parse_and_cover_the_expected_tables() -> None:
    data = load_seed_data()
    assert "item_kind" in data
    assert "composition" in data
    # Commentary keys are dropped rather than treated as tables.
    assert not any(table.startswith("_") for table in data)


def test_loading_is_idempotent(db: Session) -> None:
    """Loading the same seed files twice changes nothing.

    Re-running a load must not duplicate rows or disturb ids that other
    rows already reference.
    """
    before = db.execute(select(func.count()).select_from(Grade)).scalar()

    stats = seed_all(db)

    after = db.execute(select(func.count()).select_from(Grade)).scalar()
    assert after == before
    # Nothing written at all: a load that rewrote every row it matched would
    # create none either.
    touched = {
        table: dict(counter)
        for table, counter in stats.items()
        if set(counter) - {"unchanged"}
    }
    assert touched == {}
    assert sum(counter["unchanged"] for counter in stats.values()) > 0


def test_a_reworded_label_updates_in_place(db: Session) -> None:
    """A reworded label updates in place.

    Codes are stable, labels are editable -- so a label change must not
    create a second row.
    """
    silver = db.execute(select(Metal).where(Metal.code == "silver")).scalar_one()
    original_id, original_label = silver.id, silver.label

    silver.label = "Silver (edited)"
    db.commit()

    seed_all(db, only=["metal"])
    db.refresh(silver)

    assert silver.id == original_id
    assert silver.label == original_label


def test_a_hand_edited_row_is_never_overwritten(db: Session) -> None:
    """A person's correction outranks a shipped default."""
    silver = db.execute(select(Metal).where(Metal.code == "silver")).scalar_one()
    silver.label = "Ag - locally renamed"
    silver.source = ProvenanceSource.manual
    db.commit()

    seed_all(db, only=["metal"])
    db.refresh(silver)

    assert silver.label == "Ag - locally renamed"


def test_an_unknown_reference_is_refused_not_guessed(
    db: Session, tmp_path: Path
) -> None:
    (tmp_path / "bad.json").write_text(
        json.dumps(
            {"bullion_form": [{"code": "x", "label": "X", "metal": "unobtainium"}]}
        ),
        encoding="utf-8",
    )
    with pytest.raises(SeedError, match="unobtainium"):
        seed_all(db, tmp_path)
    db.rollback()


def test_export_writes_codes_not_ids(db: Session, tmp_path: Path) -> None:
    """Ids are per-installation; codes are portable.

    Exporting ids would make the file useless anywhere but the database it
    came from.
    """
    export_reference_data(db, tmp_path, sources=["seeded"])

    payload = json.loads((tmp_path / "composition.json").read_text(encoding="utf-8"))
    row = payload["composition"][0]

    assert "denomination" in row
    assert isinstance(row["denomination"], str)
    assert "denomination_id" not in row
    assert "metal_id" not in row
    assert "id" not in row


def test_export_round_trips_through_a_fresh_load(db: Session, tmp_path: Path) -> None:
    """An export loads cleanly into another installation.

    What comes out must go back in: the export is only useful if another
    installation can actually load it.
    """
    export_reference_data(db, tmp_path, sources=["seeded"])
    stats = seed_all(db, tmp_path)

    # Every row matched an existing one; nothing was created or changed.
    touched = {
        table: dict(counter)
        for table, counter in stats.items()
        if set(counter) - {"unchanged"}
    }
    assert touched == {}
    assert stats["grade"]["unchanged"] > 0


def test_an_exported_row_carries_everything_a_new_row_needs(
    db: Session, tmp_path: Path
) -> None:
    """Loading into the database it came from proves little about a new one.

    There every row matches an existing one, so a column the export left
    out is never missed. A new installation inserts each row, and a column
    that must have a value and has no default has to be in the file.
    """
    written = export_reference_data(db, tmp_path, sources=["seeded"])
    assert {"grade", "denomination", "composition"} <= set(written)

    for model in SEEDABLE:
        table = model.__tablename__
        if table not in written:
            continue
        needed = {
            column.name.removesuffix("_id") if column.foreign_keys else column.name
            for column in model.__table__.columns
            if not column.primary_key
            and not column.nullable
            and column.default is None
            and column.server_default is None
            and column.computed is None
            and column.name not in {"created_at", "updated_at"}
        }
        rows = json.loads((tmp_path / f"{table}.json").read_text(encoding="utf-8"))[
            table
        ]
        for row in rows:
            assert needed <= set(row), (table, row.get("code"), needed - set(row))


def test_an_export_leaves_out_what_the_database_works_out(
    db: Session, tmp_path: Path
) -> None:
    """A generated column cannot be inserted, so a new installation's load fails.

    `grade.grade_rank` is computed from the number and the plus; PostgreSQL
    refuses any value written to it.
    """
    export_reference_data(db, tmp_path, sources=["seeded"])

    payload = json.loads((tmp_path / "grade.json").read_text(encoding="utf-8"))
    assert payload["grade"]
    assert not [row["code"] for row in payload["grade"] if "grade_rank" in row]
    # What it is computed from still travels.
    assert any("numeric_value" in row for row in payload["grade"])


# ---------------------------------------------------------------------------
# A key a loader does not know is a mistake in the file
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("load", "table", "row", "misspelt"),
    [
        (
            _seed_series_year_ranges,
            "series_year_range",
            # `letter` for `letters`: read as "any letter" if it is let through.
            {"series": "morgan_dollar", "year_start": 1878, "letter": "B"},
            "letter",
        ),
        (
            _seed_series_aliases,
            "series_alias",
            {"series": "morgan_dollar", "alias": "Test Nickname", "aliass": "x"},
            "aliass",
        ),
        (
            _seed_reference_aliases,
            "reference_alias",
            {"table": "note_type", "code": "frn", "alias": "Test Name", "tabel": "x"},
            "tabel",
        ),
        (
            _seed_note_issues,
            "note_issue",
            {
                "denomination": "usd_note_1",
                "series_year": 1957,
                "note_type": "frn",
                "seal_color": "blue",
                "serial_prefx": "A",
            },
            "serial_prefx",
        ),
    ],
)
def test_a_misspelt_key_is_refused_by_every_loader(
    db: Session,
    load: Callable[[Session, dict[str, list[dict[str, Any]]]], Counter],
    table: str,
    row: dict[str, Any],
    misspelt: str,
) -> None:
    """The generic loader names an unknown column; the four others must too."""
    with pytest.raises(SeedError, match=f"'{misspelt}'"):
        load(db, {table: [row]})
    db.rollback()


def test_commentary_keys_are_still_allowed_in_every_loader(db: Session) -> None:
    """`_note` on a row, and `sources` on a note issue, are not columns."""
    shipped = load_seed_data()
    issues = [dict(row, _note="checked") for row in shipped["note_issue"]]
    assert any("sources" in row for row in issues)
    assert _seed_note_issues(db, {"note_issue": issues}) == {"unchanged": len(issues)}

    alias = {"series": "morgan_dollar", "alias": "Cartwheel", "_note": "shared"}
    assert _seed_series_aliases(db, {"series_alias": [alias]}) == {"unchanged": 1}


def test_export_excludes_one_installations_private_rows(
    db: Session, tmp_path: Path
) -> None:
    """One operator's decisions do not ship as facts.

    `manual` rows are a particular operator's decisions and must not ship
    as though they were curated facts.
    """
    db.add(
        Grade(code="LOCAL_ONLY", label="House grade", source=ProvenanceSource.manual)
    )
    db.commit()

    export_reference_data(db, tmp_path, sources=["seeded"])
    payload = json.loads((tmp_path / "grade.json").read_text(encoding="utf-8"))
    codes = {row["code"] for row in payload["grade"]}

    assert "LOCAL_ONLY" not in codes
    assert "65" in codes

    # ...but asking for them explicitly does include them.
    export_reference_data(db, tmp_path, sources=["seeded", "manual"])
    payload = json.loads((tmp_path / "grade.json").read_text(encoding="utf-8"))
    assert "LOCAL_ONLY" in {row["code"] for row in payload["grade"]}
