"""What a coin is made of, from its denomination or design series, country and year.

A public fact, not an observation: a dime struck in 1963 is 90% silver
because the law said so. Used by the defaults endpoint (`routers.defaults`)
to suggest a metal while an item is being entered, and by
`app.classifier_defaults` to fill an item's composition.

A row of `composition` names a denomination, a series, or both. A row naming
the item's series is that design's own and outranks a row for the
denomination; the denomination's row is every other coin's of that face
value and year.

A coin with a series and no year has the composition its design was struck
in, where that was one: every run of the design's years resolves to the same
row. A design brought back after `RESTRIKE_GAP` years -- the Morgan dollar
of 2021, the gold Winged Liberty Head dime of 2016 -- is a commemorative
restrike, and an undated coin is not taken for one: only the runs before
such a gap are read.
"""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import Composition, Series, SeriesYearRange

__all__ = [
    "RESTRIKE_GAP",
    "Run",
    "composition_for",
    "composition_of",
    "composition_of_series",
    "load_compositions",
    "series_runs",
]

#: A run of years a design was struck in: its denomination where the design
#: has one, the first year, and the last (None is still struck).
Run = tuple[int | None, int, int | None]

#: A run starting more than this many years after every run before it ended
#: is a restrike of an old design, not the design's own issue.
RESTRIKE_GAP = 50


def load_compositions(db: Session) -> list[Composition]:
    """Every composition, latest span first."""
    return list(
        db.execute(select(Composition).order_by(Composition.year_from.desc())).scalars()
    )


def _original(runs: list[Run]) -> tuple[Run, ...]:
    """The runs of a design's own issue: those before the first restrike gap.

    The gap is measured from the latest year any run before it ended; a run
    still struck leaves no gap after it.
    """
    kept: list[Run] = []
    latest: int | None = None
    for run in sorted(runs, key=lambda r: r[1]):
        if kept and latest is not None and run[1] - latest > RESTRIKE_GAP:
            break
        if not kept or (latest is not None and (run[2] is None or run[2] > latest)):
            latest = run[2]
        kept.append(run)
    return tuple(kept)


def series_runs(db: Session) -> dict[int, tuple[Run, ...]]:
    """Each coin design's own runs of years, by series id.

    A design with range rows is read from them, each falling back to the
    design's own denomination; one with none, from its own span. A design
    with no first year has no run.
    """
    ranges: dict[int, list[SeriesYearRange]] = {}
    for record in db.execute(select(SeriesYearRange)).scalars():
        ranges.setdefault(record.series_id, []).append(record)

    runs: dict[int, tuple[Run, ...]] = {}
    for series in db.execute(
        select(Series).where(Series.applies_to == "coin")
    ).scalars():
        if series.id in ranges:
            given: list[Run] = [
                (r.denomination_id or series.denomination_id, r.year_start, r.year_end)
                for r in ranges[series.id]
            ]
        elif series.year_start is not None:
            given = [(series.denomination_id, series.year_start, series.year_end)]
        else:
            continue
        runs[series.id] = _original(given)
    return runs


def _set_rows(
    compositions: Sequence[Composition],
    *,
    denomination_id: int | None,
    series_id: int | None,
    country_id: int,
    first: int,
    last: int | None,
    set_form_id: int | None,
) -> tuple[list[Composition], list[Composition]]:
    """A set's own rows for the coin: those reaching its years, and those covering them.

    `set_form_id` None is a set not recorded, which any set's rows reach.
    """
    end = last if last is not None else 10**6
    reaching = [
        c
        for c in compositions
        if c.set_form_id is not None
        and c.country_id == country_id
        and (set_form_id is None or c.set_form_id == set_form_id)
        and (
            (denomination_id is not None and c.denomination_id == denomination_id)
            or (series_id is not None and c.series_id == series_id)
        )
        and c.year_from <= end
        and (c.year_to is None or c.year_to >= first)
    ]
    covering = [
        c
        for c in reaching
        if c.year_from <= first
        and (c.year_to is None or (last is not None and c.year_to >= last))
    ]
    return reaching, covering


def composition_of(
    compositions: Sequence[Composition],
    *,
    denomination_id: int | None,
    series_id: int | None,
    country_id: int,
    first: int,
    last: int | None,
    set_form_id: int | None = None,
    set_unknown: bool = False,
    metal_id: int | None = None,
) -> Composition | None:
    """The composition covering every year from `first` to `last`, or None.

    `last` None is a span still open, which only a row still current covers.
    A coin recorded in a set (`set_form_id`) that has rows of its own is
    decided by them alone: the one covering its span, and none where a span
    is only partly covered -- a 2010-2020 run of Silver Proof Set dimes,
    silver through either standard. A proof whose set is not recorded
    (`set_unknown`), where a set's rows reach its years, could be from that
    set or not: the metal a person recorded on it (`metal_id`) chooses
    between the set's row and every coin's, and with none recorded it has
    no composition. Otherwise a row naming the item's series is looked for;
    where several cover the span -- a Gold Eagle with no denomination could
    be any of four sizes -- none is chosen, and the denomination's row is
    not fallen back on. With no such row, the latest row for the
    denomination alone decides.
    """
    if set_form_id is not None or set_unknown:
        reaching, covering = _set_rows(
            compositions,
            denomination_id=denomination_id,
            series_id=series_id,
            country_id=country_id,
            first=first,
            last=last,
            set_form_id=None if set_unknown else set_form_id,
        )
        if reaching and not set_unknown:
            return covering[0] if len(covering) == 1 else None
        if reaching:
            if metal_id is None:
                return None
            every_coins = composition_of(
                compositions,
                denomination_id=denomination_id,
                series_id=series_id,
                country_id=country_id,
                first=first,
                last=last,
            )
            agreeing = {
                c.id: c
                for c in (*covering, every_coins)
                if c is not None and c.metal_id == metal_id
            }
            return next(iter(agreeing.values())) if len(agreeing) == 1 else None
    covering = [
        c
        for c in compositions
        if c.set_form_id is None
        and c.country_id == country_id
        and c.year_from <= first
        and (c.year_to is None or (last is not None and c.year_to >= last))
    ]
    if series_id is not None:
        own = [
            c
            for c in covering
            if c.series_id == series_id
            and (
                c.denomination_id is None
                or denomination_id is None
                or c.denomination_id == denomination_id
            )
        ]
        if own:
            return own[0] if len(own) == 1 else None
    if denomination_id is None:
        return None
    return next(
        (
            c
            for c in sorted(covering, key=lambda c: c.year_from, reverse=True)
            if c.series_id is None and c.denomination_id == denomination_id
        ),
        None,
    )


def composition_of_series(
    compositions: Sequence[Composition],
    runs: Sequence[Run],
    *,
    denomination_id: int | None,
    series_id: int,
    country_id: int,
    set_form_id: int | None = None,
    set_unknown: bool = False,
    metal_id: int | None = None,
) -> Composition | None:
    """The one composition a design was struck in, for a coin with no year.

    None unless every run of the design resolves, and to the same row: a
    Roosevelt dime with no year could be silver or clad. A run at another
    denomination than the item's is not the item's. The set, a proof's
    unknown set and its recorded metal are read as `composition_of` reads them.
    """
    found: dict[int, Composition] = {}
    for run_denomination, first, last in runs:
        if (
            denomination_id is not None
            and run_denomination is not None
            and run_denomination != denomination_id
        ):
            continue
        composition = composition_of(
            compositions,
            denomination_id=denomination_id or run_denomination,
            series_id=series_id,
            country_id=country_id,
            first=first,
            last=last,
            set_form_id=set_form_id,
            set_unknown=set_unknown,
            metal_id=metal_id,
        )
        if composition is None:
            return None
        found[composition.id] = composition
    return next(iter(found.values())) if len(found) == 1 else None


def composition_for(
    db: Session, denomination_id: int | None, country_id: int | None, year: int | None
) -> Composition | None:
    """The published composition of a denomination's coin, or None rather than a guess.

    None when any of the three facts is missing: a year-less dime could be
    silver or clad. The coin is read as one outside any set: a Silver Proof
    Set's own rows are not consulted.
    """
    if denomination_id is None or country_id is None or year is None:
        return None
    return composition_of(
        load_compositions(db),
        denomination_id=denomination_id,
        series_id=None,
        country_id=country_id,
        first=year,
        last=year,
    )
