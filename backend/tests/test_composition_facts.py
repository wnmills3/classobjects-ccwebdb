"""The seeded coinage compositions (data/reference/composition.json).

Spot checks of spans that are easy to get wrong: a size struck in one year
only, and a year struck in two alloys, which has no row at all.
"""

from __future__ import annotations

from decimal import Decimal

from app.composition import composition_for, composition_of, load_compositions
from app.models import Composition, Country, Denomination, Series
from sqlalchemy import select
from sqlalchemy.orm import Session

from tests.builders import code_id


def _buffalo_spans(db: Session) -> dict[str, tuple[int, int | None]]:
    """Each Gold Buffalo row's span, by its denomination code."""
    rows = db.execute(
        select(Denomination.code, Composition.year_from, Composition.year_to)
        .join(Denomination, Denomination.id == Composition.denomination_id)
        .join(Series, Series.id == Composition.series_id)
        .where(Series.code == "american_gold_buffalo")
    ).all()
    return {code: (year_from, year_to) for code, year_from, year_to in rows}


def test_fractional_gold_buffalos_are_2008_only(db: Session) -> None:
    spans = _buffalo_spans(db)
    assert spans["usd_coin_5_00"] == (2008, 2008)
    assert spans["usd_coin_10_00"] == (2008, 2008)
    assert spans["usd_coin_25_00"] == (2008, 2008)
    # The one-ounce coin has been struck every year since 2006.
    assert spans["usd_coin_50_00"] == (2006, None)


def test_a_fractional_buffalo_outside_2008_has_no_composition(db: Session) -> None:
    compositions = load_compositions(db)
    buffalo = code_id(db, Series, "american_gold_buffalo")
    tenth = code_id(db, Denomination, "usd_coin_5_00")
    us = code_id(db, Country, "US")

    def of(year: int) -> Composition | None:
        return composition_of(
            compositions,
            denomination_id=tenth,
            series_id=buffalo,
            country_id=us,
            first=year,
            last=year,
        )

    found = of(2008)
    assert found is not None
    assert found.fine_weight_ozt == Decimal("0.100000")
    assert of(2006) is None
    assert of(2010) is None


def test_an_1864_cent_has_no_composition(db: Session) -> None:
    """1864 cents were struck in copper-nickel and in bronze."""
    cent = code_id(db, Denomination, "usd_coin_0_01")
    us = code_id(db, Country, "US")

    copper_nickel = composition_for(db, cent, us, 1863)
    bronze = composition_for(db, cent, us, 1865)
    assert copper_nickel is not None
    assert copper_nickel.fineness == Decimal("0.8800")
    assert bronze is not None
    assert bronze.fineness == Decimal("0.9500")
    assert composition_for(db, cent, us, 1864) is None
