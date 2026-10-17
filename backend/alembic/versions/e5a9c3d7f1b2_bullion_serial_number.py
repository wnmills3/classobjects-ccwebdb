"""A serial number on a bullion item's detail row.

Revision ID: e5a9c3d7f1b2
Revises: c8e2a5f1d7b3
Create Date: 2026-10-17 16:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e5a9c3d7f1b2"
down_revision: str | None = "c8e2a5f1d7b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The column, empty, and its index."""
    op.add_column(
        "coin_detail", sa.Column("serial_number", sa.String(64), nullable=True)
    )
    op.create_index(
        "ix_coin_detail_serial_number", "coin_detail", ["serial_number"]
    )


def downgrade() -> None:
    """The index and the column go."""
    op.drop_index("ix_coin_detail_serial_number", table_name="coin_detail")
    op.drop_column("coin_detail", "serial_number")
