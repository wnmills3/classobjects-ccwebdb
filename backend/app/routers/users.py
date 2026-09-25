"""Account administration: who can sign in, and with what rights.

Distinct from `customers`, which is who you ship to. A person may have both, an
account without ever buying, or a customer record without an account -- guest
checkout creates exactly that. Keeping them apart is why this module manages
only credentials and rights.

Accounts are never deleted, only deactivated. A user who has placed orders
cannot be removed without breaking the history those orders belong to, and
`is_active = false` achieves the same thing reversibly.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from ..deps import AdminUser, DbSession
from ..models import Customer, User, UserRole
from ..order_writes import customer_for_user
from ..schemas import UserOut
from ..security import hash_password
from .customers import CustomerOut

router = APIRouter(prefix="/users", tags=["users"])


class UserUpdate(BaseModel):
    """The fields an administrator may change on someone else's account."""

    model_config = ConfigDict(extra="forbid")

    full_name: str | None = None
    role: UserRole | None = None
    is_active: bool | None = None


class AccountCreate(BaseModel):
    """An account an administrator opens for someone else.

    The role has no default. Self-registration can only ever produce a
    customer; here either role is possible, so an administrator is never made
    by a field left out. The password is the administrator's to set and pass
    on out of band, the same as `PasswordSet` -- there is no mail to send an
    invitation with.
    """

    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    full_name: Annotated[str, Field(max_length=255)] = ""
    role: UserRole
    password: Annotated[str, Field(min_length=8, max_length=128)]


class PasswordSet(BaseModel):
    """A new password, chosen by an administrator.

    There is no mail configuration, so a reset link is not possible. An
    administrator sets the password and tells the person out of band.
    """

    model_config = ConfigDict(extra="forbid")

    password: Annotated[str, Field(min_length=8, max_length=128)]


def _admin_count(db: DbSession) -> int:
    """How many active administrators remain."""
    return (
        db.scalar(
            select(func.count())
            .select_from(User)
            .where(User.role == UserRole.manager, User.is_active.is_(True))
        )
        or 0
    )


def _refuse_last_admin(db: DbSession, target: User, update: UserUpdate) -> None:
    """Stop a change that would leave nobody able to administer the system.

    Demoting or deactivating the final administrator locks every person out
    with no way back through the application at all -- recovery would mean
    editing the database by hand. The check is deliberately about the *result*
    rather than about who is making it: an administrator demoting themselves
    is fine when someone else can still administer, and fatal when not.
    """
    losing_admin = update.role is not None and update.role is not UserRole.manager
    losing_active = update.is_active is False
    if not (losing_admin or losing_active):
        return
    is_currently_admin = target.role is UserRole.manager and target.is_active
    if is_currently_admin and _admin_count(db) <= 1:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                "This is the only active manager. Make another account a "
                "manager first, or nobody will be able to manage the site."
            ),
        )


@router.get("", response_model=list[UserOut])
def list_users(db: DbSession, _: AdminUser) -> list[User]:
    """Every account, oldest first."""
    return list(db.scalars(select(User).order_by(User.id)))


_EMAIL_TAKEN = "An account with that email already exists"


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: AccountCreate, db: DbSession, _: AdminUser) -> User:
    """Open an account for a customer or a fellow administrator.

    The shop's registration makes only customers, and promotion needed the
    person to have registered first; this is how an administrator adds either
    directly.
    """
    if db.scalar(select(User.id).where(User.email == body.email)) is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_EMAIL_TAKEN)

    user = User(
        email=body.email,
        full_name=body.full_name,
        hashed_password=hash_password(body.password),
        role=body.role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError as exc:
        # Two creations of one email at once both pass the check above; the
        # unique index stops the second, and it should read as the same 409.
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=_EMAIL_TAKEN
        ) from exc
    db.refresh(user)
    return user


@router.patch("/{user_id}", response_model=UserOut)
def update_user(user_id: int, update: UserUpdate, db: DbSession, _: AdminUser) -> User:
    """Change a name, a role, or whether the account may sign in."""
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such account"
        )

    _refuse_last_admin(db, user, update)

    if update.full_name is not None:
        user.full_name = update.full_name
    if update.role is not None:
        user.role = update.role
    if update.is_active is not None:
        user.is_active = update.is_active

    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/password", response_model=UserOut)
def set_password(user_id: int, body: PasswordSet, db: DbSession, _: AdminUser) -> User:
    """Set a new password and end every existing session for that account.

    The version bump is the point. Without it the reset changes only what the
    person types next time, while every token issued beforehand keeps working
    until it expires -- which is precisely no use when the reason for the reset
    is that someone should no longer have access.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such account"
        )

    user.hashed_password = hash_password(body.password)
    user.token_version += 1
    db.commit()
    db.refresh(user)
    return user


@router.post("/{user_id}/customer", response_model=CustomerOut)
def customer_for_account(user_id: int, db: DbSession, _: AdminUser) -> Customer:
    """The customer record behind an account, created if it has none yet.

    Lets an administrator place an order for an account holder who has never
    bought anything, and so has no customer record to choose.
    """
    user = db.get(User, user_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="No such account"
        )
    customer = customer_for_user(db, user)
    db.commit()
    db.refresh(customer)
    return customer
