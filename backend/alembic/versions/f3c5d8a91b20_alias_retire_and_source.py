"""Aliases can be retired, and say where they came from.

docs/specs/item-attributes-design.md, section 1. Seed loads only add
aliases, so removing a shipped one from the console must leave a row behind
to remember it; `is_active` is that row. `source` tells a shipped alias
(retired when removed) from one an administrator added (deleted when
removed). Existing rows are all shipped.

Revision ID: f3c5d8a91b20
Revises: e4b8c1d27f63
Create Date: 2026-09-16 16:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f3c5d8a91b20"
down_revision: str | None = "e4b8c1d27f63"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SOURCE = postgresql.ENUM(
    "seeded", "derived", "manual", name="provenance_source", create_type=False
)
_TABLES = ("series_alias", "reference_alias")


def upgrade() -> None:
    """Add is_active and source to both alias tables."""
    for table in _TABLES:
        op.add_column(
            table,
            sa.Column(
                "is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "source", _SOURCE, server_default=sa.text("'seeded'"), nullable=False
            ),
        )


def downgrade() -> None:
    """Drop them; retired aliases come back, as they were before."""
    for table in _TABLES:
        op.drop_column(table, "source")
        op.drop_column(table, "is_active")
