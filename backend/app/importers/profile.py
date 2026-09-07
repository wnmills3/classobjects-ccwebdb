"""The contract every import profile satisfies.

DURABLE. The engine depends on this and on nothing source-specific.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

# Issue severities
INFO = "info"  # a rule fired and did something worth recording
WARNING = "warning"  # a value was ambiguous; a guess was made
ERROR = "error"  # unusable or irrecoverable; needs a human

UNKNOWN = "unknown"


@dataclass(frozen=True)
class RawRow:
    """One source row, exactly as read. Values are text or None -- never coerced."""

    row_number: int
    values: dict[str, str | None]

    def get(self, column: str) -> str | None:
        """Case- and space-insensitive column lookup."""
        key = column.strip().casefold()
        for k, v in self.values.items():
            if k.strip().casefold() == key:
                return v
        return None

    def text(self, column: str) -> str:
        """The column's value as stripped text, '' when absent or blank."""
        v = self.get(column)
        return "" if v is None else str(v).strip()


@dataclass(frozen=True)
class Issue:
    """Something the rules could not decide, or decided with a caveat."""

    rule: str
    severity: str = WARNING
    column: str | None = None
    raw_value: str | None = None
    proposed: str | None = None
    note: str | None = None


@dataclass(frozen=True)
class Classification:
    """What kind of thing a row describes."""

    kind: str = UNKNOWN
    subtype: str | None = None
    #: the rule that decided it, for auditing the rule set
    rule: str | None = None


@dataclass
class RowResult:
    """What a profile made of one row: its classification, fields and issues."""

    classification: Classification = field(default_factory=Classification)
    issues: list[Issue] = field(default_factory=list)
    #: values the profile could parse out of the row. Kept loose on purpose --
    #: mapping these onto real columns belongs with the target schema, not here.
    fields: dict[str, object] = field(default_factory=dict)

    @property
    def needs_review(self) -> bool:
        """Whether anything about this row needs a human before it can be trusted."""
        return self.classification.kind == UNKNOWN or any(
            i.severity == ERROR for i in self.issues
        )


@runtime_checkable
class ImportProfile(Protocol):
    """Source-specific knowledge. Implementations are disposable."""

    name: str

    def inspect(self, row: RawRow) -> RowResult:
        """Classify the row and report anything ambiguous about it."""
        ...
