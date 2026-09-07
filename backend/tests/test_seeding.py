"""Tests for reference-data loading and export.

The export path exists so that one installation's curation can benefit the
next: a fresh install should inherit grades, mints, districts and coinage
composition rather than rebuilding them. That only works if codes -- not ids --
are what cross the boundary, and if one collection's private guesses cannot
leak into a catalogue meant to be shared.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from app.models import Grade, Metal, ProvenanceSource
from app.seeding import (
    DATA_DIR,
    SeedError,
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
    assert all(counter.get("created", 0) == 0 for counter in stats.values())


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

    assert "denomination" in row and isinstance(row["denomination"], str)
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
    created = sum(c.get("created", 0) for c in stats.values())
    assert created == 0


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
    assert "MS65" in codes

    # ...but asking for them explicitly does include them.
    export_reference_data(db, tmp_path, sources=["seeded", "manual"])
    payload = json.loads((tmp_path / "grade.json").read_text(encoding="utf-8"))
    assert "LOCAL_ONLY" in {row["code"] for row in payload["grade"]}


def test_seed_files_on_disk_load_into_an_empty_database(db: Session) -> None:
    """Every foreign key a seed file names resolves in-load.

    The shipped files must be internally consistent -- every foreign key
    they name has to resolve within the same load.
    """
    stats = seed_all(db, DATA_DIR)
    assert "composition" in stats
