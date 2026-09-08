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

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, or_, select
from sqlalchemy.exc import IntegrityError

from ..deps import AdminUser, DbSession
from ..inventory_search import plain
from ..models import REFERENCE_MODELS, ProvenanceSource, ReferenceMixin
from ..schemas import (
    ReferenceTableOut,
    ReferenceValueCreate,
    ReferenceValueOut,
    ReferenceValueRename,
)

router = APIRouter(prefix="/reference", tags=["reference"])

#: Columns every reference table shares. Anything else a table carries is
#: specific to it and goes into `extra`.
_COMMON = frozenset({"id", "code", "label", "sort_order", "is_active", "source"})

TABLES: dict[str, type[ReferenceMixin]] = {
    model.__tablename__: model for model in REFERENCE_MODELS
}


def _to_value(row: object, model: type[ReferenceMixin]) -> ReferenceValueOut:
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
        extra[column.name] = plain(getattr(row, column.name))

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
    year: Annotated[
        int | None,
        Query(
            description="Only values whose term covers this year, where a table has one"
        ),
    ] = None,
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
    if year is not None:
        stmt = _limit_to_year(model, stmt, year)
    rows = db.scalars(stmt.order_by(model.sort_order, model.code)).all()

    return ReferenceTableOut(
        table=table,
        values=[_to_value(row, model) for row in rows],
    )


def _limit_to_year(model: type, stmt: Select[Any], year: int) -> Select[Any]:
    """Narrow a term-bounded vocabulary to the values valid in a given year.

    Signature combinations are the case this exists for. A note's series year
    decides which Treasurer and Secretary can possibly appear on it, so a
    picker offering all eleven invites the wrong one to be chosen -- and the
    signatures are what separate one catalogue variant from another, so a
    wrong one is a wrong lookup.

    A table without a term is returned unfiltered rather than empty: the
    parameter is a narrowing where one is possible, not a requirement.
    """
    columns = model.__table__.columns
    if "term_from" not in columns or "term_to" not in columns:
        return stmt
    return stmt.where(
        or_(columns["term_from"].is_(None), columns["term_from"] <= year),
        or_(columns["term_to"].is_(None), columns["term_to"] >= year),
    )


def _model_or_404(table: str) -> type:
    model = TABLES.get(table)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown reference table: {table!r}",
        )
    return model


@router.post(
    "/{table}", response_model=ReferenceValueOut, status_code=status.HTTP_201_CREATED
)
def create_value(
    table: str, payload: ReferenceValueCreate, db: DbSession, _admin: AdminUser
) -> ReferenceValueOut:
    """Add a value to a vocabulary, so the tables grow with use.

    An operator entering an item that does not fit the shipped vocabulary adds
    the missing value here rather than abandoning the entry or forcing it into
    an approximate one -- which is what actually happens otherwise, and it is
    invisible afterwards.

    Marked `manual`, so one installation's additions stay distinguishable from
    the shipped catalogue and do not leave in an export unless asked for.

    Staff only. The *importer* may also invent values, because it is
    reconciling a real collection against an incomplete vocabulary; an
    anonymous request has no such standing.
    """
    model = _model_or_404(table)

    if db.scalar(select(model).where(model.code == payload.code)) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{table} already has a value with code {payload.code!r}",
        )

    known = {c.name for c in model.__table__.columns}
    unknown = set(payload.extra) - known
    if unknown:
        raise HTTPException(
            status_code=422,
            detail=f"{table} has no column(s) {sorted(unknown)}. "
            f"Available: {sorted(known - {'id'})}",
        )

    row = model(
        code=payload.code,
        label=payload.label,
        sort_order=payload.sort_order,
        source=ProvenanceSource.manual,
        **payload.extra,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Most often a NOT NULL column this table needs and the caller did not
        # supply -- a denomination without a currency, say.
        raise HTTPException(
            status_code=422,
            detail=f"{table} rejected the value: {exc.orig}",
        ) from exc

    db.commit()
    db.refresh(row)
    return _to_value(row, model)


@router.patch("/{table}/{code}", response_model=ReferenceValueOut)
def rename_value(
    table: str,
    code: str,
    payload: ReferenceValueRename,
    db: DbSession,
    _admin: AdminUser,
) -> ReferenceValueOut:
    """Change what a value is called.

    The label only. The code is the contract -- it appears in saved filters,
    bookmarked searches and any integration -- so it does not change, and
    renaming the label is precisely what lets a poorly worded one be fixed
    without breaking those.

    **Nothing needs migrating.** Every record refers to the value by foreign
    key, so the new wording is live everywhere the moment this commits. That is
    the payoff for keeping one copy of it.
    """
    model = _model_or_404(table)
    row = db.scalar(select(model).where(model.code == code))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{table} has no value with code {code!r}",
        )

    row.label = payload.label
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if payload.is_active is not None:
        # Retiring a value keeps existing records valid while removing it from
        # the pickers -- which is what you want, since deleting it is refused
        # by the foreign keys anyway.
        row.is_active = payload.is_active

    db.commit()
    db.refresh(row)
    return _to_value(row, model)
