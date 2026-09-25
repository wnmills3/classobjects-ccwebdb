"""The owner's own Friedberg catalog -- not a licensed dataset.

`CLAUDE.md` forbids shipping a publisher's arrangement: the Friedberg
catalog's mapping of attributes to its own numbers is sold, not free to
redistribute. Nothing here seeds, fetches, or hardcodes that mapping. What
this module builds instead is a place for the owner to record numbers read
off their own notes and slabs (`POST /friedberg`), search that private
catalog by what is visible on a note in hand (`GET /friedberg`), and attach
a match to an item (`POST /inventory/{item_id}/friedberg`).

See the `FriedbergNumber` docstring in `app.models.identification` for why the
identifying tuple is only partially unique, and why `verified_at` is what
turns a proposal into a fact the next lookup can trust.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import ColumnElement, and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from ..deps import AdminUser, DbSession
from ..models import (
    CurrencyDetail,
    Denomination,
    FriedbergNumber,
    InventoryItem,
    NoteIssue,
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
    SignatureChoice,
    SignatureChoicesOut,
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
        web_press=row.web_press,
        printing_facility=row.printing_facility,
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
    web_press: Annotated[
        bool | None, Query(description="Printed on a web press")
    ] = None,
    printing_facility: Annotated[
        str | None,
        Query(pattern="^(dc|fw)$", description="dc (Washington) or fw (Fort Worth)"),
    ] = None,
) -> list[FriedbergNumberOut]:
    """Search the owner's catalog by what is visible on a note in hand.

    Every supplied filter narrows -- an unknown classifier code is a 422, the
    same rule the rest of the API follows, rather than a filter that is
    silently dropped and returns the whole catalog looking like a match.

    A row matches a supplied filter when its value equals the filter or is
    NULL, so a half-known type (built from a note that did not show every
    feature) is still found by what it does know. That alone would let a row
    with *everything* NULL match any query at all, which would make the
    catalog useless once it has a few dozen such rows in it -- so a second
    condition also applies: at least one supplied filter must actually equal
    the row's value, not merely find it NULL. A row is required to know
    *something* the query asked about, not just fail to contradict it.

    With no filters at all, both conditions are vacuous and every row comes
    back -- browsing the whole catalog is a valid use of this endpoint too.
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

    if web_press is not None:
        narrow.append(
            or_(
                FriedbergNumber.web_press == web_press,
                FriedbergNumber.web_press.is_(None),
            )
        )
        hits.append(FriedbergNumber.web_press == web_press)

    # Washington or Fort Worth: a 2017-A $1 is 3005-A from one, 3006-A from
    # the other (owner, 2026-09-25).
    if printing_facility is not None:
        narrow.append(
            or_(
                FriedbergNumber.printing_facility == printing_facility,
                FriedbergNumber.printing_facility.is_(None),
            )
        )
        hits.append(FriedbergNumber.printing_facility == printing_facility)

    stmt = select(FriedbergNumber)
    if narrow:
        stmt = stmt.where(and_(*narrow), or_(*hits))

    rows = db.scalars(stmt.order_by(FriedbergNumber.id)).all()
    return [_to_out(db, row) for row in rows]


@friedberg_router.get("/signatures")
def signature_choices(
    db: DbSession,
    _admin: AdminUser,
    denomination: Annotated[str | None, Query()] = None,
    note_type: Annotated[str | None, Query()] = None,
    seal_color: Annotated[str | None, Query()] = None,
    series_year: Annotated[int | None, Query()] = None,
    series_letter: Annotated[str | None, Query()] = None,
) -> SignatureChoicesOut:
    """The Treasurer / Secretary pairs a note of this series can carry.

    **Not "whose term covers the series year".** A series is named for the
    year its design was adopted, and a lettered series is printed later under
    later officials: series 1963 is Granahan / Dillon, 1963-A Granahan /
    Fowler, whose term began in 1965. Narrowing by term hid the right pair
    for every lettered series (2026-09-23).

    So the seeded `note_issue` facts decide, matched on whatever is given. A
    blank letter matches every letter of the series -- it may just not be
    typed yet, and a list too wide is recoverable where one too narrow is
    not. A series with no facts falls back to every pair whose term ended no
    earlier than the series year. Public fact throughout, never a
    catalog's numbering.
    """
    active = select(SignatureCombination).where(
        SignatureCombination.is_active.is_(True)
    )
    ordered = (SignatureCombination.sort_order, SignatureCombination.code)
    if series_year is None:
        return _choices(db.scalars(active.order_by(*ordered)).all(), "all")

    facts = select(NoteIssue.signature_combination_id).where(
        NoteIssue.series_year == series_year
    )
    for column, model, code, name in (
        (NoteIssue.denomination_id, Denomination, denomination, "denomination"),
        (NoteIssue.note_type_id, NoteType, note_type, "note_type"),
        (NoteIssue.seal_color_id, SealColor, seal_color, "seal_color"),
    ):
        row_id = code_to_id(db, model, code, name)
        if row_id is not None:
            facts = facts.where(column == row_id)
    if series_letter:
        facts = facts.where(NoteIssue.series_letter == series_letter)

    known = db.scalars(
        active.where(SignatureCombination.id.in_(facts)).order_by(*ordered)
    ).all()
    if known:
        return _choices(known, "note_issue")
    still_in_office = active.where(
        or_(
            SignatureCombination.term_to.is_(None),
            SignatureCombination.term_to >= series_year,
        )
    )
    return _choices(db.scalars(still_in_office.order_by(*ordered)).all(), "term")


def _choices(rows: Sequence[SignatureCombination], source: str) -> SignatureChoicesOut:
    return SignatureChoicesOut(
        values=[SignatureChoice(code=row.code, label=row.label) for row in rows],
        source=source,
    )


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
        web_press=payload.web_press,
        printing_facility=payload.printing_facility,
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


def _note_detail(db: Session, item_id: int) -> CurrencyDetail:
    """The note's currency detail, or a 404 naming why there is none.

    Refused when the item has no `currency_detail` -- a coin has no Friedberg
    number, and silently doing nothing would hide that mistake rather than
    report it.
    """
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
    return detail


@item_router.delete("/{item_id}/friedberg", status_code=status.HTTP_204_NO_CONTENT)
def clear_friedberg(item_id: int, db: DbSession, _admin: AdminUser) -> None:
    """Take the Friedberg number off a note, which goes back to `unknown`.

    The catalog row stays: it records a type that exists, whether or not
    this note turned out to be one. A row confirmed by an earlier attach stays
    confirmed for the same reason.
    """
    detail = _note_detail(db, item_id)
    detail.friedberg_id = None
    detail.friedberg_status = "unknown"
    db.commit()


@item_router.post("/{item_id}/friedberg")
def attach_friedberg(
    item_id: int, payload: FriedbergAttachIn, db: DbSession, admin: AdminUser
) -> FriedbergAttachOut:
    """Attach a catalog row to a currency item.

    Refused with 404 when the item has no `currency_detail` -- a coin has no
    Friedberg number, and silently doing nothing would hide that mistake
    rather than report it.

    Confirming a match (`status="confirmed"`) stamps `verified_by_id` and
    `verified_at` on the *catalog row*, not just the item: that is what
    turns this particular proposal into a fact the next lookup can trust,
    for every item it is ever attached to afterwards.
    """
    if payload.status not in FRIEDBERG_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown friedberg_status {payload.status!r}. Expected one "
            f"of {sorted(FRIEDBERG_STATUSES)}",
        )

    detail = _note_detail(db, item_id)

    friedberg = db.get(FriedbergNumber, payload.friedberg_id)
    if friedberg is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No Friedberg catalog row with id {payload.friedberg_id}",
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
        inventory_item_id=item_id,
        friedberg_id=detail.friedberg_id,
        friedberg_status=detail.friedberg_status,
        fr_number=friedberg.fr_number,
        verified=friedberg.verified_at is not None,
        verified_at=friedberg.verified_at,
    )
