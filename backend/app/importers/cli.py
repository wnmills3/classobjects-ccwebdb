"""Import CLI.

    :: dry run, prints a report -- no database needed
    uv run python -m app.importers.cli --file <path.xlsx>

    :: dry run, also writes review files you can open in a spreadsheet
    uv run python -m app.importers.cli --file <path.xlsx> --out-dir review

    :: write staging rows to the database
    uv run python -m app.importers.cli --file <path.xlsx> --commit

Dry run is the default and touches no database, so the review files can be
produced with nothing running.
"""

from __future__ import annotations

import argparse
import sys

from . import reporting
from .engine import COMMIT, DRY_RUN, ImportEngine
from .profiles.collection_v1 import CollectionV1Profile
from .sources import XlsxSource

PROFILES = {"collection_v1": CollectionV1Profile}


def main(argv: list[str] | None = None) -> int:
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
        help="write issues.csv, corrections.csv, unclassified.csv, "
        "summary.json and report.txt here",
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

    if args.out_dir:
        paths = reporting.write_all(report, args.out_dir)
        print("")
        print("review files written:")
        for label, path in paths.items():
            print(f"  {label:<13} {path}")

    # Non-zero when anything is unusable, so a pipeline can gate on it.
    return 1 if report.issues_by_severity.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
