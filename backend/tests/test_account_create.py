"""An administrator creating an account for someone else.

Self-registration in the shop always makes a customer, and promotion was the
only way to an administrator -- so an administrator could not add a colleague,
or open an account for a customer who asked, without that person registering
themselves first. `POST /api/users` closes that gap, for administrators only.
"""

from __future__ import annotations

from app.models import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

NEW = {
    "email": "new.person@example.com",
    "full_name": "New Person",
    "role": "customer",
    "password": "a-long-enough-password",
}


def _accounts(db: Session) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(User)) or 0


def test_only_an_administrator_may_create_an_account(
    client: TestClient, customer_headers: dict[str, str], db: Session
) -> None:
    before = _accounts(db)

    assert client.post("/api/users", json=NEW).status_code == 401
    assert (
        client.post("/api/users", json=NEW, headers=customer_headers).status_code == 403
    )
    # A customer must not be able to mint an administrator, above all.
    as_admin = {**NEW, "role": "manager"}
    assert (
        client.post("/api/users", json=as_admin, headers=customer_headers).status_code
        == 403
    )

    assert _accounts(db) == before


def test_an_administrator_creates_a_customer_who_can_sign_in(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    response = client.post("/api/users", json=NEW, headers=admin_headers)

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["email"] == NEW["email"]
    assert body["full_name"] == "New Person"
    assert body["role"] == "customer"
    assert body["is_active"] is True
    assert all("password" not in key for key in body)

    login = client.post(
        "/api/auth/login", data={"username": NEW["email"], "password": NEW["password"]}
    )
    assert login.status_code == 200


def test_an_administrator_creates_another_administrator(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    body = client.post(
        "/api/users", json={**NEW, "role": "manager"}, headers=admin_headers
    ).json()
    assert body["role"] == "manager"

    tokens = client.post(
        "/api/auth/login", data={"username": NEW["email"], "password": NEW["password"]}
    ).json()
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    # The new administrator really has the rights, not just the label.
    assert client.get("/api/users", headers=headers).status_code == 200


def test_an_email_already_in_use_is_refused_and_the_account_untouched(
    client: TestClient, admin_headers: dict[str, str], customer_user: User, db: Session
) -> None:
    before = _accounts(db)

    response = client.post(
        "/api/users",
        json={**NEW, "email": customer_user.email, "role": "manager"},
        headers=admin_headers,
    )

    assert response.status_code == 409
    assert _accounts(db) == before
    stored = db.get(User, customer_user.id)
    assert stored is not None
    assert stored.role is UserRole.customer


def test_a_short_password_is_refused(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    before = _accounts(db)
    response = client.post(
        "/api/users", json={**NEW, "password": "short"}, headers=admin_headers
    )
    assert response.status_code == 422
    assert _accounts(db) == before


def test_fields_beyond_the_form_are_refused_not_ignored(
    client: TestClient, admin_headers: dict[str, str], db: Session
) -> None:
    """token_version or is_active smuggled in must not be silently accepted."""
    before = _accounts(db)
    for extra in (
        {"is_active": False},
        {"token_version": 99},
        {"hashed_password": "x"},
    ):
        response = client.post(
            "/api/users", json={**NEW, **extra}, headers=admin_headers
        )
        assert response.status_code == 422, extra
    assert _accounts(db) == before


def test_the_role_must_be_named(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """No default: an administrator is never created by leaving a field out."""
    payload = {key: value for key, value in NEW.items() if key != "role"}
    response = client.post("/api/users", json=payload, headers=admin_headers)
    assert response.status_code == 422
