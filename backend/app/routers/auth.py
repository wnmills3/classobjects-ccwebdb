"""Registration, login, token refresh and profile."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm

from .. import throttle
from ..config import settings
from ..deps import SHOP_CLOSED, CurrentUser, DbSession
from ..models import User, UserRole
from ..schemas import RefreshRequest, TokenPair, UserCreate, UserOut
from ..security import (
    UNKNOWN_ACCOUNT_HASH,
    account_by_email,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    normalize_email,
    normalize_email_if_valid,
    verify_password,
)
from ._tx import commit_unique

router = APIRouter(prefix="/auth", tags=["auth"])

# One message for every rejection path. Distinguishing an expired
# token from a revoked one would tell an attacker which half of the
# check failed.
_INVALID_REFRESH_TOKEN = "Invalid or expired refresh token"


def _caller_address(request: Request) -> str:
    """The address an attempt came from, as the attempt limits count it."""
    return request.client.host if request.client else "unknown"


def _incorrect_credentials() -> HTTPException:
    """The one refusal for a wrong password and for an unknown address."""
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Incorrect email or password",
        headers={"WWW-Authenticate": "Bearer"},
    )


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: UserCreate, request: Request, db: DbSession) -> User:
    """Self-service registration. Always creates a customer, never an admin.

    A taken address is a 409, which says that address has an account; every
    attempt, whatever its answer, counts against the caller's address
    (`throttle.REGISTRATIONS_BY_ADDRESS`), so the answer can be had for a
    handful of addresses and not for a list.
    """
    # A closed shop takes no customers: an account would only open the
    # catalog the coming-soon page is there to keep shut.
    if not settings.shop_open:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=SHOP_CLOSED)
    address = _caller_address(request)
    throttle.REGISTRATIONS_BY_ADDRESS.refuse_if_spent(address)
    throttle.REGISTRATIONS_BY_ADDRESS.record(address)
    email = normalize_email(payload.email)
    if account_by_email(db, email) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with that email already exists",
        )

    user = User(
        email=email,
        full_name=payload.full_name,
        hashed_password=hash_password(payload.password),
        role=UserRole.customer,
    )
    db.add(user)
    # Two registrations of one email at once both pass the check above;
    # the unique index stops the second, and it should read as the same
    # 409 rather than a 500 -- as `users.create_user` already does.
    commit_unique(db, "An account with that email already exists")
    db.refresh(user)
    return user


@router.post("/login")
def login(
    form: Annotated[OAuth2PasswordRequestForm, Depends()],
    request: Request,
    db: DbSession,
) -> TokenPair:
    """Exchange email and password for a token pair.

    Failed attempts are limited per caller address and per account named
    (`app.throttle`); over either limit the answer is a 429, given before
    the password is checked.
    """
    address = _caller_address(request)
    throttle.SIGN_IN_FAILURES_BY_ADDRESS.refuse_if_spent(address)
    # No address is longer than this, so the text names no account; refused
    # before it is keyed or looked up, and counted against the caller.
    if len(form.username) > throttle.MAX_KEY_LENGTH:
        throttle.SIGN_IN_FAILURES_BY_ADDRESS.record(address)
        raise _incorrect_credentials()
    # OAuth2PasswordRequestForm calls the field "username"; we use the email.
    # Text the validator refuses is looked up as typed (see
    # `normalize_email_if_valid`). The account's count is keyed in lower case,
    # as the lookup matches, so a guesser cannot spread over spellings.
    email = normalize_email_if_valid(form.username)
    account_key = email.lower()
    throttle.SIGN_IN_FAILURES_BY_ACCOUNT.refuse_if_spent(account_key)
    user = account_by_email(db, email)

    # One verification whether or not the address has an account -- against
    # a hash no account holds when it has none -- so both refusals take the
    # time argon2 takes. `user is None` is still what refuses: a password
    # that happened to match that hash signs nobody in.
    verified = verify_password(
        form.password,
        UNKNOWN_ACCOUNT_HASH if user is None else user.hashed_password,
    )
    if user is None or not verified:
        throttle.SIGN_IN_FAILURES_BY_ADDRESS.record(address)
        throttle.SIGN_IN_FAILURES_BY_ACCOUNT.record(account_key)
        raise _incorrect_credentials()
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Account is disabled"
        )

    # The account's failures only: the owner is not locked out by a guesser
    # who failed against it. The address's count is left to age out of its
    # window, or a guesser holding any one account could sign into it
    # between guesses and never meet the limit.
    throttle.SIGN_IN_FAILURES_BY_ACCOUNT.forget(account_key)

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


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(user: CurrentUser, db: DbSession) -> None:
    """Sign the account out everywhere.

    A token cannot be withdrawn one at a time without a store of them, so
    the account's version moves on and every token issued before -- this
    browser's refresh token and any other's -- is refused from now on.
    """
    user.token_version += 1
    db.commit()


@router.get("/me", response_model=UserOut)
def me(user: CurrentUser) -> User:
    """The signed-in user."""
    return user
