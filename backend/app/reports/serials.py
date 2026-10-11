"""Collection: notes whose serial numbers match one another.

`cb_serials`: every set of two or more live notes sharing a serial number,
one row per note, the sets one after another. Three parameters say what
"sharing" means -- whether the denomination must match too, whether the
letters round the digits count, and whether a star note matches the note
without the star.

The serials are read once and matched here rather than in SQL: a star
stands where a letter would, so with the letters counted a star note matches
notes it is not equal to, which no `GROUP BY` says.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Hashable, Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlencode

from pydantic import Field
from sqlalchemy import FromClause, select
from sqlalchemy.orm import Session

from ..inventory_search import view_path
from ..models import CurrencyDetail, Denomination, FedDistrict
from ..serial_patterns import digits_of
from .base import Column, Report, ReportResult
from .live_params import LiveParams, live_where
from .registry import register
from .tables import ITEM as _I

__all__ = ["CB_SERIALS", "SerialsParams"]

_CUD = CurrencyDetail.__table__.alias("cud")
_D = Denomination.__table__.alias("d")
_FD = FedDistrict.__table__.alias("fd")

_STAR = "*"


class SerialsParams(LiveParams):
    """What makes two serial numbers a match, atop status and disposition.

    The defaults are the widest reading: the digits alone, on any
    denomination, a star note beside the note without the star.
    """

    denomination: Literal["any", "must_match"] = Field(
        default="any", title="Denomination"
    )
    letters: Literal["ignore", "must_match"] = Field(default="ignore", title="Letters")
    stars: Literal["match_plain_notes", "only_other_stars"] = Field(
        default="match_plain_notes", title="Star notes"
    )


@dataclass(frozen=True)
class _Serial:
    """A serial number taken apart: its digits and the letters round them.

    `star` is where the star stands -- `"front"`, `"end"` or `""` for none --
    and is not among the letters.
    """

    digits: str
    prefix: str
    suffix: str
    star: str


def _parse(serial: str) -> _Serial | None:
    """`serial` taken apart, or None when it has no digit to match on.

    Case and spacing are not part of a serial. Anything that is neither a
    digit nor a star counts as a letter, on the side of the digits it is.
    """
    text = "".join(serial.split()).upper()
    digits = digits_of(text)
    if not digits:
        return None
    first = next(i for i, ch in enumerate(text) if ch.isdigit())
    last = max(i for i, ch in enumerate(text) if ch.isdigit())
    before, after = text[:first], text[last + 1 :]
    star = ""
    if _STAR in text:
        star = "front" if _STAR in before else "end"
    return _Serial(
        digits=digits,
        prefix=before.replace(_STAR, ""),
        suffix=after.replace(_STAR, ""),
        star=star,
    )


def _sets(
    serials: Sequence[_Serial], denominations: Sequence[int | None], p: SerialsParams
) -> list[list[int]]:
    """The indexes of `serials` gathered into sets of two or more that match.

    Each note has one key, and notes of one key are a set. With the letters
    counted and stars matching plain notes, a star note also joins every
    plain note that agrees with it on the letters it has: the star stands
    where the other letter would. Two plain notes brought together only by
    a star note between them are one set, the star note's.
    """
    wild = p.letters == "must_match" and p.stars == "match_plain_notes"
    parent = list(range(len(serials)))

    def root(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    first: dict[Hashable, int] = {}
    #: Plain notes by the letters a star note at that end would still have.
    plain: dict[Hashable, list[int]] = defaultdict(list)
    stars: list[tuple[Hashable, int]] = []

    for i, serial in enumerate(serials):
        d = denominations[i] if p.denomination == "must_match" else None
        key: Hashable
        if p.letters == "ignore":
            star = serial.star != "" if p.stars == "only_other_stars" else None
            key = (d, serial.digits, star)
        else:
            key = (d, serial.prefix, serial.digits, serial.suffix, serial.star)
        if key in first:
            parent[root(i)] = root(first[key])
        else:
            first[key] = i
        if not wild:
            continue
        front = ("front", d, serial.digits, serial.suffix)
        end = ("end", d, serial.prefix, serial.digits)
        if serial.star == "":
            plain[front].append(i)
            plain[end].append(i)
        else:
            stars.append((front if serial.star == "front" else end, i))

    # After every plain note is filed, so a star note read first still finds
    # them. Plain notes are joined only through a star note, never by the
    # one letter they differ in.
    for wanted, i in stars:
        for other in plain.get(wanted, ()):
            parent[root(other)] = root(i)

    gathered: dict[int, list[int]] = defaultdict(list)
    for i in range(len(serials)):
        gathered[root(i)].append(i)
    return [members for members in gathered.values() if len(members) > 1]


_COLUMNS = [
    Column("digits", "Serial digits", "text"),
    Column("notes", "Notes in set", "count"),
    Column("item", "Item", "text"),
    Column("serial", "Serial number", "text"),
    Column("star", "Star", "text"),
    Column("denomination", "Denomination", "text"),
    Column("series", "Series", "text"),
    Column("district", "District", "text"),
    Column("total_cost", "Total cost", "money"),
]

#: Shown in place of rows when no two notes match.
NO_MATCHES = "No two notes match on these settings."


def _cb_serials(db: Session, params: SerialsParams) -> ReportResult:
    """Every set of live notes whose serial numbers match, a row per note.

    The sets are in order of their digits and the notes of a set in order
    of item code, so a set reads as consecutive rows. Each row opens its
    item. A serial with no digit in it is matched with nothing.
    """
    src: FromClause = (
        _I.join(_CUD, _CUD.c.inventory_item_id == _I.c.id)
        .outerjoin(_D, _D.c.id == _I.c.denomination_id)
        .outerjoin(_FD, _FD.c.id == _CUD.c.fed_district_id)
    )
    src, where = live_where(src, params)
    found = (
        db.execute(
            select(
                _I.c.item_code,
                _I.c.denomination_id,
                _I.c.total_cost,
                _CUD.c.serial_number,
                _CUD.c.series_designation,
                _D.c.label.label("denomination"),
                _FD.c.letter.label("district"),
            )
            .select_from(src)
            .where(*where, _CUD.c.serial_number.is_not(None))
            .order_by(_I.c.item_code)
        )
        .mappings()
        .all()
    )
    notes = [(row, parsed) for row in found if (parsed := _parse(row["serial_number"]))]
    sets = _sets(
        [parsed for _, parsed in notes],
        [row["denomination_id"] for row, _ in notes],
        params,
    )
    if not sets:
        return ReportResult(columns=_COLUMNS, rows=[], notes=[NO_MATCHES])

    path = view_path("currency")
    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    # Members are in item-code order already; a set is placed by its digits,
    # then by its first item.
    for members in sorted(sets, key=lambda m: (notes[m[0]][1].digits, m[0])):
        for i in members:
            row, parsed = notes[i]
            rows.append(
                {
                    "digits": parsed.digits,
                    "notes": len(members),
                    "item": row["item_code"],
                    "serial": row["serial_number"],
                    "star": "Star" if parsed.star else "",
                    "denomination": row["denomination"] or "",
                    "series": row["series_designation"] or "",
                    "district": row["district"] or "",
                    "total_cost": row["total_cost"],
                }
            )
            drills.append(f"{path}?{urlencode({'item': row['item_code']})}")

    return ReportResult(
        columns=_COLUMNS,
        rows=rows,
        drills=drills,
        link_column="item",
        notes=[f"{len(sets)} sets, {len(rows)} notes."],
    )


CB_SERIALS = register(
    Report(
        id="cb_serials",
        group="Collection",
        title="Matching serial numbers",
        purpose="Notes whose serial numbers match one another, a set after a "
        "set: on the digits alone or with the letters, on any denomination or "
        "the same one, a star note beside the note without the star or not.",
        params=SerialsParams,
        run=_cb_serials,
    )
)
