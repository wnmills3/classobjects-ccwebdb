"""Import CLI.

    uv run python -m app.importers.cli --file <path>              dry run
    uv run python -m app.importers.cli --file <path> --commit     write staging
    uv run python -m app.importers.cli --file <path> --limit 200  sample

Dry run is the default and touches no database.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

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
    parser.add_argument("--top", type=int, default=25, help="rows per report section")
    parser.add_argument("--json", dest="json_out", default=None, help="also write JSON")
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

    print(report.render(top=args.top))

    if args.json_out:
        payload = {
            "source_path": report.source_path,
            "sha256": report.sha256,
            "profile": report.profile_name,
            "mode": report.mode,
            "rows": report.rows,
            "classified_pct": round(report.classified_pct, 2),
            "kinds": dict(report.kinds),
            "subtypes": dict(report.subtypes),
            "issues_by_rule": dict(report.issues_by_rule),
            "issues_by_severity": dict(report.issues_by_severity),
            "review_rows": report.review_rows,
            "unclassified_values": dict(report.unclassified_values),
            "batch_id": report.batch_id,
        }
        Path(args.json_out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nJSON written to {args.json_out}")

    # Non-zero when anything is unusable, so a pipeline can gate on it.
    return 1 if report.issues_by_severity.get("error") else 0


if __name__ == "__main__":
    sys.exit(main())
