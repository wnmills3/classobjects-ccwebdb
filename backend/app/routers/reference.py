"""The classifier vocabularies, for populating dropdowns.

Classifiers cross the API as codes, which is right for a stable contract and
useless for a human filling in a form -- nobody knows that a Morgan dollar's
denomination is `usd_coin_1_00`. This endpoint is what turns those free-text
boxes into pickers.

Public, because the catalogue's own filters need it and a vocabulary is not
data: knowing that `MS64` exists reveals nothing about what anyone owns.

Each table's extra columns come through in `extra` rather than being flattened,
so a client can show a denomination's face value or an error type's
`applies_to` without this module needing a branch per table.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from ..deps import DbSession
from ..models import REFERENCE_MODELS
from ..schemas import ReferenceTableOut, ReferenceValueOut

router = APIRouter(prefix="/reference", tags=["reference"])

#: Columns every reference table shares. Anything else a table carries is
#: specific to it and goes into `extra`.
_COMMON = frozenset({"id", "code", "label", "sort_order", "is_active", "source"})

TABLES: dict[str, type] = {model.__tablename__: model for model in REFERENCE_MODELS}


def _plain(value: Any) -> Any:
    """JSON-safe, without letting a Decimal become a float on the way out."""
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if hasattr(value, "value"):  # an enum
        return value.value
    return value


def _to_value(row: Any, model: type) -> ReferenceValueOut:
    extra: dict[str, Any] = {}
    for column in model.__table__.columns:
        if column.name in _COMMON:
            continue
        # Foreign keys are resolved to the referenced row's code, so a client
        # never has to know an id -- the same rule the rest of the API follows.
        if column.foreign_keys and column.name.endswith("_id"):
            related = getattr(row, column.name[: -len("_id")], None)
            code = getattr(related, "code", None)
            if code is not None:
                extra[column.name[: -len("_id")]] = code
            continue
        extra[column.name] = _plain(getattr(row, column.name))

    return ReferenceValueOut(
        code=row.code,
        label=row.label,
        sort_order=row.sort_order,
        source=row.source.value,
        extra={k: v for k, v in extra.items() if v is not None},
    )


@router.get("", response_model=list[str])
def list_tables() -> list[str]:
    """The vocabularies available, in dependency order."""
    return list(TABLES)


@router.get("/{table}", response_model=ReferenceTableOut)
def get_table(
    table: str,
    db: DbSession,
    include_inactive: Annotated[
        bool, Query(description="Include retired values, for editing old records")
    ] = False,
) -> ReferenceTableOut:
    """One vocabulary, ordered as it should appear in a picker.

    Retired values are hidden by default but available on request: an old
    record may still reference a classifier that should no longer be offered
    for new ones, and the form still has to render it.
    """
    model = TABLES.get(table)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown reference table: {table!r}",
        )

    stmt = select(model)
    if not include_inactive:
        stmt = stmt.where(model.is_active.is_(True))
    rows = db.scalars(stmt.order_by(model.sort_order, model.code)).all()

    return ReferenceTableOut(
        table=table,
        values=[_to_value(row, model) for row in rows],
    )
