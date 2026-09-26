"""Suggested classifiers for an item being entered, from the facts so far.

The New item form asks here as its facts are chosen: pick a $1 note of
Series 1957 and the form is told Silver Certificate, blue seal,
Priest / Anderson. The same rules as `app.classifier_defaults`, which fills
the same fields on items already recorded
(docs/specs/classifier-defaults-design.md).

Staff-only, like entering items.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..classifier_defaults import NoteFacts, suggest
from ..composition import composition_for
from ..deps import AdminUser, DbSession
from ..models import (
    Country,
    Denomination,
    FedDistrict,
    Metal,
    NoteIssue,
    NoteType,
    ReferenceMixin,
    SealColor,
    SignatureCombination,
)
from ..references import code_to_id
from ..schemas import (
    SERIES_YEAR_MAX,
    SERIES_YEAR_MIN,
    CoinSuggestionOut,
    NoteSuggestionOut,
)
from ..serial_patterns import SMALL_SIZE_FROM

router = APIRouter(prefix="/defaults", tags=["defaults"])

#: The note fields a suggestion can fill, the column each lives in, and the
#: table its code comes from.
NOTE_FIELDS: dict[str, tuple[str, type[ReferenceMixin]]] = {
    "note_type": ("note_type_id", NoteType),
    "seal_color": ("seal_color_id", SealColor),
    "signature_combination": ("signature_combination_id", SignatureCombination),
    "fed_district": ("fed_district_id", FedDistrict),
}

Code = Annotated[str | None, Query(max_length=64)]


@router.get("/note")
def suggest_note(
    db: DbSession,
    _admin: AdminUser,
    denomination: Code = None,
    series_year: Annotated[
        int | None, Query(ge=SERIES_YEAR_MIN, le=SERIES_YEAR_MAX)
    ] = None,
    series_letter: Annotated[str | None, Query(max_length=4)] = None,
    serial_number: Annotated[str | None, Query(max_length=64)] = None,
    rating: Annotated[str | None, Query(max_length=500)] = None,
    note_type: Code = None,
    seal_color: Code = None,
    signature_combination: Code = None,
    fed_district: Code = None,
) -> NoteSuggestionOut:
    """What a note with these facts would be.

    Send only what the person chose: a value sent narrows the suggestion, as
    a recorded value does, and is never suggested back. A field the facts
    leave open comes back null.
    """
    chosen = {
        "note_type": note_type,
        "seal_color": seal_color,
        "signature_combination": signature_combination,
        "fed_district": fed_district,
    }
    current = {
        column: code_to_id(db, model, chosen[name], name)
        for name, (column, model) in NOTE_FIELDS.items()
    }
    denomination_id = code_to_id(db, Denomination, denomination, "denomination")
    face = (
        db.execute(
            select(Denomination.face_value).where(Denomination.id == denomination_id)
        ).scalar_one()
        if denomination_id is not None
        else None
    )
    ids = suggest(
        db,
        NoteFacts(
            denomination_id=denomination_id,
            face=Decimal(face) if face is not None else None,
            series_year=series_year,
            series_letter=series_letter,
            serial_number=serial_number,
            rating=rating,
            current=current,
        ),
    )
    found: dict[str, str | None] = {}
    for name, (column, model) in NOTE_FIELDS.items():
        row_id = ids.get(column)
        found[name] = (
            db.execute(select(model.code).where(model.id == row_id)).scalar_one()
            if row_id is not None
            else None
        )
    return NoteSuggestionOut(
        **found, warning=_issue_warning(db, denomination_id, series_year, series_letter)
    )


def _issue_warning(
    db: Session, denomination_id: int | None, year: int | None, letter: str | None
) -> str | None:
    """Say so when no issue of this denomination is of this series.

    Only where the record covers the denomination at all -- the small-size
    US notes `note_issue` holds -- and only from 1928, when small-size notes
    begin: a large-size or foreign note is simply not described there, which
    is no reason to doubt its series. The issues that do exist are named, so
    a letter typed wrong (1953E for 1953B) is easy to put right.
    """
    if denomination_id is None or year is None or year < SMALL_SIZE_FROM:
        return None
    issues = db.execute(
        select(NoteIssue.series_year, NoteIssue.series_letter)
        .where(NoteIssue.denomination_id == denomination_id)
        .distinct()
    ).all()
    if not issues:
        return None
    wanted = (letter or "").strip().upper() or None
    if any(y == year and (ltr or None) == wanted for y, ltr in issues):
        return None
    label = db.execute(
        select(Denomination.label).where(Denomination.id == denomination_id)
    ).scalar_one()
    note = f"{label.removesuffix(' Bill')} note"
    that_year = sorted({f"{y}{ltr or ''}" for y, ltr in issues if y == year})
    if that_year:
        known = f"Series {year} on record: {', '.join(that_year)}."
    else:
        years = sorted({y for y, _ in issues})
        known = f"{note.capitalize()} series on record: {', '.join(map(str, years))}."
    return f"No {note} of Series {year}{wanted or ''} is on record. {known}"


@router.get("/coin")
def suggest_coin(
    db: DbSession,
    _admin: AdminUser,
    denomination: Code = None,
    country: Code = None,
    year: Annotated[int | None, Query(ge=-3000, le=2200)] = None,
) -> CoinSuggestionOut:
    """The metal a coin of this denomination, country and year is struck in."""
    composition = composition_for(
        db,
        code_to_id(db, Denomination, denomination, "denomination"),
        code_to_id(db, Country, country, "country"),
        year,
    )
    metal = (
        db.execute(
            select(Metal.code).where(Metal.id == composition.metal_id)
        ).scalar_one_or_none()
        if composition is not None and composition.metal_id is not None
        else None
    )
    return CoinSuggestionOut(metal=metal)
