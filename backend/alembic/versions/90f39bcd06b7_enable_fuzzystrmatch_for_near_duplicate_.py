"""enable fuzzystrmatch for near-duplicate detection

Revision ID: 90f39bcd06b7
Revises: ffe36996607c

"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "90f39bcd06b7"
down_revision: str | None = "ffe36996607c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # levenshtein(), for finding serials one character apart. A contrib module
    # shipped with PostgreSQL, so this adds no external dependency -- but it
    # is a dependency, and a migration is where one gets recorded rather than
    # discovered when a query fails on a fresh installation.
    op.execute("CREATE EXTENSION IF NOT EXISTS fuzzystrmatch")


def downgrade() -> None:
    op.execute("DROP EXTENSION IF EXISTS fuzzystrmatch")
