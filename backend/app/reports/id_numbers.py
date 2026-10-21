"""Collection: every held item a number identifies.

`cb_id_numbers`: one row per live item that holds a serial number -- a note's
on its currency detail, a bullion item's on its coin detail -- or at least
one grading certificate. These are the numbers that tell one piece from
every other of its kind, so the list is what a theft report or an insurance
claim asks for: what each piece is, its metal and fine weight, its serial,
and the grading service and certificate numbers printed on its holder.
Each row opens the item's editor.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from urllib.parse import urlencode

from sqlalchemy import FromClause, exists, or_, select
from sqlalchemy.orm import Session

from ..inventory_search import view_path
from ..models import (
    CoinDetail,
    CurrencyDetail,
    GradingService,
    ItemCertification,
    ItemImage,
    Metal,
)
from .base import Column, Report, ReportResult, among
from .live_params import NOTHING_MATCHES, LiveParams, live_where
from .registry import register
from .tables import ITEM as _I
from .tables import KIND as _K

__all__ = ["CB_ID_NUMBERS"]

_CD = CoinDetail.__table__.alias("cd")
_CUD = CurrencyDetail.__table__.alias("cud")
_MT = Metal.__table__.alias("mt")
_CERT = ItemCertification.__table__
_IMG = ItemImage.__table__

_COLUMNS = [
    Column("item", "Item", "text"),
    Column("description", "Description", "text"),
    Column("kind", "Kind", "text"),
    Column("metal", "Metal", "text"),
    Column("fine_weight_ozt", "Fine weight (ozt)", "ounces"),
    Column("serial_number", "Serial number", "text"),
    Column("grading_service", "Grading service", "text"),
    Column("cert_numbers", "Certificate numbers", "text"),
    Column("photo", "Photo", "text"),
]


def _certificates(
    db: Session, item_ids: list[int]
) -> dict[int, list[tuple[str | None, str]]]:
    """Each item's (grading service label, certificate number), in id order."""
    held: dict[int, list[tuple[str | None, str]]] = defaultdict(list)
    found = db.execute(
        select(_CERT.c.inventory_item_id, GradingService.label, _CERT.c.cert_number)
        .outerjoin(GradingService, GradingService.id == _CERT.c.grading_service_id)
        .where(among(_CERT.c.inventory_item_id, item_ids))
        .order_by(_CERT.c.inventory_item_id, _CERT.c.id)
    ).tuples()
    for item_id, service, number in found:
        held[item_id].append((service, number))
    return held


def _joined(values: list[str | None]) -> str:
    """The different values, as first met, blanks left out."""
    return ", ".join(dict.fromkeys(value for value in values if value))


def _cb_id_numbers(db: Session, params: LiveParams) -> ReportResult:
    """The held items a serial or a certificate identifies, by item code."""
    src: FromClause = (
        _I.join(_K, _K.c.id == _I.c.item_kind_id)
        .outerjoin(_CD, _CD.c.inventory_item_id == _I.c.id)
        .outerjoin(_CUD, _CUD.c.inventory_item_id == _I.c.id)
        .outerjoin(_MT, _MT.c.id == _I.c.metal_id)
    )
    src, where = live_where(src, params)
    certified = exists().where(_CERT.c.inventory_item_id == _I.c.id)
    photographed = exists().where(_IMG.c.inventory_item_id == _I.c.id)
    found = (
        db.execute(
            select(
                _I.c.id,
                _I.c.item_code,
                _I.c.description,
                _I.c.fine_weight_ozt,
                _K.c.code.label("kind_code"),
                _K.c.label.label("kind"),
                _MT.c.label.label("metal"),
                _CD.c.serial_number.label("bullion_serial"),
                _CUD.c.serial_number.label("note_serial"),
                photographed.label("photo"),
            )
            .select_from(src)
            .where(
                *where,
                or_(
                    _CD.c.serial_number.is_not(None),
                    _CUD.c.serial_number.is_not(None),
                    certified,
                ),
            )
            .order_by(_I.c.item_code)
        )
        .mappings()
        .all()
    )
    if not found:
        return ReportResult(
            columns=_COLUMNS, rows=[], drills=[], notes=[NOTHING_MATCHES]
        )

    certificates = _certificates(db, [row["id"] for row in found])
    rows: list[dict[str, object]] = []
    drills: list[str | None] = []
    fine = Decimal("0")
    for row in found:
        certs = certificates.get(row["id"], [])
        rows.append(
            {
                "item": row["item_code"],
                "description": row["description"] or "",
                "kind": row["kind"],
                "metal": row["metal"] or "",
                "fine_weight_ozt": row["fine_weight_ozt"],
                "serial_number": row["bullion_serial"] or row["note_serial"] or "",
                "grading_service": _joined([service for service, _ in certs]),
                "cert_numbers": _joined([number for _, number in certs]),
                "photo": "yes" if row["photo"] else "no",
            }
        )
        drills.append(
            f"{view_path(row['kind_code'])}?{urlencode({'item': row['item_code']})}"
        )
        fine += row["fine_weight_ozt"] or Decimal("0")

    return ReportResult(
        columns=_COLUMNS,
        rows=rows,
        drills=drills,
        totals={
            "item": f"{len(rows)} items",
            "description": None,
            "kind": None,
            "metal": None,
            "fine_weight_ozt": fine,
            "serial_number": None,
            "grading_service": None,
            "cert_numbers": None,
            "photo": None,
        },
        link_column="item",
    )


CB_ID_NUMBERS = register(
    Report(
        id="cb_id_numbers",
        group="Collection",
        title="Serial and certificate numbers",
        purpose="Every item a number identifies: a note's or a bullion "
        "item's serial number, or a grading certificate. What each is, its "
        "metal and fine weight, and the numbers -- the list a theft report "
        "or an insurer asks for. A row opens the item.",
        params=LiveParams,
        run=_cb_id_numbers,
    )
)
