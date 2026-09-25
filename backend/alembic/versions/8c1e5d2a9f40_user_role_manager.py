"""The console's role is manager, not admin (owner, 2026-09-24).

Renames the `user_role` value in place: every account that was an admin is a
manager, and no row is rewritten.

Revision ID: 8c1e5d2a9f40
Revises: 3f9d1c7a2b64
Create Date: 2026-09-24 23:50:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "8c1e5d2a9f40"
down_revision: str | None = "3f9d1c7a2b64"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """admin -> manager."""
    op.execute("ALTER TYPE user_role RENAME VALUE 'admin' TO 'manager'")


def downgrade() -> None:
    """manager -> admin."""
    op.execute("ALTER TYPE user_role RENAME VALUE 'manager' TO 'admin'")
