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
4. Only when all of that held: **remove the older workbooks**, keeping the
   newest `--keep` of them, and -- when the new one is the only one kept --
   **prune** the photograph files no image names any more.

When any step fails nothing is removed: the new workbook is left beside the
old ones, the reason is printed, and the command exits 1. So the folder
always holds at least one workbook that was proved.

**What `--keep` above 1 means.** The older workbooks kept are whole: the
photograph copy is not pruned, because a photograph removed or replaced
since they were written is still named by their rows, and pruning it would
leave them restoring rows with no picture. The copy then only grows; prune
it by hand (`python -m app.media_backup prune`) once the older workbooks
are no longer wanted. With the default of 1 the copy is pruned to what live
names today, so a photograph deleted from live is gone from the backup after
the next proved run.

**Only this command's own workbooks are removed**: a file named exactly
`ccwebdb_<8 digits>_<6 digits>.xlsx`. A workbook saved under any other name
-- a corrected copy, an export named by hand -- is never touched and takes
no place among those kept.

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
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.orm import Session

from . import media_backup, workbook_backup
from .config import REPO_ROOT, settings
from .storage import get_storage

__all__ = ["BackupRun", "main", "prove_workbook", "run"]

#: The name of a workbook this command wrote, and so may later remove: its
#: prefix and the time stamp `run` gives it, and nothing else. A name a
#: person chose begins the same way and must not match.
WORKBOOK = re.compile(r"ccwebdb_\d{8}_\d{6}\.xlsx")


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
    #: Why the photograph copy was left unpruned in a proved run, when it was.
    not_pruned: str | None = None

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
                        + _rows(difference.left_rows, "rows live", "not in live")
                        + ", "
                        + _rows(difference.right_rows, "restored", "not restored")
                    )
            finally:
                scratch.dispose()
        except workbook_backup.WorkbookError as exc:
            problems.append(f"the workbook could not be restored: {exc}")
        finally:
            with server.connect() as conn:
                _drop_scratch(conn, name)
    finally:
        server.dispose()
    return problems


def _rows(count: int, counted: str, absent: str) -> str:
    """One side's row count for a table, or that the side has no such table."""
    return absent if count < 0 else f"{count:,} {counted}"


def _drop_scratch(conn: Connection, name: str) -> None:
    """Drop the scratch database, ending the client sessions on it first.

    Client sessions only, and not `DROP DATABASE ... WITH (FORCE)`: that
    form also signals an autovacuum worker that happens to be on the
    database, which the application's role may not do, and the refusal
    would take the proof's own result with it. A worker leaves by itself
    when the database is dropped. `conn` must be in autocommit mode.
    """
    conn.execute(
        text(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname = :name AND pid <> pg_backend_pid() "
            "AND backend_type = 'client backend'"
        ),
        {"name": name},
    )
    conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))


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

        if keep > 1:
            # The older workbooks kept still name the photographs they were
            # exported with; pruned to what live names today, they would
            # restore rows whose pictures are gone.
            done.not_pruned = (
                f"the older workbooks kept (--keep {keep}) still name them"
            )
        else:
            pruned = media_backup.prune_media(db, media, delete=True)
            done.pruned, done.not_pruned = pruned.deleted, pruned.refused
    older = sorted(
        (
            path
            for path in folder.iterdir()
            if path.is_file()
            and WORKBOOK.fullmatch(path.name)
            and path != done.workbook
        ),
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
    if done.not_pruned is None:
        print(f"pruned {done.pruned:,} obsolete photograph file(s)")
    else:
        print(f"photographs not pruned: {done.not_pruned}")
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
    except workbook_backup.WorkbookError as exc:
        # The export itself was refused: no workbook was written, so there
        # is nothing to prove and nothing older may go.
        print(
            f"NOT PROVED: the database could not be exported: {exc}. Nothing "
            "was removed; the workbooks already there are untouched.",
            file=sys.stderr,
        )
        return 1
    finally:
        if live is None:
            engine.dispose()
    _report(done)
    return 0 if done.proved else 1


if __name__ == "__main__":
    sys.exit(main())
