"""What a coin is made of, from its denomination, country and year.

A public fact, not an observation: a dime struck in 1963 is 90% silver
because the law said so. Used by the defaults endpoint (`routers.defaults`)
to suggest a composition while an item is being entered.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Composition


def composition_for(
    db: Session, denomination_id: int | None, country_id: int | None, year: int | None
) -> Composition | None:
    """The published composition, or None rather than a guess.

    None when any of the three facts is missing: a year-less dime could be
    silver or clad.
    """
    if denomination_id is None or country_id is None or year is None:
        return None
    return db.execute(
        select(Composition)
        .where(
            Composition.denomination_id == denomination_id,
            Composition.country_id == country_id,
            Composition.year_from <= year,
            (Composition.year_to.is_(None)) | (Composition.year_to >= year),
        )
        .order_by(Composition.year_from.desc())
        .limit(1)
    ).scalar_one_or_none()
