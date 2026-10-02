"""The user account table.

`users` is the login table the whole application uses. The schema references
it from `customer`, `sales_order`, both type catalogs and every table that
records who did something.
"""

from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, enum_column, utcnow

__all__ = ["User", "UserRole"]


class UserRole(enum.StrEnum):
    """What a login may do. Managers see cost basis; customers do not."""

    manager = "manager"
    customer = "customer"


class User(Base):
    """A login. Referenced by customers, orders and every who-did-it column."""

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
