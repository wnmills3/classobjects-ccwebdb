"""Registration, login, token handling and role resolution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
import pytest
from app.config import settings
from app.models import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from tests.conftest import CUSTOMER_PASSWORD


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


# --------------------------------------------------------------------------
# Registration
# --------------------------------------------------------------------------


def test_register_creates_customer(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register",
        json={"email": "new@example.com", "password": "longenough", "full_name": "New"},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["email"] == "new@example.com"
    assert body["role"] == "customer"
    assert "hashed_password" not in body
    assert "password" not in body


def test_a_closed_shop_takes_no_registrations(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "shop_open", False)
    response = client.post(
        "/api/auth/register",
        json={"email": "early@example.com", "password": "longenough"},
    )
    assert response.status_code == 403
    assert response.json() == {"detail": "The shop is not open yet"}
    assert db.query(User).filter(User.email == "early@example.com").count() == 0


def test_register_cannot_self_assign_admin(client: TestClient, db: Session) -> None:
    """A caller must not be able to become an admin by passing extra fields."""
    response = client.post(
        "/api/auth/register",
        json={
            "email": "sneaky@example.com",
            "password": "longenough",
            "role": "manager",
        },
    )
    assert response.status_code == 201
    assert response.json()["role"] == "customer"

    stored = db.query(User).filter(User.email == "sneaky@example.com").one()
    assert stored.role is UserRole.customer


def test_register_rejects_duplicate_email(client: TestClient) -> None:
    payload = {"email": "dupe@example.com", "password": "longenough"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    assert client.post("/api/auth/register", json=payload).status_code == 409


def test_register_losing_a_race_for_an_email_is_a_409_not_a_500(
    client: TestClient, db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two registrations at once both pass the duplicate check.

    The loser's check is made to miss the winner's row, as it would have
    mid-race; the unique index then stops the insert, which must still read
    as the 409, not an unhandled IntegrityError.
    """
    payload = {"email": "race@example.com", "password": "longenough"}
    assert client.post("/api/auth/register", json=payload).status_code == 201
    monkeypatch.setattr(db, "scalar", lambda *_args, **_kwargs: None)

    response = client.post("/api/auth/register", json=payload)
    assert response.status_code == 409, response.text


@pytest.mark.parametrize(
    "password,reason",
    [("short", "under 8 characters"), ("", "empty")],
)
def test_register_rejects_weak_password(
    client: TestClient, password: str, reason: str
) -> None:
    response = client.post(
        "/api/auth/register",
        json={"email": "weak@example.com", "password": password},
    )
    assert response.status_code == 422, reason


def test_register_rejects_malformed_email(client: TestClient) -> None:
    response = client.post(
        "/api/auth/register", json={"email": "not-an-email", "password": "longenough"}
    )
    assert response.status_code == 422


def test_password_is_hashed_not_stored_plaintext(
    client: TestClient, db: Session
) -> None:
    client.post(
        "/api/auth/register",
        json={"email": "hash@example.com", "password": "supersecret123"},
    )
    stored = db.query(User).filter(User.email == "hash@example.com").one()
    assert stored.hashed_password != "supersecret123"
    assert stored.hashed_password.startswith("$argon2")


# --------------------------------------------------------------------------
# Login
# --------------------------------------------------------------------------


def test_login_returns_token_pair(client: TestClient, customer_user: User) -> None:
    response = client.post(
        "/api/auth/login",
        data={"username": customer_user.email, "password": CUSTOMER_PASSWORD},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]
    assert body["refresh_token"]
    assert body["access_token"] != body["refresh_token"]


def test_login_rejects_wrong_password(client: TestClient, customer_user: User) -> None:
    response = client.post(
        "/api/auth/login",
        data={"username": customer_user.email, "password": "wrongpassword"},
    )
    assert response.status_code == 401


def test_login_rejects_unknown_email(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        data={"username": "ghost@example.com", "password": "whatever123"},
    )
    assert response.status_code == 401


def test_login_rejects_disabled_account(
    client: TestClient, customer_user: User, db: Session
) -> None:
    customer_user.is_active = False
    db.commit()
    response = client.post(
        "/api/auth/login",
        data={"username": customer_user.email, "password": CUSTOMER_PASSWORD},
    )
    assert response.status_code == 403


def test_login_finds_an_address_registered_with_capitals_in_its_domain(
    client: TestClient,
) -> None:
    """Whatever registration stored for an address, login must look up too.

    Registration validates the address, which normalizes its domain; a
    login that compared the text as typed would refuse the very address the
    account was opened with, in the next request.
    """
    typed = "ada@Example.COM"
    registered = client.post(
        "/api/auth/register", json={"email": typed, "password": "longenough"}
    )
    assert registered.status_code == 201, registered.text

    response = client.post(
        "/api/auth/login", data={"username": typed, "password": "longenough"}
    )
    assert response.status_code == 200, response.text

    # And by the address as it was stored, which is the one shown back.
    stored = client.post(
        "/api/auth/login",
        data={"username": registered.json()["email"], "password": "longenough"},
    )
    assert stored.status_code == 200, stored.text


def test_login_finds_an_account_whatever_the_case_of_its_local_part(
    client: TestClient,
) -> None:
    """`Bob@` and `bob@` are one mailbox in practice, so one account."""
    registered = client.post(
        "/api/auth/register",
        json={"email": "Bob.Case@example.com", "password": "longenough"},
    )
    assert registered.status_code == 201, registered.text

    response = client.post(
        "/api/auth/login",
        data={"username": "bob.case@EXAMPLE.com", "password": "longenough"},
    )
    assert response.status_code == 200, response.text


def test_register_refuses_an_address_taken_in_another_case(
    client: TestClient, db: Session
) -> None:
    """A second spelling of a taken address is the same 409 as the first."""
    first = client.post(
        "/api/auth/register",
        json={"email": "carol@example.com", "password": "longenough"},
    )
    assert first.status_code == 201, first.text

    second = client.post(
        "/api/auth/register",
        json={"email": "Carol@Example.com", "password": "longenough"},
    )
    assert second.status_code == 409, second.text
    assert db.query(User).filter(User.email.ilike("carol@example.com")).count() == 1


def test_an_unknown_address_costs_one_password_verification_like_a_wrong_password(
    client: TestClient, customer_user: User, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both refusals do the same work, so neither answers sooner than the other.

    Verification is the slow step. Skipped for an address with no account,
    the time a refusal takes would say which addresses have one. Time is not
    asserted, only the mechanism: one verification either way.
    """
    from app.routers import auth

    real = auth.verify_password
    calls: list[str] = []

    def counted(plain: str, hashed: str) -> bool:
        """Verify as usual, remembering that it was asked."""
        calls.append(plain)
        return real(plain, hashed)

    monkeypatch.setattr(auth, "verify_password", counted)

    unknown = client.post(
        "/api/auth/login",
        data={"username": "ghost@example.com", "password": "whatever123"},
    )
    assert calls == ["whatever123"]

    wrong = client.post(
        "/api/auth/login",
        data={"username": customer_user.email, "password": "wrongpassword"},
    )
    assert calls == ["whatever123", "wrongpassword"]

    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json() == wrong.json()


def test_error_message_does_not_reveal_whether_email_exists(
    client: TestClient, customer_user: User
) -> None:
    """Wrong password and unknown user must be indistinguishable."""
    unknown = client.post(
        "/api/auth/login",
        data={"username": "ghost@example.com", "password": "whatever123"},
    )
    wrong = client.post(
        "/api/auth/login",
        data={"username": customer_user.email, "password": "wrongpassword"},
    )
    assert unknown.status_code == wrong.status_code
    assert unknown.json()["detail"] == wrong.json()["detail"]


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------


def test_me_returns_current_user(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    response = client.get("/api/auth/me", headers=customer_headers)
    assert response.status_code == 200
    assert response.json()["email"] == "customer@example.com"


def test_me_requires_a_token(client: TestClient) -> None:
    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_garbage_token(client: TestClient) -> None:
    response = client.get("/api/auth/me", headers={"Authorization": "Bearer not.a.jwt"})
    assert response.status_code == 401


def test_refresh_issues_new_tokens(client: TestClient, customer_user: User) -> None:
    login = client.post(
        "/api/auth/login",
        data={"username": customer_user.email, "password": CUSTOMER_PASSWORD},
    ).json()
    response = client.post(
        "/api/auth/refresh", json={"refresh_token": login["refresh_token"]}
    )
    assert response.status_code == 200
    assert response.json()["access_token"]


def test_refresh_token_is_not_accepted_as_access_token(
    client: TestClient, customer_user: User
) -> None:
    """A refresh token is refused where an access token is required.

    The other direction is the next test.
    """
    login = client.post(
        "/api/auth/login",
        data={"username": customer_user.email, "password": CUSTOMER_PASSWORD},
    ).json()
    response = client.get(
        "/api/auth/me",
        headers={"Authorization": f"Bearer {login['refresh_token']}"},
    )
    assert response.status_code == 401


def test_access_token_is_not_accepted_as_refresh_token(
    client: TestClient, customer_user: User
) -> None:
    login = client.post(
        "/api/auth/login",
        data={"username": customer_user.email, "password": CUSTOMER_PASSWORD},
    ).json()
    response = client.post(
        "/api/auth/refresh", json={"refresh_token": login["access_token"]}
    )
    assert response.status_code == 401


def _access_claims(user: User, *, expires_in: timedelta) -> dict[str, object]:
    """Every claim a good access token for this user carries.

    Complete on purpose: a token missing its user, its type or its version is
    refused for that, whatever else is wrong with it, so a test of one check
    has to hand over a token every *other* check accepts.
    """
    now = datetime.now(UTC)
    return {
        "sub": str(user.id),
        "type": "access",
        "iat": now,
        "exp": now + expires_in,
        "tv": user.token_version,
    }


def _me(client: TestClient, claims: dict[str, object], secret: str) -> int:
    """The status `GET /api/auth/me` gives a token of these claims and signature."""
    token = jwt.encode(claims, secret, algorithm=settings.jwt_algorithm)
    return client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    ).status_code


def test_token_signed_with_wrong_secret_is_rejected(
    client: TestClient, customer_user: User
) -> None:
    """The signature is what is refused, with every other claim in order.

    The same claims under the real secret are accepted first: without that,
    a token refused for its user or its version would pass this test with
    signature checking switched off.
    """
    claims = _access_claims(customer_user, expires_in=timedelta(minutes=5))
    assert _me(client, claims, settings.jwt_secret) == 200

    assert _me(client, claims, "x" * 64) == 401


def test_an_expired_token_is_rejected(client: TestClient, customer_user: User) -> None:
    """Correctly signed and otherwise in order, but past its expiry."""
    claims = _access_claims(customer_user, expires_in=timedelta(minutes=-5))
    assert _me(client, claims, settings.jwt_secret) == 401


def test_a_token_carrying_an_earlier_version_is_rejected(
    client: TestClient, customer_user: User
) -> None:
    """A version behind the account's is dead, however long it had to run."""
    claims = _access_claims(customer_user, expires_in=timedelta(minutes=5))
    claims["tv"] = customer_user.token_version - 1
    assert _me(client, claims, settings.jwt_secret) == 401


def test_logout_ends_every_token_the_account_was_given(
    client: TestClient, customer_user: User
) -> None:
    """Signing out leaves nothing a copied token could still use.

    Two sign-ins, as from two browsers: the second's tokens die with the
    first's, and each is shown working before, so a refusal afterwards is
    the sign-out's doing.
    """
    tokens = [
        client.post(
            "/api/auth/login",
            data={"username": customer_user.email, "password": CUSTOMER_PASSWORD},
        ).json()
        for _ in range(2)
    ]
    headers = [{"Authorization": f"Bearer {t['access_token']}"} for t in tokens]
    assert [client.get("/api/auth/me", headers=h).status_code for h in headers] == [
        200,
        200,
    ]

    out = client.post("/api/auth/logout", headers=headers[0])
    assert out.status_code == 204, out.text

    assert [client.get("/api/auth/me", headers=h).status_code for h in headers] == [
        401,
        401,
    ]
    for pair in tokens:
        again = client.post(
            "/api/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        )
        assert again.status_code == 401

    # A new sign-in is not affected.
    after = client.post(
        "/api/auth/login",
        data={"username": customer_user.email, "password": CUSTOMER_PASSWORD},
    ).json()
    fresh = {"Authorization": f"Bearer {after['access_token']}"}
    assert client.get("/api/auth/me", headers=fresh).status_code == 200


def test_logout_needs_a_signed_in_caller(client: TestClient) -> None:
    assert client.post("/api/auth/logout").status_code == 401


def test_token_for_deleted_user_is_rejected(
    client: TestClient, customer_user: User, db: Session
) -> None:
    headers = {
        "Authorization": "Bearer "
        + client.post(
            "/api/auth/login",
            data={"username": customer_user.email, "password": CUSTOMER_PASSWORD},
        ).json()["access_token"]
    }
    db.delete(customer_user)
    db.commit()
    assert client.get("/api/auth/me", headers=headers).status_code == 401
