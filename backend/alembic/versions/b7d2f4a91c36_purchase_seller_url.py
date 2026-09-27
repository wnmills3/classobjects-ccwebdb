"""The seller's store on a purchase.

The vendor is usually the marketplace -- ebay.com, whatnot.com -- so the
seller on it, who actually sold the pieces, was not recorded. One seller per
purchase: a marketplace order comes from one seller. Added empty.

Revision ID: b7d2f4a91c36
Revises: 9a4c2e7f5b18
Create Date: 2026-09-27 11:30:00.000000

"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b7d2f4a91c36"
down_revision: str | None = "9a4c2e7f5b18"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """The column, empty for every purchase already recorded."""
    op.add_column(
        "purchase_order", sa.Column("seller_url", sa.String(1000), nullable=True)
    )


def downgrade() -> None:
    """The column goes."""
    op.drop_column("purchase_order", "seller_url")
