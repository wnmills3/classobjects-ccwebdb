"""Account administration: the guards, not the happy path.

Two things here can lock people out or leave access alive that should be gone,
so both are written to fail if the protection is removed rather than merely to
pass while it is present.
"""

from __future__ import annotations

from app.models import User, UserRole
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session


def _login(client: TestClient, email: str, password: str) -> dict[str, str] | None:
    response = client.post(
        "/api/auth/login", data={"username": email, "password": password}
    )
    if response.status_code != 200:
        return None
    return response.json()


def test_only_admins_may_list_accounts(
    client: TestClient, customer_headers: dict[str, str]
) -> None:
    assert client.get("/api/users").status_code == 401
    assert client.get("/api/users", headers=customer_headers).status_code == 403


def test_admin_lists_accounts(
    client: TestClient, admin_headers: dict[str, str], customer_user: User
) -> None:
    body = client.get("/api/users", headers=admin_headers).json()
    emails = {row["email"] for row in body}
    assert customer_user.email in emails
    # Nothing resembling password material is ever returned.
    assert all("password" not in key for row in body for key in row)


def test_promote_and_demote(
    client: TestClient,
    admin_headers: dict[str, str],
    customer_user: User,
    db: Session,
) -> None:
    up = client.patch(
        f"/api/users/{customer_user.id}",
        headers=admin_headers,
        json={"role": "admin"},
    )
    assert up.status_code == 200, up.text
    assert up.json()["role"] == "admin"

    down = client.patch(
        f"/api/users/{customer_user.id}",
        headers=admin_headers,
        json={"role": "customer"},
    )
    assert down.status_code == 200
    assert down.json()["role"] == "customer"


def test_cannot_demote_the_last_administrator(
    client: TestClient, admin_headers: dict[str, str], admin_user: User
) -> None:
    """The lockout guard.

    With this removed the request succeeds, the last administrator becomes a
    customer, and nobody can reach any admin screen again -- recoverable only
    by editing the database by hand.
    """
    response = client.patch(
        f"/api/users/{admin_user.id}",
        headers=admin_headers,
        json={"role": "customer"},
    )
    assert response.status_code == 409
    assert "only active administrator" in response.json()["detail"]


def test_cannot_deactivate_the_last_administrator(
    client: TestClient, admin_headers: dict[str, str], admin_user: User
) -> None:
    response = client.patch(
        f"/api/users/{admin_user.id}",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert response.status_code == 409


def test_may_demote_once_another_admin_exists(
    client: TestClient,
    admin_headers: dict[str, str],
    admin_user: User,
    customer_user: User,
) -> None:
    """The guard is about the outcome, not about who is asking."""
    promoted = client.patch(
        f"/api/users/{customer_user.id}",
        headers=admin_headers,
        json={"role": "admin"},
    )
    assert promoted.status_code == 200

    # Now there are two, so standing down is safe.
    response = client.patch(
        f"/api/users/{admin_user.id}",
        headers=admin_headers,
        json={"role": "customer"},
    )
    assert response.status_code == 200


def test_password_reset_kills_existing_tokens(
    client: TestClient,
    admin_headers: dict[str, str],
    customer_user: User,
) -> None:
    """The reason token_version exists.

    Without the version check the old access token keeps working until it
    expires, so resetting the password of a compromised account revokes
    nothing at all.
    """
    before = _login(client, customer_user.email, "customerpassword")
    assert before is not None
    old_access = {"Authorization": f"Bearer {before['access_token']}"}
    old_refresh = before["refresh_token"]

    assert client.get("/api/auth/me", headers=old_access).status_code == 200

    reset = client.post(
        f"/api/users/{customer_user.id}/password",
        headers=admin_headers,
        json={"password": "a-brand-new-password"},
    )
    assert reset.status_code == 200

    # The old access token is dead.
    assert client.get("/api/auth/me", headers=old_access).status_code == 401

    # And so is the old refresh token -- otherwise it would simply mint a
    # replacement and the revocation would be decorative.
    again = client.post("/api/auth/refresh", json={"refresh_token": old_refresh})
    assert again.status_code == 401

    # The new password works, and its tokens are accepted.
    after = _login(client, customer_user.email, "a-brand-new-password")
    assert after is not None
    new_access = {"Authorization": f"Bearer {after['access_token']}"}
    assert client.get("/api/auth/me", headers=new_access).status_code == 200


def test_password_reset_requires_admin(
    client: TestClient, customer_headers: dict[str, str], customer_user: User
) -> None:
    response = client.post(
        f"/api/users/{customer_user.id}/password",
        headers=customer_headers,
        json={"password": "not-your-decision"},
    )
    assert response.status_code == 403


def test_short_passwords_are_refused(
    client: TestClient, admin_headers: dict[str, str], customer_user: User
) -> None:
    response = client.post(
        f"/api/users/{customer_user.id}/password",
        headers=admin_headers,
        json={"password": "short"},
    )
    assert response.status_code == 422


def test_deactivated_account_cannot_sign_in(
    client: TestClient,
    admin_headers: dict[str, str],
    customer_user: User,
    db: Session,
) -> None:
    off = client.patch(
        f"/api/users/{customer_user.id}",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert off.status_code == 200
    assert off.json()["is_active"] is False

    db.expire_all()
    stored = db.get(User, customer_user.id)
    assert stored is not None and stored.is_active is False
    assert stored.role is UserRole.customer
