"""Sales platforms: the web store, marketplaces, live shows, auction houses.

Admin only. The web store exists from the start and is the one platform that
cannot change kind or be retired -- checkout and the public catalog are
defined by it. Default fees are for estimating a sale's net; a sale records
what was actually charged (selling design).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.exc import StaleDataError

from ..deps import AdminUser, DbSession
from ..models import SalesVenue, SalesVenueKind, Vendor
from ..references import code_to_id
from ..schemas import SalesVenueCreate, SalesVenueOut, SalesVenueUpdate
from ._resolve import (
    found_or_404,
    get_or_422,
    refuse_null_required,
    refuse_stale_version,
)

router = APIRouter(prefix="/sales-venues", tags=["selling"])

_STALE = "This platform was changed by someone else. Reload and reapply your changes."


def _out(db: Session, venue: SalesVenue) -> SalesVenueOut:
    """Shape one platform row for the wire, with its kind and vendor resolved."""
    vendor = db.get(Vendor, venue.vendor_id) if venue.vendor_id else None
    kind = db.get(SalesVenueKind, venue.sales_venue_kind_id)
    return SalesVenueOut(
        code=venue.code,
        name=venue.name,
        kind=kind.code if kind is not None else "",
        is_own_store=venue.is_own_store,
        vendor_id=venue.vendor_id,
        vendor_name=vendor.name if vendor is not None else None,
        account_handle=venue.account_handle,
        listing_url_template=venue.listing_url_template,
        commission_rate=venue.commission_rate,
        processing_rate=venue.processing_rate,
        processing_fixed=venue.processing_fixed,
        listing_fee=venue.listing_fee,
        terms_as_of=venue.terms_as_of,
        notes=venue.notes,
        is_active=venue.is_active,
        version=venue.version,
    )


def _kind_id(db: Session, code: str) -> int:
    """Resolve a `sales_venue_kind` code, refusing `own_store` -- there is only one."""
    if code == "own_store":
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="There is only one web store; choose another kind",
        )
    found = code_to_id(db, SalesVenueKind, code, "kind")
    if found is None:  # code_to_id returns None only for an empty code
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="kind is required"
        )
    return found


#: Columns that are `NOT NULL` on `SalesVenue` but optional on `SalesVenueUpdate`
#: -- omitting one leaves it alone, but an explicit null is a client mistake,
#: not a request to clear a column that cannot be cleared.
_REQUIRED_ON_UPDATE = frozenset({"name", "is_active"})


def _check_vendor(db: Session, vendor_id: int | None, venue_id: int | None) -> None:
    """The purchase source exists and no other platform is linked to it."""
    if vendor_id is None:
        return
    get_or_422(db, Vendor, vendor_id, f"Unknown vendor_id: {vendor_id}")
    other = db.scalar(
        select(SalesVenue).where(
            SalesVenue.vendor_id == vendor_id, SalesVenue.id != (venue_id or 0)
        )
    )
    if other is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"That purchase source is already linked to {other.name}",
        )


@router.get("")
def list_sales_venues(db: DbSession, _admin: AdminUser) -> list[SalesVenueOut]:
    """Every platform, the web store first, retired ones included."""
    venues = db.scalars(
        select(SalesVenue).order_by(SalesVenue.is_own_store.desc(), SalesVenue.name)
    ).all()
    return [_out(db, venue) for venue in venues]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_sales_venue(
    payload: SalesVenueCreate, db: DbSession, _admin: AdminUser
) -> SalesVenueOut:
    """Add a platform the business sells through."""
    data = payload.model_dump()
    kind_id = _kind_id(db, data.pop("kind"))
    _check_vendor(db, data["vendor_id"], None)
    if db.scalar(select(SalesVenue.id).where(SalesVenue.code == data["code"])):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"A platform with code {data['code']} already exists",
        )
    venue = SalesVenue(**data, sales_venue_kind_id=kind_id)
    db.add(venue)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two submissions racing past the checks above.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That platform code or purchase source is already in use",
        ) from exc
    db.refresh(venue)
    return _out(db, venue)


@router.patch("/{code}")
def update_sales_venue(
    code: str, payload: SalesVenueUpdate, db: DbSession, _admin: AdminUser
) -> SalesVenueOut:
    """Change a platform. Send `version` to be told about conflicts."""
    venue = found_or_404(
        db.scalar(select(SalesVenue).where(SalesVenue.code == code)),
        "No such platform",
    )

    # exclude_unset: an omitted field is left alone, an explicit null clears it
    # -- except name and is_active, which are NOT NULL columns and are refused
    # by refuse_null_required below rather than silently left unchanged.
    data: dict[str, Any] = payload.model_dump(exclude_unset=True)
    refuse_stale_version(data.pop("version", None), venue.version, _STALE)
    refuse_null_required(data, _REQUIRED_ON_UPDATE)

    if venue.is_own_store:
        if "kind" in data:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="The web store's kind cannot change",
            )
        if data.get("is_active") is False:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail="The web store cannot be retired",
            )
    if "kind" in data:
        venue.sales_venue_kind_id = _kind_id(db, data.pop("kind"))
    if "vendor_id" in data:
        _check_vendor(db, data["vendor_id"], venue.id)
    for field, value in data.items():
        setattr(venue, field, value)

    try:
        db.commit()
    except StaleDataError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_STALE
        ) from exc
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="That purchase source is already linked to another platform",
        ) from exc
    db.refresh(venue)
    return _out(db, venue)
