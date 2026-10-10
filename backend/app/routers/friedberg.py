"""The owner's own Friedberg catalog -- not a licensed dataset.

`docs/reference-data.md` forbids shipping a publisher's arrangement: the Friedberg
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
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import InstrumentedAttribute, Session
from sqlalchemy.sql.elements import ColumnElement

from ..deps import AdminUser, DbSession
from ..fr_format import (
    DISTRICT_SQL_PATTERN,
    fr_district,
    fr_problem,
    fr_traits,
    seal_shade,
)
from ..models import (
    CurrencyDetail,
    Denomination,
    FriedbergNumber,
    InventoryItem,
    NoteIssue,
    NoteType,
    ProvenanceSource,
    SealColor,
    SignatureCombination,
    utcnow,
)
from ..references import code_of, code_to_id
from ..schemas import (
    FriedbergAttachIn,
    FriedbergAttachOut,
    FriedbergCatalogRow,
    FriedbergNumberCreate,
    FriedbergNumberOut,
    FriedbergNumberUpdate,
    SignatureChoice,
    SignatureChoicesOut,
)
from ._resolve import get_or_404

#: The catalog's own endpoints live under `/friedberg`; attaching a row to an
#: item, and clearing it, are inventory-shaped, so they get their own router
#: the same way `acquisitions.py` splits purchase orders from storage
#: locations.

friedberg_router = APIRouter(prefix="/friedberg", tags=["friedberg"])
item_router = APIRouter(prefix="/inventory", tags=["friedberg"])

#: Mirrors the `ck_currency_detail_friedberg_status` check constraint. Kept
#: here, not imported from the model, because a `CheckConstraint` string is
#: not something Python code can introspect.
FRIEDBERG_STATUSES = frozenset({"unknown", "proposed", "confirmed", "conflicting"})
#: The statuses a number is attached with. `unknown` is a note with no
#: number -- what clearing one leaves -- so it is not among them.
ATTACHABLE_STATUSES = FRIEDBERG_STATUSES - {"unknown"}


class CombinationRecorded(Exception):
    """A number refused because its type is already in the catalog. 409.

    Carries the row that holds the combination, so the console can name it
    and offer to correct that row's number, rather than a raw database error
    that never says which row is in the way. Rendered by `main.py` as
    `{detail, existing: {id, fr_number}}`.
    """

    def __init__(self, row: FriedbergNumber) -> None:
        """Name the row already holding the combination."""
        self.detail = (
            f"That combination is already recorded as {row.fr_number} (row {row.id})."
        )
        super().__init__(self.detail)
        self.existing = {"id": row.id, "fr_number": row.fr_number}


#: The columns of `uq_friedberg_number_identity`, compared NULLS NOT DISTINCT
#: as that index does -- less `is_star` and `is_mule`, which
#: `_same_combination` reads from the number.
_IDENTITY = (
    "denomination_id",
    "series_year",
    "series_letter",
    "note_type_id",
    "district_letter",
    "web_press",
    "signature_combination_id",
    "seal_color_id",
    "printing_facility",
)


def _district_of(recorded: str | None, fr_number: str) -> str | None:
    """A row's district: the one its number carries, else the one recorded.

    A note with no district entered gives the lookup none to record, but
    `9907-G` is district G's number all the same. Left unknown, the first
    number recorded for a series would hold the combination for every
    district, and the next district's number would be refused as that type
    recorded twice.

    The number's comes first for a correction's sake: a row whose number is
    corrected from one district's to another's moves with it. A number
    recorded new never disagrees with the district sent beside it --
    `FriedbergNumberCreate` refuses that.
    """
    return fr_district(fr_number) or recorded


#: A row's district for a search: the one recorded, else the one its number
#: carries. Rows recorded before a number's district was kept have none of
#: their own, and read as unknown they would be offered to a note of any
#: district.
_SEARCH_DISTRICT = func.coalesce(
    FriedbergNumber.district_letter,
    func.substring(FriedbergNumber.fr_number, DISTRICT_SQL_PATTERN),
)


def _same_combination(
    db: Session, row: FriedbergNumber, *, fr_number: str | None = None
) -> FriedbergNumber | None:
    """The catalog row that already has `row`'s identifying attributes.

    Only where the index applies: a denomination, a series year and a note
    type all known. A half-known type is allowed to repeat.

    The star and the mule are read from the number (`fr_traits`) rather than
    from `row.is_star`/`is_mule`, which the database generates and a row not
    yet flushed -- or one about to take a corrected `fr_number` -- does not
    hold yet.

    The district is read from the number too (`_district_of`) where the row
    has none, as it is when the row is saved.
    """
    if (
        row.denomination_id is None
        or row.series_year is None
        or row.note_type_id is None
    ):
        return None
    number = fr_number if fr_number is not None else row.fr_number
    star, mule = fr_traits(number)
    identity = {column: getattr(row, column) for column in _IDENTITY}
    identity["district_letter"] = _district_of(row.district_letter, number)
    conditions = [
        getattr(FriedbergNumber, column).is_not_distinct_from(value)
        for column, value in identity.items()
    ]
    conditions += [FriedbergNumber.is_star.is_(star), FriedbergNumber.is_mule.is_(mule)]
    if row.id is not None:
        conditions.append(FriedbergNumber.id != row.id)
    return db.scalar(select(FriedbergNumber).where(*conditions).limit(1))


def _refuse_to_confirm_a_slip(row: FriedbergNumber) -> None:
    """422 for confirming a number that is not in a Friedberg number's form.

    Confirming is what makes a number trusted -- the next lookup attaches it
    in one step -- so a slip such as `3007-` has to be corrected first. It
    may still be attached as proposed.
    """
    problem = fr_problem(row.fr_number)
    if problem is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Correct {row.fr_number} before confirming it: {problem}",
        )


#: The seal an `LGS` number goes with. Not in the seed data, so
#: it is named by code; where it is missing, no recorded seal is light green
#: and an LGS number is refused against any seal that is recorded.
LIGHT_GREEN_SEAL = "light_green"


def _refuse_shade_mismatch(
    db: Session, fr_number: str, seal_color_id: int | None, whose: str
) -> None:
    """422 when a number's seal shade contradicts a recorded seal.

    `2008-B LGS` is a light green seal's number, so a seal recorded as
    anything else is a slip in one of the two; `DGS` on a
    light green seal is the same slip the other way. A seal not recorded yet
    contradicts nothing and is allowed.
    """
    shade = seal_shade(fr_number)
    if shade is None or seal_color_id is None:
        return
    seal = db.get_one(SealColor, seal_color_id)
    light = seal.code == LIGHT_GREEN_SEAL
    if shade == "LGS" and not light:
        problem = f"is a light green seal's number, but {whose} seal is {seal.label}"
    elif shade == "DGS" and light:
        problem = f"is a dark green seal's number, but {whose} seal is {seal.label}"
    else:
        return
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=f"{fr_number} {problem}. Correct one of them.",
    )


def _items_using(db: Session, friedberg_id: int) -> int:
    """How many notes hold this catalog row."""
    return (
        db.scalar(
            select(func.count()).where(CurrencyDetail.friedberg_id == friedberg_id)
        )
        or 0
    )


def _to_out(db: Session, row: FriedbergNumber) -> FriedbergNumberOut:
    """A catalog row as the API returns it, its classifiers as codes."""
    return FriedbergNumberOut(
        id=row.id,
        fr_number=row.fr_number,
        note_type=code_of(db, NoteType, row.note_type_id),
        denomination=code_of(db, Denomination, row.denomination_id),
        series_year=row.series_year,
        series_letter=row.series_letter,
        seal_color=code_of(db, SealColor, row.seal_color_id),
        signature_combination=code_of(
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

    A row's district is the one it records or else the one its number
    carries (`_SEARCH_DISTRICT`): `9907-E` is never offered to a district G
    note, whatever the row was recorded with.
    """
    # Resolved in this order, so an unknown code is refused the same way
    # whichever others were sent too. Washington or Fort Worth: the two
    # facilities' printings of one series carry different numbers.
    filters: list[tuple[InstrumentedAttribute[Any] | ColumnElement[Any], object]] = [
        (
            FriedbergNumber.note_type_id,
            code_to_id(db, NoteType, note_type, "note_type"),
        ),
        (
            FriedbergNumber.denomination_id,
            code_to_id(db, Denomination, denomination, "denomination"),
        ),
        (FriedbergNumber.series_year, series_year),
        (FriedbergNumber.series_letter, series_letter),
        (
            FriedbergNumber.seal_color_id,
            code_to_id(db, SealColor, seal_color, "seal_color"),
        ),
        (
            FriedbergNumber.signature_combination_id,
            code_to_id(
                db, SignatureCombination, signature_combination, "signature_combination"
            ),
        ),
        (_SEARCH_DISTRICT, district_letter),
        (FriedbergNumber.web_press, web_press),
        (FriedbergNumber.printing_facility, printing_facility),
    ]
    supplied = [(column, value) for column, value in filters if value is not None]

    stmt = select(FriedbergNumber)
    if supplied:
        # Each supplied filter contributes two conditions: the NULL-tolerant
        # one (the row's value equals the filter or is unknown), all of which
        # must hold -- the stated matching rule -- and the strict one (the
        # row's value actually equals it), at least one of which must hold,
        # which is what stops a completely unknown row from matching
        # regardless of what was asked. See the docstring.
        stmt = stmt.where(
            and_(
                *(or_(column == value, column.is_(None)) for column, value in supplied)
            ),
            or_(*(column == value for column, value in supplied)),
        )

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
    Fowler, whose term began in 1965. Narrowing by term would hide the right
    pair for every lettered series.

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
    """The signature pairs to offer, with the rule (`source`) that chose them."""
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
        district_letter=_district_of(payload.district_letter, payload.fr_number),
        size_class=payload.size_class,
        web_press=payload.web_press,
        printing_facility=payload.printing_facility,
        description=payload.description,
        source=ProvenanceSource.manual,
    )
    _refuse_shade_mismatch(db, row.fr_number, row.seal_color_id, "its")
    held = _same_combination(db, row)
    if held is not None:
        raise CombinationRecorded(held)
    db.add(row)
    try:
        db.flush()
    except IntegrityError as exc:
        db.rollback()
        # Two recordings of one type, or of one number, racing past the
        # checks above: the unique index stops the second, and it reads as
        # the same refusal.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That combination is already recorded under another number.",
        ) from exc

    db.commit()
    db.refresh(row)
    return _to_out(db, row)


@friedberg_router.get("/catalog")
def list_catalog(
    db: DbSession,
    _admin: AdminUser,
    q: Annotated[
        str | None, Query(description="Part of a number or a description")
    ] = None,
) -> list[FriedbergCatalogRow]:
    """Every catalog row, with how many notes hold it, for the Lists page."""
    counts = dict(
        db.execute(
            select(CurrencyDetail.friedberg_id, func.count())
            .where(CurrencyDetail.friedberg_id.is_not(None))
            .group_by(CurrencyDetail.friedberg_id)
        )
        .tuples()
        .all()
    )
    stmt = select(FriedbergNumber).order_by(FriedbergNumber.fr_number)
    text = (q or "").strip()
    if text:
        pattern = f"%{text}%"
        stmt = stmt.where(
            or_(
                FriedbergNumber.fr_number.ilike(pattern),
                FriedbergNumber.description.ilike(pattern),
            )
        )
    return [
        FriedbergCatalogRow(
            **_to_out(db, row).model_dump(), item_count=counts.get(row.id, 0)
        )
        for row in db.scalars(stmt).all()
    ]


@friedberg_router.patch("/{friedberg_id}")
def update_catalog_row(
    friedberg_id: int,
    payload: FriedbergNumberUpdate,
    db: DbSession,
    admin: AdminUser,
) -> FriedbergNumberOut:
    """Correct a catalog row's number or description, or its confirmation.

    The notes that hold the row keep it: they hold the row, not its text, so
    a corrected number is what every one of them shows from now on.
    """
    row = get_or_404(
        db,
        FriedbergNumber,
        friedberg_id,
        f"No Friedberg catalog row with id {friedberg_id}",
    )
    sent = payload.model_fields_set
    if "fr_number" in sent and payload.fr_number is not None:
        clash = db.scalar(
            select(FriedbergNumber).where(
                FriedbergNumber.fr_number == payload.fr_number,
                FriedbergNumber.id != row.id,
            )
        )
        if clash is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"fr_number {payload.fr_number!r} is already recorded as "
                f"row {clash.id}",
            )
        # Adding or dropping a mule's `m` or a star changes which type the
        # row is, and the type may already be on file under another number.
        _refuse_shade_mismatch(db, payload.fr_number, row.seal_color_id, "this row's")
        held = _same_combination(db, row, fr_number=payload.fr_number)
        if held is not None:
            raise CombinationRecorded(held)
        row.district_letter = _district_of(row.district_letter, payload.fr_number)
        row.fr_number = payload.fr_number
    if "description" in sent:
        row.description = payload.description
    if "verified" in sent and payload.verified is not None:
        if payload.verified:
            _refuse_to_confirm_a_slip(row)
            row.verified_by_id = admin.id
            row.verified_at = utcnow()
        else:
            row.verified_by_id = None
            row.verified_at = None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        # Two corrections, or a correction and a recording, racing past the
        # checks above: the unique index stops the second, and it reads as
        # the same refusal.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That combination is already recorded under another number.",
        ) from exc
    db.refresh(row)
    return _to_out(db, row)


@friedberg_router.delete("/{friedberg_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_catalog_row(friedberg_id: int, db: DbSession, _admin: AdminUser) -> Response:
    """Delete a catalog row no note holds -- a type recorded by mistake."""
    row = get_or_404(
        db,
        FriedbergNumber,
        friedberg_id,
        f"No Friedberg catalog row with id {friedberg_id}",
    )
    used = _items_using(db, row.id)
    if used:
        noun = "item" if used == 1 else "items"
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"{row.fr_number} is on {used} {noun}; clear it there first",
        )
    db.delete(row)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _note_detail(db: Session, item_id: int) -> CurrencyDetail:
    """The note's currency detail, or a 404 naming why there is none.

    Refused when the item has no `currency_detail` -- a coin has no Friedberg
    number, and silently doing nothing would hide that mistake rather than
    report it.
    """
    item = get_or_404(db, InventoryItem, item_id, "Inventory item not found")
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
    if payload.status not in ATTACHABLE_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"Unknown friedberg_status {payload.status!r}. Expected one "
            f"of {sorted(ATTACHABLE_STATUSES)}",
        )

    detail = _note_detail(db, item_id)

    friedberg = get_or_404(
        db,
        FriedbergNumber,
        payload.friedberg_id,
        f"No Friedberg catalog row with id {payload.friedberg_id}",
    )

    if payload.status == "confirmed":
        _refuse_to_confirm_a_slip(friedberg)
    # What the note in hand shows is what the number must agree with.
    _refuse_shade_mismatch(db, friedberg.fr_number, detail.seal_color_id, "the note's")
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
