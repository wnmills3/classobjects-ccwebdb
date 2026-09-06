"""Writing an import report to files that can be reviewed without a database.

DURABLE. CSV is deliberate: these get opened in a spreadsheet next to the
source, so `row_number` can be typed straight into a Go To dialog.
"""

from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

from .engine import ImportReport

#: utf-8-sig so Excel detects the encoding instead of mangling non-ASCII.
ENCODING = "utf-8-sig"

ISSUE_FIELDS = [
    "row_number",
    "severity",
    "rule",
    "column",
    "raw_value",
    "suggested_fix",
    "note",
    "classified_kind",
]


def _writer(path: Path, fieldnames: list[str]):
    handle = path.open("w", newline="", encoding=ENCODING)
    # QUOTE_ALL keeps values that look like formulas or numbers intact.
    writer = csv.DictWriter(
        handle, fieldnames=fieldnames, quoting=csv.QUOTE_ALL, extrasaction="ignore"
    )
    writer.writeheader()
    return handle, writer


def write_issues_csv(report: ImportReport, path: str | Path) -> int:
    """One line per issue, with the whole source row alongside it."""
    path = Path(path)
    source_cols = [f"src::{c}" for c in report.columns]
    handle, writer = _writer(path, ISSUE_FIELDS + source_cols)
    try:
        for rec in sorted(report.issues, key=lambda r: (r.row_number, r.rule)):
            row = {
                "row_number": rec.row_number,
                "severity": rec.severity,
                "rule": rec.rule,
                "column": rec.column or "",
                "raw_value": rec.raw_value or "",
                "suggested_fix": rec.proposed or "",
                "note": rec.note or "",
                "classified_kind": rec.kind,
            }
            for col in report.columns:
                row[f"src::{col}"] = rec.row_values.get(col) or ""
            writer.writerow(row)
    finally:
        handle.close()
    return len(report.issues)


def write_corrections_csv(report: ImportReport, path: str | Path) -> int:
    """Distinct values with a concrete suggested fix, and where to fix them.

    This is the file to work from when correcting the source: one line per
    distinct typo, with every row number carrying it.
    """
    path = Path(path)
    grouped: dict[tuple[str, str, str], list[int]] = defaultdict(list)
    for rec in report.corrections():
        key = (rec.column or "", rec.raw_value or "", rec.proposed or "")
        grouped[key].append(rec.row_number)

    handle, writer = _writer(
        path, ["column", "raw_value", "suggested_fix", "row_count", "rows"]
    )
    try:
        for (column, raw, proposed), rows in sorted(
            grouped.items(), key=lambda kv: (-len(kv[1]), kv[0])
        ):
            writer.writerow(
                {
                    "column": column,
                    "raw_value": raw,
                    "suggested_fix": proposed,
                    "row_count": len(rows),
                    "rows": " ".join(str(r) for r in sorted(rows)),
                }
            )
    finally:
        handle.close()
    return len(grouped)


def write_unclassified_csv(report: ImportReport, path: str | Path) -> int:
    """Distinct values no rule could place, and the rows carrying them."""
    path = Path(path)
    handle, writer = _writer(path, ["raw_value", "row_count", "rows"])
    try:
        for value, count in report.unclassified_values.most_common():
            rows = sorted(report.unclassified_rows.get(value, []))
            writer.writerow(
                {
                    "raw_value": value,
                    "row_count": count,
                    "rows": " ".join(str(r) for r in rows),
                }
            )
    finally:
        handle.close()
    return len(report.unclassified_values)


def write_summary_json(report: ImportReport, path: str | Path) -> None:
    Path(path).write_text(
        json.dumps(
            {
                "source_path": report.source_path,
                "sha256": report.sha256,
                "profile": report.profile_name,
                "mode": report.mode,
                "rows": report.rows,
                "classified": report.classified,
                "classified_pct": round(report.classified_pct, 2),
                "review_rows": report.review_rows,
                "kinds": dict(report.kinds),
                "subtypes": dict(report.subtypes),
                "issues_by_rule": dict(report.issues_by_rule),
                "issues_by_severity": dict(report.issues_by_severity),
                "unclassified_values": dict(report.unclassified_values),
                "batch_id": report.batch_id,
                "elapsed_seconds": round(report.elapsed_seconds, 3),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def write_all(report: ImportReport, out_dir: str | Path) -> dict[str, Path]:
    """Write the full review set. Returns the paths written."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {
        "issues": out / "issues.csv",
        "corrections": out / "corrections.csv",
        "unclassified": out / "unclassified.csv",
        "summary": out / "summary.json",
        "report": out / "report.txt",
    }
    write_issues_csv(report, paths["issues"])
    write_corrections_csv(report, paths["corrections"])
    write_unclassified_csv(report, paths["unclassified"])
    write_summary_json(report, paths["summary"])
    paths["report"].write_text(report.render(top=200), encoding="utf-8")
    return paths
