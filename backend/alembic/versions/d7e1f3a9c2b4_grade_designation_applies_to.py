"""Grade designations say whether they are a coin's or a note's.

`grade_designation.applies_to` (coin | currency | any), the marker series,
error types and attributes already carry. EPQ and PPQ are paper qualities;
every other seeded designation (DCAM, FBL, RD, PL ...) is a coin's. The
item editor offers a note only the paper ones, and the API refuses a
designation sent for the wrong kind.

Revision ID: d7e1f3a9c2b4
Revises: c5a9d3e8f174
Create Date: 2026-09-23 23:15:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d7e1f3a9c2b4"
down_revision: str | None = "c5a9d3e8f174"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The type exists already (item_attribute, error_type, series).
_APPLIES = postgresql.ENUM(
    "coin", "currency", "any", name="applies_to", create_type=False
)


def upgrade() -> None:
    """Add the column, every row a coin's, then mark the paper qualities."""
    op.add_column(
        "grade_designation",
        sa.Column("applies_to", _APPLIES, nullable=False, server_default="coin"),
    )
    op.alter_column("grade_designation", "applies_to", server_default=None)
    op.execute(
        sa.text(
            "UPDATE grade_designation SET applies_to = 'currency' "
            "WHERE code IN ('EPQ', 'PPQ')"
        )
    )


def downgrade() -> None:
    """Drop the column; the enum type stays, others use it."""
    op.drop_column("grade_designation", "applies_to")
