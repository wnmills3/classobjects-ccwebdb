"""Shared column conventions for every model in the schema.

The rules encoded here come from `docs/database-design.md` section 11:
tables singular and snake_case, foreign keys `<table>_id`, booleans that read
as assertions, timestamps `*_at` and dates `*_on`.
"""

from __future__ import annotations

import enum
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Enum, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, declared_attr, mapped_column

from ..database import Base

__all__ = [
    "Base",
    "ProvenanceSource",
    "ReferenceMixin",
    "TimestampMixin",
    "enum_column",
    "utcnow",
]


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def enum_column(py_enum: type[enum.Enum], name: str) -> Enum:
    """Store enum *values* (not member names) in a native PostgreSQL enum.

    Without ``values_callable`` SQLAlchemy persists the member *name*, so a
    member whose name and value differ would round-trip incorrectly.
    """
    return Enum(
        py_enum,
        name=name,
        values_callable=lambda e: [member.value for member in e],
    )


class ProvenanceSource(str, enum.Enum):
    """How a row came to exist.

    A machine guess must never be indistinguishable from a curated fact, so
    every reference row and every resolved identifier records its provenance.
    """

    seeded = "seeded"
    derived = "derived"
    manual = "manual"


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )


class ReferenceMixin:
    """The shape every classifier table shares.

    Classifiers are foreign keys, never free text: anything used to search,
    filter, group or report gets a stable ``code`` and an editable ``label``.
    ``code`` is machine-facing and must not change once rows reference it;
    ``label`` is display text and may be reworded freely.
    """

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    source: Mapped[ProvenanceSource] = mapped_column(
        enum_column(ProvenanceSource, "provenance_source"),
        default=ProvenanceSource.seeded,
        nullable=False,
    )

    @declared_attr.directive
    def __table_args__(cls) -> tuple:  # noqa: N805
        return (UniqueConstraint("code", name=f"uq_{cls.__tablename__}_code"),)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.code}>"
