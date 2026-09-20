"""Shared FastAPI dependencies: current user resolution and role gates."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from .config import settings
from .database import get_db
from .models import User, UserRole
from .security import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.api_prefix}/auth/login")

DbSession = Annotated[Session, Depends(get_db)]

_credentials_error = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Could not validate credentials",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    db: DbSession,
    token: Annotated[str, Depends(oauth2_scheme)],
) -> User:
    """Resolve the bearer token to the user it belongs to."""
    try:
        payload = decode_token(token, expected_type="access")
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise _credentials_error from None

    user = db.get(User, user_id)
    if user is None:
        raise _credentials_error
    # A token issued before the password last changed is dead. Absent claim
    # means a token minted before this check existed, which is also dead --
    # failing closed is the only safe reading of "I cannot tell".
    if payload.get("tv") != user.token_version:
        raise _credentials_error
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_admin(user: CurrentUser) -> User:
    """Reject anyone who is not an administrator."""
    if user.role is not UserRole.admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required",
        )
    return user


AdminUser = Annotated[User, Depends(require_admin)]

#: The same scheme, but a missing Authorization header is not an error. Used
#: only by `get_optional_user`; every gated endpoint keeps `oauth2_scheme`,
#: which still answers 401.
_optional_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.api_prefix}/auth/login", auto_error=False
)


def get_optional_user(
    db: DbSession,
    token: Annotated[str | None, Depends(_optional_scheme)],
) -> User | None:
    """Who is calling, when the endpoint serves anonymous callers too.

    For a public endpoint that shows an administrator more than it shows a
    buyer -- `routers.catalog.list_catalog` and its `include_inactive`. The
    shop must answer a signed-out browser, so the whole endpoint cannot take
    `CurrentUser`, and a parameter documented as an admin preview cannot be
    honoured on the strength of a caller asking for it.

    Anything short of a good token is `None`, not a 401: an expired or
    revoked one is a caller with no privileges, and on an endpoint that
    serves everyone that is the same as being signed out. Failing closed is
    what makes `None` safe to treat as "show the public view".
    """
    if token is None:
        return None
    try:
        return get_current_user(db, token)
    except HTTPException:
        return None


OptionalUser = Annotated[User | None, Depends(get_optional_user)]


def is_admin(user: User | None) -> bool:
    """Whether this caller is a signed-in administrator."""
    return user is not None and user.role is UserRole.admin
