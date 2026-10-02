"""The web address a photograph was fetched from.

A photograph filed from a web address -- a seller's listing picture -- kept
only its bytes; where it came from was not recorded. `image.source_url` holds
that address. Added empty: `app.image_sources` fills it for photographs
already stored, from a manifest of the addresses they were fetched from.

Revision ID: a7d3f1c9e5b2
Revises: f6c9e4a1b3d5
Create Date: 2026-10-02 12:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3f1c9e5b2"
down_revision: str | None = "f6c9e4a1b3d5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The column, empty."""
    op.add_column("image", sa.Column("source_url", sa.String(2000), nullable=True))


def downgrade() -> None:
    """The column goes."""
    op.drop_column("image", "source_url")
