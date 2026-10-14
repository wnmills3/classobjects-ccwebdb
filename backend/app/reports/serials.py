"""Collection: notes whose serial numbers match one another.

`cb_serials`: every set of two or more live notes sharing a serial number,
one row per set, which opens the list of its notes. Three parameters say what
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
from decimal import Decimal
from typing import Literal, cast
from urllib.parse import urlencode

from pydantic import Field
from sqlalchemy import FromClause, select
from sqlalchemy.orm import Session

from ..inventory_search import view_path
from ..models import CurrencyDetail, Denomination, FedDistrict
from ..serial_patterns import digits_of
from .base import ZERO_MONEY, Column, Report, ReportResult
from .live_params import LiveParams, live_where
from .registry import register
from .tables import ITEM as _I

__all__ = ["CB_SERIALS", "NO_MATCHES", "SerialsParams"]

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
    digit nor a star counts as a letter, on the side of the digits it is;
    one between two digits is on neither side and is left out.
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


class _Joined:
    """Which of the indexes `0 .. size - 1` have been joined into one set."""

    def __init__(self, size: int) -> None:
        """Every index alone, at first."""
        self._parent = list(range(size))

    def root(self, i: int) -> int:
        """The index that stands for `i`'s set."""
        parent = self._parent
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def join(self, i: int, into: int) -> None:
        """Put `i`'s set into `into`'s."""
        self._parent[self.root(i)] = self.root(into)

    def sets(self) -> list[list[int]]:
        """The sets of two or more, each in index order."""
        gathered: dict[int, list[int]] = defaultdict(list)
        for i in range(len(self._parent)):
            gathered[self.root(i)].append(i)
        return [members for members in gathered.values() if len(members) > 1]


def _key(serial: _Serial, denomination: int | None, p: SerialsParams) -> Hashable:
    """What two notes must share to match on these settings."""
    if p.letters == "ignore":
        star = serial.star != "" if p.stars == "only_other_stars" else None
        # The letters' places held empty: one shape of key in either mode.
        return (denomination, None, serial.digits, None, star)
    return (denomination, serial.prefix, serial.digits, serial.suffix, serial.star)


def _file_for_stars(
    serial: _Serial,
    i: int,
    denomination: int | None,
    plain: dict[Hashable, list[int]],
    stars: list[tuple[Hashable, int]],
) -> None:
    """File note `i` by the letters a star note at either end would still have.

    A plain note goes under both ends; a star note goes in `stars`, under the
    end its star stands at, to be matched against them.
    """
    front = ("front", denomination, serial.digits, serial.suffix)
    end = ("end", denomination, serial.prefix, serial.digits)
    if serial.star == "":
        plain[front].append(i)
        plain[end].append(i)
    else:
        stars.append((front if serial.star == "front" else end, i))


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
    joined = _Joined(len(serials))
    first: dict[Hashable, int] = {}
    #: Plain notes by the letters a star note at that end would still have.
    plain: dict[Hashable, list[int]] = defaultdict(list)
    stars: list[tuple[Hashable, int]] = []

    for i, serial in enumerate(serials):
        d = denominations[i] if p.denomination == "must_match" else None
        key = _key(serial, d, p)
        if key in first:
            joined.join(i, into=first[key])
        else:
            first[key] = i
        if wild:
            _file_for_stars(serial, i, d, plain, stars)

    # After every plain note is filed, so a star note read first still finds
    # them. Plain notes are joined only through a star note, never by the
    # one letter they differ in.
    for wanted, i in stars:
        for other in plain.get(wanted, ()):
            joined.join(other, into=i)
    return joined.sets()


_COLUMNS = [
    Column("digits", "Serial digits", "text"),
    Column("notes", "Notes", "count"),
    Column("stars", "Star notes", "count"),
    Column("serials", "Serial numbers", "text"),
    Column("denominations", "Denominations", "text"),
    Column("series", "Series", "text"),
    Column("districts", "Districts", "text"),
    Column("total_cost", "Total cost", "money"),
]


def _listed(values: Sequence[object]) -> str:
    """The different values among `values`, as first met, blanks left out."""
    return ", ".join(dict.fromkeys(str(value) for value in values if value))


#: Shown in place of rows when no two notes match.
NO_MATCHES = "No two notes match on these settings."


def _cb_serials(db: Session, params: SerialsParams) -> ReportResult:
    """Every set of live notes whose serial numbers match, a row per set.

    A row says what its notes are -- how many, how many of them star notes,
    the serials as written, and the denominations, series and districts
    among them -- and opens the Currency list of exactly those notes
    (`item_codes=`), whatever status they are in now. The sets are in order
    of their digits; the notes and the cost are totalled. A serial with no
    digit in it is matched with nothing.
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
        held = [notes[i] for i in members]
        rows.append(
            {
                "digits": held[0][1].digits,
                "notes": len(held),
                "stars": sum(1 for _, parsed in held if parsed.star),
                "serials": _listed([row["serial_number"] for row, _ in held]),
                "denominations": _listed([row["denomination"] for row, _ in held]),
                "series": _listed([row["series_designation"] for row, _ in held]),
                "districts": _listed([row["district"] for row, _ in held]),
                "total_cost": sum((row["total_cost"] for row, _ in held), ZERO_MONEY),
            }
        )
        codes = ",".join(row["item_code"] for row, _ in held)
        drills.append(f"{path}?{urlencode({'item_codes': codes})}")

    return ReportResult(
        columns=_COLUMNS,
        rows=rows,
        drills=drills,
        totals={
            "digits": f"{len(rows)} sets",
            "notes": sum(cast(int, row["notes"]) for row in rows),
            "stars": sum(cast(int, row["stars"]) for row in rows),
            "serials": None,
            "denominations": None,
            "series": None,
            "districts": None,
            "total_cost": sum(
                (cast(Decimal, row["total_cost"]) for row in rows), ZERO_MONEY
            ),
        },
    )


CB_SERIALS = register(
    Report(
        id="cb_serials",
        group="Collection",
        title="Matching serial numbers",
        purpose="Sets of notes whose serial numbers match one another: on the "
        "digits alone or with the letters, on any denomination or the same "
        "one, a star note beside the note without the star or not. A set "
        "opens the list of its notes.",
        params=SerialsParams,
        run=_cb_serials,
    )
)
