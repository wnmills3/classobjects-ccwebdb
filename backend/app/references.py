"""Resolving classifier codes supplied by API clients.

Classifiers cross the API as codes because ids are per-installation and codes
are the stable contract. Two rules make that safe:

**An unknown code is an error, not a null.** Silently storing NULL for a code
the caller believed in produces an item that is quietly unclassified, and the
caller is never told. It raises 422 naming the field and the value.

**A retired code is refused -- unless the row already holds it** (ruling
S5, 2026-09-22). `routers.reference.get_table` serves retired values so a
form can render an old record (`include_inactive`), and saving that record
back sends the same code. Refusing it would make every save of an affected
item fail once its classifier was retired, with a message saying its own
rendered value does not exist. So `keep` names the id the row holds now:
that value stays acceptable, and any *new* use of a retired value is still
refused, with "Retired" in the message rather than "Unknown".

**The API never creates classifier rows while resolving a code.** Letting a
request invent classifiers that way is how a reference table fills up with
typos.
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import ReferenceMixin

__all__ = ["code_of", "code_to_id", "id_to_code", "require_code"]


def code_to_id(
    db: Session,
    model: type[ReferenceMixin],
    code: str | None,
    field: str,
    *,
    keep: int | None = None,
) -> int | None:
    """Resolve a classifier code, or None when the caller supplied none.

    `None` means only "no code was supplied". A code that does not resolve
    raises 422 rather than returning None, so a caller must not read None as
    "not found". A retired code is refused too, unless it is the value the
    row already holds (`keep`, the row's current id for this field).
    """
    if code is None or code == "":
        return None
    found = db.execute(
        select(model.id, model.is_active).where(model.code == code)
    ).one_or_none()
    if found is None:
        raise HTTPException(status_code=422, detail=f"Unknown {field}: {code!r}")
    if not found.is_active and found.id != keep:
        raise HTTPException(
            status_code=422,
            detail=f"Retired {field}: {code!r} can no longer be chosen",
        )
    return int(found.id)


def require_code(
    db: Session,
    model: type[ReferenceMixin],
    code: str,
    field: str,
    *,
    keep: int | None = None,
) -> int:
    """Same, for a classifier the schema cannot do without."""
    resolved = code_to_id(db, model, code, field, keep=keep)
    if resolved is None:
        raise HTTPException(
            status_code=422,
            detail=f"{field} is required",
        )
    return resolved


def id_to_code(row: object | None) -> str | None:
    """The code of a related reference row, for building a response."""
    return getattr(row, "code", None) if row is not None else None


def code_of(db: Session, model: type[ReferenceMixin], row_id: int | None) -> str | None:
    """The code a classifier's foreign key resolves to, or None when unset.

    Looked up by id rather than through an ORM relationship, because not
    every classifier column has one -- `inventory_item.series_id` is set and
    read as a plain column with no `InventoryItem.series` relationship
    declared -- and one lookup path that works for all of them is simpler
    than two.
    """
    if row_id is None:
        return None
    row = db.get(model, row_id)
    return row.code if row is not None else None
