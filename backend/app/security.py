"""Password hashing (argon2) and JWT issuing / verification."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .config import settings

_hasher = PasswordHasher()

TokenType = Literal["access", "refresh"]


def hash_password(plain: str) -> str:
    """Hash a password with argon2, salt included in the returned digest."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Check a password against a stored hash.

    Returns False for a malformed hash as well as a wrong password: a
    corrupted stored value must not authenticate anyone, and must not raise
    into the caller either.
    """
    try:
        _hasher.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False
    return True


def needs_rehash(hashed: str) -> bool:
    """True when argon2 parameters have changed since this hash was made."""
    try:
        return _hasher.check_needs_rehash(hashed)
    except InvalidHashError:
        return False


def _create_token(subject: str, token_type: TokenType, expires: timedelta) -> str:
    """Issue a signed JWT carrying its own type.

    The type is inside the payload so an access token cannot be presented
    where a refresh token is required, or the reverse -- the two have very
    different lifetimes and a swap would silently extend one of them.
    """
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,  # must be a string per RFC 7519
        "type": token_type,
        "iat": now,
        "exp": now + expires,
        "jti": uuid.uuid4().hex,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_access_token(user_id: int) -> str:
    """A short-lived token for ordinary requests."""
    return _create_token(
        str(user_id),
        "access",
        timedelta(minutes=settings.access_token_expire_minutes),
    )


def create_refresh_token(user_id: int) -> str:
    """A long-lived token whose only use is obtaining a new access token."""
    return _create_token(
        str(user_id),
        "refresh",
        timedelta(days=settings.refresh_token_expire_days),
    )


def decode_token(token: str, expected_type: TokenType) -> dict[str, Any]:
    """Decode and validate a token, raising jwt exceptions on failure.

    An access token is not accepted where a refresh token is required and
    vice versa, so a leaked short-lived token cannot be used to mint new ones.
    """
    payload = jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
    if payload.get("type") != expected_type:
        raise jwt.InvalidTokenError(
            f"expected a {expected_type} token, got {payload.get('type')!r}"
        )
    return payload
