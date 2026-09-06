"""The import engine.

DURABLE. It knows how to read a source into staging, hand each row to a
profile, and account for what came back. It knows nothing about coins.

Dry-run touches no database at all, so a profile's rules can be iterated on
without any infrastructure standing up.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from sqlalchemy.orm import Session

from .models import ImportBatch, ImportIssue, ImportRow
from .profile import ERROR, ImportProfile, RawRow, UNKNOWN

DRY_RUN = "dry_run"
COMMIT = "commit"


class Source(Protocol):
    kind: str
    sha256: str

    def read_rows(self, limit: int | None = None) -> Iterator[RawRow]: ...


@dataclass
class ImportReport:
    source_path: str
    source_kind: str
    sha256: str
    profile_name: str
    mode: str
    rows: int = 0
    kinds: Counter = field(default_factory=Counter)
    subtypes: Counter = field(default_factory=Counter)
    issues_by_rule: Counter = field(default_factory=Counter)
    issues_by_severity: Counter = field(default_factory=Counter)
    review_rows: int = 0
    #: distinct unclassified values -- reviewing these is far cheaper than rows
    unclassified_values: Counter = field(default_factory=Counter)
    batch_id: int | None = None
    elapsed_seconds: float = 0.0

    @property
    def classified(self) -> int:
        return self.rows - self.kinds.get(UNKNOWN, 0)

    @property
    def classified_pct(self) -> float:
        return (self.classified / self.rows * 100) if self.rows else 0.0

    def render(self, top: int = 25) -> str:
        out: list[str] = []
        add = out.append
        add(f"source   : {self.source_path}")
        add(f"sha256   : {self.sha256[:16]}...")
        add(f"profile  : {self.profile_name}")
        add(f"mode     : {self.mode}" + (f"  (batch {self.batch_id})" if self.batch_id else ""))
        add(f"rows     : {self.rows}   in {self.elapsed_seconds:.2f}s")
        add("")
        add(f"KIND  ({self.classified}/{self.rows} = {self.classified_pct:.1f}% classified)")
        for kind, n in self.kinds.most_common():
            add(f"  {n:>6}  {n / self.rows * 100:5.1f}%  {kind}")
        if self.subtypes:
            add("")
            add("SUBTYPE")
            for sub, n in self.subtypes.most_common(top):
                add(f"  {n:>6}  {sub}")
        add("")
        add(f"ISSUES  ({sum(self.issues_by_severity.values())} across {self.review_rows} rows needing review)")
        for sev in ("error", "warning", "info"):
            if self.issues_by_severity.get(sev):
                add(f"  {self.issues_by_severity[sev]:>6}  {sev}")
        if self.issues_by_rule:
            add("")
            add("BY RULE")
            for rule, n in self.issues_by_rule.most_common(top):
                add(f"  {n:>6}  {rule}")
        if self.unclassified_values:
            add("")
            add(
                f"UNCLASSIFIED VALUES  ({len(self.unclassified_values)} distinct, "
                f"{sum(self.unclassified_values.values())} rows)"
            )
            for value, n in self.unclassified_values.most_common(top):
                add(f"  {n:>6}  {value!r}")
        return "\n".join(out)


class ImportEngine:
    def __init__(self, profile: ImportProfile, session: Session | None = None) -> None:
        self.profile = profile
        self.session = session

    def run(
        self,
        source: Source,
        *,
        mode: str = DRY_RUN,
        limit: int | None = None,
    ) -> ImportReport:
        if mode == COMMIT and self.session is None:
            raise ValueError("commit mode requires a database session")

        started = datetime.now(timezone.utc)
        report = ImportReport(
            source_path=str(getattr(source, "path", "<source>")),
            source_kind=source.kind,
            sha256=source.sha256,
            profile_name=self.profile.name,
            mode=mode,
        )

        batch: ImportBatch | None = None
        if mode == COMMIT:
            batch = ImportBatch(
                source_path=report.source_path,
                source_kind=source.kind,
                sha256=source.sha256,
                profile_name=self.profile.name,
                mode=mode,
                started_at=started,
            )
            self.session.add(batch)
            self.session.flush()  # assign batch.id
            report.batch_id = batch.id

        pending: list[ImportRow] = []
        for raw_row in source.read_rows(limit=limit):
            result = self.profile.inspect(raw_row)
            report.rows += 1
            report.kinds[result.classification.kind] += 1
            if result.classification.subtype:
                report.subtypes[
                    f"{result.classification.kind}/{result.classification.subtype}"
                ] += 1
            for issue in result.issues:
                report.issues_by_rule[issue.rule] += 1
                report.issues_by_severity[issue.severity] += 1
            if result.needs_review:
                report.review_rows += 1
            if result.classification.kind == UNKNOWN:
                # the value the profile could not place, for distinct-value review
                marker = next(
                    (i.raw_value for i in result.issues if i.rule == "unclassified"),
                    None,
                )
                report.unclassified_values[marker if marker is not None else "<blank>"] += 1

            if mode == COMMIT:
                row = ImportRow(
                    batch_id=batch.id,
                    row_number=raw_row.row_number,
                    raw=raw_row.values,
                    status="needs_review" if result.needs_review else "classified",
                    item_kind=result.classification.kind,
                    subtype=result.classification.subtype,
                    classified_by_rule=result.classification.rule,
                )
                row.issues = [
                    ImportIssue(
                        rule=i.rule,
                        severity=i.severity,
                        column_name=i.column,
                        raw_value=(i.raw_value or "")[:512] or None,
                        proposed=(i.proposed or "")[:512] or None,
                        note=i.note,
                    )
                    for i in result.issues
                ]
                pending.append(row)
                if len(pending) >= 500:
                    self.session.add_all(pending)
                    self.session.flush()
                    pending.clear()

        if mode == COMMIT:
            if pending:
                self.session.add_all(pending)
            batch.row_count = report.rows
            batch.finished_at = datetime.now(timezone.utc)
            self.session.commit()

        report.elapsed_seconds = (
            datetime.now(timezone.utc) - started
        ).total_seconds()
        return report
