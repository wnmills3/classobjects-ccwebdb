"""Spot prices: what a troy ounce of each metal is quoted at.

Manager only. A price is a quote in a time series (`metal_price`), never a
column that is overwritten: recording one adds a row, the newest row for a
metal is its price, and what a holding was worth "as of" an earlier day can
still be asked. So a price typed wrong is corrected by recording the right
one, not by editing the wrong one away.

Melt value is never stored. It is the newest quote times an item's fine
weight, worked out where it is shown -- this table, the `cb_metal` report
and the `item_valuation` view -- so a new quote changes every melt value at
once.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..deps import AdminUser, DbSession
from ..live import live_item
from ..models import Disposition, InventoryItem, ItemStatus, Metal, MetalPrice
from ..models.valuation import melt_value
from ..references import require_code
from ..schemas import MetalPriceIn, MetalPriceOut

router = APIRouter(prefix="/metal-prices", tags=["selling"])


def _latest(db: Session) -> dict[int, MetalPrice]:
    """Each metal's newest quote, by metal id."""
    newest = (
        select(MetalPrice.metal_id, func.max(MetalPrice.quoted_at).label("quoted_at"))
        .group_by(MetalPrice.metal_id)
        .subquery()
    )
    quotes = db.scalars(
        select(MetalPrice).join(
            newest,
            (newest.c.metal_id == MetalPrice.metal_id)
            & (newest.c.quoted_at == MetalPrice.quoted_at),
        )
    ).all()
    return {quote.metal_id: quote for quote in quotes}


#: The dispositions of an item still owned and in hand. A coin on offer is
#: still metal in the drawer until it sells.
_IN_HAND = ("held", "listed")


def _held(db: Session) -> dict[int, Decimal]:
    """Fine troy ounces in hand of each metal.

    Over live items that are `received` and either `held` or `listed`; one
    sold, returned, cancelled, missing or still on order is not metal in
    hand. This is wider on purpose than the `cb_metal` report's default of
    received and held, which leaves out what is on offer. An item with no
    fine weight adds nothing, and a metal none of whose items has one is not
    in the result.
    """
    rows = db.execute(
        select(
            Metal.id,
            func.sum(InventoryItem.fine_weight_ozt * InventoryItem.piece_count),
        )
        .join(InventoryItem, InventoryItem.metal_id == Metal.id)
        .join(ItemStatus, ItemStatus.id == InventoryItem.status_id)
        .join(Disposition, Disposition.id == InventoryItem.disposition_id)
        .where(
            live_item(),
            ItemStatus.code == "received",
            Disposition.code.in_(_IN_HAND),
        )
        .group_by(Metal.id)
    )
    return {
        metal_id: ounces for metal_id, ounces in rows.tuples() if ounces is not None
    }


def _rows(db: Session) -> list[MetalPriceOut]:
    """Every metal in use or in hand, with its newest quote and what is held.

    A retired metal is still listed while any of it is in hand: retiring one
    stops it being offered, and what is held of it still has a melt value.
    """
    latest = _latest(db)
    held = _held(db)
    metals = db.scalars(
        select(Metal)
        .where(or_(Metal.is_active, Metal.id.in_(list(held))))
        .order_by(Metal.sort_order, Metal.id)
    ).all()
    out: list[MetalPriceOut] = []
    for metal in metals:
        quote = latest.get(metal.id)
        ounces = held.get(metal.id)
        out.append(
            MetalPriceOut(
                metal=metal.code,
                label=metal.label,
                price_per_ozt=quote.price_per_ozt if quote else None,
                quoted_at=quote.quoted_at if quote else None,
                source=quote.source if quote else None,
                fine_ozt_held=ounces,
                melt_value=melt_value(ounces, quote.price_per_ozt)
                if quote and ounces is not None
                else None,
            )
        )
    return out


@router.get("")
def list_metal_prices(db: DbSession, _admin: AdminUser) -> list[MetalPriceOut]:
    """Each metal's newest spot price, the fine ounces in hand, and their melt value.

    A metal with no quote has no price and no melt value; one nothing is
    held of has no ounces. Both are null rather than zero: "not quoted" and
    "quoted at nothing" are different facts.
    """
    return _rows(db)


@router.post("", status_code=status.HTTP_201_CREATED)
def record_metal_price(
    payload: MetalPriceIn, db: DbSession, _admin: AdminUser
) -> list[MetalPriceOut]:
    """Record a spot price for a metal, as of now, and answer the whole table.

    A new row every time, marked `manual`: the quote before it stays in the
    series. An unknown metal is a 422.
    """
    metal_id = require_code(db, Metal, payload.metal, "metal")
    db.add(MetalPrice(metal_id=metal_id, price_per_ozt=payload.price_per_ozt))
    db.commit()
    return _rows(db)
