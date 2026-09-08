"""The user account table.

This module is what remains of the original storefront scaffold. Its flat
`coins` catalogue and its `orders` tables were superseded by the target schema
-- `inventory_item`, `listing`, `sales_order` -- and have been removed.

`users` was never scaffold: it is the login table the whole application uses,
and the target schema references it from `customer`, `item_status_history`,
`location_history` and both type catalogues.

One rename outlived the tables. The scaffold's PostgreSQL enum type was called
``item_kind``, and the target schema has a reference *table* of that name; in
PostgreSQL a table implicitly creates a composite type, so the two collided.
The enum was renamed to ``coin_kind`` and then dropped with `coins`.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, enum_column, utcnow

__all__ = ["User", "UserRole"]


class UserRole(enum.StrEnum):
    """What a login may do. Administrators see cost basis; customers do not."""

    admin = "admin"
    customer = "customer"


class User(Base):
    """A login. Referenced by customer, history rows and both type catalogues."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(
        String(255), unique=True, index=True, nullable=False
    )
    full_name: Mapped[str] = mapped_column(String(255), default="", nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[UserRole] = mapped_column(
        enum_column(UserRole, "user_role"),
        default=UserRole.customer,
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: Bumped whenever the password changes, and carried inside every token.
    #:
    #: Tokens are stateless JWTs with no server-side store, so without this a
    #: password reset changes only what the person types next time -- every
    #: token issued before the reset keeps working until it expires. That is
    #: useless for the case the reset exists for, which is revoking access to
    #: an account that should no longer have it.
    token_version: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
