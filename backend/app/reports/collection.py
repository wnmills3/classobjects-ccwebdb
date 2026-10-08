"""Collection: what the collection is made of.

Six reports, sharing one params shape (`LiveParams`: `status`/`disposition`,
defined once in `.live_params` alongside its filter and drill helpers, and
reused rather than redeclared) and one live-row source (`.tables`).

`cb_holdings`: live items grouped by kind and then by denomination, with
items, pieces and total cost, a subtotal row per kind, and an overall total.
`cb_designs`: design series held, across every non-currency kind. `cb_notes`:
note type x series designation, with the seal colors and Federal Reserve
districts present, and star notes/fancy serials when the attribute
vocabulary names them. `cb_grades`: grade band x strike type x grading
service, coins and currency both. `cb_metal`: metal x form over items with a
fine weight, with melt value at the latest spot price. `cb_attributes`: every
attribute and error type a live item carries.

Money is summed in SQL, never accumulated a row at a time in Python; a
report's own grand total, where it has one, adds already-summed Decimal row
values, which is exact rather than a second, independent sum that could
drift from the rows a reader can already add up.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from decimal import Decimal
from typing import cast
from urllib.parse import urlencode

from sqlalchemy import (
    ColumnElement,
    FromClause,
    RowMapping,
    and_,
    case,
    exists,
    func,
    or_,
    select,
)
from sqlalchemy.orm import Session

from ..inventory_search import COIN_VIEW, CURRENCY_VIEW, MISSING_FIELDS
from ..models import (
    BullionForm,
    CurrencyDetail,
    Denomination,
    ErrorType,
    FedDistrict,
    Grade,
    GradingService,
    ItemAttribute,
    ItemAttributeLink,
    ItemError,
    Metal,
    MetalPrice,
    NoteType,
    SealColor,
    Series,
    StrikeType,
)
from ..models.valuation import melt_value
from .base import Column, Report, ReportResult
from .live_params import (
    NOTHING_MATCHES,
    DispositionParam,
    LiveParams,
    StatusParam,
    kind_query_string,
    live_where,
    status_disposition_params,
)
from .registry import register
from .tables import ITEM as _I
from .tables import KIND as _K

__all__ = [
    "CB_ATTRIBUTES",
    "CB_DESIGNS",
    "CB_GRADES",
    "CB_HOLDINGS",
    "CB_METAL",
    "CB_NOTES",
    "AttributesParams",
    "DesignsParams",
    "DispositionParam",
    "GradesParams",
    "HoldingsParams",
    "LiveParams",
    "MetalParams",
    "NotesParams",
    "StatusParam",
]

#: `denomination`, aliased as the search aliases it, alongside the shared
#: `i`/`k` of `.tables`.
_D = Denomination.__table__.alias("d")


class HoldingsParams(LiveParams):
    """`cb_holdings` takes no parameters beyond status and disposition."""


#: Which search view an item's kind is browsed in, as `view_path` splits it:
#: currency has its own; every other kind is `COIN_VIEW`. Shared by
#: `cb_grades` and `cb_attributes`, the two reports that group across both
#: views at once.
_VIEW_NAME = case((_K.c.code == "currency", CURRENCY_VIEW.name), else_=COIN_VIEW.name)


def _source_and_where(
    params: HoldingsParams,
) -> tuple[FromClause, list[ColumnElement[bool]]]:
    """The FROM clause and WHERE clauses every aggregate level shares."""
    src: FromClause = _I.join(_K, _K.c.id == _I.c.item_kind_id).outerjoin(
        _D, _D.c.id == _I.c.denomination_id
    )
    return live_where(src, params)


def _cb_holdings(db: Session, params: HoldingsParams) -> ReportResult:
    """Kind x denomination: items, pieces and total cost, with subtotals.

    Three queries at three grouping levels -- by kind and denomination, by
    kind alone, and not grouped at all -- rather than one query whose rows
    this function then adds up in Python: each total is exactly what
    PostgreSQL's own `sum` returns for that group, so a subtotal cannot drift
    from the sum of its rows, nor the grand total from the sum of the
    subtotals, by so much as a cent.
    """
    columns = [
        Column("kind", "Kind", "text"),
        Column("denomination", "Denomination", "text"),
        Column("items", "Items", "count"),
        Column("pieces", "Pieces", "count"),
        Column("total_cost", "Total cost", "money"),
    ]
    src, where = _source_and_where(params)

    overall = (
        db.execute(
            select(
                func.count().label("items"),
                func.sum(_I.c.piece_count).label("pieces"),
                func.sum(_I.c.total_cost).label("total_cost"),
            )
            .select_from(src)
            .where(*where)
        )
        .mappings()
        .one()
    )

    if not overall["items"]:
        return ReportResult(
            columns=columns,
            rows=[],
            totals=None,
            drills=[],
            notes=[NOTHING_MATCHES],
        )

    denom_rows = (
        db.execute(
            select(
                _K.c.id.label("kind_id"),
                _K.c.code.label("kind_code"),
                _K.c.label.label("kind_label"),
                _D.c.id.label("denom_id"),
                _D.c.code.label("denom_code"),
                _D.c.label.label("denom_label"),
                _D.c.sort_order.label("denom_sort"),
                func.count().label("items"),
                func.sum(_I.c.piece_count).label("pieces"),
                func.sum(_I.c.total_cost).label("total_cost"),
            )
            .select_from(src)
            .where(*where)
            .group_by(
                _K.c.id,
                _K.c.code,
                _K.c.label,
                _D.c.id,
                _D.c.code,
                _D.c.label,
                _D.c.sort_order,
            )
            .order_by(
                _K.c.sort_order,
                # A tiebreak, not a sort key of its own: two kinds sharing a
                # `sort_order` must still stay contiguous, which is what the
                # subtotal loop below depends on -- it closes a kind's
                # subtotal the moment `kind_code` changes, so an
                # out-of-order pair of rows for the same kind would split
                # into two subtotal rows for it.
                _K.c.id,
                _D.c.sort_order.asc().nulls_last(),
                _D.c.label.asc().nulls_last(),
            )
        )
        .mappings()
        .all()
    )

    kind_subtotals = {
        row["kind_code"]: row
        for row in db.execute(
            select(
                _K.c.code.label("kind_code"),
                _K.c.label.label("kind_label"),
                func.count().label("items"),
                func.sum(_I.c.piece_count).label("pieces"),
                func.sum(_I.c.total_cost).label("total_cost"),
            )
            .select_from(src)
            .where(*where)
            .group_by(_K.c.code, _K.c.label)
        )
        .mappings()
        .all()
    }

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    current_kind: str | None = None

    def _append_subtotal(kind_code: str) -> None:
        """Add the kind's subtotal row, and the search that lists its items."""
        subtotal = kind_subtotals[kind_code]
        rows.append(
            {
                "kind": subtotal["kind_label"],
                "denomination": f"All {subtotal['kind_label']}",
                "items": subtotal["items"],
                "pieces": subtotal["pieces"],
                "total_cost": subtotal["total_cost"],
            }
        )
        drills.append(kind_query_string(kind_code, params))

    for row in denom_rows:
        kind_code = row["kind_code"]
        if current_kind is not None and kind_code != current_kind:
            _append_subtotal(current_kind)
        current_kind = kind_code

        denom_code = row["denom_code"]
        if denom_code is not None:
            rows.append(
                {
                    "kind": row["kind_label"],
                    "denomination": row["denom_label"],
                    "items": row["items"],
                    "pieces": row["pieces"],
                    "total_cost": row["total_cost"],
                }
            )
            drills.append(kind_query_string(kind_code, params, denom_code=denom_code))
        else:
            rows.append(
                {
                    "kind": row["kind_label"],
                    "denomination": "No denomination",
                    "items": row["items"],
                    "pieces": row["pieces"],
                    "total_cost": row["total_cost"],
                }
            )
            # Whether the kind can carry a denomination at all is read from
            # `MISSING_FIELDS` -- the table the `missing=` filter and
            # `dq_completeness` read -- so a kind it does not apply to never
            # gets a `missing=denomination` link, whose search would come
            # back empty and disagree with this row's own count.
            drills.append(
                kind_query_string(kind_code, params, missing_denom=True)
                if MISSING_FIELDS["denomination"].applies_to(kind_code)
                else None
            )

    if current_kind is not None:
        _append_subtotal(current_kind)

    totals = {
        "kind": "All kinds",
        "denomination": None,
        "items": overall["items"],
        "pieces": overall["pieces"],
        "total_cost": overall["total_cost"],
    }

    return ReportResult(columns=columns, rows=rows, totals=totals, drills=drills)


CB_HOLDINGS = register(
    Report(
        id="cb_holdings",
        group="Collection",
        title="Holdings",
        purpose="What the collection is made of: kind x denomination, with "
        "items, pieces and total cost.",
        params=HoldingsParams,
        run=_cb_holdings,
    )
)


# ---------------------------------------------------------------------------
# cb_designs
# ---------------------------------------------------------------------------


class DesignsParams(LiveParams):
    """`cb_designs` takes no parameters beyond status and disposition."""


_SER = Series.__table__.alias("ser")

_NO_SERIES = "No series"


def _year_span(year_min: int | None, year_max: int | None) -> str:
    """The year span as text: "1878-1904", one year, or empty."""
    if year_min is None and year_max is None:
        return ""
    if year_min == year_max:
        return str(year_min)
    return f"{year_min}-{year_max}"


def _designs_query_string(series_code: str | None, params: DesignsParams) -> str:
    """A series row's own coin search; `missing=series` for "No series"."""
    query: dict[str, str] = {}
    if series_code is not None:
        query["series"] = series_code
    else:
        query["missing"] = "series"
    query.update(status_disposition_params(params))
    return f"/inventory/coins?{urlencode(query)}"


def _cb_designs(db: Session, params: DesignsParams) -> ReportResult:
    """Design series held, across every non-currency kind: items, year span, cost.

    Series is optional for currency: a note's year is its series year, and
    its own design identity is the Friedberg number rather than a series
    (`MISSING_FIELDS["series"]`). Currency is excluded here altogether rather
    than folded into "No series", which would read as a gap in a note that
    has none.
    """
    src: FromClause = _I.join(_K, _K.c.id == _I.c.item_kind_id).outerjoin(
        _SER, _SER.c.id == _I.c.series_id
    )
    src, where = live_where(src, params)
    where.append(_K.c.code != "currency")

    columns = [
        Column("series", "Series", "text"),
        Column("items", "Items", "count"),
        Column("year_span", "Year span", "text"),
        Column("total_cost", "Total cost", "money"),
    ]

    rows_data = (
        db.execute(
            select(
                _SER.c.code.label("series_code"),
                _SER.c.label.label("series_label"),
                func.count().label("items"),
                func.min(_I.c.year_start).label("year_min"),
                func.max(_I.c.year_start).label("year_max"),
                func.sum(_I.c.total_cost).label("total_cost"),
            )
            .select_from(src)
            .where(*where)
            .group_by(_SER.c.id, _SER.c.code, _SER.c.label, _SER.c.sort_order)
            # The vocabulary's own order, as its pickers list it; "No
            # series" (all three NULL) last.
            .order_by(
                _SER.c.sort_order.asc().nulls_last(),
                _SER.c.label.asc().nulls_last(),
                _SER.c.id.asc().nulls_last(),
            )
        )
        .mappings()
        .all()
    )
    if not rows_data:
        return ReportResult(
            columns=columns, rows=[], totals=None, drills=[], notes=[NOTHING_MATCHES]
        )

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for row in rows_data:
        series_code = row["series_code"]
        series_label = row["series_label"] if series_code is not None else _NO_SERIES
        rows.append(
            {
                "series": series_label,
                "items": row["items"],
                "year_span": _year_span(row["year_min"], row["year_max"]),
                "total_cost": row["total_cost"],
            }
        )
        drills.append(_designs_query_string(series_code, params))

    totals: dict[str, object] = {
        "series": "All designs",
        "items": sum(cast(int, r["items"]) for r in rows),
        "year_span": None,
        "total_cost": sum((cast(Decimal, r["total_cost"]) for r in rows), Decimal("0")),
    }

    return ReportResult(columns=columns, rows=rows, totals=totals, drills=drills)


CB_DESIGNS = register(
    Report(
        id="cb_designs",
        group="Collection",
        title="Coins by design",
        purpose="Design series held, across every non-currency kind: items, "
        "year span, and total cost.",
        params=DesignsParams,
        run=_cb_designs,
    )
)


# ---------------------------------------------------------------------------
# cb_notes
# ---------------------------------------------------------------------------


class NotesParams(LiveParams):
    """`cb_notes` takes no parameters beyond status and disposition."""


_NT = NoteType.__table__.alias("nt")
_CUD = CurrencyDetail.__table__.alias("cud")
_SEAL = SealColor.__table__.alias("sc")
_DISTRICT = FedDistrict.__table__.alias("fd")

_NO_NOTE_TYPE = "No note type"
_NO_SERIES_YEAR = "No series year"

#: The attribute codes meaning a star note and a fancy serial
#: (`backend/data/reference/attribute.json`). Looked up by code rather than
#: assumed present, so a vocabulary that ever drops one is a note here, not a
#: silent 0 that reads as "the collection has none".
_STAR_CODE = "star"
_FANCY_SERIAL_CODE = "fancy_serial"


def _attribute_id(db: Session, code: str) -> int | None:
    """The id of the seeded `item_attribute` row named `code`, or None."""
    return db.execute(
        select(ItemAttribute.id).where(ItemAttribute.code == code)
    ).scalar_one_or_none()


def _has_attribute_id(attribute_id: int) -> ColumnElement[bool]:
    """An item carries this attribute, and has not had it removed."""
    link = ItemAttributeLink.__table__.alias()
    return exists(
        select(1).where(
            link.c.inventory_item_id == _I.c.id,
            link.c.item_attribute_id == attribute_id,
            link.c.removed_at.is_(None),
        )
    )


def _notes_query_string(
    note_type_code: str | None, series_designation: str | None, params: NotesParams
) -> str | None:
    """The currency search naming exactly this note type and series designation.

    Only when both are known: either alone would widen the search to more
    than this row's own items.
    """
    if note_type_code is None or series_designation is None:
        return None
    query = {"note_type": note_type_code, "series_designation": series_designation}
    query.update(status_disposition_params(params))
    return f"/inventory/currency?{urlencode(query)}"


def _cb_notes(db: Session, params: NotesParams) -> ReportResult:
    """Note type x series designation: items, seal colors, districts, cost.

    Star notes and fancy serials are counted from the attribute codes that
    mean them, when the vocabulary carries them (it does today); if a future
    vocabulary ever drops one, those columns are omitted rather than shown
    as an all-zero column that reads as "the collection has none".
    """
    src: FromClause = (
        _I.join(_K, _K.c.id == _I.c.item_kind_id)
        .outerjoin(_CUD, _CUD.c.inventory_item_id == _I.c.id)
        .outerjoin(_NT, _NT.c.id == _CUD.c.note_type_id)
    )
    src, where = live_where(src, params)
    where.append(_K.c.code == "currency")

    star_id = _attribute_id(db, _STAR_CODE)
    fancy_id = _attribute_id(db, _FANCY_SERIAL_CODE)

    select_cols = [
        _NT.c.id.label("note_type_id"),
        _NT.c.code.label("note_type_code"),
        _NT.c.label.label("note_type_label"),
        _NT.c.sort_order.label("note_type_sort"),
        _CUD.c.series_designation.label("series_designation"),
        func.count().label("items"),
        func.sum(_I.c.total_cost).label("total_cost"),
    ]
    if star_id is not None:
        select_cols.append(
            func.count().filter(_has_attribute_id(star_id)).label("star_notes")
        )
    if fancy_id is not None:
        select_cols.append(
            func.count().filter(_has_attribute_id(fancy_id)).label("fancy_serials")
        )

    columns = [
        Column("note_type", "Note type", "text"),
        Column("series_designation", "Series", "text"),
        Column("items", "Items", "count"),
        Column("seal_colors", "Seal colors", "text"),
        Column("fed_districts", "Federal Reserve districts", "text"),
    ]
    if star_id is not None:
        columns.append(Column("star_notes", "Star notes", "count"))
    if fancy_id is not None:
        columns.append(Column("fancy_serials", "Fancy serials", "count"))
    columns.append(Column("total_cost", "Total cost", "money"))

    notes: list[str] = []
    if star_id is None or fancy_id is None:
        notes.append(
            "A star-note or fancy-serial column is omitted when the attribute "
            "vocabulary carries no code for it."
        )

    rows_data = (
        db.execute(
            select(*select_cols)
            .select_from(src)
            .where(*where)
            .group_by(
                _NT.c.id,
                _NT.c.code,
                _NT.c.label,
                _NT.c.sort_order,
                _CUD.c.series_designation,
            )
            .order_by(
                _NT.c.sort_order.asc().nulls_last(),
                _NT.c.id.asc().nulls_last(),
                _CUD.c.series_designation.asc().nulls_last(),
            )
        )
        .mappings()
        .all()
    )
    if not rows_data:
        return ReportResult(
            columns=columns, rows=[], totals=None, drills=[], notes=[NOTHING_MATCHES]
        )

    label_rows = (
        db.execute(
            select(
                _NT.c.id.label("note_type_id"),
                _CUD.c.series_designation.label("series_designation"),
                _SEAL.c.label.label("seal_label"),
                _DISTRICT.c.letter.label("district_letter"),
            )
            .select_from(
                src.outerjoin(_SEAL, _SEAL.c.id == _CUD.c.seal_color_id).outerjoin(
                    _DISTRICT, _DISTRICT.c.id == _CUD.c.fed_district_id
                )
            )
            .where(*where)
            .distinct()
        )
        .mappings()
        .all()
    )
    seals: dict[tuple[int | None, str | None], set[str]] = defaultdict(set)
    districts: dict[tuple[int | None, str | None], set[str]] = defaultdict(set)
    for lr in label_rows:
        key = (lr["note_type_id"], lr["series_designation"])
        if lr["seal_label"] is not None:
            seals[key].add(lr["seal_label"])
        if lr["district_letter"] is not None:
            districts[key].add(lr["district_letter"])

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for row in rows_data:
        key = (row["note_type_id"], row["series_designation"])
        entry: dict[str, object] = {
            "note_type": row["note_type_label"] or _NO_NOTE_TYPE,
            "series_designation": row["series_designation"] or _NO_SERIES_YEAR,
            "items": row["items"],
            "seal_colors": ", ".join(sorted(seals.get(key, ()))),
            "fed_districts": ", ".join(sorted(districts.get(key, ()))),
            "total_cost": row["total_cost"],
        }
        if star_id is not None:
            entry["star_notes"] = row["star_notes"]
        if fancy_id is not None:
            entry["fancy_serials"] = row["fancy_serials"]
        rows.append(entry)
        drills.append(
            _notes_query_string(
                row["note_type_code"], row["series_designation"], params
            )
        )

    totals: dict[str, object] = {
        "note_type": "All note types",
        "series_designation": None,
        "items": sum(cast(int, r["items"]) for r in rows),
        "seal_colors": None,
        "fed_districts": None,
        "total_cost": sum((cast(Decimal, r["total_cost"]) for r in rows), Decimal("0")),
    }
    if star_id is not None:
        totals["star_notes"] = sum(cast(int, r["star_notes"]) for r in rows)
    if fancy_id is not None:
        totals["fancy_serials"] = sum(cast(int, r["fancy_serials"]) for r in rows)

    return ReportResult(
        columns=columns, rows=rows, totals=totals, drills=drills, notes=notes
    )


CB_NOTES = register(
    Report(
        id="cb_notes",
        group="Collection",
        title="Notes",
        purpose="Note type x series designation: items, seal colors and "
        "Federal Reserve districts present, star notes and fancy serials, "
        "and total cost.",
        params=NotesParams,
        run=_cb_notes,
    )
)


# ---------------------------------------------------------------------------
# cb_grades
# ---------------------------------------------------------------------------


class GradesParams(LiveParams):
    """`cb_grades` takes no parameters beyond status and disposition."""


_GR = Grade.__table__.alias("g")
_STRIKE = StrikeType.__table__.alias("stk")
_SERVICE = GradingService.__table__.alias("gs")

_NO_STRIKE_TYPE = "No strike type"
_RAW = "Raw"
#: Not "Ungraded": a grade can be a word (Circulated, Ungraded itself) with
#: no `numeric_value` at all, and this band is exactly "no number to band".
_NO_NUMERIC_GRADE = "No numeric grade"

#: Band rank -> (label, `grade_min`, `grade_max`). Rank 5 (no numeric grade)
#: has no entry: there is no search term for it that matches this report's
#: own reach across every item kind, not only the ones `missing=grade`
#: (`MISSING_FIELDS`) applies to.
#: `49%`/`59%`/`64%` (not `49`/`59`/`64`) so a plus grade at the top of a
#: band -- 49+, rank 49.5 -- stays inside it, matching the band's own numeric
#: cutoff on `numeric_value` rather than falling out through `grade_rank`.
_BANDS: dict[int, tuple[str, str, str]] = {
    1: ("1-49", "1", "49%"),
    2: ("50-59", "50", "59%"),
    3: ("60-64", "60", "64%"),
    4: ("65-70", "65", "70%"),
}

#: No numeric grade (no grade at all, or a grade with no numeric value)
#: ranks last; reused, unlabeled, in both the SELECT list and the GROUP BY.
_BAND_RANK = case(
    (or_(_I.c.grade_id.is_(None), _GR.c.numeric_value.is_(None)), 5),
    (_GR.c.numeric_value <= 49, 1),
    (_GR.c.numeric_value <= 59, 2),
    (_GR.c.numeric_value <= 64, 3),
    else_=4,
)


def _grades_drill(
    rows_data: Sequence[RowMapping], row: RowMapping, params: GradesParams
) -> str | None:
    """This band's own search, kept only when no other row would be swept in.

    `grade_min`/`grade_max` state the band exactly, and a present strike
    type or grading service narrows the search exactly too -- together they
    are precisely this row's own group-by key. A missing strike type or
    grading service is left unfiltered instead, since "no strike type" and
    "no grading service" ("Raw") are not something the search can ask for
    directly; leaving it out also lets in any other row that shares this
    view and band but names a strike type or grading service this row does
    not filter on.

    Computed from this report's own rows rather than a second query: the
    group-by already partitions every live item into exactly these
    buckets, so summing the buckets a wider search would also match is
    exact, and the drill is kept only when that sum is this row's own
    count -- no other bucket is being swept in.
    """
    band_rank = row["band_rank"]
    if band_rank not in _BANDS:
        return None
    strike_code = row["strike_code"]
    service_code = row["service_code"]
    swept_in = sum(
        r["items"]
        for r in rows_data
        if r["view"] == row["view"]
        and r["band_rank"] == band_rank
        and (strike_code is None or r["strike_code"] == strike_code)
        and (service_code is None or r["service_code"] == service_code)
    )
    if swept_in != row["items"]:
        return None
    _, low, high = _BANDS[band_rank]
    query: dict[str, str] = {"grade_min": low, "grade_max": high}
    if strike_code is not None:
        query["strike_type"] = strike_code
    if service_code is not None:
        query["grading_service"] = service_code
    query.update(status_disposition_params(params))
    return f"/inventory/{row['view']}?{urlencode(query)}"


def _cb_grades(db: Session, params: GradesParams) -> ReportResult:
    """Band x strike type x grading service, coins and currency both."""
    src: FromClause = (
        _I.join(_K, _K.c.id == _I.c.item_kind_id)
        .outerjoin(_GR, _GR.c.id == _I.c.grade_id)
        .outerjoin(_STRIKE, _STRIKE.c.id == _I.c.strike_type_id)
        .outerjoin(_SERVICE, _SERVICE.c.id == _I.c.grading_service_id)
    )
    src, where = live_where(src, params)

    columns = [
        Column("view", "View", "text"),
        Column("band", "Band", "text"),
        Column("strike_type", "Strike type", "text"),
        Column("grading_service", "Grading service", "text"),
        Column("items", "Items", "count"),
        Column("total_cost", "Total cost", "money"),
    ]

    rows_data = (
        db.execute(
            select(
                _VIEW_NAME.label("view"),
                _BAND_RANK.label("band_rank"),
                _STRIKE.c.code.label("strike_code"),
                _STRIKE.c.label.label("strike_label"),
                _SERVICE.c.code.label("service_code"),
                _SERVICE.c.label.label("service_label"),
                func.count().label("items"),
                func.sum(_I.c.total_cost).label("total_cost"),
            )
            .select_from(src)
            .where(*where)
            .group_by(
                _VIEW_NAME,
                _BAND_RANK,
                _STRIKE.c.code,
                _STRIKE.c.label,
                _SERVICE.c.code,
                _SERVICE.c.label,
            )
            .order_by(
                _VIEW_NAME,
                _BAND_RANK,
                _STRIKE.c.label.asc().nulls_last(),
                _SERVICE.c.label.asc().nulls_last(),
            )
        )
        .mappings()
        .all()
    )
    if not rows_data:
        return ReportResult(
            columns=columns, rows=[], totals=None, drills=[], notes=[NOTHING_MATCHES]
        )

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for row in rows_data:
        band_rank = row["band_rank"]
        band = _BANDS[band_rank][0] if band_rank in _BANDS else _NO_NUMERIC_GRADE
        rows.append(
            {
                "view": "Currency" if row["view"] == CURRENCY_VIEW.name else "Coins",
                "band": band,
                "strike_type": row["strike_label"] or _NO_STRIKE_TYPE,
                "grading_service": row["service_label"] or _RAW,
                "items": row["items"],
                "total_cost": row["total_cost"],
            }
        )
        drills.append(_grades_drill(rows_data, row, params))

    totals: dict[str, object] = {
        "view": "All views",
        "band": None,
        "strike_type": None,
        "grading_service": None,
        "items": sum(cast(int, r["items"]) for r in rows),
        "total_cost": sum((cast(Decimal, r["total_cost"]) for r in rows), Decimal("0")),
    }

    return ReportResult(columns=columns, rows=rows, totals=totals, drills=drills)


CB_GRADES = register(
    Report(
        id="cb_grades",
        group="Collection",
        title="Grades",
        purpose="Grade band x strike type x grading service, across coins "
        "and currency, with items and total cost.",
        params=GradesParams,
        run=_cb_grades,
    )
)


# ---------------------------------------------------------------------------
# cb_metal
# ---------------------------------------------------------------------------


class MetalParams(LiveParams):
    """`cb_metal` takes no parameters beyond status and disposition."""


_MT = Metal.__table__.alias("mt")
_BF = BullionForm.__table__.alias("bf")

_NO_METAL = "No metal"
_NO_BULLION_FORM = "No bullion form"

#: The latest quote for a metal, matching how `item_valuation`
#: (`models/views.py`) reads the spot price -- the newest `quoted_at` --
#: expressed as a correlated subquery rather than that view's `DISTINCT ON`,
#: since this query already groups by `mt.id`.
_LATEST_PRICE = (
    select(MetalPrice.price_per_ozt)
    .where(MetalPrice.metal_id == _MT.c.id)
    .order_by(MetalPrice.quoted_at.desc())
    .limit(1)
    .correlate(_MT)
    .scalar_subquery()
)


def _metal_form(kind_code: str, kind_label: str, bullion_form_label: str | None) -> str:
    """The form label: Coin, the bullion form's label, or else the kind's."""
    if kind_code == "coin":
        return "Coin"
    if kind_code == "bullion":
        return bullion_form_label or _NO_BULLION_FORM
    return kind_label


def _metal_drill(
    kind_code: str,
    metal_code: str | None,
    bullion_form_code: str | None,
    params: MetalParams,
    expected: int,
    bucket_kind_metal_form: int,
    bucket_kind_metal: int,
) -> str | None:
    """This metal x form's own coin search, kept only when its count agrees.

    Currency is never drilled: the currency search has no `metal` or
    `bullion_form` filter at all. A bullion row with no form is never
    drilled either: the search has no `missing=bullion_form` to name "no
    form" with, and leaving `bullion_form=` out entirely would sweep in
    every other form of the same metal.

    For every other row, the fine-weight condition this report groups by
    has no search filter of its own, so the drill is kept only when the
    bucket the search would *actually* run against -- with no fine-weight
    condition at all -- already equals `expected`. That bucket is
    `bucket_kind_metal_form` (kind, metal, bullion form) when the search
    will filter on all three -- a bullion row naming a form -- and
    `bucket_kind_metal` (kind, metal only) otherwise, since a non-bullion
    kind's search never filters on bullion form at all, stray or not, and
    checking the narrower triple there would miss a sibling row that a
    real search sweeps in regardless.

    A "No metal" row uses `missing=metal` where that field applies to the
    kind (`MISSING_FIELDS`); where it does not (a set, say), there is no way
    to ask the search for "no metal" at all, and the row is never drilled.
    """
    if kind_code == "currency":
        return None
    query: dict[str, str] = {"kind": kind_code}
    if metal_code is not None:
        query["metal"] = metal_code
    elif MISSING_FIELDS["metal"].applies_to(kind_code):
        query["missing"] = "metal"
    else:
        return None
    if kind_code == "bullion":
        if bullion_form_code is None:
            return None
        query["bullion_form"] = bullion_form_code
        bucket_total = bucket_kind_metal_form
    else:
        bucket_total = bucket_kind_metal
    if bucket_total != expected:
        return None
    query.update(status_disposition_params(params))
    return f"/inventory/coins?{urlencode(query)}"


def _cb_metal(db: Session, params: MetalParams) -> ReportResult:
    """Metal x form, over items with a fine weight: items, ounces, cost, melt.

    Fine weight is recorded per piece, not per lot -- `item_valuation`
    (`models/views.py`) multiplies it by `piece_count` to reach a lot's total
    melt, and this report's own ounces column matches that rather than
    undercounting a multi-piece row.
    """
    src: FromClause = (
        _I.join(_K, _K.c.id == _I.c.item_kind_id)
        .outerjoin(_MT, _MT.c.id == _I.c.metal_id)
        .outerjoin(_BF, _BF.c.id == _I.c.bullion_form_id)
    )
    src, where = live_where(src, params)

    columns = [
        Column("metal", "Metal", "text"),
        Column("form", "Form", "text"),
        Column("items", "Items", "count"),
        Column("ounces", "Fine troy ounces", "ounces"),
        Column("total_cost", "Total cost", "money"),
        Column("melt", "Melt value", "money"),
    ]

    rows_data = (
        db.execute(
            select(
                _K.c.id.label("kind_id"),
                _MT.c.id.label("metal_id"),
                _BF.c.id.label("bullion_form_id"),
                _MT.c.code.label("metal_code"),
                _MT.c.label.label("metal_label"),
                _MT.c.sort_order.label("metal_sort"),
                _K.c.code.label("kind_code"),
                _K.c.label.label("kind_label"),
                _K.c.sort_order.label("kind_sort"),
                _BF.c.code.label("bullion_form_code"),
                _BF.c.label.label("bullion_form_label"),
                func.count().label("items"),
                func.sum(_I.c.fine_weight_ozt * _I.c.piece_count).label("ounces"),
                func.sum(_I.c.total_cost).label("total_cost"),
                _LATEST_PRICE.label("price_per_ozt"),
            )
            .select_from(src)
            .where(*where, _I.c.fine_weight_ozt.is_not(None))
            .group_by(
                _K.c.id,
                _MT.c.id,
                _BF.c.id,
                _MT.c.code,
                _MT.c.label,
                _MT.c.sort_order,
                _K.c.code,
                _K.c.label,
                _K.c.sort_order,
                _BF.c.code,
                _BF.c.label,
            )
            .order_by(
                _MT.c.sort_order.asc().nulls_last(),
                _MT.c.label.asc().nulls_last(),
                _K.c.sort_order,
                _BF.c.label.asc().nulls_last(),
            )
        )
        .mappings()
        .all()
    )
    if not rows_data:
        return ReportResult(
            columns=columns, rows=[], totals=None, drills=[], notes=[NOTHING_MATCHES]
        )

    #: The same kind x metal x bullion form buckets, counted with no
    #: fine-weight condition at all -- one query, not one per row -- so a
    #: drill can tell whether the bucket the search would actually run
    #: against (which has no fine-weight filter to offer) holds anything
    #: this row does not already count.
    bucket_kind_metal_form: dict[tuple[int, int | None, int | None], int] = {
        (r["kind_id"], r["metal_id"], r["bullion_form_id"]): r["items"]
        for r in db.execute(
            select(
                _K.c.id.label("kind_id"),
                _MT.c.id.label("metal_id"),
                _BF.c.id.label("bullion_form_id"),
                func.count().label("items"),
            )
            .select_from(src)
            .where(*where)
            .group_by(_K.c.id, _MT.c.id, _BF.c.id)
        )
        .mappings()
        .all()
    }
    #: The coarser (kind, metal) bucket a non-bullion row's search actually
    #: matches -- it has no bullion-form filter at all -- summed from the
    #: triple above rather than asked for again: every triple sharing a
    #: (kind, metal) is one more bullion-form value the unfiltered search
    #: would also return.
    bucket_kind_metal: dict[tuple[int, int | None], int] = defaultdict(int)
    for (kind_id, metal_id, _bullion_form_id), count in bucket_kind_metal_form.items():
        bucket_kind_metal[(kind_id, metal_id)] += count

    unpriced_labels: set[str] = set()
    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for row in rows_data:
        price = cast("Decimal | None", row["price_per_ozt"])
        ounces = cast(Decimal, row["ounces"])
        # Rounded once per row (to the row's own total ounces), not once per
        # item and then summed -- a row's melt may therefore differ from the
        # sum of `item_valuation.melt_value` over its items by a cent.
        melt = melt_value(ounces, price) if price is not None else None
        metal_label = cast("str | None", row["metal_label"]) or _NO_METAL
        if price is None:
            unpriced_labels.add(metal_label)
        rows.append(
            {
                "metal": metal_label,
                "form": _metal_form(
                    row["kind_code"], row["kind_label"], row["bullion_form_label"]
                ),
                "items": row["items"],
                "ounces": ounces,
                "total_cost": row["total_cost"],
                "melt": melt,
            }
        )
        drills.append(
            _metal_drill(
                row["kind_code"],
                row["metal_code"],
                row["bullion_form_code"],
                params,
                row["items"],
                bucket_kind_metal_form[
                    (row["kind_id"], row["metal_id"], row["bullion_form_id"])
                ],
                bucket_kind_metal[(row["kind_id"], row["metal_id"])],
            )
        )

    priced_melt = [cast(Decimal, r["melt"]) for r in rows if r["melt"] is not None]
    totals: dict[str, object] = {
        "metal": "All metals",
        "form": None,
        "items": sum(cast(int, r["items"]) for r in rows),
        "ounces": sum((cast(Decimal, r["ounces"]) for r in rows), Decimal("0")),
        "total_cost": sum((cast(Decimal, r["total_cost"]) for r in rows), Decimal("0")),
        # Empty, not zero, when nothing is priced: a total of zero would
        # read as "the priced items are worth nothing" rather than "there
        # is nothing to total".
        "melt": sum(priced_melt, Decimal("0")) if priced_melt else None,
    }

    notes: list[str] = []
    if unpriced_labels:
        names = ", ".join(sorted(unpriced_labels))
        notes.append(f"Melt value leaves out metals with no recorded price: {names}.")

    return ReportResult(
        columns=columns, rows=rows, totals=totals, drills=drills, notes=notes
    )


CB_METAL = register(
    Report(
        id="cb_metal",
        group="Collection",
        title="Precious metal",
        purpose="Metal x form, over items with a fine weight: items, "
        "ounces, cost, and melt value at the latest spot price.",
        params=MetalParams,
        run=_cb_metal,
    )
)


# ---------------------------------------------------------------------------
# cb_attributes
# ---------------------------------------------------------------------------


class AttributesParams(LiveParams):
    """`cb_attributes` takes no parameters beyond status and disposition."""


_MARK_ATTRIBUTE = "Attribute"
_MARK_ERROR = "Error"


def _mark_query_string(
    key: str, code: str, view_name: str, params: AttributesParams
) -> str:
    """This mark's own search, on the view it was carried on."""
    query = {key: code}
    query.update(status_disposition_params(params))
    return f"/inventory/{view_name}?{urlencode(query)}"


def _attribute_rows(db: Session, params: AttributesParams) -> list[dict[str, object]]:
    """One row per attribute x view carried by a live item, not removed."""
    ial = ItemAttributeLink.__table__.alias("ial")
    ia = ItemAttribute.__table__.alias("ia")
    src: FromClause = (
        _I.join(_K, _K.c.id == _I.c.item_kind_id)
        .join(ial, and_(ial.c.inventory_item_id == _I.c.id, ial.c.removed_at.is_(None)))
        .join(ia, ia.c.id == ial.c.item_attribute_id)
    )
    src, where = live_where(src, params)

    rows = (
        db.execute(
            select(
                ia.c.code.label("code"),
                ia.c.label.label("label"),
                ia.c.sort_order.label("sort"),
                _VIEW_NAME.label("view"),
                func.count().label("items"),
            )
            .select_from(src)
            .where(*where)
            .group_by(ia.c.code, ia.c.label, ia.c.sort_order, _VIEW_NAME)
        )
        .mappings()
        .all()
    )
    return [
        {
            "mark": _MARK_ATTRIBUTE,
            "label": row["label"],
            "sort": row["sort"],
            "view": row["view"],
            "items": row["items"],
            "code": row["code"],
        }
        for row in rows
    ]


def _error_rows(db: Session, params: AttributesParams) -> list[dict[str, object]]:
    """One row per error type x view carried by a live item."""
    ie = ItemError.__table__.alias("ie")
    et = ErrorType.__table__.alias("et")
    src: FromClause = (
        _I.join(_K, _K.c.id == _I.c.item_kind_id)
        .join(ie, ie.c.inventory_item_id == _I.c.id)
        .join(et, et.c.id == ie.c.error_type_id)
    )
    src, where = live_where(src, params)

    rows = (
        db.execute(
            select(
                et.c.code.label("code"),
                et.c.label.label("label"),
                et.c.sort_order.label("sort"),
                _VIEW_NAME.label("view"),
                func.count().label("items"),
            )
            .select_from(src)
            .where(*where)
            .group_by(et.c.code, et.c.label, et.c.sort_order, _VIEW_NAME)
        )
        .mappings()
        .all()
    )
    return [
        {
            "mark": _MARK_ERROR,
            "label": row["label"],
            "sort": row["sort"],
            "view": row["view"],
            "items": row["items"],
            "code": row["code"],
        }
        for row in rows
    ]


def _cb_attributes(db: Session, params: AttributesParams) -> ReportResult:
    """Every attribute and error type a live item carries, split by view.

    One row per mark per view: an attribute or error type seeded to apply to
    either kind (`applies_to: any`) can be carried by both a coin and a
    note, and each view has its own search, so a mark present on both is two
    rows rather than one whose drill could only ever open one of them.

    No total: an item can carry several attributes or errors at once, so
    summing these counts is not the number of items with one (the same
    reasoning `dq_issues` gives for skipping its own total).
    """
    columns = [
        Column("mark", "Mark", "text"),
        Column("label", "Label", "text"),
        Column("view", "View", "text"),
        Column("items", "Items", "count"),
    ]
    combined = sorted(
        [*_attribute_rows(db, params), *_error_rows(db, params)],
        key=lambda r: (
            cast(str, r["mark"]) != _MARK_ATTRIBUTE,
            cast(int, r["sort"]),
            cast(str, r["label"]),
            cast(str, r["view"]),
        ),
    )
    if not combined:
        return ReportResult(
            columns=columns, rows=[], totals=None, drills=[], notes=[NOTHING_MATCHES]
        )

    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    for entry in combined:
        view_name = cast(str, entry["view"])
        rows.append(
            {
                "mark": entry["mark"],
                "label": entry["label"],
                "view": "Currency" if view_name == CURRENCY_VIEW.name else "Coins",
                "items": entry["items"],
            }
        )
        key = "attribute" if entry["mark"] == _MARK_ATTRIBUTE else "error_type"
        drills.append(
            _mark_query_string(key, cast(str, entry["code"]), view_name, params)
        )

    return ReportResult(
        columns=columns,
        rows=rows,
        drills=drills,
        notes=[
            "No total: an item can carry several attributes or errors at "
            "once, so summing these counts is not the number of items with "
            "one."
        ],
    )


CB_ATTRIBUTES = register(
    Report(
        id="cb_attributes",
        group="Collection",
        title="Attributes and errors",
        purpose="Every attribute and error type a live item carries, split "
        "by the view it was recorded on, with items.",
        params=AttributesParams,
        run=_cb_attributes,
    )
)
