r"""Import CLI.

    :: dry run -- no database needed. Writes review files to logs\\import\\.
    uv run python -m app.importers.cli --file <path.xlsx>

    :: somewhere else
    uv run python -m app.importers.cli --file <path.xlsx> --out-dir <dir>

    :: console report only
    uv run python -m app.importers.cli --file <path.xlsx> --no-files

    :: write staging rows to the database
    uv run python -m app.importers.cli --file <path.xlsx> --commit

Dry run is the default and touches no database, so the review files can be
produced with nothing running.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..config import REPO_ROOT
from . import reporting
from .engine import COMMIT, DRY_RUN, ImportEngine
from .profiles.collection_v1 import CollectionV1Profile
from .sources import XlsxSource

PROFILES = {"collection_v1": CollectionV1Profile}

#: Inside the project and gitignored, so review output never lands somewhere
#: surprising and never reaches version control.
DEFAULT_OUT_DIR = REPO_ROOT / "logs" / "import"


def main(argv: list[str] | None = None) -> int:
    """Run an import from the command line. Dry-run unless --commit is given."""
    parser = argparse.ArgumentParser(prog="import", description=__doc__)
    parser.add_argument("--file", required=True, help="source .xlsx")
    parser.add_argument("--sheet", default=None, help="worksheet name")
    parser.add_argument("--profile", default="collection_v1", choices=sorted(PROFILES))
    parser.add_argument("--commit", action="store_true", help="write staging rows")
    parser.add_argument("--limit", type=int, default=None, help="only N rows")
    parser.add_argument("--top", type=int, default=25, help="entries per section")
    parser.add_argument(
        "--out-dir",
        default=None,
        help=f"where to write review files (default: {DEFAULT_OUT_DIR})",
    )
    parser.add_argument(
        "--no-files", action="store_true", help="console report only, write nothing"
    )
    parser.add_argument("--quiet", action="store_true", help="suppress the report")
    args = parser.parse_args(argv)

    source = XlsxSource(args.file, sheet=args.sheet)
    profile = PROFILES[args.profile]()

    session = None
    if args.commit:
        from ..database import SessionLocal

        session = SessionLocal()

    try:
        engine = ImportEngine(profile, session=session)
        report = engine.run(
            source, mode=COMMIT if args.commit else DRY_RUN, limit=args.limit
        )
    finally:
        if session is not None:
            session.close()

    if not args.quiet:
        print(report.render(top=args.top))

    if not args.no_files:
        out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
        paths = reporting.write_all(
            report, out_dir, accepted=getattr(profile, "known_good", None)
        )
        print("")
        print("review files written:")
        for label, path in paths.items():
            try:
                shown = path.relative_to(REPO_ROOT)
            except ValueError:
                shown = path
            print(f"  {label:<13} {shown}")

    # Non-zero when anything is unusable, so a pipeline can gate on it.
    return 1 if report.issues_by_severity.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
