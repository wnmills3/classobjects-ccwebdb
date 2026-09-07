"""The import engine.

DURABLE. It knows how to read a source into staging, hand each row to a
profile, and account for what came back. It knows nothing about coins.

Dry-run touches no database at all, so a profile's rules can be iterated on
without any infrastructure standing up, and the exception report can be
produced and reviewed with nothing running.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.orm import Session

from .loader import SchemaLoader
from .models import ImportBatch, ImportIssue, ImportRow
from .profile import UNKNOWN, ImportProfile, RawRow

DRY_RUN = "dry_run"
COMMIT = "commit"

#: Safety valve: a pathological source should not exhaust memory building a
#: report nobody could read anyway.
MAX_RETAINED_ISSUES = 100_000
MAX_DISTINCT_PER_COLUMN = 50_000


class Source(Protocol):
    """Anything the engine can read rows from."""

    kind: str
    sha256: str

    def read_rows(self, limit: int | None = None) -> Iterator[RawRow]:
        """Yield rows from the source, at most `limit` of them."""
        ...


@dataclass
class IssueRecord:
    """One issue, with enough context to act on it in the source file.

    `row_number` is the row as a spreadsheet shows it (header is row 1), so it
    can be typed straight into a Go To dialog.
    """

    row_number: int
    kind: str
    rule: str
    severity: str
    column: str | None
    raw_value: str | None
    proposed: str | None
    note: str | None
    row_values: dict[str, str | None] = field(default_factory=dict)


@dataclass
class ImportReport:
    """Everything one run observed, whether or not it wrote anything."""

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
    #: value -> the source rows carrying it, so a fix can be made at source
    unclassified_rows: dict[str, list[int]] = field(
        default_factory=lambda: defaultdict(list)
    )
    #: every issue, with its full source row
    issues: list[IssueRecord] = field(default_factory=list)
    #: source column order, for stable report output
    columns: list[str] = field(default_factory=list)
    #: column -> value frequencies, for column profiling
    column_values: dict[str, Counter] = field(
        default_factory=lambda: defaultdict(Counter)
    )
    batch_id: int | None = None
    #: inventory_item rows written by normalisation (0 in dry-run)
    items_created: int = 0
    #: reference rows the loader had to invent, by table. A large number
    #: here means the seeded vocabulary is missing something real.
    derived_reference_rows: dict[str, int] = field(default_factory=dict)
    elapsed_seconds: float = 0.0

    @property
    def classified(self) -> int:
        """Rows the profile could put a kind to."""
        return self.rows - self.kinds.get(UNKNOWN, 0)

    @property
    def classified_pct(self) -> float:
        """Classified rows as a percentage of all rows."""
        return (self.classified / self.rows * 100) if self.rows else 0.0

    def corrections(self) -> list[IssueRecord]:
        """Issues that name a concrete fix -- typos and the like."""
        return [i for i in self.issues if i.proposed]

    def render(self, top: int = 25, example_rows: int = 6) -> str:
        """The run as a report a human can act on, source row numbers included."""
        out: list[str] = []
        add = out.append
        add(f"source   : {self.source_path}")
        add(f"sha256   : {self.sha256[:16]}...")
        add(f"profile  : {self.profile_name}")
        add(
            f"mode     : {self.mode}"
            + (f"  (batch {self.batch_id})" if self.batch_id else "")
        )
        add(f"rows     : {self.rows}   in {self.elapsed_seconds:.2f}s")
        if self.items_created:
            add(f"items    : {self.items_created} inventory_item rows written")
        if self.derived_reference_rows:
            invented = ", ".join(
                f"{t} {n}" for t, n in sorted(self.derived_reference_rows.items())
            )
            add(f"derived  : {invented}")
        add("")
        add(
            f"KIND  ({self.classified}/{self.rows} = "
            f"{self.classified_pct:.1f}% classified)"
        )
        for kind, n in self.kinds.most_common():
            add(f"  {n:>6}  {n / self.rows * 100:5.1f}%  {kind}")
        if self.subtypes:
            add("")
            add("SUBTYPE")
            for sub, n in self.subtypes.most_common(top):
                add(f"  {n:>6}  {sub}")

        add("")
        add(
            f"ISSUES  ({sum(self.issues_by_severity.values())} across "
            f"{self.review_rows} rows needing review)"
        )
        for sev in ("error", "warning", "info"):
            if self.issues_by_severity.get(sev):
                add(f"  {self.issues_by_severity[sev]:>6}  {sev}")
        if self.issues_by_rule:
            add("")
            add("BY RULE")
            for rule, n in self.issues_by_rule.most_common(top):
                add(f"  {n:>6}  {rule}")

        # Corrections are the most actionable thing here: a known typo and the
        # exact rows to fix it in.
        fixes = self.corrections()
        if fixes:
            grouped: dict[tuple[str, str], list[int]] = defaultdict(list)
            for rec in fixes:
                grouped[(rec.raw_value or "", rec.proposed or "")].append(
                    rec.row_number
                )
            add("")
            add(f"SUGGESTED SOURCE CORRECTIONS  ({len(grouped)} distinct)")
            for (raw, proposed), rows in sorted(
                grouped.items(), key=lambda kv: -len(kv[1])
            )[:top]:
                shown = ", ".join(str(r) for r in rows[:example_rows])
                more = (
                    f" (+{len(rows) - example_rows} more)"
                    if len(rows) > example_rows
                    else ""
                )
                add(f"  {len(rows):>4}  {raw!r} -> {proposed!r}")
                add(f"        rows {shown}{more}")

        if self.unclassified_values:
            add("")
            add(
                f"UNCLASSIFIED VALUES  ({len(self.unclassified_values)} distinct, "
                f"{sum(self.unclassified_values.values())} rows)"
            )
            for value, n in self.unclassified_values.most_common(top):
                rows = self.unclassified_rows.get(value, [])
                shown = ", ".join(str(r) for r in rows[:example_rows])
                more = (
                    f" (+{len(rows) - example_rows} more)"
                    if len(rows) > example_rows
                    else ""
                )
                add(f"  {n:>6}  {value!r}")
                if shown:
                    add(f"          rows {shown}{more}")
        return "\n".join(out)


class ImportEngine:
    """Reads a source into staging and hands each row to a profile."""

    def __init__(self, profile: ImportProfile, session: Session | None = None) -> None:
        """A session is required only to commit; a dry run needs none."""
        self.profile = profile
        self.session = session

    def run(
        self,
        source: Source,
        *,
        mode: str = DRY_RUN,
        limit: int | None = None,
        normalise: bool = True,
    ) -> ImportReport:
        """Read the source, classify every row, and report what was found."""
        if mode == COMMIT and self.session is None:
            raise ValueError("commit mode requires a database session")
        # Bound once, so the rest of the method works with a plain Session
        # rather than re-narrowing an Optional at every use.
        session = self.session

        started = datetime.now(UTC)
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
            assert session is not None
            session.add(batch)
            session.flush()  # assign batch.id
            report.batch_id = batch.id

        # One loader per run: it caches every reference lookup, which turns
        # a per-row query storm into a few dozen queries for the whole file.
        loader = SchemaLoader(self.session) if mode == COMMIT and normalise else None

        seen_columns: list[str] = []
        pending: list[ImportRow] = []

        for raw_row in source.read_rows(limit=limit):
            result = self.profile.inspect(raw_row)
            report.rows += 1

            for column, value in raw_row.values.items():
                if column not in seen_columns:
                    seen_columns.append(column)
                counts = report.column_values[column]
                # bounded: a pathological column must not exhaust memory
                if value is not None and (
                    len(counts) < MAX_DISTINCT_PER_COLUMN or value in counts
                ):
                    counts[value] += 1

            kind = result.classification.kind
            report.kinds[kind] += 1
            if result.classification.subtype:
                report.subtypes[f"{kind}/{result.classification.subtype}"] += 1

            for issue in result.issues:
                report.issues_by_rule[issue.rule] += 1
                report.issues_by_severity[issue.severity] += 1
                if len(report.issues) < MAX_RETAINED_ISSUES:
                    report.issues.append(
                        IssueRecord(
                            row_number=raw_row.row_number,
                            kind=kind,
                            rule=issue.rule,
                            severity=issue.severity,
                            column=issue.column,
                            raw_value=issue.raw_value,
                            proposed=issue.proposed,
                            note=issue.note,
                            row_values=dict(raw_row.values),
                        )
                    )

            if result.needs_review:
                report.review_rows += 1

            if kind == UNKNOWN:
                marker = (
                    next(
                        (
                            i.raw_value
                            for i in result.issues
                            if i.rule == "unclassified"
                        ),
                        None,
                    )
                    or "<blank>"
                )
                report.unclassified_values[marker] += 1
                report.unclassified_rows[marker].append(raw_row.row_number)

            if mode == COMMIT:
                row = ImportRow(
                    batch_id=batch.id,
                    row_number=raw_row.row_number,
                    raw=raw_row.values,
                    status="needs_review" if result.needs_review else "classified",
                    item_kind=kind,
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
                    session.add_all(pending)
                    session.flush()
                    pending.clear()

                # Staging is verbatim and always written. Normalising into the
                # target schema is separate and skippable, so a batch can be
                # captured for review before anything is interpreted.
                if loader is not None:
                    item = loader.load(
                        kind, result.classification.subtype, result.fields
                    )
                    row.inventory_item_id = item.id
                    row.status = "needs_review" if result.needs_review else "imported"
                    report.items_created += 1

        if mode == COMMIT:
            if pending:
                session.add_all(pending)
            batch.row_count = report.rows
            batch.finished_at = datetime.now(UTC)
            if loader is not None:
                report.derived_reference_rows = dict(loader.derived)
            session.commit()

        report.columns = seen_columns
        report.elapsed_seconds = (datetime.now(UTC) - started).total_seconds()
        return report
