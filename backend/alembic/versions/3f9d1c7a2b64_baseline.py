"""The schema as it stands: the first revision.

Runs `backend/alembic/baseline.sql` -- the tables, views, types, indexes and
the rows a fresh install needs before `python -m app.seeding load`. Every
later change to the schema is a new migration on top of this one.

Revision ID: 3f9d1c7a2b64
Revises:
Create Date: 2026-09-24 20:00:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from alembic import op

revision: str = "3f9d1c7a2b64"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BASELINE = Path(__file__).resolve().parents[1] / "baseline.sql"


def upgrade() -> None:
    """Create the whole schema from the frozen SQL."""
    op.get_bind().exec_driver_sql(BASELINE.read_text(encoding="utf-8"))


def downgrade() -> None:
    """Refuse: there is nothing before the baseline to go back to."""
    raise NotImplementedError(
        "the baseline is the first revision; restore a backup instead"
    )
