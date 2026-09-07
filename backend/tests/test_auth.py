"""Registration, login, token handling and role resolution."""

from __future__ import annotations

import pytest
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


def test_register_cannot_self_assign_admin(client: TestClient, db: Session) -> None:
    """A caller must not be able to become an admin by passing extra fields."""
    response = client.post(
        "/api/auth/register",
        json={
            "email": "sneaky@example.com",
            "password": "longenough",
            "role": "admin",
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
    assert body["access_token"] and body["refresh_token"]
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
    """Token type confusion must be rejected in both directions."""
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


def test_token_signed_with_wrong_secret_is_rejected(client: TestClient) -> None:
    import jwt

    forged = jwt.encode({"sub": "1", "type": "access"}, "x" * 64)
    response = client.get("/api/auth/me", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401


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
