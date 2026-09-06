"""Catalogue endpoints. Reads are public; writes require an administrator."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError

from ..deps import AdminUser, DbSession
from ..models import Coin, CoinKind
from ..schemas import CoinCreate, CoinOut, CoinPage, CoinUpdate

router = APIRouter(prefix="/coins", tags=["coins"])


@router.get("", response_model=CoinPage)
def list_coins(
    db: DbSession,
    q: Annotated[str | None, Query(description="Free text over title/SKU/country")] = None,
    kind: CoinKind | None = None,
    country: str | None = None,
    year_min: int | None = None,
    year_max: int | None = None,
    in_stock: Annotated[bool, Query(description="Only items with quantity > 0")] = False,
    include_inactive: Annotated[
        bool, Query(description="Admin preview of unpublished items")
    ] = False,
    limit: Annotated[int, Query(ge=1, le=200)] = 24,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> CoinPage:
    """Browse the catalogue. Inactive items are hidden by default."""
    filters = []
    if not include_inactive:
        filters.append(Coin.is_active.is_(True))
    if q:
        pattern = f"%{q}%"
        filters.append(
            or_(
                Coin.title.ilike(pattern),
                Coin.sku.ilike(pattern),
                Coin.country.ilike(pattern),
                Coin.denomination.ilike(pattern),
            )
        )
    if kind is not None:
        filters.append(Coin.kind == kind)
    if country:
        filters.append(Coin.country.ilike(country))
    if year_min is not None:
        filters.append(Coin.year >= year_min)
    if year_max is not None:
        filters.append(Coin.year <= year_max)
    if in_stock:
        filters.append(Coin.quantity > 0)

    total = db.scalar(select(func.count()).select_from(Coin).where(*filters)) or 0
    rows = db.scalars(
        select(Coin).where(*filters).order_by(Coin.id.desc()).limit(limit).offset(offset)
    ).all()

    return CoinPage(
        items=[CoinOut.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/{coin_id}", response_model=CoinOut)
def get_coin(coin_id: int, db: DbSession) -> Coin:
    coin = db.get(Coin, coin_id)
    if coin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coin not found")
    return coin


@router.post("", response_model=CoinOut, status_code=status.HTTP_201_CREATED)
def create_coin(payload: CoinCreate, db: DbSession, _admin: AdminUser) -> Coin:
    coin = Coin(**payload.model_dump())
    db.add(coin)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"SKU {payload.sku!r} already exists",
        ) from None
    db.refresh(coin)
    return coin


@router.patch("/{coin_id}", response_model=CoinOut)
def update_coin(
    coin_id: int, payload: CoinUpdate, db: DbSession, _admin: AdminUser
) -> Coin:
    coin = db.get(Coin, coin_id)
    if coin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coin not found")

    # exclude_unset so an omitted field is left alone rather than nulled.
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(coin, field, value)

    db.commit()
    db.refresh(coin)
    return coin


@router.delete("/{coin_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_coin(coin_id: int, db: DbSession, _admin: AdminUser) -> None:
    coin = db.get(Coin, coin_id)
    if coin is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Coin not found")

    if coin.order_items:
        # Preserve order history: retire the item instead of deleting it.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This item appears in existing orders and cannot be deleted. "
                "Set is_active to false to withdraw it from sale."
            ),
        )

    db.delete(coin)
    db.commit()
