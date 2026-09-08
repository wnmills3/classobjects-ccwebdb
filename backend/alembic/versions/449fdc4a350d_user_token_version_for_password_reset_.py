"""user token_version for password-reset revocation

Tokens are stateless JWTs with no server-side store, so there is nothing to
delete when an account should lose access. This column is carried inside every
token and compared against the user row on each request; bumping it on a
password change invalidates every token issued before that moment.

Existing rows start at 1, matching the default, so no one is signed out by the
migration itself.

Revision ID: 449fdc4a350d
Revises: 5c6585b1eadf
Create Date: 2026-09-07 19:45:54.911438

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "449fdc4a350d"
down_revision: str | None = "5c6585b1eadf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "token_version",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "token_version")
