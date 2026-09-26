"""Customer records: who you ship to, and where.

Distinct from `users`, which is who can sign in. A guest checkout creates a
customer with no account at all, and an account holder who has never bought
anything has no customer record -- so the two are separate tables joined by a
nullable `user_id`.

Addresses are superseded rather than edited. A customer moves, and the order
they placed last year must still show where it was actually sent; rewriting the
row in place would quietly rewrite history.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import AdminUser, DbSession
from ..models import Address, Country, Customer
from ..order_writes import Line, place_order
from ..references import code_to_id
from ..schemas import (
    AddressIn,
    AdminOrderCreate,
    CustomerOut,
    CustomerUpdate,
    OrderOut,
)
from ._resolve import get_or_404
from .orders import order_out

router = APIRouter(prefix="/customers", tags=["customers"])


@router.get("", response_model=list[CustomerOut])
def list_customers(db: DbSession, _: AdminUser) -> list[Customer]:
    """Every customer, with their addresses."""
    return list(
        db.scalars(
            select(Customer)
            .options(selectinload(Customer.addresses))
            .order_by(Customer.id)
        )
    )


@router.patch("/{customer_id}", response_model=CustomerOut)
def update_customer(
    customer_id: int, update: CustomerUpdate, db: DbSession, _: AdminUser
) -> Customer:
    """Correct a customer's contact details."""
    customer = get_or_404(db, Customer, customer_id, "No such customer")

    fields = update.model_dump(exclude_unset=True)
    # `display_name` is NOT NULL: an explicit null would reach the database
    # and come back as a 500 rather than a refusal naming the field.
    if "display_name" in fields and fields["display_name"] is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="display_name cannot be null",
        )
    for name, value in fields.items():
        setattr(customer, name, value)

    db.commit()
    db.refresh(customer)
    return customer


@router.post(
    "/{customer_id}/addresses",
    response_model=CustomerOut,
    status_code=status.HTTP_201_CREATED,
)
def add_address(
    customer_id: int, body: AddressIn, db: DbSession, _: AdminUser
) -> Customer:
    """Record a new address, retiring the previous default of the same kind.

    The old row is kept and closed with `valid_to` rather than being changed,
    so an order shipped to the previous address still resolves to where it
    actually went.
    """
    customer = get_or_404(db, Customer, customer_id, "No such customer")

    country_id = code_to_id(db, Country, body.country, "country")

    if body.is_default:
        # The partial unique index allows one default per kind, so the
        # incumbent must be stood down before the new row is inserted.
        previous = db.scalars(
            select(Address).where(
                Address.customer_id == customer_id,
                Address.address_kind == body.address_kind,
                Address.is_default.is_(True),
            )
        ).all()
        for old in previous:
            old.is_default = False
            if old.valid_to is None:
                old.valid_to = datetime.now(UTC).date()
        db.flush()

    db.add(
        Address(
            customer_id=customer_id,
            address_kind=body.address_kind,
            line1=body.line1,
            line2=body.line2,
            city=body.city,
            region=body.region,
            postal_code=body.postal_code,
            country_id=country_id,
            is_default=body.is_default,
            valid_from=datetime.now(UTC).date(),
        )
    )
    db.commit()
    db.refresh(customer)
    return customer


@router.post(
    "/{customer_id}/orders",
    response_model=OrderOut,
    status_code=status.HTTP_201_CREATED,
)
def place_order_for_customer(
    customer_id: int, body: AdminOrderCreate, db: DbSession, admin: AdminUser
) -> OrderOut:
    """Place an order for a customer: a phone, walk-in or account holder's order."""
    customer = get_or_404(db, Customer, customer_id, "No such customer")
    order = place_order(
        db,
        customer,
        [Line(i.listing_id, i.quantity, i.unit_price) for i in body.items],
        placed_by=admin,
        notes=body.notes,
    )
    db.commit()
    return order_out(db, order.id, for_admin=True)
