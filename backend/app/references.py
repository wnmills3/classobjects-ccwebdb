"""Resolving classifier codes supplied by API clients.

Classifiers cross the API as codes because ids are per-installation and codes
are the stable contract. Two rules make that safe:

**An unknown code is an error, not a null.** Silently storing NULL for a code
the caller believed in produces an item that is quietly unclassified, and the
caller is never told. It raises 422 naming the field and the value.

*Unknown* here includes *retired*, and the message says "Unknown" either way.
That is a known gap rather than a decision: `routers.reference.get_table`
deliberately serves retired values so a form can render an old record
(`include_inactive`), but saving that record back resolves the same code
through `code_to_id`, which filters on `is_active` and refuses it. Nothing is
retired today, so nothing hits it; the first classifier retired while still
in use will make every save of an affected item fail with a message saying
its own rendered value does not exist. Fixing it needs a rule this codebase
has not chosen -- most likely "a code already stored on this row stays
acceptable, a new one does not" -- which is a call for the owner, not a
default to slip in here.

**The API never creates classifier rows.** The importer may add `derived` rows
because it is reconciling a real collection against an incomplete vocabulary;
a web request has no such standing, and letting one invent classifiers is how
a reference table fills up with typos.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ReferenceMixin

__all__ = ["code_to_id", "id_to_code", "require_code"]


def code_to_id(
    db: Session, model: type[ReferenceMixin], code: str | None, field: str
) -> int | None:
    """Resolve a classifier code, or None when the caller supplied none.

    `None` means only "no code was supplied". A code that does not resolve --
    including one that exists but is retired -- raises 422 rather than
    returning None, so a caller must not read None as "not found".
    """
    if code is None or code == "":
        return None
    found = db.execute(
        select(model.id).where(model.code == code, model.is_active.is_(True))
    ).scalar_one_or_none()
    if found is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown {field}: {code!r}",
        )
    return found


def require_code(
    db: Session, model: type[ReferenceMixin], code: str, field: str
) -> int:
    """Same, for a classifier the schema cannot do without."""
    resolved = code_to_id(db, model, code, field)
    if resolved is None:
        raise HTTPException(
            status_code=422,
            detail=f"{field} is required",
        )
    return resolved


def id_to_code(row: object | None) -> str | None:
    """The code of a related reference row, for building a response."""
    return getattr(row, "code", None) if row is not None else None
