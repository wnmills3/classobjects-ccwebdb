"""The owner's own Friedberg catalogue -- not a licensed dataset.

`CLAUDE.md` forbids shipping a publisher's arrangement: the Friedberg
catalogue's mapping of attributes to its own numbers is sold, not free to
redistribute. Nothing here seeds, fetches, or hardcodes that mapping. What
this module builds instead is a place for the owner to record numbers read
off their own notes and slabs (`POST /friedberg`), search that private
catalogue by what is visible on a note in hand (`GET /friedberg`), and attach
a match to an item (`POST /inventory/{item_id}/friedberg`).

See the `FriedbergNumber` docstring in `app.models.identification` for why the
identifying tuple is only partially unique, and why `verified_at` is what
turns a proposal into a fact the next lookup can trust.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..deps import AdminUser, DbSession
from ..models import (
    Denomination,
    FriedbergNumber,
    InventoryItem,
    NoteType,
    ProvenanceSource,
    ReferenceMixin,
    SealColor,
    SignatureCombination,
    utcnow,
)
from ..references import code_to_id
from ..schemas import (
    FriedbergAttachIn,
    FriedbergAttachOut,
    FriedbergNumberCreate,
    FriedbergNumberOut,
)

#: Endpoints 1 and 2 live under `/friedberg`; endpoint 3 attaches a result to
#: an item, which is inventory-shaped, so it gets its own router the same way
#: `acquisitions.py` splits purchase orders from storage locations.
friedberg_router = APIRouter(prefix="/friedberg", tags=["friedberg"])
item_router = APIRouter(prefix="/inventory", tags=["friedberg"])

#: Mirrors the `ck_currency_detail_friedberg_status` check constraint. Kept
#: here, not imported from the model, because a `CheckConstraint` string is
#: not something Python code can introspect.
FRIEDBERG_STATUSES = frozenset({"unknown", "proposed", "confirmed", "conflicting"})


def _resolved_code(
    db: Session, model: type[ReferenceMixin], row_id: int | None
) -> str | None:
    """The code of a classifier row referenced by id, for building a response."""
    if row_id is None:
        return None
    row = db.get(model, row_id)
    return row.code if row is not None else None


def _to_out(db: Session, row: FriedbergNumber) -> FriedbergNumberOut:
    return FriedbergNumberOut(
        id=row.id,
        fr_number=row.fr_number,
        note_type=_resolved_code(db, NoteType, row.note_type_id),
        denomination=_resolved_code(db, Denomination, row.denomination_id),
        series_year=row.series_year,
        series_letter=row.series_letter,
        seal_color=_resolved_code(db, SealColor, row.seal_color_id),
        signature_combination=_resolved_code(
            db, SignatureCombination, row.signature_combination_id
        ),
        district_letter=row.district_letter,
        size_class=row.size_class,
        description=row.description,
        source=row.source.value,
        verified=row.verified_at is not None,
        verified_at=row.verified_at,
    )


@friedberg_router.get("")
def search_friedberg(
    db: DbSession,
    _admin: AdminUser,
    note_type: Annotated[str | None, Query(description="A note_type code")] = None,
    denomination: Annotated[
        str | None, Query(description="A denomination code")
    ] = None,
    series_year: Annotated[int | None, Query()] = None,
    series_letter: Annotated[str | None, Query()] = None,
    seal_color: Annotated[str | None, Query(description="A seal_color code")] = None,
    signature_combination: Annotated[
        str | None, Query(description="A signature_combination code")
    ] = None,
    district_letter: Annotated[str | None, Query()] = None,
) -> list[FriedbergNumberOut]:
    """Search the owner's catalogue by what is visible on a note in hand.

    Every supplied filter narrows -- an unknown classifier code is a 422, the
    same rule the rest of the API follows, rather than a filter that is
    silently dropped and returns the whole catalogue looking like a match.

    A row matches a supplied filter when its value equals the filter or is
    NULL, so a half-known type (built from a note that did not show every
    feature) is still found by what it does know. That alone would let a row
    with *everything* NULL match any query at all, which would make the
    catalogue useless once it has a few dozen such rows in it -- so a second
    condition also applies: at least one supplied filter must actually equal
    the row's value, not merely find it NULL. A row is required to know
    *something* the query asked about, not just fail to contradict it.

    With no filters at all, both conditions are vacuous and every row comes
    back -- browsing the whole catalogue is a valid use of this endpoint too.
    """
    # Every filter contributes two conditions: `narrow` is NULL-tolerant (the
    # row's value must equal the filter or be unknown), `hits` is not (the
    # row's value must actually equal it). ANDing every `narrow` condition
    # implements the stated matching rule; ORing every `hits` condition on
    # top is what stops a completely unknown row from matching regardless of
    # what was asked -- see the docstring.
    narrow: list[ColumnElement[bool]] = []
    hits: list[ColumnElement[bool]] = []

    note_type_id = code_to_id(db, NoteType, note_type, "note_type")
    if note_type_id is not None:
        narrow.append(
            or_(
                FriedbergNumber.note_type_id == note_type_id,
                FriedbergNumber.note_type_id.is_(None),
            )
        )
        hits.append(FriedbergNumber.note_type_id == note_type_id)

    denomination_id = code_to_id(db, Denomination, denomination, "denomination")
    if denomination_id is not None:
        narrow.append(
            or_(
                FriedbergNumber.denomination_id == denomination_id,
                FriedbergNumber.denomination_id.is_(None),
            )
        )
        hits.append(FriedbergNumber.denomination_id == denomination_id)

    if series_year is not None:
        narrow.append(
            or_(
                FriedbergNumber.series_year == series_year,
                FriedbergNumber.series_year.is_(None),
            )
        )
        hits.append(FriedbergNumber.series_year == series_year)

    if series_letter is not None:
        narrow.append(
            or_(
                FriedbergNumber.series_letter == series_letter,
                FriedbergNumber.series_letter.is_(None),
            )
        )
        hits.append(FriedbergNumber.series_letter == series_letter)

    seal_color_id = code_to_id(db, SealColor, seal_color, "seal_color")
    if seal_color_id is not None:
        narrow.append(
            or_(
                FriedbergNumber.seal_color_id == seal_color_id,
                FriedbergNumber.seal_color_id.is_(None),
            )
        )
        hits.append(FriedbergNumber.seal_color_id == seal_color_id)

    signature_combination_id = code_to_id(
        db, SignatureCombination, signature_combination, "signature_combination"
    )
    if signature_combination_id is not None:
        narrow.append(
            or_(
                FriedbergNumber.signature_combination_id == signature_combination_id,
                FriedbergNumber.signature_combination_id.is_(None),
            )
        )
        hits.append(
            FriedbergNumber.signature_combination_id == signature_combination_id
        )

    if district_letter is not None:
        narrow.append(
            or_(
                FriedbergNumber.district_letter == district_letter,
                FriedbergNumber.district_letter.is_(None),
            )
        )
        hits.append(FriedbergNumber.district_letter == district_letter)

    stmt = select(FriedbergNumber)
    if narrow:
        stmt = stmt.where(and_(*narrow), or_(*hits))

    rows = db.scalars(stmt.order_by(FriedbergNumber.id)).all()
    return [_to_out(db, row) for row in rows]


@friedberg_router.post("", status_code=status.HTTP_201_CREATED)
def create_friedberg_number(
    payload: FriedbergNumberCreate, db: DbSession, _admin: AdminUser
) -> FriedbergNumberOut:
    """Record a Friedberg number read off a note or slab in hand.

    `fr_number` is unconditionally unique, so the same type arriving twice is
    a 409 naming the row already on file rather than an integrity error --
    that is a legitimate, expected event, not a bug report.
    """
    existing = db.scalar(
        select(FriedbergNumber).where(FriedbergNumber.fr_number == payload.fr_number)
    )
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"fr_number {payload.fr_number!r} is already recorded as "
            f"row {existing.id}",
        )

    row = FriedbergNumber(
        fr_number=payload.fr_number,
        note_type_id=code_to_id(db, NoteType, payload.note_type, "note_type"),
        denomination_id=code_to_id(
            db, Denomination, payload.denomination, "denomination"
        ),
        series_year=payload.series_year,
        series_letter=payload.series_letter,
        seal_color_id=code_to_id(db, SealColor, payload.seal_color, "seal_color"),
        signature_combination_id=code_to_id(
            db,
            SignatureCombination,
            payload.signature_combination,
            "signature_combination",
        ),
        district_letter=payload.district_letter,
        size_class=payload.size_class,
        description=payload.description,
        source=ProvenanceSource.manual,
    )
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # The partial unique index on the identifying tuple: a second row
        # proposing the same denomination/year/letter/note-type/district
        # combination under a different fr_number. Also a real conflict, just
        # not the one the fr_number pre-check catches.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"That attribute combination is already recorded: {exc.orig}",
        ) from exc

    db.commit()
    db.refresh(row)
    return _to_out(db, row)


@item_router.post("/{item_id}/friedberg")
def attach_friedberg(
    item_id: int, payload: FriedbergAttachIn, db: DbSession, admin: AdminUser
) -> FriedbergAttachOut:
    """Attach a catalogue row to a currency item.

    Refused with 404 when the item has no `currency_detail` -- a coin has no
    Friedberg number, and silently doing nothing would hide that mistake
    rather than report it.

    Confirming a match (`status="confirmed"`) stamps `verified_by_id` and
    `verified_at` on the *catalogue row*, not just the item: that is what
    turns this particular proposal into a fact the next lookup can trust,
    for every item it is ever attached to afterwards.
    """
    if payload.status not in FRIEDBERG_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown friedberg_status {payload.status!r}. Expected one "
            f"of {sorted(FRIEDBERG_STATUSES)}",
        )

    item = db.get(InventoryItem, item_id)
    if item is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Inventory item not found"
        )
    detail = item.currency_detail
    if detail is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Item has no currency_detail -- only a banknote can carry "
            "a Friedberg number",
        )

    friedberg = db.get(FriedbergNumber, payload.friedberg_id)
    if friedberg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No Friedberg catalogue row with id {payload.friedberg_id}",
        )

    detail.friedberg_id = friedberg.id
    detail.friedberg_status = payload.status
    if payload.status == "confirmed":
        friedberg.verified_by_id = admin.id
        friedberg.verified_at = utcnow()

    db.commit()
    db.refresh(detail)
    db.refresh(friedberg)

    return FriedbergAttachOut(
        inventory_item_id=item.id,
        friedberg_id=detail.friedberg_id,
        friedberg_status=detail.friedberg_status,
        fr_number=friedberg.fr_number,
        verified=friedberg.verified_at is not None,
        verified_at=friedberg.verified_at,
    )
