"""Registration, login, token refresh and profile."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select

from ..deps import CurrentUser, DbSession
from ..models import User, UserRole
from ..schemas import RefreshRequest, TokenPair, UserCreate, UserOut
from ..security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# One message for every rejection path. Distinguishing an expired
# token from a revoked one would tell an attacker which half of the
# check failed.
_INVALID_REFRESH_TOKEN = "Invalid or expired refresh token"


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, db: DbSession) -> User:
    """Self-service registration. Always creates a customer, never an admin."""
    existing = db.scalar(select(User).where(User.email == payload.email))
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        )

    user = User(
        email=payload.email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=UserRole.customer,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login")
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    db: DbSession,
) -> TokenPair:
    """Exchange email and password for a token pair."""
    # OAuth2PasswordRequestForm calls the field "username"; we use the email.
    user = db.scalar(select(User).where(User.email == form.username))

    # Verify even when the user is missing would be ideal to equalise timing;
    # argon2 is slow enough that we simply fail closed here.
    if user is None or not verify_password(form.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    # Transparently upgrade the stored hash if argon2 parameters changed.
    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(form.password)
        db.commit()

    return TokenPair(
        access_token=create_access_token(user.id, user.token_version),
        refresh_token=create_refresh_token(user.id, user.token_version),
    )


@router.post("/refresh")
def refresh(payload: RefreshRequest, db: DbSession) -> TokenPair:
    """Exchange a refresh token for a fresh pair.

    The token type is checked, so an access token cannot be used here to
    extend its own lifetime indefinitely.
    """
    try:
        decoded = decode_token(payload.refresh_token, expected_type="refresh")
        user_id = int(decoded["sub"])
    except (jwt.PyJWTError, KeyError, TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_REFRESH_TOKEN,
        ) from None

    user = db.get(User, user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_REFRESH_TOKEN,
        )
    # Without this the revocation is decorative: a refresh token issued before
    # the password changed would mint a brand-new access token carrying the
    # *current* version, and the reset would have revoked nothing at all.
    if decoded.get("tv") != user.token_version:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_INVALID_REFRESH_TOKEN,
        )

    return TokenPair(
        access_token=create_access_token(user.id, user.token_version),
        refresh_token=create_refresh_token(user.id, user.token_version),
    )


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    """The signed-in user."""
    return user
