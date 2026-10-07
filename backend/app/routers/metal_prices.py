"""Spot prices: what a troy ounce of each metal is quoted at.

Manager only. A price is a quote in a time series (`metal_price`), never a
column that is overwritten: recording one adds a row, the newest row for a
metal is its price, and what a holding was worth "as of" an earlier day can
still be asked. So a price typed wrong is corrected by recording the right
one, not by editing the wrong one away.

Melt value is never stored. It is the newest quote times an item's fine
weight, worked out where it is shown -- the `item_valuation` view and the
`cb_metal` report -- so a new quote changes every melt value at once.
"""

from __future__ import annotations

from decimal import Decimal

from fastapi import APIRouter, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..deps import AdminUser, DbSession
from ..live import live_item
from ..models import InventoryItem, Metal, MetalPrice
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


def _held(db: Session) -> dict[int, Decimal]:
    """Fine troy ounces held of each metal, over live items with a fine weight."""
    rows = db.execute(
        select(
            Metal.id,
            func.sum(InventoryItem.fine_weight_ozt * InventoryItem.piece_count),
        )
        .join(InventoryItem, InventoryItem.metal_id == Metal.id)
        .where(live_item(), InventoryItem.fine_weight_ozt.is_not(None))
        .group_by(Metal.id)
    )
    return {
        metal_id: ounces for metal_id, ounces in rows.tuples() if ounces is not None
    }


def _rows(db: Session) -> list[MetalPriceOut]:
    """Every metal in use, with its newest quote and what is held of it."""
    latest = _latest(db)
    held = _held(db)
    metals = db.scalars(
        select(Metal).where(Metal.is_active).order_by(Metal.sort_order, Metal.id)
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
                # To the cent: a price times ounces is money once multiplied.
                melt_value=(ounces * quote.price_per_ozt).quantize(Decimal("0.01"))
                if quote and ounces is not None
                else None,
            )
        )
    return out


@router.get("")
def list_metal_prices(db: DbSession, _admin: AdminUser) -> list[MetalPriceOut]:
    """Each metal's newest spot price, the fine ounces held, and their melt value.

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
