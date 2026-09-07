"""Staging tables for imports.

DURABLE. The two-stage pattern (verbatim staging -> normalisation -> review
queue) outlives any particular source, so these tables are part of the schema
rather than part of the throwaway importer.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..database import Base


def utcnow() -> datetime:
    """Now, in UTC. A function so it is evaluated per row, not at import."""
    return datetime.now(UTC)


class ImportBatch(Base):
    """One run of the importer against one source file."""

    __tablename__ = "import_batch"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    source_path: Mapped[str] = mapped_column(String(1024), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    # Content hash, so re-running against an unchanged file is recognisable.
    sha256: Mapped[str] = mapped_column(String(64), index=True, nullable=False)
    profile_name: Mapped[str] = mapped_column(String(64), nullable=False)
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    rows: Mapped[list[ImportRow]] = relationship(
        back_populates="batch", cascade="all, delete-orphan"
    )


class ImportRow(Base):
    """One source row, stored exactly as read.

    `raw` is JSONB rather than fixed columns so a source whose shape changes
    needs no migration, and the engine stays source-agnostic.
    """

    __tablename__ = "import_row"
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number", name="uq_import_row_batch_number"),
        Index("ix_import_row_status", "status"),
        Index("ix_import_row_kind", "item_kind"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    batch_id: Mapped[int] = mapped_column(
        ForeignKey("import_batch.id", ondelete="CASCADE"), index=True, nullable=False
    )
    row_number: Mapped[int] = mapped_column(Integer, nullable=False)
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)

    # pending | classified | needs_review | imported | rejected
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    item_kind: Mapped[str | None] = mapped_column(String(32), nullable=True)
    subtype: Mapped[str | None] = mapped_column(String(64), nullable=True)
    classified_by_rule: Mapped[str | None] = mapped_column(String(64), nullable=True)

    #: set once the row has been turned into a real record
    #: Set once the row has been normalised into the target schema. A real
    #: foreign key, not a loose number: staging that claims to point at an
    #: item which no longer exists is worse than staging that admits it.
    inventory_item_id: Mapped[int | None] = mapped_column(
        ForeignKey("inventory_item.id", ondelete="SET NULL"),
        index=True,
        nullable=True,
    )

    batch: Mapped[ImportBatch] = relationship(back_populates="rows")
    issues: Mapped[list[ImportIssue]] = relationship(
        back_populates="row", cascade="all, delete-orphan"
    )


class ImportIssue(Base):
    """Anything a rule could not decide, or decided with a caveat.

    This is the review queue. Reviewing *distinct values* rather than rows is
    what makes it tractable, so `raw_value` is indexed.
    """

    __tablename__ = "import_issue"
    __table_args__ = (
        Index("ix_import_issue_rule", "rule"),
        Index("ix_import_issue_severity", "severity"),
        Index("ix_import_issue_raw_value", "raw_value"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    import_row_id: Mapped[int] = mapped_column(
        ForeignKey("import_row.id", ondelete="CASCADE"), index=True, nullable=False
    )
    rule: Mapped[str] = mapped_column(String(64), nullable=False)
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    column_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_value: Mapped[str | None] = mapped_column(String(512), nullable=True)
    proposed: Mapped[str | None] = mapped_column(String(512), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)

    resolved_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    row: Mapped[ImportRow] = relationship(back_populates="issues")
