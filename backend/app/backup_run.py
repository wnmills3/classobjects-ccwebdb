"""One whole backup, proved before anything older is removed.

What a person does by hand after a day's entry, as one command that can run
unattended:

1. **Export** the database to a workbook in the backup folder, and copy the
   photographs beside it (`app.workbook_backup`, `app.media_backup`).
2. **Prove the workbook.** It is loaded into a new, empty database on the
   same server and that database compared with live, every row of every
   table. A backup that has not been restored is not known to restore.
3. **Check the photographs**: every file the image rows name is in the
   copy and is the bytes its row describes.
4. Only when all of that held: **prune** the photograph files no image
   names any more, and **remove the older workbooks**, keeping the newest
   `--keep` of them.

When any step fails nothing is removed: the new workbook is left beside the
old ones, the reason is printed, and the command exits 1. So the folder
always holds at least one workbook that was proved.

Usage, from `backend/`:

    python -m app.backup_run FOLDER            export, prove, tidy
    python -m app.backup_run FOLDER --keep 3   keep the newest three workbooks

The scratch database is named `ccwebdb_proof_<time>` and is dropped whether
the proof held or not. An edit made to live between the export and the
comparison shows as a difference: the run fails, removes nothing, and the
next run sets it right.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from . import media_backup, workbook_backup
from .config import REPO_ROOT, settings
from .storage import get_storage

__all__ = ["BackupRun", "main", "prove_workbook", "run"]

#: A workbook this command wrote, and so may later remove.
WORKBOOKS = "ccwebdb_*.xlsx"


@dataclass
class BackupRun:
    """What one run did, and everything that went wrong."""

    #: The workbook written.
    workbook: Path
    #: Rows exported.
    rows: int = 0
    #: Photograph files copied this run, and already there.
    copied: int = 0
    present: int = 0
    #: Each thing that failed; empty when the backup is proved.
    problems: list[str] = field(default_factory=list)
    #: Obsolete photograph files and older workbooks removed.
    pruned: int = 0
    removed: list[Path] = field(default_factory=list)

    @property
    def proved(self) -> bool:
        """Whether every step held, so the tidying was allowed."""
        return not self.problems


def _scratch_url(live_url: str, name: str) -> str:
    """The address of a database called `name` on live's own server."""
    return make_url(live_url).set(database=name).render_as_string(hide_password=False)


def prove_workbook(live: Engine, workbook: Path, *, stamp: str) -> list[str]:
    """Load `workbook` into a new database and compare it with live.

    Returns what differs, one line per table; empty when the workbook holds
    exactly what live holds. The scratch database is created with the
    migrations, as a real restore is, and dropped afterwards either way.
    """
    name = f"ccwebdb_proof_{stamp}"
    url = _scratch_url(live.url.render_as_string(hide_password=False), name)
    # CREATE and DROP DATABASE cannot run inside a transaction.
    server = create_engine(_scratch_url(url, "postgres"), isolation_level="AUTOCOMMIT")
    problems: list[str] = []
    try:
        with server.connect() as conn:
            conn.execute(text(f'CREATE DATABASE "{name}"'))
        try:
            backend = REPO_ROOT / "backend"
            config = Config(str(backend / "alembic.ini"))
            config.set_main_option("script_location", str(backend / "alembic"))
            config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
            command.upgrade(config, "head")
            workbook_backup.import_workbook(workbook, url)
            scratch = create_engine(url)
            try:
                for difference in workbook_backup.compare(live, scratch):
                    problems.append(
                        f"the workbook differs from live in {difference.table}: "
                        f"{difference.left_rows:,} rows live, "
                        f"{difference.right_rows:,} restored"
                    )
            finally:
                scratch.dispose()
        except workbook_backup.WorkbookError as exc:
            problems.append(f"the workbook could not be restored: {exc}")
        finally:
            with server.connect() as conn:
                conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        server.dispose()
    return problems


def run(
    live: Engine,
    folder: Path,
    *,
    keep: int = 1,
    stamp: str | None = None,
    prove: Callable[..., list[str]] | None = None,
) -> BackupRun:
    """Export to `folder`, prove the copy, and tidy only if it is proved.

    `keep` is how many workbooks stay, the new one among them. `stamp` names
    the workbook and the scratch database; `prove` is the proof,
    `prove_workbook` unless another is given.
    """
    stamp = stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    media = folder / "media"
    done = BackupRun(workbook=folder / f"ccwebdb_{stamp}.xlsx")

    counts = workbook_backup.export_workbook(live, done.workbook)
    done.rows = sum(counts.values())
    with Session(live) as db:
        copied = media_backup.copy_media(db, get_storage(), media)
        done.copied, done.present = copied.copied, copied.present
        done.problems.extend(copied.problems)
        proof = prove or prove_workbook
        done.problems.extend(proof(live, done.workbook, stamp=stamp))
        done.problems.extend(media_backup.check_media(db, media))
        if not done.proved:
            return done

        pruned = media_backup.prune_media(db, media, delete=True)
        done.pruned = pruned.deleted
    older = sorted(
        (path for path in folder.glob(WORKBOOKS) if path != done.workbook),
        key=lambda path: path.name,
        reverse=True,
    )
    for path in older[max(keep - 1, 0) :]:
        path.unlink()
        done.removed.append(path)
    return done


def _report(done: BackupRun) -> None:
    """Print what the run did; each problem on a line of its own."""
    print(f"wrote {done.workbook} ({done.rows:,} rows)")
    print(f"photographs: {done.copied:,} copied, {done.present:,} already there")
    if not done.proved:
        for problem in done.problems:
            print(f"  {problem}", file=sys.stderr)
        print(
            f"NOT PROVED: {len(done.problems)} problem(s). Nothing was removed; "
            "the workbooks already there are untouched.",
            file=sys.stderr,
        )
        return
    print("proved: the workbook restores to exactly what live holds,")
    print("        and every photograph is in the copy and matches its row")
    print(f"pruned {done.pruned:,} obsolete photograph file(s)")
    for path in done.removed:
        print(f"removed the older workbook {path.name}")


def main(argv: Sequence[str] | None = None, *, live: Engine | None = None) -> int:
    """Run one backup into a folder; exit 1 when it could not be proved.

    `live` is the database to back up; run as a module it is the
    application's own. A caller that already has one -- the tests, which
    must never let this open the live database -- passes it.
    """
    parser = argparse.ArgumentParser(prog="backup_run", description=__doc__)
    parser.add_argument("folder", type=Path, help="where the backup is kept")
    parser.add_argument(
        "--keep", type=int, default=1, help="workbooks to keep (default 1)"
    )
    args = parser.parse_args(argv)
    if args.keep < 1:
        parser.error("--keep must be at least 1: the new workbook is one of them")

    engine = live or create_engine(settings.database_url)
    try:
        done = run(engine, args.folder, keep=args.keep)
    finally:
        if live is None:
            engine.dispose()
    _report(done)
    return 0 if done.proved else 1


if __name__ == "__main__":
    sys.exit(main())
