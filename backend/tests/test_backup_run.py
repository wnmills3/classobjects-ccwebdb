"""`app.backup_run`: a backup is tidied only once it is proved.

`run` and `main` are always handed the test server's own engine: left to
themselves they open the application's database, which on this machine is
the live one. The export and the proof -- a restore into a second database
-- are replaced here by ones that answer as the test says: the test
database is built from the models and has no migration revision to export
or restore. Both are exercised for real by running the command.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from app import backup_run, media_backup, workbook_backup
from app.backup_run import main, run
from sqlalchemy import Engine


@pytest.fixture(autouse=True)
def exported(monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    """Each workbook the run exports, written as a small file in its place."""
    written: list[Path] = []

    def export(_engine: Engine, path: Path) -> dict[str, int]:
        """Write a stand-in workbook and say three rows went into it."""
        path.write_bytes(b"the new workbook")
        written.append(path)
        return {"inventory_item": 3}

    monkeypatch.setattr(workbook_backup, "export_workbook", export)
    return written


def _proved(*_args: object, **_kwargs: object) -> list[str]:
    """A proof that finds the workbook right."""
    return []


DIFFERENCE = "the workbook differs from live in inventory_item: 3 rows live, 2 restored"


def _differs(*_args: object, **_kwargs: object) -> list[str]:
    """A proof that finds the workbook wrong."""
    return [DIFFERENCE]


def _older(folder: Path, *stamps: str) -> list[Path]:
    """Earlier backups' workbooks in `folder`, one per time stamp."""
    folder.mkdir(parents=True, exist_ok=True)
    made = []
    for stamp in stamps:
        path = folder / f"ccwebdb_{stamp}.xlsx"
        path.write_bytes(b"an earlier workbook")
        made.append(path)
    return made


def _names(folder: Path) -> list[str]:
    """The files directly in `folder`, by name."""
    return sorted(path.name for path in folder.iterdir() if path.is_file())


def test_a_proved_backup_replaces_the_older_workbooks(
    engine: Engine, tmp_path: Path
) -> None:
    folder = tmp_path / "backup"
    _older(folder, "20260101_000000", "20260301_000000")

    done = run(engine, folder, stamp="20261007_120000", prove=_proved)

    assert done.proved
    assert _names(folder) == ["ccwebdb_20261007_120000.xlsx"]
    assert sorted(path.name for path in done.removed) == [
        "ccwebdb_20260101_000000.xlsx",
        "ccwebdb_20260301_000000.xlsx",
    ]
    assert done.workbook.read_bytes() == b"the new workbook"
    assert done.rows == 3


def test_a_backup_that_is_not_proved_removes_nothing(
    engine: Engine, tmp_path: Path
) -> None:
    folder = tmp_path / "backup"
    _older(folder, "20260101_000000", "20260301_000000")

    done = run(engine, folder, stamp="20261007_120000", prove=_differs)

    assert not done.proved
    assert done.problems == [DIFFERENCE]
    assert done.removed == []
    # The older ones are the only proved copies there are: all still there,
    # with the new one left beside them to be looked at.
    assert _names(folder) == [
        "ccwebdb_20260101_000000.xlsx",
        "ccwebdb_20260301_000000.xlsx",
        "ccwebdb_20261007_120000.xlsx",
    ]


def test_photographs_are_pruned_only_when_the_backup_is_proved(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pruned: list[Path] = []

    def prune(_db: object, folder: Path, *, delete: bool) -> media_backup.MediaPrune:
        """Note the folder a prune was asked to delete from."""
        assert delete
        pruned.append(folder)
        return media_backup.MediaPrune(deleted=4)

    monkeypatch.setattr(media_backup, "prune_media", prune)
    folder = tmp_path / "backup"

    unproved = run(engine, folder, stamp="20261007_120000", prove=_differs)
    assert pruned == []
    assert unproved.pruned == 0

    proved = run(engine, folder, stamp="20261007_120500", prove=_proved)
    assert pruned == [folder / "media"]
    assert proved.pruned == 4


def test_a_photograph_wrong_in_the_copy_leaves_the_backup_unproved(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        media_backup,
        "check_media",
        lambda _db, _folder: ["missing from the copy: originals/ab/abc.jpg"],
    )
    folder = tmp_path / "backup"
    _older(folder, "20260101_000000")

    done = run(engine, folder, stamp="20261007_120000", prove=_proved)

    assert done.problems == ["missing from the copy: originals/ab/abc.jpg"]
    assert done.removed == []
    assert (folder / "ccwebdb_20260101_000000.xlsx").is_file()


def test_keep_says_how_many_workbooks_stay_the_newest_first(
    engine: Engine, tmp_path: Path
) -> None:
    folder = tmp_path / "backup"
    # Not made in date order: the name's time stamp is what orders them.
    _older(folder, "20260301_000000", "20260101_000000", "20260201_000000")

    done = run(engine, folder, keep=2, stamp="20261007_120000", prove=_proved)

    assert _names(folder) == [
        "ccwebdb_20260301_000000.xlsx",
        "ccwebdb_20261007_120000.xlsx",
    ]
    assert len(done.removed) == 2


def test_only_this_command_s_workbooks_are_ever_removed(
    engine: Engine, tmp_path: Path
) -> None:
    folder = tmp_path / "backup"
    _older(folder, "20260101_000000")
    (folder / "wnm3_coins.xlsx").write_bytes(b"someone else's workbook")
    (folder / "notes.txt").write_text("keep me", encoding="utf-8")
    (folder / "media").mkdir()
    (folder / "media" / "ccwebdb_20200101_000000.xlsx").write_bytes(b"not at the top")

    run(engine, folder, stamp="20261007_120000", prove=_proved)

    assert _names(folder) == [
        "ccwebdb_20261007_120000.xlsx",
        "notes.txt",
        "wnm3_coins.xlsx",
    ]
    assert (folder / "media" / "ccwebdb_20200101_000000.xlsx").is_file()


def test_the_proof_is_handed_the_workbook_that_was_just_written(
    engine: Engine, tmp_path: Path
) -> None:
    seen: list[tuple[bool, str]] = []

    def prove(live: Engine, workbook: Path, *, stamp: str) -> list[str]:
        """Note that the file is already on disk when the proof starts."""
        seen.append((live is engine and workbook.is_file(), stamp))
        return []

    done = run(engine, tmp_path / "backup", stamp="20261007_120000", prove=prove)

    assert seen == [(True, "20261007_120000")]
    assert done.workbook.name == "ccwebdb_20261007_120000.xlsx"


def test_the_command_exits_0_and_says_what_it_proved(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(backup_run, "prove_workbook", _proved)
    folder = tmp_path / "backup"
    _older(folder, "20260101_000000")

    assert main([str(folder)], live=engine) == 0

    printed = capsys.readouterr()
    assert "proved: the workbook restores to exactly what live holds" in printed.out
    assert "removed the older workbook ccwebdb_20260101_000000.xlsx" in printed.out
    assert printed.err == ""


def test_the_command_exits_1_and_says_nothing_was_removed(
    engine: Engine,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(backup_run, "prove_workbook", _differs)
    folder = tmp_path / "backup"
    _older(folder, "20260101_000000")

    assert main([str(folder)], live=engine) == 1

    printed = capsys.readouterr()
    assert "NOT PROVED: 1 problem(s). Nothing was removed" in printed.err
    assert DIFFERENCE in printed.err
    assert (folder / "ccwebdb_20260101_000000.xlsx").is_file()


def test_keeping_no_workbook_at_all_is_refused(
    engine: Engine, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as stopped:
        main([str(tmp_path / "backup"), "--keep", "0"], live=engine)
    assert stopped.value.code == 2
    assert "--keep must be at least 1" in capsys.readouterr().err
    assert not (tmp_path / "backup").exists()
