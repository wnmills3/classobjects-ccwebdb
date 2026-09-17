"""Remember values merged into others.

A value merged into another is deleted (app.reference_merge). This table
keeps the fact, so a seed load does not bring a shipped value back.

Revision ID: b8e1f4a2c975
Revises: a7d4e2c9b813
Create Date: 2026-09-17 09:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b8e1f4a2c975"
down_revision: str | None = "a7d4e2c9b813"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create reference_merge."""
    op.create_table(
        "reference_merge",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("table_name", sa.String(length=64), nullable=False),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("merged_into", sa.String(length=64), nullable=False),
        sa.Column("merged_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("merged_by_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["merged_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("table_name", "code", name="uq_reference_merge"),
    )


def downgrade() -> None:
    """Drop it. Merged values stay deleted; a seed load may bring them back."""
    op.drop_table("reference_merge")
