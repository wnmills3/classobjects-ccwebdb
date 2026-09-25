"""The classifier vocabularies, for populating dropdowns.

Classifiers cross the API as codes, which is right for a stable contract and
useless for a human filling in a form -- nobody knows that a Morgan dollar's
denomination is `usd_coin_1_00`. This endpoint is what turns those free-text
boxes into pickers.

Public, because the catalog's own filters need it and a vocabulary is not
data: knowing that `MS64` exists reveals nothing about what anyone owns.

Each table's extra columns come through in `extra` rather than being flattened,
so a client can show a denomination's face value or an error type's
`applies_to` without this module needing a branch per table. Each value's
aliases come with it, so a picker can find "Walker" and say what it is.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import Select, func, or_, select
from sqlalchemy.exc import IntegrityError

from .. import aliases, reference_merge, sale_state
from ..deps import AdminUser, DbSession
from ..inventory_search import plain
from ..models import REFERENCE_MODELS, InventoryItem, ProvenanceSource, ReferenceMixin
from ..schemas import (
    ReferenceAliasIn,
    ReferenceMergeIn,
    ReferenceMergeOut,
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

#: Vocabularies the application branches on value by value -- a status, a
#: kind, a strike -- and single values it looks up by code. Any of them may be
#: renamed, since a label is only what a person reads, but retiring one would
#: make the lookup fail: receiving, a pass or a sale would stop.
_CODE_KEYED_TABLES = frozenset(
    {
        "item_status",
        "disposition",
        "sales_order_status",
        "shipment_status",
        "strike_type",
        "item_kind",
        "grade_scale",
        "valuation_basis",
        "authenticity",
        "sales_venue_kind",
        # `sales_writes.record_sale` resolves every fee line's kind through
        # `require_code`, which filters on `is_active`: retiring `commission`
        # would make every sale charging one fail with 422 "Unknown fee",
        # naming a code the dialog itself had just offered.
        "sales_fee_kind",
    }
)
_CODE_KEYED_VALUES = frozenset(
    {
        ("vendor_kind", "unknown"),
        ("storage_form", "single"),
        ("currency", "USD"),
        ("country", "US"),
        ("note_type", "frn"),
        # `photo_names` turns a filename's sequence number into one of these
        # three codes and `photo_import` resolves each through `code_to_id`,
        # which refuses a retired value. Retiring one would fail every
        # import of a photograph named for it -- the other image roles are
        # descriptive and may be retired freely.
        ("image_role", "obverse"),
        ("image_role", "reverse"),
        ("image_role", "unassigned"),
    }
)

#: Vocabularies whose order is their meaning, so they keep `sort_order`.
#: Three reasons an entry belongs here: a scale (grade runs 70, 69+, 69, ...;
#: denomination runs face value ascending, coins then notes -- once a picker
#: filters by kind, a note's nine denominations read $1 -> $1000 in order), a
#: lifecycle (item_status, disposition, sales_order_status, shipment_status
#: each run ordered -> received -> ... or an equivalent progression), or a
#: curated sequence (item_kind is ranked by how often a kind occurs -- coin
#: and currency cover the whole collection, so they lead; signature_combination
#: is chronological, and its picker is narrowed to a stretch of that timeline
#: by a note's series year; `sales_fee_kind` is curated too: the migration
#: orders it commission, processing, listing, shipping label, promotion,
#: other, which is the order a person reads a platform's statement in and
#: puts the catch-all last. Alphabetical by label puts "Other" third, and
#: `docs/system-administration.md` already prints the curated order in
#: writing). Everything else is a descriptive list that is scanned by name,
#: and alphabetical is the only order a reader can predict.
_SEQUENCED_TABLES = frozenset(
    {
        "grade",
        "denomination",
        "item_status",
        "disposition",
        "sales_order_status",
        "shipment_status",
        "item_kind",
        "signature_combination",
        "sales_fee_kind",
    }
)


def retirable(table: str, code: str) -> bool:
    """Whether a value may be retired; see _CODE_KEYED_TABLES."""
    return table not in _CODE_KEYED_TABLES and (table, code) not in _CODE_KEYED_VALUES


def _to_value(
    row: ReferenceMixin,
    model: type[ReferenceMixin],
    names: list[str] | None = None,
    retired: list[str] | None = None,
) -> ReferenceValueOut:
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
        is_active=row.is_active,
        retirable=retirable(model.__tablename__, row.code),
        extra={k: v for k, v in extra.items() if v is not None},
        aliases=names or [],
        retired_aliases=retired or [],
    )


def _value_with_aliases(
    db: DbSession, row: ReferenceMixin, model: type[ReferenceMixin]
) -> ReferenceValueOut:
    """One value, with its current and retired aliases."""
    everything = aliases.aliases_by_row(db, model, include_retired=True).get(row.id, [])
    active = set(aliases.aliases_by_row(db, model).get(row.id, []))
    return _to_value(
        row,
        model,
        [a for a in everything if a in active],
        [a for a in everything if a not in active],
    )


@router.get("")
def list_tables() -> list[str]:
    """The vocabularies available, in dependency order."""
    return list(TABLES)


@router.get("/{table}")
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
    order = (
        (model.sort_order, model.code)
        if table in _SEQUENCED_TABLES
        else (func.lower(model.label), model.code)
    )
    rows = db.scalars(stmt.order_by(*order)).all()
    active = aliases.aliases_by_row(db, model)
    retired: dict[int, list[str]] = {}
    if include_inactive:
        for row_id, names in aliases.aliases_by_row(
            db, model, include_retired=True
        ).items():
            current = set(active.get(row_id, []))
            retired[row_id] = [n for n in names if n not in current]

    return ReferenceTableOut(
        table=table,
        values=[
            _to_value(row, model, active.get(row.id), retired.get(row.id))
            for row in rows
        ],
        sequenced=table in _SEQUENCED_TABLES,
    )


def _limit_to_year(
    model: type[ReferenceMixin], stmt: Select[Any], year: int
) -> Select[Any]:
    """Narrow a term-bounded vocabulary to the values in office in a given year.

    Answers "who held office in this year", and nothing more. It is **not**
    the pairs a note of that *series* can carry: a lettered series is printed
    later, under later officials (1963-A is Granahan / Fowler, in office from
    1965), so narrowing a note's signatures this way hides the right pair.
    The Friedberg lookup used it for that until 2026-09-23 and now asks
    `GET /friedberg/signatures`, which reads the `note_issue` facts.

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


def _model_or_404(table: str) -> type[ReferenceMixin]:
    model = TABLES.get(table)
    if model is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Unknown reference table: {table!r}",
        )
    return model


@router.post(
    "/{table}",
    status_code=status.HTTP_201_CREATED,
    responses={
        422: {
            "description": "Request validation failed, or the payload names a column "
            "the table does not have, or the row was rejected by a database "
            "constraint.",
            # Restates FastAPI's generated content. Passing only a description
            # replaces the whole 422 entry and drops the schema reference.
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/HTTPValidationError"}
                }
            },
        }
    },
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
    the shipped catalog and do not leave in an export unless asked for.

    Staff only: an anonymous request has no standing to extend a
    vocabulary.
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


@router.patch("/{table}/{code}")
def rename_value(
    table: str,
    code: str,
    payload: ReferenceValueRename,
    db: DbSession,
    _admin: AdminUser,
) -> ReferenceValueOut:
    """Change what a value is called, where it sorts, or retire it.

    The label only. The code is the contract -- it appears in saved filters,
    bookmarked searches and any integration -- so it does not change, and
    renaming the label is precisely what lets a poorly worded one be fixed
    without breaking those.

    A renamed or reordered value becomes `manual`, so the next seed load
    leaves it as the person set it rather than putting the shipped wording
    back. Retiring is refused for a value the application looks up by code
    (409); it may still be renamed.

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

    label = " ".join(payload.label.split())
    if not label:
        raise HTTPException(status_code=422, detail="A label needs some text.")
    if payload.is_active is False and not retirable(table, code):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{row.label} cannot be retired: the application looks it up "
            "by its code. It can still be renamed.",
        )

    if label != row.label or (
        payload.sort_order is not None and payload.sort_order != row.sort_order
    ):
        row.source = ProvenanceSource.manual
    row.label = label
    if payload.sort_order is not None:
        row.sort_order = payload.sort_order
    if payload.is_active is not None:
        # Retiring a value keeps existing records valid while removing it from
        # the pickers -- which is what you want, since deleting it is refused
        # by the foreign keys anyway.
        row.is_active = payload.is_active

    db.commit()
    db.refresh(row)
    return _value_with_aliases(db, row, model)


def _row_or_404(
    db: DbSession, table: str, code: str
) -> tuple[type[ReferenceMixin], ReferenceMixin]:
    model = _model_or_404(table)
    row = db.scalar(select(model).where(model.code == code))
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"{table} has no value with code {code!r}",
        )
    return model, row


@router.post("/{table}/{code}/aliases", status_code=status.HTTP_201_CREATED)
def add_alias(
    table: str,
    code: str,
    payload: ReferenceAliasIn,
    db: DbSession,
    _admin: AdminUser,
) -> ReferenceValueOut:
    """Give a value another name: what people write instead of its label.

    The standard term stays the label; the owner's word becomes an alias
    ("Ultra Cameo" for UCAM). Search and the pickers recognize it at once. A
    retired shipped alias is brought back rather than copied.

    Two values may share an alias ("Cartwheel" is any large silver dollar):
    search finds both, and `aliases.resolve`, which cannot choose, names
    neither.
    Refused when the alias is another value's own label or code, which would
    always win over it.
    """
    model, row = _row_or_404(db, table, code)
    try:
        aliases.add_alias(db, model, row.id, payload.alias)
    except aliases.AliasError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return _value_with_aliases(db, row, model)


@router.delete("/{table}/{code}/aliases")
def remove_alias(
    table: str,
    code: str,
    alias: Annotated[str, Query(min_length=1, max_length=64)],
    db: DbSession,
    _admin: AdminUser,
) -> ReferenceValueOut:
    """Take a name away from a value.

    A shipped alias is retired, not deleted, so the next seed load does not
    bring it back; one added here is deleted.
    """
    model, row = _row_or_404(db, table, code)
    try:
        aliases.remove_alias(db, model, row.id, alias)
    except aliases.AliasError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)
        ) from exc
    db.commit()
    return _value_with_aliases(db, row, model)


@router.post("/{table}/{code}/merge")
def merge_value(
    table: str,
    code: str,
    payload: ReferenceMergeIn,
    db: DbSession,
    admin: AdminUser,
) -> ReferenceMergeOut:
    """Merge a value into another: move its items, keep its names, delete it.

    `dry_run` reports what would move and changes nothing -- the console
    shows it before asking. Refused (409) for a value another vocabulary or
    a facts table uses, for one the application looks up by code, and for a
    retired target (app.reference_merge).

    Refused (409) again when any item it would move is for sale, until
    `acknowledge_for_sale` says the caller has seen that -- the dry run names
    those items in `for_sale`, so the console can ask before it comes to this.
    """
    model = _model_or_404(table)
    try:
        if payload.dry_run:
            result, _, _ = reference_merge.plan(db, model, code, payload.into)
        else:
            planned, _, _ = reference_merge.plan(db, model, code, payload.into)
            if planned.for_sale_count:
                for_sale_items = db.scalars(
                    select(InventoryItem).where(
                        InventoryItem.item_code.in_(planned.for_sale)
                    )
                ).all()
                sale_state.guard(
                    db,
                    list(for_sale_items),
                    acknowledged=payload.acknowledge_for_sale,
                )
            result = reference_merge.merge(
                db, model, code, payload.into, user_id=admin.id
            )
    except reference_merge.NoSuchValue as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except reference_merge.MergeError as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    if payload.dry_run:
        db.rollback()
    else:
        db.commit()
    return ReferenceMergeOut(
        table=result.table,
        code=result.code,
        into=result.into,
        dry_run=payload.dry_run,
        moved=result.moved,
        items=result.items,
        dropped=result.dropped,
        aliases=result.aliases,
        for_sale=result.for_sale,
        for_sale_count=result.for_sale_count,
    )
