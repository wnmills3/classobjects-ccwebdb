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

import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..deps import AdminUser, DbSession
from ..models import Address, AddressKind, Country, Customer
from ..references import code_to_id

router = APIRouter(prefix="/customers", tags=["customers"])

#: E.164: a leading +, a country code that cannot start with 0, then digits.
#: Stored whole rather than split into a country code and the rest, because two
#: columns can disagree and this one cannot. The default country code is a
#: presentation concern and belongs in the form, not the database.
_E164 = re.compile(r"^\+[1-9]\d{6,14}$")


class AddressOut(BaseModel):
    """An address as the API returns it, with its country as a code."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    address_kind: AddressKind
    line1: str
    line2: str | None
    city: str
    region: str | None
    postal_code: str | None
    is_default: bool
    valid_to: object | None


class CustomerOut(BaseModel):
    """A customer and their addresses."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int | None
    display_name: str
    email: EmailStr | None
    phone: str | None
    notes: str | None
    addresses: list[AddressOut] = []


class CustomerUpdate(BaseModel):
    """The fields an administrator may correct on a customer record."""

    model_config = ConfigDict(extra="forbid")

    display_name: str | None = None
    email: EmailStr | None = None
    phone: str | None = None
    notes: str | None = None

    @field_validator("phone")
    @classmethod
    def _e164(cls, value: str | None) -> str | None:
        """Require E.164, so a number is dialable from anywhere.

        A bare '555 1234' is ambiguous the moment a customer is not in the
        same country as the seller, and this collection already has overseas
        buyers in prospect. Empty clears the number.
        """
        if value is None or value.strip() == "":
            return None
        compact = re.sub(r"[\s()\-.]", "", value.strip())
        if not _E164.match(compact):
            raise ValueError("phone must be in international form, e.g. +12125551234")
        return compact


class AddressIn(BaseModel):
    """A new address. Supersedes the previous default of the same kind."""

    model_config = ConfigDict(extra="forbid")

    address_kind: AddressKind = AddressKind.shipping
    line1: Annotated[str, Field(min_length=1, max_length=255)]
    line2: str | None = None
    city: Annotated[str, Field(min_length=1, max_length=128)]
    region: str | None = None
    postal_code: str | None = None
    country: str | None = None
    is_default: bool = True


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
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such customer"
        )

    fields = update.model_dump(exclude_unset=True)
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
    customer = db.get(Customer, customer_id)
    if customer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such customer"
        )

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
